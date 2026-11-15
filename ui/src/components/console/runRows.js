/**
 * runRows — the pure helpers behind a row on the Runs surface. No React.
 *
 * Every value here is derived from fields `GET /api/runs` actually returns
 * (`started_at`, `completed_at`, `status`, `mode`, `target`,
 * `detected_count`, `expected_detections`). Where the API did not report a
 * value the helper returns a dash with a title saying so — a fabricated
 * "0 detections" on a run that detected things is the kind of number that
 * ends up in a customer readout.
 *
 * Two rules worth naming:
 *   - timestamps go through `parseServerTime`, because SimCore's timestamps
 *     are naive UTC and `new Date()` would shift them by the DC's offset;
 *   - a run that did not complete has NO coverage number. `detectionCell`
 *     renders `unproven` for a failed/aborted run with zero hits, mirroring
 *     `isRunUnproven`, because "0 / 10" on a run that died at step one reads
 *     as "Cortex missed ten things".
 */
import { parseServerTime } from '../../api/time.js'
import { isRunComplete, isRunUnproven } from './runStatus.js'

export const PAGE_SIZE = 25

/** "2m ago" · "4h ago" · "3d ago"; a dash when the value is not a time. */
export function relativeTime(value, now = Date.now()) {
  const t = parseServerTime(value)
  if (Number.isNaN(t)) return '—'
  const diff = Math.max(0, now - t)
  const s = Math.floor(diff / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

/** "2026-11-02 22:07:46 UTC" — the zone is stated because the value is UTC. */
export function absoluteTime(value) {
  const t = parseServerTime(value)
  if (Number.isNaN(t)) return ''
  return `${new Date(t).toISOString().replace('T', ' ').slice(0, 19)} UTC`
}

function formatSpan(ms) {
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  if (m < 60) return rs ? `${m}m ${rs}s` : `${m}m`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return rm ? `${h}h ${rm}m` : `${h}h`
}

/**
 * Wall-clock span of a run. `completed_at − started_at` once terminal, live
 * elapsed while `running`, and a dash where nothing can be measured: a staged
 * push bundle never phones home, and a failed run with no `completed_at` is
 * not "still running".
 */
export function runDuration(run, now = Date.now()) {
  if (!run) return '—'
  const status = String(run.status || '').toLowerCase()
  if (status === 'staged') return '—'
  const start = parseServerTime(run.started_at)
  if (Number.isNaN(start)) return '—'
  if (status === 'running') return formatSpan(Math.max(0, now - start))
  const end = parseServerTime(run.completed_at)
  if (Number.isNaN(end)) return '—'
  return formatSpan(Math.max(0, end - start))
}

/**
 * The detections cell: `{ text, tone, title }`.
 *
 * tone ∈ hit | partial | zero | live | unproven | none. `zero` is a complete
 * run with no hit yet — NOT a miss: without a reconcile, un-fired rows are
 * `pending` by the engine's own rule, and the row cannot tell the two apart.
 */
export function detectionCell(run) {
  const expected = run?.expected_detections
  const detected = run?.detected_count
  if (typeof expected !== 'number' || typeof detected !== 'number' || expected === 0) {
    return { text: '—', tone: 'none', title: 'detections not reported for this run' }
  }
  const status = String(run.status || '').toLowerCase()
  if (isRunUnproven(status) && detected === 0) {
    return {
      text: 'unproven',
      tone: 'unproven',
      title: 'the run did not complete — a zero here is not a measurement of the tenant',
    }
  }
  const text = `${detected} / ${expected}`
  const title = `${detected} of ${expected} expected detections observed on this SimCore`
  if (status === 'running') return { text, tone: 'live', title }
  const tone = detected === expected ? 'hit' : detected > 0 ? 'partial' : 'zero'
  return { text, tone, title }
}

/** Who ran it: the beacon id for a pull run, "push bundle" for a push run. */
export function runAgent(run) {
  if (!run) return '—'
  if (String(run.mode || '').toLowerCase() === 'push') return 'push bundle'
  return run.target || '—'
}

/** Canonical status token for filtering (legacy `completed` → `complete`). */
export function statusKey(status) {
  const s = String(status || '').toLowerCase()
  return isRunComplete(s) ? 'complete' : s
}

export function filterRuns(runs = [], { scenario = '', status = '' } = {}) {
  const want = status ? statusKey(status) : ''
  return runs.filter((r) => {
    if (!r) return false
    if (scenario && r.scenario_id !== scenario) return false
    if (want && statusKey(r.status) !== want) return false
    return true
  })
}

/**
 * Collapse runs of CONSECUTIVE repeats of one scenario into a group once the
 * run reaches `min`. Rows stay in the order given (newest first), so a group's
 * `newest` is its first row. Shorter runs stay as plain rows — two attempts
 * side by side are worth seeing in full.
 *
 * @returns {Array<{kind:'group', key, scenarioId, rows, newest, byStatus}
 *                | {kind:'row', key, rows:[run], run}>}
 */
export function groupRuns(runs = [], min = 3) {
  const out = []
  let buf = []
  const flush = () => {
    if (!buf.length) return
    if (buf.length >= min) {
      const byStatus = {}
      for (const r of buf) {
        const k = statusKey(r.status) || 'unknown'
        byStatus[k] = (byStatus[k] || 0) + 1
      }
      out.push({
        kind: 'group',
        key: `g-${buf[0].run_id || buf[0].id}`,
        scenarioId: buf[0].scenario_id,
        rows: buf,
        newest: buf[0],
        byStatus,
      })
    } else {
      for (const r of buf) out.push({ kind: 'row', key: r.run_id || String(r.id), rows: [r], run: r })
    }
    buf = []
  }
  for (const r of runs) {
    if (!r) continue
    if (buf.length && buf[0].scenario_id !== r.scenario_id) flush()
    buf.push(r)
  }
  flush()
  return out
}

/** One page of `items`; the page number clamps rather than going empty. */
export function pageOf(items = [], page = 1, size = PAGE_SIZE) {
  const total = items.length
  const pages = Math.max(1, Math.ceil(total / size))
  const p = Math.min(Math.max(1, Number(page) || 1), pages)
  return { items: items.slice((p - 1) * size, p * size), page: p, pages, total }
}
