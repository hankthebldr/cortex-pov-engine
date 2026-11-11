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

/** Door ids the readiness lists as not open, most-held-back first. */
export function closedDoorIds(readiness) {
  const closed = Array.isArray(readiness && readiness.closed) ? readiness.closed : []
  return closed.map((c) => c && c.door_id).filter(Boolean)
}

/**
 * The real state of one door, from GET /api/doors rows. Agent-scoped keys are
 * `<door_id>:<agent_id>`, tenant keys are the bare id. A door with no row (or
 * no rows at all) is `unknown`/`authored`: being told nothing is not being told
 * it is closed.
 */
export function doorStateFor(doorRows, doorId, agentId = null) {
  const rows = Array.isArray(doorRows) ? doorRows : []
  const keys = agentId ? [`${doorId}:${agentId}`, doorId] : [doorId]
  for (const key of keys) {
    const row = rows.find((r) => r && r.key === key)
    if (row) return { state: row.state || 'unknown', rung: row.rung || 'authored' }
  }
  return { state: 'unknown', rung: 'authored' }
}

/**
 * Split the readiness's not-open doors by their real state.
 * @returns {{closed: Array, unknown: Array, closedDetections: number}}
 */
export function classifyDoors(readiness, doorRows, agentId = null) {
  const list = Array.isArray(readiness && readiness.closed) ? readiness.closed : []
  const closed = []
  const unknown = []
  let closedDetections = 0
  for (const entry of list) {
    if (!entry || !entry.door_id) continue
    const st = doorStateFor(doorRows, entry.door_id, agentId)
    const door = { door_id: entry.door_id, detections: entry.detections || 0, ...st }
    if (st.state === 'closed') {
      closed.push(door)
      closedDetections += door.detections
    } else if (st.state !== 'open') {
      unknown.push(door)
    }
  }
  return { closed, unknown, closedDetections }
}

const describeDoor = (d) => `${d.door_id}: ${d.state === 'closed' ? 'closed' : d.rung}`

/**
 * @returns {{label: string, tone: 'ready'|'partial'|'needs'|'unknown'|'error', title: string}}
 */
export function chipFor(readiness, doorRows = null, agentId = null) {
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
  const { closed, unknown } = classifyDoors(readiness, doorRows, agentId)
  const doors = [...closed, ...unknown].map(describeDoor)
  if (kind === 'partial') {
    const k = readiness.scorable
    const n = readiness.total
    return {
      label: `PARTIAL ${k}/${n}`, tone: 'partial',
      title: `${k} of ${n} detections can be scored` + (doors.length ? `; not open: ${doors.join(', ')}` : ''),
    }
  }
  if (kind === 'needs_doors') {
    if (closed.length) {
      return { label: 'NEEDS DOORS', tone: 'needs', title: `not open: ${doors.join(', ')}` }
    }
    return {
      label: 'NOT CHECKED', tone: 'unknown',
      title: doors.length ? `doors not checked yet: ${doors.join(', ')}` : 'doors not checked yet',
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
