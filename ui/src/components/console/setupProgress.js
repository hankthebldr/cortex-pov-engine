/**
 * setupProgress.js — what this instance still needs before a POV can run.
 *
 * Pure: takes what the EnvironmentProvider already fetched (agents, tenants,
 * runs) plus the DC's own confirmations, and returns the Get started checklist.
 * No literals stand in for state — every `done` is either read from SimCore or
 * explicitly confirmed by the person at the keyboard, and the two are labelled
 * differently (`source: 'verified' | 'confirmed'`) so the page never presents
 * a click as a measurement. The agent step has a third value,
 * `'awaiting_first_poll'`: SimCore holds an enrolled row but no beacon has
 * polled, which is neither a verified agent nor nothing.
 *
 * WHY TWO STEPS ARE SELF-CONFIRMED
 * The data collector and the Broker VM live in the customer's Cortex tenant.
 * SimCore has no API that can see either one today, so it cannot honestly mark
 * them done. The alternatives were to hide them (and leave a DC to discover
 * mid-POV that no network telemetry arrives) or to fake a check. Instead they
 * are guided steps the DC confirms, shown as "you confirmed", never "verified".
 *
 * WHY TWO STEPS ARE OPTIONAL
 * Endpoint scenarios need only an agent. The collector and the Broker VM matter
 * for network-plane and third-party data-stream scenarios, so they are listed —
 * with what they unlock — but they do not hold the "ready" state hostage.
 */

import { parseServerTime } from '../../api/time.js'
import { installerOS } from './deployCommands.js'

export const STEP_IDS = ['agent', 'tenant', 'collector', 'bvm', 'simulate']

// ── Agent liveness ───────────────────────────────────────────────────────────
// ONE model for every surface that colours an agent (Get started, the Agents
// cards, the header switcher). Three surfaces each deriving their own answer is
// how a row SimCore calls offline read "live" on one of them.
//
// Precedence, and why:
//   1. ENROLLED beats everything. `POST /api/agents/enroll` writes the row with
//      registered_at == last_seen and status 'online', and `GET /api/agents`
//      then derives 'online' for 30 s — before any beacon has polled. The
//      Windows installer enrols with capabilities ['shell']; only a running
//      beacon re-registers with 'powershell'. Either signature means "the
//      installer got this far", not "a beacon is talking to SimCore".
//   2. An explicit server status (online / stale / offline) is the answer.
//      It is measured on SimCore's clock; the browser's can be minutes off.
//      An unrecognised value is UNKNOWN — never a reason to fall back to age.
//   3. Only a row with no status falls back to age: SimCore's own
//      last_seen_age_seconds first, the browser clock last.
export const LIVENESS = Object.freeze({
  ONLINE: 'online',
  STALE: 'stale',
  OFFLINE: 'offline',
  ENROLLED: 'enrolled',
  UNKNOWN: 'unknown',
})

export const ENROLLED_LABEL = 'enrolled — waiting for first beacon poll'

// 'active' / 'idle' / 'busy' are what older builds cached in Agent.status
// before GET /api/agents derived liveness at read time.
const SERVER_ONLINE = new Set(['online', 'active', 'idle', 'busy'])
// Fallback only: 60 s matches the beacon's default poll window with headroom.
const FALLBACK_ONLINE_S = 60
// SimCore reports a never-seen row's age as 1e9 s.
const NEVER_S = 1e8

/** True when the row was written by enrolment and no beacon has polled since. */
export function awaitingFirstPoll(a) {
  if (!a || typeof a !== 'object') return false
  const reg = parseServerTime(a.registered_at)
  const seen = parseServerTime(a.last_seen)
  // "To the second": both columns come from ONE utcnow() at enrolment.
  if (Number.isFinite(reg) && Number.isFinite(seen) && Math.floor(reg / 1000) === Math.floor(seen / 1000)) {
    return true
  }
  if (installerOS(a.os || a.platform) === 'windows') {
    const caps = Array.isArray(a.capabilities) ? a.capabilities.map((c) => String(c).toLowerCase()) : []
    if (!caps.includes('powershell')) return true
  }
  return false
}

/**
 * @param {Object} a       an /api/agents row
 * @param {number} [nowMs] browser clock, only for rows SimCore gave no age
 * @returns {{state: string, label: string, ageSeconds: number|null,
 *            ageSource: 'server'|'browser'|null, source: string|null}}
 */
export function agentLiveness(a, nowMs = Date.now()) {
  if (!a || typeof a !== 'object') {
    return { state: LIVENESS.UNKNOWN, label: 'unknown', ageSeconds: null, ageSource: null, source: null }
  }
  const serverAge = typeof a.last_seen_age_seconds === 'number' && Number.isFinite(a.last_seen_age_seconds)
    ? a.last_seen_age_seconds
    : null
  const seenMs = parseServerTime(a.last_seen || a.last_seen_at || a.updated_at)
  const browserAge = Number.isFinite(seenMs) ? Math.max(0, (nowMs - seenMs) / 1000) : null
  const ageSeconds = serverAge !== null ? (serverAge >= NEVER_S ? null : serverAge) : browserAge
  const ageSource = serverAge !== null ? 'server' : browserAge !== null ? 'browser' : null
  const out = (state, label, source) => ({ state, label, ageSeconds, ageSource, source })

  if (awaitingFirstPoll(a)) return out(LIVENESS.ENROLLED, ENROLLED_LABEL, 'enrollment')

  const raw = a.status == null ? '' : String(a.status).trim()
  const st = raw.toLowerCase()
  if (st) {
    if (SERVER_ONLINE.has(st)) return out(LIVENESS.ONLINE, 'online', 'server-status')
    if (st === 'stale') return out(LIVENESS.STALE, 'stale', 'server-status')
    if (st === 'offline') return out(LIVENESS.OFFLINE, 'offline', 'server-status')
    return out(LIVENESS.UNKNOWN, `status "${raw}" not recognised`, 'server-status')
  }
  if (serverAge !== null) {
    return serverAge < FALLBACK_ONLINE_S
      ? out(LIVENESS.ONLINE, 'online', 'server-age')
      : out(LIVENESS.STALE, 'stale', 'server-age')
  }
  if (browserAge !== null) {
    return browserAge < FALLBACK_ONLINE_S
      ? out(LIVENESS.ONLINE, 'online', 'browser-clock')
      : out(LIVENESS.STALE, 'stale', 'browser-clock')
  }
  return out(LIVENESS.UNKNOWN, 'never seen', null)
}

export function agentIsOnline(a) {
  return agentLiveness(a).state === LIVENESS.ONLINE
}

/** A run that executed its chain. Detections may or may not have fired —
 *  that is the result, not a setup problem. */
export function runSucceeded(r) {
  const st = String(r?.status || '').toLowerCase()
  return st === 'completed' || st === 'complete' || st === 'succeeded' || st === 'partial'
}

/** A run that stopped before its chain finished (a step exited non-zero, the
 *  identity was missing, it was aborted). Setup is not done until one runs. */
export function runFailed(r) {
  const st = String(r?.status || '').toLowerCase()
  return st === 'failed' || st === 'error' || st === 'aborted' || st === 'cancelled'
}

/**
 * @param {Object} input
 * @param {Object[]} input.agents
 * @param {Object[]} input.tenants   registered XSIAM tenants
 * @param {Object[]} input.runs
 * @param {Object}   input.confirmed { collector?: boolean, bvm?: boolean }
 * @returns {{ steps: Object[], requiredLeft: number, next: Object|null, ready: boolean }}
 */
export function setupProgress({ agents = [], tenants = [], runs = [], confirmed = {} } = {}) {
  const online = agents.filter(agentIsOnline)
  const waiting = agents.filter((a) => agentLiveness(a).state === LIVENESS.ENROLLED)
  const succeeded = runs.filter(runSucceeded)
  const lastFailed = !succeeded.length ? runs.find(runFailed) || null : null

  const notOnline = agents.length - waiting.length
  const steps = [
    {
      id: 'agent',
      title: 'Install an agent',
      why: 'The agent runs each simulation step on a host you control.',
      required: true,
      done: online.length > 0,
      // A row the installer only enrolled is not a measurement of a beacon, so
      // it never earns 'verified' — the step says what it is waiting for.
      source: online.length || !waiting.length ? 'verified' : 'awaiting_first_poll',
      status: online.length
        ? `${online.length} online — ${online.map((a) => a.hostname || a.agent_id).slice(0, 3).join(', ')}`
        : waiting.length
          ? `${waiting.length} ${ENROLLED_LABEL}${notOnline ? ` · ${notOnline} registered, not online` : ''}`
          : agents.length
            ? `${agents.length} registered, none online`
            : 'No agent has checked in yet',
    },
    {
      id: 'tenant',
      title: 'Connect your Cortex tenant',
      why: 'POVengine reads alerts back from the tenant to show which detections fired.',
      required: true,
      done: tenants.length > 0,
      source: 'verified',
      status: tenants.length
        ? `Connected to ${tenants.map((t) => t.name || t.id).join(', ')}`
        : 'No tenant connected',
    },
    {
      id: 'collector',
      title: 'Set up a data collector',
      why: 'Needed for third-party log scenarios (syslog, HTTP). Skip for endpoint-only POVs.',
      required: false,
      done: !!confirmed.collector,
      source: 'confirmed',
      status: confirmed.collector ? 'You confirmed this is set up' : 'Optional',
    },
    {
      id: 'bvm',
      title: 'Set up the Broker VM',
      why: 'Relays network and syslog traffic into the tenant. Needed for network-plane scenarios.',
      required: false,
      done: !!confirmed.bvm,
      source: 'confirmed',
      status: confirmed.bvm ? 'You confirmed this is set up' : 'Optional',
    },
    {
      id: 'simulate',
      title: 'Run your first simulation',
      why: 'Launch a scenario on the agent and watch the expected detections come back.',
      required: true,
      done: succeeded.length > 0,
      source: 'verified',
      // A failed run is the most useful state to be specific about: the chain
      // did not execute, so nothing was proven yet, and the reason is one
      // click away in Runs.
      failedRun: lastFailed,
      status: succeeded.length
        ? `${succeeded.length} run${succeeded.length === 1 ? '' : 's'} completed`
        : lastFailed
          ? 'Your last run stopped before it finished — open it to see which step failed'
          : runs.length ? 'A run is in progress' : 'No runs yet',
    },
  ]

  const requiredLeft = steps.filter((s) => s.required && !s.done).length
  // The next step is the first unfinished REQUIRED one; optional steps are
  // offered, never pushed.
  const next = steps.find((s) => s.required && !s.done) || null
  return { steps, requiredLeft, next, ready: requiredLeft === 0 }
}

// ── Confirmations ────────────────────────────────────────────────────────────
// Per browser, because there is no SimCore endpoint to hold them. Losing them
// (private window, cleared storage) only re-shows two optional steps.
const LS_KEY = 'cortexsim.setup.confirmed.v1'

export function readConfirmed() {
  try {
    const raw = window.localStorage.getItem(LS_KEY)
    const v = raw ? JSON.parse(raw) : {}
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

export function writeConfirmed(next) {
  try { window.localStorage.setItem(LS_KEY, JSON.stringify(next)) } catch { /* storage blocked */ }
}
