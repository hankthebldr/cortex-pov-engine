/**
 * doorModel.js — pure reading of a door-readiness object for the Library chip
 * and the launch preflight's Data path check.
 *
 * Shape (core/engine/door_resolution.py::scenario_readiness):
 *   { scenario_id, state: ready|partial|needs_doors|not_evaluated, scorable,
 *     total, underived, route_blocked, unevaluated,
 *     not_open: [{door_id, detections}], agent_scoped_evaluated }
 *
 * `not_open` lists the doors that are not in the open set. It does NOT say a
 * door is closed: a door nobody has checked is not open either. The real state
 * of each door comes from GET /api/doors, which this module re-joins.
 *
 * HONESTY RULES
 *   - Absent readiness is NOT CHECKED, never READY: "we were not told" is not
 *     "every door is open".
 *   - A state this console does not recognise is surfaced as an error tone with
 *     the offending value in the title. It is never mapped onto a known state.
 *   - Nothing is excluded from scoring yet and no door event is written, so no
 *     door reads `open` or `closed`; the answer everywhere is NOT CHECKED. That
 *     is the truth, not a bug. The doors decide whether a detection CAN FIRE,
 *     never whether it is scored.
 */

export const KNOWN_STATES = ['ready', 'partial', 'needs_doors', 'not_evaluated']

/** Shown wherever agent-scoped doors were skipped because no target is selected. */
export const AGENT_NOT_EVALUATED = 'agent-scoped doors not evaluated: no target selected'

/**
 * Detections on a collector step whose target this console has not resolved.
 * BIOC and IOC never fire on a collector dataset (it is not stitched); whether
 * the others can depends on the target, which is not known here.
 */
export function routeBlockedNote(count, total) {
  return `${count} of ${total} detections are on a collector route this console has not resolved `
    + '(BIOC and IOC never fire there)'
}

/**
 * Classify a readiness value into one of: 'absent' | 'unrecognised' | a known state.
 * Shared by the chip and the preflight check so the two can never disagree.
 */
export function readinessKind(readiness) {
  if (readiness === undefined || readiness === null) return 'absent'
  if (typeof readiness !== 'object' || !KNOWN_STATES.includes(readiness.state)) return 'unrecognised'
  return readiness.state
}

/** The readiness's not-open list, or [] when it has none. */
function notOpenList(readiness) {
  return Array.isArray(readiness && readiness.not_open) ? readiness.not_open : []
}

/** Door ids the readiness lists as not open, most-held-back first. */
export function notOpenDoorIds(readiness) {
  return notOpenList(readiness).map((c) => c && c.door_id).filter(Boolean)
}

/**
 * The real state of one door, from GET /api/doors rows. Agent-scoped keys are
 * `<door_id>:<agent_id>`, tenant keys are the bare id. A door with no row (or
 * no rows at all) is `unknown`/`authored`: being told nothing is not being told
 * it is closed. `label` is the door's own name from any row for that door, else
 * its id.
 */
export function doorStateFor(doorRows, doorId, agentId = null) {
  const rows = Array.isArray(doorRows) ? doorRows : []
  const named = rows.find((r) => r && r.door_id === doorId && r.label)
  const label = named ? named.label : doorId
  const keys = agentId ? [`${doorId}:${agentId}`, doorId] : [doorId]
  for (const key of keys) {
    const row = rows.find((r) => r && r.key === key)
    if (row) return { state: row.state || 'unknown', rung: row.rung || 'authored', label: row.label || label }
  }
  return { state: 'unknown', rung: 'authored', label }
}

/**
 * Split the readiness's not-open doors by their real state.
 *
 * `closedDetections` is the number of detections behind a closed door when that
 * number is known exactly — one closed door, whose own count it is. With two or
 * more closed doors it is `null`: a detection held back by two doors is listed
 * under both, so summing the per-door counts would overcount.
 * `allClosed` is true when every not-open door is conclusively closed (vacuously
 * true when none is listed).
 * @returns {{closed: Array, unknown: Array, closedDetections: number|null, allClosed: boolean}}
 */
export function classifyDoors(readiness, doorRows, agentId = null) {
  const list = notOpenList(readiness).filter((entry) => entry && entry.door_id)
  const closed = []
  const unknown = []
  for (const entry of list) {
    const st = doorStateFor(doorRows, entry.door_id, agentId)
    const door = { door_id: entry.door_id, detections: entry.detections || 0, ...st }
    if (st.state === 'closed') closed.push(door)
    else if (st.state !== 'open') unknown.push(door)
  }
  return {
    closed,
    unknown,
    closedDetections: closed.length === 1 ? closed[0].detections : null,
    allClosed: closed.length === list.length,
  }
}

const describeDoor = (d) => `${d.label || d.door_id}: ${d.state === 'closed' ? 'closed' : d.rung}`

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
  // Notes that hold whatever the state: the collector route no door check
  // changes, and agent doors skipped for want of a target (spec §7 "says so").
  const notes = []
  const rb = readiness.route_blocked || 0
  if (rb > 0) notes.push(routeBlockedNote(rb, readiness.total))
  if (readiness.agent_scoped_evaluated === false) notes.push(AGENT_NOT_EVALUATED)
  const titled = (head) => [head, ...notes].filter(Boolean).join('; ')

  if (kind === 'ready') {
    return { label: 'READY', tone: 'ready', title: titled('every door this scenario needs is open') }
  }
  const { closed, unknown } = classifyDoors(readiness, doorRows, agentId)
  const doors = [...closed, ...unknown].map(describeDoor)
  if (kind === 'partial') {
    const k = readiness.scorable
    const n = readiness.total
    return {
      label: `PARTIAL ${k}/${n}`, tone: 'partial',
      title: titled(`${k} of ${n} detections can fire` + (doors.length ? `; not open: ${doors.join(', ')}` : '')),
    }
  }
  if (kind === 'needs_doors') {
    if (closed.length) {
      return { label: 'NEEDS DOORS', tone: 'needs', title: titled(`not open: ${doors.join(', ')}`) }
    }
    // A collector-only scenario lists no door, so its title is the route note alone.
    return {
      label: 'NOT CHECKED', tone: 'unknown',
      title: titled(doors.length ? `doors not checked yet: ${doors.join(', ')}` : '') || 'doors not checked yet',
    }
  }
  return { label: 'NOT CHECKED', tone: 'unknown', title: titled('no detection could be judged') }
}

/** Ready or partial: at least one door-backed detection can fire. */
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
