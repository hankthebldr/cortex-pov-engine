/**
 * newRun — the model behind the "New POV run" panel (design C1, "CHANGES
 * SINCE v26"): where a run can go, what it will do, and why Run is not yet
 * allowed. Pure functions of the scenario, the agents and the operator's
 * choices, so the panel renders exactly what these return.
 */
import { LIVENESS, agentLiveness } from './setupProgress.js'
import { agentIdOf } from '../../api/ids.js'

export const OFFLINE_BUNDLE = Object.freeze({ kind: 'push', id: 'bundle', label: 'Offline bundle' })

// An agent's liveness as a StatusIcon status + word, and whether a run can be
// sent to it now. Online and stale beacons are choosable (a stale one may
// still poll); an offline beacon, or one that has never polled, is listed so
// the DC sees it, but cannot be picked.
const AGENT_STATE = {
  [LIVENESS.ONLINE]: { status: 'up', word: 'Online', choosable: true },
  [LIVENESS.STALE]: { status: 'degraded', word: 'Stale', choosable: true },
  [LIVENESS.OFFLINE]: { status: 'down', word: 'Offline', choosable: false },
  [LIVENESS.ENROLLED]: { status: 'unknown', word: 'Not polled yet', choosable: false },
  [LIVENESS.UNKNOWN]: { status: 'unknown', word: 'Unknown', choosable: false },
}

/**
 * Every place a run can go: each enrolled agent, then the offline bundle.
 * @param {Array} agents  /api/agents rows
 * @returns {Array<{kind, id, label, sub, status, word, choosable}>}
 */
export function runTargets(agents = []) {
  const rows = (agents || []).map((a) => {
    const id = agentIdOf(a)
    const live = agentLiveness(a)
    const st = AGENT_STATE[live.state] || AGENT_STATE[LIVENESS.UNKNOWN]
    return {
      kind: 'agent',
      id,
      label: a.hostname || a.host || id,
      sub: [a.os || a.platform, 'agent'].filter(Boolean).join(' · '),
      ...st,
    }
  })
  rows.push({
    ...OFFLINE_BUNDLE,
    sub: 'You run the bundle on the target. POVengine takes your word until it reports in.',
    status: 'unknown',
    word: 'Unknown',
    choosable: true,
  })
  return rows
}

function cleanupCount(scenario) {
  const c = scenario?.cleanup
  if (Array.isArray(c)) return c.length
  if (c && Array.isArray(c.commands)) return c.commands.length
  return 0
}

/** What a beacon advertises when it runs the scenario's cleanup (agent/capabilities.go). */
export const CLEANUP_CAPABILITY = 'cleanup'

/**
 * Who runs the scenario's cleanup, in one short line. A bundle (bash,
 * PowerShell, the k8s bootstrap) runs it itself. A beacon runs it after the
 * run only when it advertises `cleanup`; an older one is sent the commands and
 * ignores them (SimCore's CLEANUP_NOT_RUN), so the DC runs them. An agent
 * whose record has not loaded is not credited.
 */
function teardownLine(count, mode, agent) {
  if (count === 0) return 'nothing to tear down'
  const n = `${count} command${count === 1 ? '' : 's'}`
  if (mode === 'push') return `${n}, run by the bundle`
  const caps = Array.isArray(agent?.capabilities) ? agent.capabilities : []
  if (caps.includes(CLEANUP_CAPABILITY)) return `${n}, run by the agent after the run`
  return `${n}, run ${count === 1 ? 'it' : 'them'} yourself`
}

/**
 * What the run will do, as label/value rows.
 * @param {Object} scenario  the full scenario document
 * @param {{mode: 'pull'|'push', identity?: string, agent?: Object|null}} opts
 *   `agent` is the chosen agent's /api/agents row (its `capabilities`).
 */
export function runPlan(scenario, { mode, identity, agent = null } = {}) {
  const steps = (scenario?.steps || []).filter((s) => s && typeof s === 'object')
  const detections = steps.reduce((n, s) => n + (s.expected_detections || []).length, 0)
  const teardown = teardownLine(cleanupCount(scenario), mode, agent)
  return [
    { k: 'Steps', v: String(steps.length) },
    { k: 'Detections it should raise', v: String(detections) },
    { k: 'Runs as', v: identity || 'scenario default' },
    { k: 'Writes to Cortex or the firewall', v: 'never' },
    { k: 'Teardown', v: teardown },
  ]
}

/**
 * Why Run is not allowed yet, in the operator's words. Empty means Run.
 * @param {Object} opts
 * @param {string[]} opts.base        the launch hook's and the gate's own reasons
 * @param {Object|null} opts.target   the chosen target
 * @param {boolean} opts.authorised   the authorisation tick
 */
export function runBlockers({ base = [], target = null, authorised = false } = {}) {
  const out = []
  if (!target) out.push('Choose where it runs')
  if (!authorised) out.push('Confirm you are authorised to run it on this tenant')
  for (const b of base) if (!out.includes(b)) out.push(b)
  return out
}
