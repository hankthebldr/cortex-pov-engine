/**
 * runTopology — the pure model behind a run's Topology tab.
 *
 * A lane is a step's SIGNAL PATH — the mechanism that produced its evidence,
 * the same five lanes the Composer bands by (`composerLanes.js`), so a node's
 * lane on the design canvas and its lane here mean the same thing. There is
 * ONE `laneOf()` (through `composerSignal.signalOf`); this module calls it and
 * adds nothing of its own to the derivation.
 *
 * Inputs are live records only:
 *   - the scenario's `steps[]` (for `channel`, `eal`, `command`, identity,
 *     names). When the scenario cannot be loaded the step ids still come from
 *     the transcript, outcomes and Result rows, but a path is read from the
 *     COMMAND and no row carries one, so every step is listed as unplaced;
 *   - the run record (`status`, `tc_verdict`, `output`, `step_outcomes[]`) for
 *     the host's per-step record (rc, termination), joined by `buildStepRows`;
 *   - the run's `Result` rows for per-step detection tones.
 *
 * Honesty rules:
 *   - a step whose path cannot be read (no command, an unknown EAL plugin) is
 *     listed as `unplaced` WITH the reason — never defaulted into a lane;
 *   - a lane with no step is `empty` — dimmed, not "clean";
 *   - the Engine terminal is a VIEW onto rows already counted in their step's
 *     cell (Correlation / Analytics / ABIOC), never a second population, so
 *     the page's totals cannot double-count;
 *   - a `miss` tone needs a proven run (see `runTechniques.resultTone`).
 */
import { STEP_LANES, BANDS } from './composerLanes.js'
import { signalOf } from './composerSignal.js'
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
    // A Result row records each expected detection's PLANE (the observer). A
    // path is read from the step's command, which no row carries, so these
    // steps are listed, not placed.
    steps = ids.map((id) => ({
      id,
      command: null,
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
    const signal = signalOf(step)
    if (signal.unplaced) {
      const reason = stepsFrom === 'run'
        ? 'scenario not loaded: the signal path is read from the step\'s command'
        : signal.reason
      unplaced.push({ ...cell, signal, lane: null, reason })
      return
    }
    columns.push({ ...cell, signal, lane: signal.path })
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

  // Lane crossings along execution order: where the chain changed path.
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
    label: BANDS[ENGINE_LANE]?.label || 'CORTEX ENGINE',
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
