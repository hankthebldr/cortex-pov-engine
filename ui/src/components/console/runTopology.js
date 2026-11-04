/**
 * runTopology — the pure model behind a run's Topology tab.
 *
 * A lane is the ingestion DOOR a step launched through — the same five doors
 * the Composer bands by (`composerLanes.js`), so a node's lane on the design
 * canvas and its lane here mean the same thing. There is ONE `laneOf()`; this
 * module calls it and adds nothing of its own to the derivation.
 *
 * Inputs are live records only:
 *   - the scenario's `steps[]` (for `channel`, `plane`, identity, names) — when
 *     the scenario cannot be loaded, the run's `Result` rows carry each
 *     detection's `plane`, so lanes are rebuilt from those instead;
 *   - the run record (`status`, `tc_verdict`, `output`, `step_outcomes[]`) for
 *     the host's per-step record (rc, termination), joined by `buildStepRows`;
 *   - the run's `Result` rows for per-step detection tones.
 *
 * Honesty rules:
 *   - a step whose door cannot be derived (no channel, no plane anywhere) is
 *     listed as `unplaced` WITH the reason — never defaulted into ENDPOINT;
 *   - a lane with no step is `empty` — dimmed, not "clean";
 *   - the Engine terminal is a VIEW onto rows already counted in their step's
 *     cell (Correlation / Analytics / ABIOC), never a second population, so
 *     the page's totals cannot double-count;
 *   - a `miss` tone needs a proven run (see `runTechniques.resultTone`).
 */
import { laneOf, STEP_LANES, BANDS } from './composerLanes.js'
import { buildStepRows, parseStepTranscript } from './useRunStepTail.js'
import { resultTone, worstTone } from './runTechniques.js'
import { isRunComplete } from './runStatus.js'

export const ENGINE_LANE = 'ENG'

/** Detection types that fire in the Engine rather than at a door. */
export const ENGINE_SIGNAL_TYPES = new Set(['CORRELATION', 'ANALYTICS', 'ABIOC'])

export function isEngineRow(row) {
  const t = String(row?.signal_type || row?.detection_kind || '').toUpperCase()
  if (ENGINE_SIGNAL_TYPES.has(t)) return true
  return String(row?.plane || '').toUpperCase() === 'ANALYTICS'
}

/**
 * A scenario step in the shape `laneOf` reads. The corpus names the field
 * `expected_detections`; the Composer's step model names it `detections`.
 */
export function toLaneStep(step) {
  if (!step) return null
  const detections = Array.isArray(step.detections)
    ? step.detections
    : Array.isArray(step.expected_detections) ? step.expected_detections : []
  return { ...step, detections }
}

/** Whether `laneOf` would derive a door from DATA rather than its default. */
export function doorKnown(step, draftPlane = null) {
  if (!step) return false
  if (step.channel) return true
  if (step.plane) return true
  if (step.detections?.[0]?.plane) return true
  return !!draftPlane
}

function groupByStep(results) {
  const by = new Map()
  for (const r of results) {
    const id = r?.step_id || ''
    if (!by.has(id)) by.set(id, [])
    by.get(id).push(r)
  }
  return by
}

/**
 * @param {object} p
 * @param {object|null} p.scenario  `GET /api/scenarios/{id}` (may be null)
 * @param {object|null} p.run       `GET /api/runs/{id}` (status, output, step_outcomes)
 * @param {Array}       p.results   `GET /api/results/{run_id}` rows
 * @param {Array}       [p.stepRows] pre-built `buildStepRows` output (a live hook's); built here when absent
 */
export function buildTopologyModel({ scenario = null, run = null, results = [], stepRows = null } = {}) {
  const proven = isRunComplete(run?.status)
  const rows = Array.isArray(results) ? results.filter(Boolean) : []
  const byStep = groupByStep(rows)
  const draftPlane = scenario?.plane || null

  // The step list: the scenario's when we have it, else the ids the host and
  // the rows know about, in transcript order where the transcript has one.
  let steps
  let stepsFrom
  if (Array.isArray(scenario?.steps) && scenario.steps.length) {
    steps = scenario.steps.map(toLaneStep)
    stepsFrom = 'scenario'
  } else {
    const parsed = parseStepTranscript(run?.output)
    const ids = [...parsed.order]
    for (const o of run?.step_outcomes || []) if (o?.step_id && !ids.includes(o.step_id)) ids.push(o.step_id)
    for (const id of byStep.keys()) if (id && !ids.includes(id)) ids.push(id)
    steps = ids.map((id) => ({
      id,
      // Result rows record each expected detection's plane: that is live data
      // about THIS run, so lanes can still be derived without the scenario.
      detections: (byStep.get(id) || []).map((r) => ({ plane: r.plane })),
    }))
    stepsFrom = 'run'
  }

  const hostRows = Array.isArray(stepRows) && stepRows.length
    ? stepRows
    : buildStepRows(steps, run, parseStepTranscript(run?.output))
  const hostById = new Map(hostRows.map((r) => [r.id, r]))

  const columns = []
  const unplaced = []
  steps.forEach((step, i) => {
    const srows = byStep.get(step.id) || []
    const tones = srows.map((r) => resultTone(r, proven))
    const host = hostById.get(step.id) || null
    const cell = {
      id: step.id,
      index: i + 1,
      name: step.name || host?.name || null,
      identity: host?.identity || step.identity || null,
      technique: host?.technique || step.mitre_technique || null,
      status: host?.status || 'queued',
      rc: host?.rc ?? null,
      duration: host?.duration || null,
      reason: host?.reason || null,
      identityNote: host?.identityNote || null,
      channel: step.channel || 'agent',
      rows: srows,
      engineRows: srows.filter(isEngineRow),
      hits: tones.filter((t) => t === 'hit').length,
      denominator: tones.filter((t) => t !== 'excluded').length,
      tone: srows.length ? worstTone(tones) : 'none',
    }
    if (!doorKnown(step, draftPlane)) {
      unplaced.push({ ...cell, lane: null, reason: 'no channel and no plane on the step or its result rows' })
      return
    }
    columns.push({ ...cell, lane: laneOf(step, null, draftPlane) })
  })

  const lanes = STEP_LANES.map((key) => {
    const band = BANDS[key]
    const cells = columns.filter((c) => c.lane === key)
    return {
      key,
      label: band?.label || key,
      sub: band?.sub || '',
      cells,
      empty: cells.length === 0,
      tone: worstTone(cells.filter((c) => c.rows.length).map((c) => c.tone)),
      hits: cells.reduce((n, c) => n + c.hits, 0),
      denominator: cells.reduce((n, c) => n + c.denominator, 0),
    }
  })

  // Lane crossings along execution order: where the chain changed doors.
  const crossings = []
  for (let i = 1; i < columns.length; i += 1) {
    if (columns[i].lane !== columns[i - 1].lane) {
      crossings.push({ from: columns[i - 1].lane, to: columns[i].lane, at: columns[i].id })
    }
  }

  const engineRows = rows.filter(isEngineRow)
  const engineTones = engineRows.map((r) => resultTone(r, proven))
  const engine = {
    key: ENGINE_LANE,
    label: BANDS[ENGINE_LANE]?.label || 'ANALYTICS',
    sub: BANDS[ENGINE_LANE]?.sub || 'Cortex Engine',
    rows: engineRows,
    hits: engineTones.filter((t) => t === 'hit').length,
    denominator: engineTones.filter((t) => t !== 'excluded').length,
    tone: worstTone(engineTones),
    empty: engineRows.length === 0,
    tcVerdict: run?.tc_verdict || null,
    tcVerdictDetail: run?.tc_verdict_detail || null,
  }

  const allTones = rows.map((r) => resultTone(r, proven))
  return {
    proven,
    runStatus: run?.status || null,
    stepsFrom,
    columns,
    lanes,
    engine,
    crossings,
    unplaced,
    totals: {
      steps: steps.length,
      rows: rows.length,
      hits: allTones.filter((t) => t === 'hit').length,
      denominator: allTones.filter((t) => t !== 'excluded').length,
    },
  }
}
