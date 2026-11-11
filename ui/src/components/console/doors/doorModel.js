/**
 * doorModel.js — pure reading of a door-readiness object for the Library chip
 * and the launch preflight's Data path check.
 *
 * Shape (core/engine/door_resolution.py::scenario_readiness):
 *   { scenario_id, state: ready|partial|needs_doors|not_evaluated, scorable,
 *     total, underived, route_blocked, unevaluated,
 *     closed: [{door_id, detections}], agent_scoped_evaluated }
 *
 * HONESTY RULES
 *   - Absent readiness is NOT CHECKED, never READY: "we were not told" is not
 *     "every door is open".
 *   - A state this console does not recognise is surfaced as an error tone with
 *     the offending value in the title. It is never mapped onto a known state.
 *   - Phase 1 writes no door events, so nothing reads `open` yet; the common
 *     answers are NEEDS DOORS and NOT CHECKED. That is the truth, not a bug.
 */

export const KNOWN_STATES = ['ready', 'partial', 'needs_doors', 'not_evaluated']

/**
 * Classify a readiness value into one of: 'absent' | 'unrecognised' | a known state.
 * Shared by the chip and the preflight check so the two can never disagree.
 */
export function readinessKind(readiness) {
  if (readiness === undefined || readiness === null) return 'absent'
  if (typeof readiness !== 'object' || !KNOWN_STATES.includes(readiness.state)) return 'unrecognised'
  return readiness.state
}

/** Closed door ids, most-held-back first (the server already sorts them). */
export function closedDoorIds(readiness) {
  const closed = Array.isArray(readiness && readiness.closed) ? readiness.closed : []
  return closed.map((c) => c && c.door_id).filter(Boolean)
}

/**
 * @returns {{label: string, tone: 'ready'|'partial'|'needs'|'unknown'|'error', title: string}}
 */
export function chipFor(readiness) {
  const kind = readinessKind(readiness)
  if (kind === 'absent') {
    return { label: 'NOT CHECKED', tone: 'unknown', title: 'door readiness not reported' }
  }
  if (kind === 'unrecognised') {
    const shown = readiness && typeof readiness === 'object' ? JSON.stringify(readiness.state) : typeof readiness
    return {
      label: 'NOT CHECKED', tone: 'error',
      title: `unrecognised door readiness state ${shown}`,
    }
  }
  if (kind === 'ready') {
    return { label: 'READY', tone: 'ready', title: 'every door this scenario needs is open' }
  }
  if (kind === 'partial') {
    const k = readiness.scorable
    const n = readiness.total
    const doors = closedDoorIds(readiness)
    return {
      label: `PARTIAL ${k}/${n}`, tone: 'partial',
      title: `${k} of ${n} detections can be scored`
        + (doors.length ? `; closed: ${doors.join(', ')}` : ''),
    }
  }
  if (kind === 'needs_doors') {
    const doors = closedDoorIds(readiness)
    return {
      label: 'NEEDS DOORS', tone: 'needs',
      title: doors.length ? `closed doors: ${doors.join(', ')}` : 'no detection can be scored yet',
    }
  }
  return { label: 'NOT CHECKED', tone: 'unknown', title: 'no detection could be judged' }
}

/** Ready or partial: at least one door-backed detection can be scored. */
export function isRunnable(readiness) {
  const kind = readinessKind(readiness)
  return kind === 'ready' || kind === 'partial'
}

/**
 * The opt-in "Runnable here" filter. Off (the default) returns the input
 * untouched — nothing is ever hidden unless the operator asks. On, a scenario
 * with no readiness entry is not runnable: absence is not evidence.
 */
export function filterRunnable(scenarios, readinessMap, enabled) {
  if (!enabled) return scenarios
  const map = readinessMap || {}
  return scenarios.filter((s) => isRunnable(map[s.scenario_id || s.id]))
}
