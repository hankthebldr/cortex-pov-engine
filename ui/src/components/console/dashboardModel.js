/**
 * dashboardModel.js — the pure model behind Dashboards (design v33 C8).
 *
 * Every view counts the same records: the scenario list (GET /api/scenarios),
 * one row per scenario, so a scenario added to the library shows up in every
 * dashboard at once. What each scenario has PROVEN comes from the run list
 * (GET /api/runs) and nowhere else:
 *
 *   Detected      a run of it was read back from the tenant and SimCore
 *                 counted at least one detection on that run
 *   Not seen yet  a run of it was read back and nothing was detected
 *   Pending       it was launched, and no run of it has been read back
 *   Never run     no run of it executed a step
 *
 * AUTHORED IS NOT PROVEN. A scenario that exists, or that ran and was never
 * read back, is never counted as covered. "Read back" is `readBackOf(run)`,
 * the one rule the Runs and Results pages use.
 *
 * Nothing here reads `povdata/` for values; the plane table there is naming
 * only. An unrecognised response shape raises instead of reading as empty.
 */
import { readBackOf, runLifecycle, PHASE } from './runLifecycle.js'
import { PLANES } from './povdata/planes.js'
import { ATTACK_TACTICS, tacticIndex } from './attackTactics.js'
import { doorStateFor, notOpenDoorIds, readinessKind } from './doors/doorModel.js'

/**
 * The state vocabulary. `status` is the kit StatusIcon tone, `rank` orders
 * how far along the proof path a state is (a technique or cell takes the best
 * state any of its scenarios reached). `unknown` is used only when the run
 * list could not be read: then no scenario can be called detected or never run.
 */
export const STATE = Object.freeze({
  detected: Object.freeze({ key: 'detected', word: 'Detected', status: 'up', rank: 4, sub: 'in a read-back run' }),
  not_seen: Object.freeze({ key: 'not_seen', word: 'Not seen yet', status: 'degraded', rank: 3, sub: 'read back, nothing detected' }),
  pending: Object.freeze({ key: 'pending', word: 'Pending', status: 'unknown', rank: 2, sub: 'launched, not read back yet' }),
  never: Object.freeze({ key: 'never', word: 'Never run', status: 'off', rank: 1, sub: 'authored, not proven' }),
  unknown: Object.freeze({ key: 'unknown', word: "Can't check", status: 'unknown', rank: 0, sub: 'the run list did not load' }),
})

/** The four states a scenario can be in, best first. */
export const STATE_ORDER = Object.freeze(['detected', 'not_seen', 'pending', 'never'])

/** A technique no in-scope scenario exercises (drawn dashed). */
export const NO_SCENARIO = Object.freeze({ key: 'none', word: 'No scenario', status: 'off' })

/** Filter value meaning "no filter". */
export const ALL = 'All'
/** Column for scenarios that declare no detection with a plane. */
export const NO_SOURCE = '(none)'
/** Row for scenarios with no use case reference. */
export const NO_USE_CASE = '(no use case)'
/** Column for a technique whose tactic is not one of the 14 Enterprise tactics. */
export const OTHER_TACTIC = '(other)'
/** Planes × doors: readiness lists no door that is not open. */
export const NO_DOOR = '(no door)'
/** Planes × doors: no readiness reported for the scenario. */
export const NOT_CHECKED = '(not checked)'

export function idOf(s) {
  return (s && (s.scenario_id || s.id)) || null
}

/** Plane codes in the vocabulary's order; a code the vocabulary lacks follows, A→Z. */
export function orderPlanes(codes) {
  const known = PLANES.map((p) => p[0])
  return [...new Set(codes)].sort((a, b) => {
    const ia = known.indexOf(a)
    const ib = known.indexOf(b)
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return 1
    return String(a).localeCompare(String(b))
  })
}

/** The state one run gives its scenario. */
export function runState(run) {
  if (readBackOf(run)) {
    const n = run.detected_count
    if (typeof n === 'number' && n > 0) return 'detected'
    if (n === 0) return 'not_seen'
    // Read back, but this SimCore reported no count: not a claim either way.
    return 'pending'
  }
  return runLifecycle(run).phase === PHASE.NOTHING ? 'never' : 'pending'
}

/**
 * scenario_id → state, from the run list. A scenario with no run is absent
 * from the map and reads `never` through `stateOf`.
 * @param {Array} runs  run rows (the summary shape is enough)
 * @returns {Map<string, string>}
 */
export function scenarioStates(runs) {
  if (!Array.isArray(runs)) throw new Error('run list is not a list')
  const out = new Map()
  for (const run of runs) {
    const id = run && run.scenario_id
    if (!id) continue
    const next = runState(run)
    const cur = out.get(id)
    if (!cur || STATE[next].rank > STATE[cur].rank) out.set(id, next)
  }
  return out
}

/** One scenario's state. `states` null means the run list did not load. */
export function stateOf(states, id) {
  if (!states) return 'unknown'
  return states.get(id) || 'never'
}

/** The best state among `ids`, or `none` when there are none. */
export function bestState(ids, states) {
  let best = null
  for (const id of ids) {
    const st = stateOf(states, id)
    if (best === null || STATE[st].rank > STATE[best].rank) best = st
  }
  return best === null ? NO_SCENARIO.key : best
}

/** How many of `ids` are in each state. */
export function countStates(ids, states) {
  const out = { detected: 0, not_seen: 0, pending: 0, never: 0, unknown: 0 }
  for (const id of ids) out[stateOf(states, id)] += 1
  return out
}

/** Sort ids best state first, then by id. */
export function sortByState(ids, states) {
  return [...ids].sort((a, b) => (STATE[stateOf(states, b)].rank - STATE[stateOf(states, a)].rank)
    || String(a).localeCompare(String(b)))
}

/**
 * The use case a scenario proves: the parent UC of its `uc_ref`. The index
 * writes a use-case scenario as `UCS-<DOMAIN>-<NN>` under `UC-<DOMAIN>`, and
 * `uc_name` is that parent's name.
 */
export function useCaseOf(s) {
  const ref = typeof s?.uc_ref === 'string' ? s.uc_ref.trim() : ''
  const m = /^UCS-(.+)-\d+$/.exec(ref)
  const id = m ? `UC-${m[1]}` : ref
  return { id: id || NO_USE_CASE, name: typeof s?.uc_name === 'string' ? s.uc_name : '' }
}

/** The planes a scenario's expected detections are raised on, in plane order. */
export function detectionSourcesOf(s) {
  const out = []
  for (const step of Array.isArray(s?.steps) ? s.steps : []) {
    for (const d of Array.isArray(step?.expected_detections) ? step.expected_detections : []) {
      const p = typeof d?.plane === 'string' ? d.plane.trim().toUpperCase() : ''
      if (p) out.push(p)
    }
  }
  return orderPlanes(out)
}

function planeOf(s) {
  return typeof s?.plane === 'string' && s.plane.trim() ? s.plane.trim().toUpperCase() : NO_SOURCE
}

/**
 * Detection source × Use case. Rows are use cases, columns the planes the
 * corpus raises detections on. A scenario with detections on two planes is
 * counted in both cells, so a row's cells can sum past its total; the total
 * is the number of scenarios.
 * @returns {{columns: string[], rows: Array<{id, name, ids: string[], cells: Object<string, string[]>}>}}
 */
export function sourceUseCaseGrid(scenarios) {
  if (!Array.isArray(scenarios)) throw new Error('scenario list is not a list')
  const rows = new Map()
  const cols = new Set()
  for (const s of scenarios) {
    const id = idOf(s)
    if (!id) continue
    const uc = useCaseOf(s)
    if (!rows.has(uc.id)) rows.set(uc.id, { id: uc.id, name: uc.name, ids: [], cells: {} })
    const row = rows.get(uc.id)
    if (!row.name && uc.name) row.name = uc.name
    row.ids.push(id)
    const sources = detectionSourcesOf(s)
    for (const src of sources.length ? sources : [NO_SOURCE]) {
      cols.add(src)
      ;(row.cells[src] = row.cells[src] || []).push(id)
    }
  }
  const columns = orderPlanes([...cols].filter((c) => c !== NO_SOURCE))
  if (cols.has(NO_SOURCE)) columns.push(NO_SOURCE)
  const list = [...rows.values()].sort((a, b) => {
    if (a.id === NO_USE_CASE) return 1
    if (b.id === NO_USE_CASE) return -1
    return a.id.localeCompare(b.id)
  })
  return { columns, rows: list }
}

/** The plane / detection-source / gaps filter for the ATT&CK matrix. */
export function scenarioFilter(scenarios, { plane = ALL, source = ALL } = {}) {
  if (plane === ALL && source === ALL) return () => true
  const byId = new Map((scenarios || []).map((s) => [idOf(s), s]))
  return (id) => {
    const s = byId.get(id)
    if (!s) return false
    if (plane !== ALL && planeOf(s) !== plane) return false
    if (source !== ALL && !detectionSourcesOf(s).includes(source)) return false
    return true
  }
}

function techniqueRow(t) {
  if (!t || typeof t !== 'object' || typeof t.technique_id !== 'string' || !Array.isArray(t.scenarios)) {
    throw new Error('GET /api/mitre/coverage returned a technique with an unrecognised shape')
  }
  return t
}

/**
 * The ATT&CK matrix: the 14 Enterprise tactics as columns, each technique the
 * coverage endpoint reports as a tile, coloured by the best state any in-scope
 * scenario reached. A technique no in-scope scenario exercises is `none`
 * (drawn dashed). A technique filed under a tactic id that is not one of the
 * 14 lands in a trailing column rather than vanishing.
 *
 * Placement is the endpoint's: it files a step technique under its scenario's
 * tactic.
 *
 * @param {Object} coverage   GET /api/mitre/coverage
 * @param {Array} scenarios   GET /api/scenarios rows
 * @param {Map|null} states   scenarioStates(runs), null when runs did not load
 * @returns {{columns: Array, tallies: Object, total: number}}
 */
export function mitreMatrix(coverage, scenarios, states, { plane = ALL, source = ALL, gapsOnly = false } = {}) {
  if (!coverage || typeof coverage !== 'object' || !Array.isArray(coverage.by_tactic)) {
    throw new Error('GET /api/mitre/coverage returned an unrecognised shape (no by_tactic list)')
  }
  const inScope = scenarioFilter(scenarios, { plane, source })
  const columns = ATTACK_TACTICS.map((t) => ({ id: t.id, name: t.name, tiles: [] }))
  const other = { id: OTHER_TACTIC, name: 'Tactic not recognised', tiles: [] }
  const tallies = { detected: 0, not_seen: 0, pending: 0, never: 0, unknown: 0, none: 0 }
  let total = 0
  for (const tactic of coverage.by_tactic) {
    if (!tactic || !Array.isArray(tactic.techniques)) {
      throw new Error('GET /api/mitre/coverage returned a tactic with no techniques list')
    }
    for (const raw of tactic.techniques) {
      const t = techniqueRow(raw)
      const ids = t.scenarios.filter(inScope)
      const state = bestState(ids, states)
      const tacticId = t.tactic_id || tactic.tactic_id || ''
      const tile = { id: t.technique_id, name: t.technique_name || t.technique_id, tacticId, ids, state }
      tallies[state] += 1
      total += 1
      const at = tacticIndex(tacticId)
      ;(at === -1 ? other : columns[at]).tiles.push(tile)
    }
  }
  const all = other.tiles.length ? [...columns, other] : columns
  for (const col of all) {
    col.tiles.sort((a, b) => a.id.localeCompare(b.id))
    col.detected = col.tiles.filter((t) => t.state === 'detected').length
    col.count = col.tiles.length
    if (gapsOnly) col.tiles = col.tiles.filter((t) => t.state !== 'detected')
  }
  return { columns: all, tallies, total }
}

/**
 * Planes × doors: how many scenarios on each plane are waiting on each door
 * that is not open (GET /api/doors/readiness `not_open`). A scenario waiting
 * on two doors is counted under both. One whose readiness lists no door is
 * `NO_DOOR`; one with no readiness, an unrecognised one, or one where no
 * detection could be judged is `NOT_CHECKED`, never ready.
 *
 * @param {Array} scenarios
 * @param {Object} readiness   GET /api/doors/readiness `scenarios`
 * @param {Array} doorRows     GET /api/doors `doors`
 * @param {string|null} agentId  the agent the readiness was read for; an
 *                               agent-scoped door's state is that agent's
 */
export function planeDoorGrid(scenarios, readiness, doorRows, agentId = null) {
  if (!Array.isArray(scenarios)) throw new Error('scenario list is not a list')
  if (!readiness || typeof readiness !== 'object' || Array.isArray(readiness)) {
    throw new Error('door readiness is not an object')
  }
  const rows = new Map()
  const cols = new Map()
  const extra = new Set()
  for (const s of scenarios) {
    const id = idOf(s)
    if (!id) continue
    const plane = planeOf(s)
    if (!rows.has(plane)) rows.set(plane, { id: plane, ids: [], cells: {} })
    const row = rows.get(plane)
    row.ids.push(id)
    const r = readiness[id]
    const kind = readinessKind(r)
    const unchecked = kind === 'absent' || kind === 'unrecognised' || kind === 'not_evaluated'
    const doors = unchecked ? null : notOpenDoorIds(r)
    const keys = doors === null ? [NOT_CHECKED] : doors.length ? doors : [NO_DOOR]
    for (const key of keys) {
      if (key === NOT_CHECKED || key === NO_DOOR) extra.add(key)
      else if (!cols.has(key)) cols.set(key, doorStateFor(doorRows, key, agentId))
      const cell = (row.cells[key] = row.cells[key] || { ids: [], detections: 0 })
      cell.ids.push(id)
      if (doors && doors.length) {
        const entry = r.not_open.find((d) => d && d.door_id === key)
        cell.detections += Number(entry?.detections) || 0
      }
    }
  }
  // Doors in the registry's order (GET /api/doors lists them that way); a door
  // id the rows do not name follows, A→Z.
  const registryOrder = [...new Set((Array.isArray(doorRows) ? doorRows : []).map((d) => d && d.door_id).filter(Boolean))]
  const rank = (id) => (registryOrder.includes(id) ? registryOrder.indexOf(id) : registryOrder.length)
  const columns = [...cols.entries()]
    .sort(([a], [b]) => (rank(a) - rank(b)) || a.localeCompare(b))
    .map(([id, st]) => ({ id, label: st.label, state: st.state, rung: st.rung }))
  if (extra.has(NO_DOOR)) columns.push({ id: NO_DOOR, label: 'No door listed', state: null, rung: null })
  if (extra.has(NOT_CHECKED)) columns.push({ id: NOT_CHECKED, label: 'Not checked', state: null, rung: null })
  const order = orderPlanes([...rows.keys()].filter((p) => p !== NO_SOURCE))
  if (rows.has(NO_SOURCE)) order.push(NO_SOURCE)
  return { columns, rows: order.map((p) => rows.get(p)) }
}

/** A door's state as a kit status: open is up, closed is down, anything else unknown. */
export function doorStatus(state) {
  if (state === 'open') return { status: 'up', word: 'Open' }
  if (state === 'closed') return { status: 'down', word: 'Closed' }
  return { status: 'unknown', word: 'Not checked' }
}
