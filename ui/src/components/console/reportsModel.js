/**
 * reportsModel.js — the pure model behind Reports › Executions. No React.
 *
 * Everything drawn there is a value GET /api/reports/executions sent. This
 * file names it (a tone and one word per status and outcome), orders it and
 * sorts it; it decides nothing. Outcome words come from `outcomes.js`, the
 * console's one outcome vocabulary.
 *
 * Three readings it must never produce:
 *   - a detection nobody reviewed as Missed (`not_reviewed` is its own bucket);
 *   - a scenario, technique or test case no reviewed run reached as zeros
 *     (`outcomes: null` draws a dash);
 *   - a value outside the vocabulary as nothing (it is shown as Unrecognised).
 *
 * An unrecognised response shape raises instead of reading as an empty engine.
 */
import { outcomeMeta } from '../../outcomes.js'
import { parseServerTime } from '../../api/time.js'

const HOUR = 3600e3

/** Time-range presets: `[id, label, milliseconds back]`; `all` has no bound. */
export const RANGES = Object.freeze([
  ['24h', '24 h', 24 * HOUR],
  ['7d', '7 d', 7 * 24 * HOUR],
  ['30d', '30 d', 30 * 24 * HOUR],
  ['all', 'All', null],
])

/** The ISO `since` a preset means at `now`, or undefined for all time. */
export function sinceFor(rangeId, now = Date.now()) {
  const range = RANGES.find(([id]) => id === rangeId)
  if (!range || range[2] === null) return undefined
  return new Date(now - range[2]).toISOString()
}

/** `Run.status` → StatusIcon tone and one word. */
const RUN_STATUS = Object.freeze({
  complete: { status: 'up', word: 'Finished' },
  failed: { status: 'down', word: 'Failed' },
  aborted: { status: 'degraded', word: 'Aborted' },
  staged: { status: 'off', word: 'Staged' },
  running: { status: 'running', word: 'Running' },
  pending: { status: 'unknown', word: 'Queued' },
})
export const RUN_STATUS_ORDER = Object.freeze(['complete', 'failed', 'aborted', 'staged', 'running', 'pending'])

/** A status outside the vocabulary keeps its own text and never a colour. */
export function runStatusMeta(status) {
  return RUN_STATUS[status] || { status: 'unknown', word: String(status ?? 'Unknown') }
}

/** Detection buckets in display order: `[key, tone]`. Words come from `outcomes.js`. */
const OUTCOME_TONES = Object.freeze([
  ['prevented', 'up'],
  ['detected', 'up'],
  ['detected_not_prevented', 'down'],
  ['missed', 'down'],
  ['pending', 'unknown'],
  ['not_reached', 'off'],
  ['not_executed', 'off'],
  ['not_reviewed', 'unknown'],
])
export const NOT_REVIEWED_WORD = 'Not reviewed'

function outcomeWord(key) {
  return key === 'not_reviewed' ? NOT_REVIEWED_WORD : outcomeMeta(key).label
}

function unrecognisedChip(map, noun) {
  const entries = Object.entries(map || {})
  if (!entries.length) return null
  return {
    key: 'unrecognised',
    status: 'degraded',
    word: 'Unrecognised',
    n: entries.reduce((sum, [, n]) => sum + n, 0),
    title: `${noun} this console does not know: ${entries.map(([k, n]) => `${k} (${n})`).join(', ')}`,
  }
}

/**
 * The non-zero detection buckets of an `outcomes` object, in order, as
 * `{key, status, word, n, title?}`. Null in (no reviewed run) is null out.
 */
export function outcomeChips(outcomes) {
  if (!outcomes) return null
  const chips = OUTCOME_TONES
    .filter(([key]) => outcomes[key] > 0)
    .map(([key, status]) => ({ key, status, word: outcomeWord(key), n: outcomes[key] }))
  const odd = unrecognisedChip(outcomes.unrecognised, 'Outcomes')
  return odd ? [...chips, odd] : chips
}

/** Runs by status as chips; a status outside the vocabulary is its own chip. */
export function runStatusChips(runs) {
  const chips = RUN_STATUS_ORDER
    .filter((key) => runs?.by_status?.[key] > 0)
    .map((key) => ({ key, ...runStatusMeta(key), n: runs.by_status[key] }))
  const odd = unrecognisedChip(runs?.unrecognised, 'Run statuses')
  return odd ? [...chips, odd] : chips
}

/** What the host recorded about steps, as chips. */
export function stepChips(steps) {
  if (!steps) return []
  const chips = [
    ['ran', 'up', 'Ran'],
    ['failed', 'down', 'Failed'],
    ['did_not_run', 'off', 'Did not run'],
    ['interrupted', 'degraded', 'Interrupted'],
  ].filter(([key]) => steps[key] > 0).map(([key, status, word]) => ({ key, status, word, n: steps[key] }))
  const by = Object.entries(steps.interrupted_by || {})
  const interrupted = chips.find((c) => c.key === 'interrupted')
  if (interrupted && by.length) interrupted.title = by.map(([k, n]) => `${k.replace(/_/g, ' ')} (${n})`).join(', ')
  const odd = unrecognisedChip(steps.unrecognised, 'Step endings')
  if (odd) chips.push(odd)
  if (steps.runs_without_host_record > 0) {
    chips.push({
      key: 'no_host_record',
      status: 'unknown',
      word: 'No host record',
      n: steps.runs_without_host_record,
      title: 'Runs whose host reported no steps',
    })
  }
  return chips
}

/** The three table groupings: `[id, label]`. */
export const GROUPINGS = Object.freeze([
  ['scenario', 'By scenario'],
  ['technique', 'By technique'],
  ['test_case', 'By test case'],
])

const time = (v) => {
  const t = parseServerTime(v)
  return Number.isNaN(t) ? null : t
}
const text = (v) => (v == null ? null : String(v).toLowerCase())
const hits = (o) => (o ? (o.detected || 0) + (o.prevented || 0) : null)

/**
 * Columns per grouping: `{id, label, sort?, title?}`. `sort` reads the value a
 * column orders by; a column without one is not sortable.
 *
 * The scenario table reads as the mapping, left to right: scenario, plane,
 * techniques, UC / TC, its runs, and the outcome. Sources and First run come
 * last because they are the two that give way when the window is too narrow
 * for twelve columns (coverage.css): the table always fits its card, and
 * nothing is pinned over another column.
 */
export const COLUMNS = Object.freeze({
  scenario: [
    { id: 'scenario', label: 'Scenario', sort: (r) => text(r.scenario_id) },
    { id: 'plane', label: 'Plane', sort: (r) => text(r.plane) },
    { id: 'techniques', label: 'Techniques', sort: (r) => r.techniques.length },
    { id: 'uctc', label: 'UC / TC', sort: (r) => text(r.uc_ref) },
    { id: 'runs', label: 'Runs', sort: (r) => r.runs },
    { id: 'last', label: 'Last run', sort: (r) => time(r.last_run_at) },
    { id: 'status', label: 'Last status', sort: (r) => text(r.last_status) },
    { id: 'reviewed', label: 'Reviewed', sort: (r) => r.reviewed_runs, title: 'Runs reviewed against the tenant' },
    { id: 'outcomes', label: 'Outcomes', sort: (r) => hits(r.outcomes), title: 'Its most recent reviewed run' },
    { id: 'sources', label: 'Sources', title: 'Detection sources: the ingestion doors its detections need' },
    { id: 'first', label: 'First run', sort: (r) => time(r.first_run_at) },
    { id: 'action', label: 'Open' },
  ],
  technique: [
    { id: 'technique', label: 'Technique', sort: (r) => text(r.technique) },
    { id: 'scenarios', label: 'Scenarios', sort: (r) => r.scenarios.length },
    { id: 'runs', label: 'Runs', sort: (r) => r.runs },
    { id: 'last', label: 'Last run', sort: (r) => time(r.last_run_at) },
    { id: 'reviewed', label: 'Reviewed', sort: (r) => r.reviewed_scenarios, title: 'Scenarios with a reviewed run' },
    { id: 'outcomes', label: 'Outcomes', sort: (r) => hits(r.outcomes), title: "Each scenario's most recent reviewed run" },
  ],
  test_case: [
    { id: 'tc', label: 'Test case', sort: (r) => text(r.tc_ref) },
    { id: 'uc', label: 'Use case', sort: (r) => text(r.uc_ref) },
    { id: 'scenarios', label: 'Scenarios', sort: (r) => r.scenarios.length },
    { id: 'runs', label: 'Runs', sort: (r) => r.runs },
    { id: 'last', label: 'Last run', sort: (r) => time(r.last_run_at) },
    { id: 'reviewed', label: 'Reviewed', sort: (r) => r.reviewed_scenarios, title: 'Scenarios with a reviewed run' },
    { id: 'outcomes', label: 'Outcomes', sort: (r) => hits(r.outcomes), title: "Each scenario's most recent reviewed run" },
  ],
})

/** Where each grouping starts: newest run first. */
export const DEFAULT_SORT = Object.freeze({ id: 'last', dir: 'desc' })

/** The rows of one grouping, and the key that identifies a row. */
export function rowsOf(report, grouping) {
  if (grouping === 'technique') return { rows: report.by_technique, keyOf: (r) => r.technique }
  if (grouping === 'test_case') return { rows: report.by_test_case, keyOf: (r) => r.tc_ref }
  return { rows: report.by_scenario, keyOf: (r) => r.scenario_id }
}

/** Sort a copy of `rows` by a column. A row with no value sorts last either way. */
export function sortRows(rows, column, dir = 'asc') {
  if (!column?.sort) return [...rows]
  const sign = dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const va = column.sort(a)
    const vb = column.sort(b)
    if (va == null && vb == null) return 0
    if (va == null) return 1
    if (vb == null) return -1
    if (va < vb) return -sign
    if (va > vb) return sign
    return 0
  })
}

/** `aria-sort` for a header. */
export function ariaSort(column, sort) {
  if (!column.sort) return undefined
  if (sort.id !== column.id) return 'none'
  return sort.dir === 'desc' ? 'descending' : 'ascending'
}

/** The next sort when a header is pressed: the same column flips, a new one ascends. */
export function nextSort(sort, columnId) {
  if (sort.id === columnId) return { id: columnId, dir: sort.dir === 'asc' ? 'desc' : 'asc' }
  return { id: columnId, dir: 'asc' }
}

/** Refuse a body that is not the report: it must never read as "no runs". */
export function checkReport(data) {
  const ok = data && typeof data === 'object' && data.totals && typeof data.totals === 'object'
    && data.totals.runs && data.totals.detections && data.totals.steps && data.totals.scenarios
    && Array.isArray(data.by_scenario) && Array.isArray(data.by_technique) && Array.isArray(data.by_test_case)
    && typeof data.empty === 'boolean'
  if (!ok) throw new Error('GET /api/reports/executions returned an unrecognised shape')
  return data
}

/** "first + N" for a list cell: `{shown, more, all}`. */
export function clip(list, shown = 2) {
  const all = Array.isArray(list) ? list : []
  return { shown: all.slice(0, shown), more: Math.max(0, all.length - shown), all }
}
