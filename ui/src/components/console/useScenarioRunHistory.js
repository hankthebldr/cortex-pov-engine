import { useEffect, useMemo, useState } from 'react'
import { getRuns } from '../../api/client.js'

/**
 * useScenarioRunHistory — fetch the run list once and roll it up
 * by scenario_id so the ScenarioGrid can render an at-a-glance
 * history badge.
 *
 * Keeps the cost flat: one /api/runs call instead of N per-scenario
 * detail fetches. We trade granularity for snappiness — the badge
 * shows count + recency + last status, which is enough to help DCs
 * pick "what haven't I tested" vs "what's been re-run a dozen times."
 *
 * Returns:
 *   {
 *     historyByScenario: Map<scenario_id, {
 *       count, lastRunAt, lastStatus, lastRunId
 *     }>,
 *     loading, refresh
 *   }
 */
export default function useScenarioRunHistory() {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = () => {
    setLoading(true)
    getRuns()
      .then((list) => setRuns(Array.isArray(list) ? list : []))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false))
  }

  useEffect(() => { refresh() }, [])

  const historyByScenario = useMemo(() => buildHistory(runs), [runs])
  const runsByScenario    = useMemo(() => groupRuns(runs), [runs])

  return { historyByScenario, runsByScenario, loading, refresh }
}

/**
 * Group raw runs by scenario_id, sorted most-recent first. Returns a
 * Map<scenario_id, Array<run>> — the inspector uses this to render
 * a "last 5 runs" list per-scenario without re-fetching.
 */
function groupRuns(runs) {
  const map = new Map()
  for (const r of runs) {
    const sid = r.scenario_id || r.scenarioId
    if (!sid) continue
    if (!map.has(sid)) map.set(sid, [])
    map.get(sid).push(r)
  }
  for (const list of map.values()) {
    list.sort((a, b) => parseTs(b.started_at || b.created_at) - parseTs(a.started_at || a.created_at))
  }
  return map
}

function buildHistory(runs) {
  const map = new Map()
  for (const r of runs) {
    const sid = r.scenario_id || r.scenarioId
    if (!sid) continue
    const startedAt = parseTs(r.started_at || r.created_at || r.timestamp)
    const entry = map.get(sid) || { count: 0, lastRunAt: 0, lastStatus: null, lastRunId: null }
    entry.count += 1
    if (startedAt > entry.lastRunAt) {
      entry.lastRunAt = startedAt
      entry.lastStatus = (r.status || 'unknown').toLowerCase()
      entry.lastRunId = r.id || r.run_id
    }
    map.set(sid, entry)
  }
  return map
}

function parseTs(t) {
  if (!t) return 0
  const n = Date.parse(t)
  return Number.isFinite(n) ? n : 0
}

/**
 * Format a Unix-ms timestamp as a relative "5m ago" / "3h ago" /
 * "2d ago" string. Past-only — future timestamps are clamped to
 * "just now" since we never display upcoming events here.
 */
export function formatAgo(ms) {
  if (!ms) return ''
  const delta = Math.max(0, Date.now() - ms)
  const s = Math.floor(delta / 1000)
  if (s < 60) return 'just now'
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}d ago`
  const mo = Math.floor(d / 30)
  return `${mo}mo ago`
}
