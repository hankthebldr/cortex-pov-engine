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

/**
 * What the run will do, as label/value rows.
 * @param {Object} scenario  the full scenario document
 * @param {{mode: 'pull'|'push', identity?: string}} opts
 */
export function runPlan(scenario, { mode, identity } = {}) {
  const steps = (scenario?.steps || []).filter((s) => s && typeof s === 'object')
  const detections = steps.reduce((n, s) => n + (s.expected_detections || []).length, 0)
  const cleanup = cleanupCount(scenario)
  let teardown
  if (cleanup === 0) teardown = 'nothing to tear down'
  else if (mode === 'push') teardown = `${cleanup} command${cleanup === 1 ? '' : 's'}, run by the bundle`
  else teardown = `${cleanup} command${cleanup === 1 ? '' : 's'}, not run by the agent — run them yourself`
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
