/**
 * runChain.js — what each card on Run detail's live chain says (design v26
 * §4.6, §9.3). Pure, no React.
 *
 * Two sources, the same split useRunStepTail keeps:
 *   - the host's record of each step (`buildStepRows`: ok · error · severed ·
 *     not_reached · running · queued · staged) says whether it RAN;
 *   - the run's Result rows say whether its detection was SEEN — and they mean
 *     something only once the run has been read back from the tenant.
 *
 * So before the read back a card says only "Ran — not read back" or "Did not
 * run" (a step with no record yet is left Waiting). After it: Detected when a
 * row for the step is a hit, Broken when the step failed on the host and
 * nothing was seen, Not seen when it expected a detection that did not come.
 * A step that expected nothing keeps "Ran" — never "Not seen", which would
 * claim an absence nobody looked for.
 */
import { PHASE } from './runLifecycle.js'
import { componentDestination } from './searchIndex.js'

const RAN = new Set(['ok', 'error', 'severed'])
const DID_NOT_RUN_OUTCOMES = new Set(['not_executed', 'not_reached'])

function isHit(r) {
  if (!r) return false
  if (r.outcome === 'detected' || r.outcome === 'prevented') return true
  return r.outcome == null && r.observed === true
}

/**
 * @param {Array<{id: string, status: string}>} stepRows  `buildStepRows` output
 * @param {string} phase                               runLifecycle phase
 * @param {Array} results                              raw /api/results rows (step_id, outcome, observed)
 * @returns {Object<string, string>}  stepId → a ChainStage live state
 */
export function liveStepStates(stepRows = [], phase, results = []) {
  const byStep = new Map()
  for (const r of Array.isArray(results) ? results : []) {
    if (!r || !r.step_id) continue
    if (!byStep.has(r.step_id)) byStep.set(r.step_id, [])
    byStep.get(r.step_id).push(r)
  }
  const readBack = phase === PHASE.READY
  const states = {}
  for (const row of stepRows) {
    if (!row || !row.id) continue
    const rs = byStep.get(row.id) || []
    if (row.status === 'not_reached'
      || (rs.length > 0 && rs.every((r) => DID_NOT_RUN_OUTCOMES.has(r.outcome)))) {
      states[row.id] = 'did_not_run'
      continue
    }
    if (!readBack) {
      if (RAN.has(row.status)) states[row.id] = 'ran'
      continue
    }
    if (rs.some(isHit)) states[row.id] = 'detected'
    else if (row.status === 'error' || row.status === 'severed') states[row.id] = 'broken'
    else if (rs.length) states[row.id] = 'not_seen'
    else if (RAN.has(row.status)) states[row.id] = 'ran'
  }
  return states
}

/**
 * The component behind each signal-path lane, for the lane-header button. Keys
 * are the component table's (`componentModel.js`), so the rail, the header,
 * ⌘K and the run chain all open the same page for a component. Every lane
 * ChainStage turns into a button has a destination, so none is a dead end.
 */
const LANE_COMPONENT = {
  ENDPOINT: 'agent',    // the beacon runs it; the Cortex Agent sees the process
  NONE: 'agent',        // the step only logs or waits, on the beacon
  COLLECTOR: 'http',    // records POSTed to an HTTP collector or Broker VM
  WIRE: 'ngfw',         // a sensor on the wire: the NGFW
  CLOUD: 'tenant',      // the provider's audit log, ingested by the tenant
  ENG: 'tenant',        // tenant content: correlation rules
}

/** The route ({ destination, params? }) behind a lane, or null. */
export function laneDestination(laneKey, isValid = () => true) {
  const key = LANE_COMPONENT[laneKey]
  return key ? componentDestination(key, isValid) : null
}
