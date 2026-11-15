/**
 * uctcProofPath — how far each detection test case has got along the proof
 * path (design C6). Pure, no React.
 *
 *   Has a scenario › Components ready › Ran › Read back › Detected
 *
 * Every stage is read from a SimCore API, never assumed:
 *
 *   Has a scenario    GET /api/scenarios — a scenario whose `tc_refs` (or
 *                     `tc_ref`) names the test case. Drafts are not listed
 *                     there, so a draft never counts.
 *   Components ready  GET /api/doors/readiness (per scenario) joined to
 *                     GET /api/doors (each door's own state). Ready or partial
 *                     means at least one detection can fire. A door has to be
 *                     CHECKED and found closed before a test case reads "not
 *                     ready"; a door nobody has checked is "Can't check".
 *   Ran               GET /api/runs — a run of an evidencing scenario that
 *                     executed a step carrying a detection (runLifecycle). A
 *                     run whose verdict reason is `not_executed` tested nothing.
 *   Read back         readBackOf(run): SimCore recorded a tenant read for it.
 *   Detected          a read-back run with `detected_count > 0`.
 *
 * AUTHORED IS NOT PROVEN. Only "Detected" counts as covered. A source that did
 * not load makes the stages that need it `unknown` ("Can't check"), never
 * `todo`: "we could not look" is not "it is missing". An empty scenario list is
 * a broken deployment, not zero coverage, so it reads `unknown` too.
 *
 * Only DET and HNT rows are on this path: posture, platform and automation
 * rows are proven by an assertion run, not by a detection.
 */
import { readinessKind, classifyDoors } from './doors/doorModel.js'
import { runLifecycle, readBackOf, PHASE } from './runLifecycle.js'
import { detectionTypeToken } from './DetectionTypeChip.jsx'
import { parseServerTime } from '../../api/time.js'
import { runIdOf } from '../../api/ids.js'

export const DETECTION_CLASSES = ['DET', 'HNT']

export const STAGES = Object.freeze(['scenario', 'components', 'ran', 'readBack', 'detected'])

/** The funnel's names for each stage. */
export const STAGE_LABELS = Object.freeze({
  scenario: 'Has a scenario',
  components: 'Components ready',
  ran: 'Ran',
  readBack: 'Read back',
  detected: 'Detected',
})

/** The word a stage cell shows once the stage is done. */
export const STAGE_DONE_WORDS = Object.freeze({
  scenario: 'Authored',
  components: 'Ready',
  ran: 'Ran',
  readBack: 'Read back',
  detected: 'Detected',
})

/** Where a test case stalls, earliest first. `unknown` sorts last. */
export const GAP_KINDS = Object.freeze(['no_scenario', 'component', 'never_ran', 'not_read_back', 'not_seen', 'unknown'])

export const GAP_LABELS = Object.freeze({
  no_scenario: 'No scenario',
  component: 'Component not ready',
  never_ran: 'Never ran',
  not_read_back: 'Not read back',
  not_seen: 'Not seen yet',
  unknown: "Can't check",
})

/** StatusIcon status for each gap kind (design C6: ✕ · ! · – · – · ! · ?). */
export const GAP_STATUS = Object.freeze({
  no_scenario: 'down',
  component: 'degraded',
  never_ran: 'off',
  not_read_back: 'off',
  not_seen: 'degraded',
  unknown: 'unknown',
})

/** The stage a gap kind stalls at. */
export const GAP_STAGE = Object.freeze({
  no_scenario: 'scenario',
  component: 'components',
  never_ran: 'ran',
  not_read_back: 'readBack',
  not_seen: 'detected',
})

/** The short word the stalled stage's cell shows. */
export const GAP_CELL_WORDS = Object.freeze({
  no_scenario: 'None',
  component: 'Not ready',
  never_ran: 'Never ran',
  not_read_back: 'Not read back',
  not_seen: 'Not seen yet',
})

/** The detection mechanisms the design draws a card for, in its order. */
export const MECHANISMS = Object.freeze(['BIOC', 'ABIOC', 'XQL', 'Correlation', 'IOC'])

const MECH_LABEL = { bioc: 'BIOC', abioc: 'ABIOC', xql: 'XQL', correlation: 'Correlation', ioc: 'IOC', analytics: 'Analytics' }

/** A detection type as its mechanism label; an unrecognised type keeps its own name. */
export function mechanismOf(type) {
  const token = detectionTypeToken(type)
  if (token) return MECH_LABEL[token]
  const raw = String(type || '').trim()
  return raw || null
}

/**
 * The console component a door's data arrives through: the key
 * `componentRoute` / `componentConfigure` understand. Read from the door's id
 * in the ingestion-door registry (core/engine/ingestion_doors.py).
 */
export function componentForDoor(doorId) {
  const id = String(doorId || '')
  if (!id) return null
  if (id.startsWith('xdr_agent')) return 'agent'
  if (id.startsWith('ngfw_')) return 'ngfw'
  if (id === 'http_collector') return 'http'
  if (id.startsWith('syslog_')) return 'bvm'
  return 'tenant'
}

export function isDetectionTestCase(tc) {
  return DETECTION_CLASSES.includes(tc && tc.validation_class)
}

/**
 * A source's state from what arrived: 'loading' | 'error' | 'empty' | 'ok'.
 * `emptyIsFault` is for a list that is never legitimately empty (the scenario
 * catalog); zero runs is a real answer.
 */
export function sourceState({ loading = false, error = null, data, emptyIsFault = false } = {}) {
  const hasData = Array.isArray(data) ? data.length > 0 : data != null
  if (hasData) return 'ok'
  if (loading) return 'loading'
  if (error) return 'error'
  if (data == null) return 'error'
  if (emptyIsFault) return 'empty'
  return 'ok'
}

function scenarioIdOf(s) {
  return s && (s.scenario_id || s.id) ? String(s.scenario_id || s.id) : null
}

/** A scenario's evidence set, mirroring `Scenario.to_dict()` (tc_ref first). */
export function tcRefsOf(scenario) {
  const refs = Array.isArray(scenario && scenario.tc_refs) ? scenario.tc_refs.slice() : []
  if (scenario && scenario.tc_ref && !refs.includes(scenario.tc_ref)) refs.unshift(scenario.tc_ref)
  return refs
}

/** tc_id → [scenario], the primary binding (tc_ref) first, then by id. */
export function scenariosByTc(scenarios = []) {
  const out = new Map()
  for (const s of scenarios || []) {
    const sid = scenarioIdOf(s)
    if (!sid) continue
    for (const tc of tcRefsOf(s)) {
      if (!out.has(tc)) out.set(tc, [])
      out.get(tc).push(s)
    }
  }
  for (const [tc, list] of out) {
    list.sort((a, b) => {
      const pa = a.tc_ref === tc ? 0 : 1
      const pb = b.tc_ref === tc ? 0 : 1
      return pa - pb || scenarioIdOf(a).localeCompare(scenarioIdOf(b))
    })
  }
  return out
}

/** scenario_id → [run], newest first. */
export function runsByScenario(runs = []) {
  const out = new Map()
  for (const r of runs || []) {
    if (!r || !r.scenario_id) continue
    if (!out.has(r.scenario_id)) out.set(r.scenario_id, [])
    out.get(r.scenario_id).push(r)
  }
  for (const list of out.values()) list.sort(newestFirst)
  return out
}

function startedAt(run) {
  const t = parseServerTime(run && run.started_at)
  return Number.isNaN(t) ? -Infinity : t
}

function newestFirst(a, b) {
  return startedAt(b) - startedAt(a)
}

function reasonOf(run) {
  const d = run && run.tc_verdict_detail
  return d && typeof d === 'object' && d.reason ? String(d.reason) : null
}

/**
 * What the runs of a test case's scenarios say, bucketed by lifecycle.
 *
 *   executed   ended having run a step that carries a detection
 *   readBack   executed and read back from the tenant
 *   detected   read back with a reported detection (`detected_count > 0`)
 *   inFlight   `running` (still executing, or queued) plus `waiting` (a
 *              staged push bundle that has not reported running)
 *   nothing    ended with no step that carries a detection run
 *   pendingReasons  {reason: count} over pending verdicts, as /api/uctc rolls it up
 */
export function runFacts(runs = []) {
  const sorted = (runs || []).filter(Boolean).slice().sort(newestFirst)
  const facts = {
    total: sorted.length,
    executed: [],
    readBack: [],
    detected: [],
    inFlight: [],
    running: [],
    waiting: [],
    nothing: [],
    countsReported: true,
    pendingReasons: {},
  }
  for (const run of sorted) {
    const lc = runLifecycle(run)
    const reason = reasonOf(run)
    const status = String(run.status || '').toLowerCase()
    if ((run.tc_verdict || 'pending') === 'pending' && reason) {
      facts.pendingReasons[reason] = (facts.pendingReasons[reason] || 0) + 1
    }
    if (lc.phase === PHASE.RUNNING) { facts.inFlight.push(run); facts.running.push(run); continue }
    if (lc.phase === PHASE.NOTHING || reason === 'not_executed') { facts.nothing.push(run); continue }
    // A staged push bundle executes off-box: until it is read back, nothing
    // here says it ran. It is waiting (design C2b), not running.
    if (status === 'staged' && !readBackOf(run)) { facts.inFlight.push(run); facts.waiting.push(run); continue }
    facts.executed.push(run)
    if (readBackOf(run)) {
      facts.readBack.push(run)
      if (typeof run.detected_count !== 'number') facts.countsReported = false
      else if (run.detected_count > 0) facts.detected.push(run)
    }
  }
  return facts
}

/**
 * Components readiness over a test case's scenarios.
 * @returns {{state: 'done'|'todo'|'unknown', component: string|null, door: object|null}}
 *   `component` is the console component the signal comes through (the door
 *   holding back the most detections), or null when no door is named.
 */
export function componentsOf(scenarioIds, { readiness, doorRows, readinessState, doorsState }) {
  if (readinessState !== 'ok') return { state: 'unknown', component: null, door: null }
  const map = readiness || {}
  let runnable = false
  let closed = null
  let named = null
  for (const sid of scenarioIds) {
    const r = map[sid]
    const kind = readinessKind(r)
    if (kind === 'ready' || kind === 'partial') runnable = true
    const notOpen = Array.isArray(r && r.not_open) ? r.not_open : []
    for (const entry of notOpen) {
      if (!entry || !entry.door_id) continue
      if (!named || (entry.detections || 0) > (named.detections || 0)) named = entry
    }
    if (kind === 'needs_doors' && doorsState === 'ok') {
      const c = classifyDoors(r, doorRows).closed
      if (c.length && !closed) closed = c[0]
    }
  }
  if (runnable) {
    return { state: 'done', component: named ? componentForDoor(named.door_id) : null, door: named }
  }
  if (closed) return { state: 'todo', component: componentForDoor(closed.door_id), door: closed }
  return { state: 'unknown', component: named ? componentForDoor(named.door_id) : null, door: named }
}

/**
 * One test case's proof path.
 * @param {object} tc   an /api/uctc/test-cases row
 * @param {object} ctx  {scenariosByTc, runsByScenario, readiness, doorRows, sources}
 */
export function proofPath(tc, ctx) {
  const sources = ctx.sources || {}
  const scenarios = (ctx.scenariosByTc && ctx.scenariosByTc.get(tc.tc_id)) || []
  const scenarioIds = scenarios.map(scenarioIdOf)
  const stages = {}

  stages.scenario = sources.scenarios !== 'ok' ? 'unknown' : (scenarioIds.length ? 'done' : 'todo')

  const comp = stages.scenario === 'done'
    ? componentsOf(scenarioIds, {
      readiness: ctx.readiness,
      doorRows: ctx.doorRows,
      readinessState: sources.readiness,
      doorsState: sources.doors,
    })
    : { state: 'na', component: null, door: null }
  stages.components = comp.state

  const runs = scenarioIds.flatMap((sid) => (ctx.runsByScenario && ctx.runsByScenario.get(sid)) || [])
  const facts = runFacts(runs)
  const runsOk = sources.runs === 'ok'

  if (stages.scenario !== 'done') {
    stages.ran = 'na'; stages.readBack = 'na'; stages.detected = 'na'
  } else if (!runsOk) {
    stages.ran = 'unknown'; stages.readBack = 'unknown'; stages.detected = 'unknown'
  } else {
    stages.ran = facts.executed.length ? 'done' : 'todo'
    stages.readBack = facts.readBack.length ? 'done' : 'todo'
    stages.detected = facts.detected.length ? 'done' : (facts.readBack.length && !facts.countsReported ? 'unknown' : 'todo')
  }

  let stall = null
  if (stages.scenario === 'unknown') stall = 'unknown'
  else if (stages.scenario === 'todo') stall = 'no_scenario'
  else if (stages.ran === 'unknown') stall = 'unknown'
  else if (stages.ran === 'todo') stall = stages.components === 'todo' ? 'component' : 'never_ran'
  else if (stages.readBack === 'todo') stall = 'not_read_back'
  else if (stages.detected === 'unknown') stall = 'unknown'
  else if (stages.detected === 'todo') stall = 'not_seen'

  // The run behind the stall, and the verdict reason it carries, if any.
  let run = null
  if (stall === 'never_ran') run = facts.inFlight[0] || facts.nothing[0] || null
  else if (stall === 'not_read_back') run = facts.executed.find((r) => !readBackOf(r)) || null
  else if (stall === 'not_seen' || stall === 'unknown') run = facts.readBack[0] || null
  else if (stall === null) run = facts.detected[0] || null
  const reason = run && (run.tc_verdict || 'pending') === 'pending' ? reasonOf(run) : null

  return {
    tc,
    tcId: tc.tc_id,
    ucId: tc.uc_id,
    scenarioIds,
    stages,
    stall,
    covered: stall === null,
    run,
    runId: runIdOf(run),
    inFlight: stall === 'never_ran' && facts.inFlight.length > 0 && facts.inFlight[0] === run,
    reason,
    component: comp.component,
    door: comp.door,
    facts,
    unknownSource: unknownSourceFor(stages, sources),
  }
}

function unknownSourceFor(stages, sources) {
  if (stages.scenario === 'unknown') return sources.scenarios === 'empty' ? 'scenarios-empty' : 'scenarios'
  if (stages.ran === 'unknown') return 'runs'
  if (stages.detected === 'unknown') return 'counts'
  return null
}

/** Proof paths for the detection test cases, in index order. */
export function buildProofPaths(testCases = [], { scenarios = [], runs = [], readiness = null, doorRows = null, sources = {} } = {}) {
  const ctx = {
    scenariosByTc: scenariosByTc(scenarios),
    runsByScenario: runsByScenario(runs),
    readiness,
    doorRows,
    sources,
  }
  return (testCases || []).filter(isDetectionTestCase).map((tc) => proofPath(tc, ctx))
}

/**
 * The five funnel counts. `done` is null when nothing was seen done and some
 * paths could not be checked: a zero there would be a guess. When some are
 * done and some unknown, `done` is a floor and `unknown` says by how much.
 */
export function funnel(paths = []) {
  const of = paths.length
  return STAGES.map((stage) => {
    const done = paths.filter((p) => p.stages[stage] === 'done').length
    const unknown = paths.filter((p) => p.stages[stage] === 'unknown').length
    return {
      stage,
      label: STAGE_LABELS[stage],
      done: done === 0 && unknown > 0 ? null : done,
      unknown,
      of,
    }
  })
}

const GAP_ORDER = Object.fromEntries(GAP_KINDS.map((k, i) => [k, i]))

/** Every path not yet detected, earliest stall first, then by test case id. */
export function gaps(paths = []) {
  return paths
    .filter((p) => p.stall)
    .slice()
    .sort((a, b) => GAP_ORDER[a.stall] - GAP_ORDER[b.stall] || String(a.tcId).localeCompare(String(b.tcId)))
}

/** {kind: count} for every gap kind, plus `all`. */
export function gapCounts(paths = []) {
  const out = { all: 0 }
  for (const k of GAP_KINDS) out[k] = 0
  for (const p of paths) {
    if (!p.stall) continue
    out.all += 1
    out[p.stall] += 1
  }
  return out
}

/** The detection types a scenario declares, as mechanism labels with counts. */
export function mechanismCounts(scenario) {
  const out = new Map()
  const steps = Array.isArray(scenario && scenario.steps) ? scenario.steps : []
  for (const step of steps) {
    const dets = Array.isArray(step && step.expected_detections) ? step.expected_detections : []
    for (const d of dets) {
      const m = mechanismOf(d && d.type)
      if (m) out.set(m, (out.get(m) || 0) + 1)
    }
  }
  return out
}

/**
 * The Mechanisms tab: one card per mechanism and one row per test case that
 * relies on at least one.
 *
 * The tenant state is "Can't check" for every mechanism: no SimCore API reads
 * whether a mechanism's content is present in the tenant, so nothing here may
 * read Present.
 */
export function mechanismModel(paths = [], scenarios = []) {
  const byId = new Map((scenarios || []).map((s) => [scenarioIdOf(s), s]))
  const countsBySid = new Map()
  const countsFor = (sid) => {
    if (!countsBySid.has(sid)) countsBySid.set(sid, mechanismCounts(byId.get(sid)))
    return countsBySid.get(sid)
  }

  const rows = []
  const tcsBy = new Map()
  const relied = new Set()
  for (const p of paths) {
    if (!p.scenarioIds.length) continue
    const mechs = new Set()
    for (const sid of p.scenarioIds) {
      relied.add(sid)
      for (const m of countsFor(sid).keys()) mechs.add(m)
    }
    if (!mechs.size) continue
    const ordered = orderMechanisms([...mechs])
    for (const m of ordered) tcsBy.set(m, (tcsBy.get(m) || 0) + 1)
    rows.push({ path: p, mechanisms: ordered, tenant: 'unknown' })
  }

  const objects = new Map()
  for (const sid of relied) {
    for (const [m, n] of countsFor(sid)) objects.set(m, (objects.get(m) || 0) + n)
  }

  const types = orderMechanisms([...new Set([...MECHANISMS, ...tcsBy.keys()])])
  const cards = types.map((type) => ({
    type,
    tcs: tcsBy.get(type) || 0,
    objects: objects.get(type) || 0,
    tenant: 'unknown',
  }))
  return { cards, rows }
}

function orderMechanisms(list) {
  const rank = (m) => {
    const i = MECHANISMS.indexOf(m)
    return i < 0 ? MECHANISMS.length : i
  }
  return list.slice().sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
}

/**
 * A use case's verdict from its detection test cases:
 *   proven   every one detected in a read-back run
 *   partial  at least one detected
 *   blocked  none detected, and a component one needs is checked and not ready
 *   unknown  none detected that we know of, and at least one could not be
 *            checked: "pending" would claim we looked
 *   pending  none detected yet
 *   none     no detection test case under it
 */
export function useCaseVerdict(paths = []) {
  if (!paths.length) return 'none'
  const covered = paths.filter((p) => p.covered).length
  if (covered === paths.length) return 'proven'
  if (covered > 0) return 'partial'
  if (paths.some((p) => p.stall === 'component')) return 'blocked'
  if (paths.some((p) => p.stall === 'unknown')) return 'unknown'
  return 'pending'
}

/** use case id → its detection paths. */
export function pathsByUseCase(paths = []) {
  const out = new Map()
  for (const p of paths) {
    if (!out.has(p.ucId)) out.set(p.ucId, [])
    out.get(p.ucId).push(p)
  }
  return out
}

/**
 * The four Index tiles. A count is null when the sources it needs did not
 * load: "Blocked" also needs at least one door to have been checked, because
 * "0 blocked" when nothing was checked would read as "nothing is broken".
 */
export function indexTiles({ useCaseIds = [], testCases = [], paths = [], sources = {}, doorsChecked = false } = {}) {
  const byUc = pathsByUseCase(paths)
  const verdicts = useCaseIds.map((id) => useCaseVerdict(byUc.get(id) || []))
  const core = sources.scenarios === 'ok' && sources.runs === 'ok'
  const doors = core && sources.readiness === 'ok' && sources.doors === 'ok' && doorsChecked
  const count = (...vs) => verdicts.filter((v) => vs.includes(v)).length
  return {
    useCases: useCaseIds.length,
    testCases: testCases.length,
    detectionTestCases: paths.length,
    partlyProven: core ? count('partial', 'proven') : null,
    proven: core ? count('proven') : null,
    pending: core ? count('pending') : null,
    blocked: doors ? count('blocked') : null,
  }
}

/** True when at least one door has a checked state (open or closed). */
export function anyDoorChecked(doorRows) {
  return Array.isArray(doorRows) && doorRows.some((r) => r && (r.state === 'open' || r.state === 'closed'))
}
