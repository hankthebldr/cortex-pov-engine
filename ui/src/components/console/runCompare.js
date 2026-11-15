/**
 * runCompare.js — the pure model behind Runs · Compare (design change C2).
 * No React, no fetch.
 *
 * Two or three FINISHED runs side by side: one row per expected detection,
 * one column per run, then four paired rows (Detected · Steps that ran ·
 * Fastest detection · Workflow).
 *
 * THE HONESTY RULE. Only a run that has been read back (`readBackOf`) may say
 * "Detected" or "Not seen" for a detection. Any other run says what the run
 * itself knows: "Ran — not read back" when the step ran, "Did not run" when it
 * did not. A Result row on an unread run can carry `observed: true` (a hand
 * mark) or an outcome; neither is a tenant read, so neither is shown. The
 * words are chainStage's LIVE_STATES, the same vocabulary the chain draws.
 *
 * Every number is a value the API returned or a count of rows it returned;
 * where it returned nothing the cell is a dash with a reason, never a zero.
 */
import { runIdOf } from '../../api/ids.js'
import { isRunTerminal } from './runStatus.js'
import { readBackOf, PHASE } from './runLifecycle.js'
import { LIVE_STATES } from './chainStage.js'
import { resultStatus, isHitStatus, isInDenominator, outcomeMeta } from '../../outcomes.js'

/** Compare takes two or three runs. */
export const COMPARE_MIN = 2
export const COMPARE_MAX = 3

/** What a DC is told when a fourth run is picked. */
export const COMPARE_FULL = 'Compare up to three runs — remove one first.'

/** A run Compare can take: it ended here. Running and staged runs cannot. */
export function isComparable(run) {
  return !!run && isRunTerminal(run.status)
}

/**
 * Toggle `id` in the picked list. A pick past COMPARE_MAX is refused and the
 * list is returned unchanged with `refused: true`.
 */
export function togglePick(picked, id) {
  const list = Array.isArray(picked) ? picked : []
  if (list.includes(id)) return { picked: list.filter((x) => x !== id), refused: false }
  if (list.length >= COMPARE_MAX) return { picked: list, refused: true }
  return { picked: [...list, id], refused: false }
}

/** A run's lifecycle phase in one word, for its chip and column header. */
export const PHASE_STATUS = Object.freeze({
  [PHASE.READY]:   { status: 'up',      word: 'Read back' },
  [PHASE.UNREAD]:  { status: 'off',     word: 'Not read back' },
  [PHASE.READING]: { status: 'unknown', word: 'Reading…' },
  [PHASE.NOTHING]: { status: 'off',     word: 'Nothing ran' },
  [PHASE.RUNNING]: { status: 'unknown', word: 'Running' },
})

/** Outcomes whose step never ran (core/engine/outcome.py). */
const NEVER_RAN = new Set(['not_reached', 'not_executed'])

/**
 * One detection cell: a LIVE_STATES key, or `absent` when the detection is not
 * part of that run.
 *
 * @param {Object} run        the run (only `readBackOf(run)` is read here)
 * @param {Object|null} row   the run's Result row for this detection
 * @param {Object} [opts]
 * @param {boolean} [opts.nothingRan]  the run ended before any step ran
 */
export function compareCell(run, row, { nothingRan = false } = {}) {
  if (!row) return 'absent'
  const status = resultStatus(row)
  const ran = !nothingRan && !NEVER_RAN.has(status)
  // Only a read-back run may claim Detected / Not seen.
  if (!readBackOf(run)) return ran ? 'ran' : 'did_not_run'
  if (!ran) return 'did_not_run'
  return isHitStatus(status) ? 'detected' : 'not_seen'
}

/** The words and StatusIcon tone of a cell. */
export function cellMeta(key) {
  if (key === 'absent') return { status: null, word: 'Not in this run' }
  return LIVE_STATES[key]
}

/** Seconds as "38s" / "2m 5s"; a dash when there is no number. */
export function formatSeconds(value) {
  const n = Number(value)
  if (value == null || !Number.isFinite(n) || n < 0) return '—'
  const s = Math.round(n)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  return rs ? `${m}m ${rs}s` : `${m}m`
}

/** The identity a detection lines up on across runs. */
function detectionKey(row) {
  return row.detection_id || `${row.signal_type || ''}:${row.expected_detection || row.step_id || row.id}`
}

/**
 * Build the grid.
 *
 * @param {Object} input
 * @param {Array}  input.runs         the picked runs, in pick order
 * @param {Object} input.lifecycles   runId → runLifecycle(run)
 * @param {Object} input.results      runId → { rows, coverage } | { error } | undefined (loading)
 * @returns {{ columns: Array, rows: Array, summary: Array, mixed: boolean }}
 */
export function buildCompare({ runs = [], lifecycles = {}, results = {} } = {}) {
  const columns = runs.map((run) => {
    const id = runIdOf(run)
    const lc = lifecycles[id] || { phase: PHASE.UNREAD }
    const entry = results[id]
    return {
      id,
      run,
      phase: lc.phase,
      readBack: !!readBackOf(run),
      nothingRan: lc.phase === PHASE.NOTHING,
      loading: entry === undefined,
      error: entry && entry.error ? entry.error : null,
      rows: entry && Array.isArray(entry.rows) ? entry.rows : [],
      coverage: entry && entry.coverage ? entry.coverage : null,
    }
  })

  // One row per detection, in the order the picked runs first name them. A
  // detection named twice in one run (two steps) lines up by occurrence.
  const order = []
  const byKey = new Map()
  for (const col of columns) {
    const seen = new Map()
    col.keyed = new Map()
    for (const r of col.rows) {
      const base = detectionKey(r)
      const n = (seen.get(base) || 0) + 1
      seen.set(base, n)
      const key = n > 1 ? `${base}#${n}` : base
      col.keyed.set(key, r)
      if (!byKey.has(key)) {
        byKey.set(key, {
          key,
          type: r.signal_type || '',
          slug: r.detection_id || r.expected_detection || '(unnamed)',
          title: r.detection_name || r.expected_detection || '',
          step: r.step_id || '',
        })
        order.push(key)
      }
    }
  }

  const rows = order.map((key) => {
    const row = byKey.get(key)
    return {
      ...row,
      cells: columns.map((col) => {
        if (col.loading || col.error) return { key: col.loading ? 'loading' : 'error', title: '' }
        const r = col.keyed.get(key) || null
        const k = compareCell(col.run, r, { nothingRan: col.nothingRan })
        let title = ''
        if (k === 'detected' || k === 'not_seen') {
          const st = resultStatus(r)
          title = outcomeMeta(st).label + (r.mttd_seconds != null && isHitStatus(st) ? ` · ${formatSeconds(r.mttd_seconds)}` : '')
        } else if (k === 'ran') {
          title = 'the step ran; the tenant has not been read for this run'
        } else if (k === 'did_not_run') {
          title = 'the step that carries this detection did not run'
        }
        return { key: k, title }
      }),
    }
  })

  const summary = [
    { key: 'detected', label: 'Detected', values: columns.map(detectedValue) },
    { key: 'steps', label: 'Steps that ran', values: columns.map(stepsValue) },
    { key: 'fastest', label: 'Fastest detection', values: columns.map(fastestValue) },
    { key: 'workflow', label: 'Workflow', values: columns.map((c) => ({
      text: c.run.scenario_id || '—',
      title: c.run.scenario_id ? 'the scenario this run executed' : 'the run names no scenario',
    })) },
  ]

  const scenarios = new Set(columns.map((c) => c.run.scenario_id || ''))
  return { columns, rows, summary, mixed: scenarios.size > 1 }
}

const NOT_READ = { text: '—', title: 'not read back — no detection claim' }

function unloaded(col) {
  if (col.loading) return { text: '…', title: 'loading this run’s results' }
  if (col.error) return { text: '—', title: `results could not be loaded: ${col.error}` }
  return null
}

function detectedValue(col) {
  if (!col.readBack) return NOT_READ
  const u = unloaded(col)
  if (u) return u
  const cov = col.coverage
  if (cov && Number.isFinite(cov.denominator) && Number.isFinite(cov.observed_in_denominator)) {
    return { text: `${cov.observed_in_denominator} of ${cov.denominator}`, title: 'over the rows whose step ran' }
  }
  if (!col.rows.length) return { text: '—', title: 'this run has no expected detections' }
  const inDen = col.rows.filter((r) => isInDenominator(r.outcome, r.outcome_reason))
  const hits = inDen.filter((r) => isHitStatus(resultStatus(r))).length
  return { text: `${hits} of ${inDen.length}`, title: 'over the rows whose step ran' }
}

function stepsValue(col) {
  const u = unloaded(col)
  if (u) return u
  const steps = new Map()
  for (const r of col.rows) {
    const id = r.step_id || '(no step)'
    const ran = !col.nothingRan && !NEVER_RAN.has(resultStatus(r))
    steps.set(id, steps.get(id) || ran)
  }
  if (!steps.size) return { text: '—', title: 'this run has no step that carries a detection' }
  const ran = [...steps.values()].filter(Boolean).length
  return {
    text: `${ran} of ${steps.size}`,
    title: 'counted over the steps that carry an expected detection',
    short: ran < steps.size,
  }
}

function fastestValue(col) {
  if (!col.readBack) return NOT_READ
  const u = unloaded(col)
  if (u) return u
  const times = col.rows
    .filter((r) => isHitStatus(resultStatus(r)) && r.mttd_seconds != null && Number.isFinite(Number(r.mttd_seconds)))
    .map((r) => Number(r.mttd_seconds))
  if (!times.length) return { text: '—', title: 'no detection in this run carries a time' }
  return { text: formatSeconds(Math.min(...times)), title: 'launch to the first alert matched' }
}
