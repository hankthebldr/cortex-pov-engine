/**
 * componentModel.js — the eight components a POV runs on, and what each one's
 * status is right now (design v26 §1, §4.2, §9.2).
 *
 * Pure: it takes what the console already read from SimCore and returns one
 * status per component. The rail's status dots, the "N issues" count and the
 * Components page all read this one function, so a component cannot be green
 * in one place and unknown in another.
 *
 * THE RULE (v26 §0.1, §7): a status is either read from SimCore or it is not
 * green. Where SimCore has no API for a component (Broker VM, XDR Collector,
 * Cortex Engine) it reads Unknown — "Can't check", or "You confirmed" once the
 * DC ticked it on Get started — and never Up. A list that has not arrived is
 * Unknown, never Off: "none" is a fact only once SimCore has said so.
 *
 * Each status is { status, word?, detail }:
 *   status  up | degraded | down | off | unknown   (StatusIcon's vocabulary)
 *   word    overrides the StatusIcon word for the same tone ("You confirmed")
 *   detail  one line, plain words: what was read, or why nothing could be
 *   issue   Degraded / Down only: a short name for the problem (≤ 24 chars),
 *           the ErrorBadge text that opens the fix
 */
import { agentLiveness, LIVENESS } from './setupProgress.js'

export const COMPONENTS = Object.freeze([
  { key: 'pov',    name: 'POVengine',      icon: 'settings-edit' },
  { key: 'agent',  name: 'Agents',         icon: 'fingerprint-scan' },
  { key: 'tenant', name: 'Tenant',         icon: 'people' },
  { key: 'http',   name: 'HTTP collector', icon: 'doc-search' },
  { key: 'bvm',    name: 'Broker VM',      icon: 'building' },
  { key: 'ngfw',   name: 'Firewall',       icon: 'threat-network' },
  { key: 'xdrc',   name: 'XDR Collector',  icon: 'apps-grid' },
  { key: 'engine', name: 'Cortex Engine',  icon: 'keyboard' },
])

export const COMPONENT_KEYS = COMPONENTS.map((c) => c.key)

/**
 * The Configure step ids of the components SimCore has no API for. Their
 * status is the DC's ticks on those steps (stored as '<key>:<step id>'):
 * every step ticked reads "You confirmed"; anything less reads Can't check.
 */
export const COMPONENT_STEPS = Object.freeze({
  bvm: ['image', 'deploy', 'console', 'register', 'syslog', 'reach'],
  xdrc: ['installer', 'install', 'profile', 'policy', 'arrive'],
  engine: ['create', 'install', 'run'],
})

const NO_READ = {
  bvm: 'POVengine has no way to see a Broker VM',
  xdrc: 'POVengine has no way to see an XDR Collector',
  engine: 'POVengine has no way to see a Cortex Engine',
}

function confirmedStatus(key, confirmed, extraYes = false) {
  const ids = COMPONENT_STEPS[key]
  const ticked = ids.filter((id) => confirmed?.[`${key}:${id}`]).length
  if (extraYes || ticked === ids.length) {
    return { status: 'unknown', word: YOU_CONFIRMED, detail: `You confirmed it is set up; ${NO_READ[key].replace('has no way to see', 'cannot see')}` }
  }
  return {
    status: 'unknown',
    word: CANT_CHECK,
    detail: ticked ? `${NO_READ[key]} · you confirmed ${ticked} of ${ids.length} steps` : NO_READ[key],
  }
}

export function componentByKey(key) {
  return COMPONENTS.find((c) => c.key === key) || null
}

const CANT_CHECK = "Can't check"
const YOU_CONFIRMED = 'You confirmed'

// Worst first. Unknown sits above Up: one tenant that was never tested next to
// one that passed is not a healthy pair.
const RANK = { down: 4, degraded: 3, unknown: 2, off: 1, up: 0 }
function worst(list) {
  return list.reduce((acc, s) => (RANK[s.status] > RANK[acc.status] ? s : acc), list[0])
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

// ── POVengine ───────────────────────────────────────────────────────────────
function povStatus(health) {
  if (!health) return { status: 'unknown', detail: 'No answer from /api/health yet' }
  if (!health.reachable) return { status: 'down', issue: 'Not answering', detail: 'POVengine did not answer /api/health' }
  const degraded = Array.isArray(health.degraded) ? health.degraded : []
  if (degraded.length) {
    return { status: 'degraded', issue: 'Components degraded', detail: `Not ok: ${degraded.join(', ')}` }
  }
  const gaps = Array.isArray(health.gaps) ? health.gaps.length : 0
  return {
    status: 'up',
    detail: gaps
      ? `Every reported component ok · ${gaps} not reported by this build`
      : 'Every component ok',
  }
}

// ── Agents ──────────────────────────────────────────────────────────────────
function agentStatus(agents, loaded, error) {
  if (error) return { status: 'unknown', detail: 'Could not read the agent list' }
  if (!loaded) return { status: 'unknown', detail: 'Reading the agent list' }
  if (!agents.length) return { status: 'off', detail: 'No agent has enrolled' }
  const states = agents.map((a) => agentLiveness(a).state)
  const online = states.filter((s) => s === LIVENESS.ONLINE).length
  const waiting = states.filter((s) => s === LIVENESS.ENROLLED).length
  const stale = states.filter((s) => s === LIVENESS.STALE).length
  if (online === agents.length) return { status: 'up', detail: `${plural(online, 'agent')} online` }
  if (online > 0) return { status: 'degraded', issue: 'Agents offline', detail: `${online} of ${agents.length} online` }
  if (waiting === agents.length) {
    return { status: 'degraded', issue: 'Waiting for first poll', detail: 'Enrolled — waiting for the first beacon poll' }
  }
  // Stale is SimCore's word for "seen, but not within the poll window" — a
  // beacon that may still come back, not one that is gone.
  if (stale > 0) {
    return { status: 'degraded', issue: 'Agents stale', detail: `None online · ${stale} stale` }
  }
  return { status: 'down', issue: 'None online', detail: `None online (${plural(agents.length, 'agent')} enrolled)` }
}

// ── Tenant ──────────────────────────────────────────────────────────────────
// `last_verified_*` is written by POST /api/xsiam/tenants/{name}/test and by
// the reconcile pull — a real answer from the tenant. A key that was stored and
// never tested is Unknown, not Up.
function tenantStatus(tenants, loaded, error) {
  if (error) return { status: 'unknown', detail: 'Could not read the tenant list' }
  if (!loaded) return { status: 'unknown', detail: 'Reading the tenant list' }
  if (!tenants.length) return { status: 'off', detail: 'No tenant connected' }
  const each = tenants.map((t) => {
    const name = t.name || t.id
    if (t.last_verified_ok === true) return { status: 'up', detail: `${name}: last test passed` }
    if (t.last_verified_ok === false) {
      return {
        status: 'down',
        issue: 'Tenant test failed',
        detail: `${name}: last test failed${t.last_verified_error ? ` — ${t.last_verified_error}` : ''}`,
      }
    }
    return { status: 'unknown', detail: `${name}: key stored, never tested` }
  })
  return worst(each)
}

// ── HTTP collector ──────────────────────────────────────────────────────────
// The collector target's stored preflight verdict (target_preflight.py).
// `accepted` is the only rung that saw the collector take a record; a target
// that was only reachable has not been shown to accept anything.
const VERDICT = {
  accepted: { status: 'up', text: 'accepted a test record' },
  reachable: { status: 'degraded', text: 'reachable, nothing written yet', issue: 'Nothing written yet' },
  refused: { status: 'down', text: 'refused the test record', issue: 'Collector refused' },
  unreachable: { status: 'down', text: 'unreachable', issue: 'Collector unreachable' },
  not_configured: { status: 'down', text: 'not configured', issue: 'Target not configured' },
}
function httpStatus(targets, error, confirmed) {
  if (error) return { status: 'unknown', detail: 'Could not read the collector targets' }
  if (!Array.isArray(targets)) return { status: 'unknown', detail: 'Reading the collector targets' }
  const collectors = targets.filter((t) => (t.kind || 'xsiam_http_collector') === 'xsiam_http_collector')
  if (!collectors.length) {
    return confirmed.collector
      ? { status: 'unknown', word: YOU_CONFIRMED, detail: 'You confirmed a collector exists; POVengine holds no target for it' }
      : { status: 'off', detail: 'No collector target' }
  }
  const each = collectors.map((t) => {
    const v = VERDICT[t.last_check?.verdict]
    if (!v) return { status: 'unknown', detail: `${t.name}: never checked` }
    const code = t.last_check?.code ? ` (${t.last_check.code})` : ''
    return {
      status: v.status,
      ...(v.issue ? { issue: v.issue } : {}),
      detail: `${t.name}: ${v.text}${v.status === 'down' ? code : ''}`,
    }
  })
  return worst(each)
}

// ── Firewall ────────────────────────────────────────────────────────────────
// The registry stores only `last_verified_ok = overall != blocked`, so a pass
// cannot tell ok from degraded. It reads "Not blocked" in the unknown tone and
// the Firewall page's own check gives the full verdict.
function ngfwStatus(firewalls, error) {
  if (error) return { status: 'unknown', detail: 'Could not read the firewall registry' }
  if (!Array.isArray(firewalls)) return { status: 'unknown', detail: 'Reading the firewall registry' }
  if (!firewalls.length) return { status: 'off', detail: 'No firewall registered' }
  const each = firewalls.map((f) => {
    if (f.last_verified_ok === false) {
      return {
        status: 'down',
        issue: 'Firewall check blocked',
        detail: `${f.name}: last check blocked${f.last_verified_error ? ` — ${f.last_verified_error}` : ''}`,
      }
    }
    if (f.last_verified_ok === true) {
      return { status: 'unknown', word: 'Not blocked', detail: `${f.name}: last check not blocked — open the check for ok or degraded` }
    }
    return { status: 'unknown', detail: `${f.name}: registered, never checked` }
  })
  return worst(each)
}

/**
 * @param {Object} s
 * @param {Object|null} s.healthModel       buildHealthModel(/api/health)
 * @param {Object[]}    s.agents
 * @param {boolean}     s.agentsLoaded
 * @param {string|null} s.agentsError
 * @param {Object[]}    s.tenants           xsiam_tenant integrations
 * @param {boolean}     s.tenantsLoaded
 * @param {string|null} s.tenantsError
 * @param {Object[]|null} s.collectorTargets GET /api/collector-targets targets[]
 * @param {string|null} s.collectorError
 * @param {Object[]|null} s.firewalls        GET /api/ngfw firewalls[]
 * @param {string|null} s.firewallError
 * @param {Object}      s.confirmed         Get started's "you confirmed" ticks
 * @returns {Object<string, {status, word?, detail}>}
 */
export function componentStatuses({
  healthModel = null,
  agents = [], agentsLoaded = true, agentsError = null,
  tenants = [], tenantsLoaded = true, tenantsError = null,
  collectorTargets = null, collectorError = null,
  firewalls = null, firewallError = null,
  confirmed = {},
} = {}) {
  return {
    pov: povStatus(healthModel),
    agent: agentStatus(Array.isArray(agents) ? agents : [], agentsLoaded, agentsError),
    tenant: tenantStatus(Array.isArray(tenants) ? tenants : [], tenantsLoaded, tenantsError),
    http: httpStatus(collectorTargets, collectorError, confirmed || {}),
    // Get started's own Broker VM tick counts as confirming the whole thing.
    bvm: confirmedStatus('bvm', confirmed || {}, !!confirmed?.bvm),
    ngfw: ngfwStatus(firewalls, firewallError),
    xdrc: confirmedStatus('xdrc', confirmed || {}),
    engine: confirmedStatus('engine', confirmed || {}),
  }
}

/** Degraded and Down are issues; Off (not set up) and Unknown are not. */
export function issueCount(statuses) {
  return Object.values(statuses || {}).filter((s) => s && (s.status === 'degraded' || s.status === 'down')).length
}
