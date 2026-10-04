/**
 * setupProgress.js — what this instance still needs before a POV can run.
 *
 * Pure: takes what the EnvironmentProvider already fetched (agents, tenants,
 * runs) plus the DC's own confirmations, and returns the Get started checklist.
 * No literals stand in for state — every `done` is either read from SimCore or
 * explicitly confirmed by the person at the keyboard, and the two are labelled
 * differently (`source: 'verified' | 'confirmed'`) so the page never presents
 * a click as a measurement.
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

export const STEP_IDS = ['agent', 'tenant', 'collector', 'bvm', 'simulate']

const ONLINE = new Set(['online', 'active', 'idle', 'busy'])

export function agentIsOnline(a) {
  if (!a) return false
  const st = String(a.status || '').toLowerCase()
  if (ONLINE.has(st)) return true
  // Some SimCore builds report only the age; 60s matches the beacon's default
  // poll window with headroom for a slow tick.
  return typeof a.last_seen_age_seconds === 'number' && a.last_seen_age_seconds < 60
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
  const succeeded = runs.filter(runSucceeded)
  const lastFailed = !succeeded.length ? runs.find(runFailed) || null : null

  const steps = [
    {
      id: 'agent',
      title: 'Install an agent',
      why: 'The agent runs each simulation step on a host you control.',
      required: true,
      done: online.length > 0,
      source: 'verified',
      status: online.length
        ? `${online.length} online — ${online.map((a) => a.hostname || a.agent_id).slice(0, 3).join(', ')}`
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
