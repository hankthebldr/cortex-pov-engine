import { useState, useEffect, useMemo, useCallback } from 'react'
import { getResultsForRun, validateResult } from '../../api/client.js'

/**
 * useResultsData — fetches and folds /api/results/:runId into:
 *   - rows: render-ready rows for the scorecard
 *   - kpis: summary metrics (coverage %, MTTD median, stitch count, pending count)
 *
 * Provides validate(resultId, observed, notes) which optimistically updates
 * local state and refetches on completion.
 *
 * @param {string|null} runId
 * @returns {{ rows, kpis, loading, error, validate, refresh }}
 */
export default function useResultsData(runId) {
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const refresh = useCallback(async () => {
    if (!runId) { setPayload(null); return }
    setLoading(true)
    try {
      const data = await getResultsForRun(runId)
      setPayload(data)
      setError(null)
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Poll every 8s while results are pending.
  useEffect(() => {
    if (!runId) return undefined
    const t = setInterval(refresh, 8_000)
    return () => clearInterval(t)
  }, [runId, refresh])

  const rows = useMemo(() => {
    const list = (payload && (payload.results || payload)) || []
    if (!Array.isArray(list)) return []
    return list.map((r) => ({
      id: r.id ?? r.result_id ?? null,
      tid: r.mitre_technique || r.tid || '—',
      plane: (r.plane || '').toUpperCase(),
      detectionType: (r.detection_type || r.type || '').toUpperCase(),
      alert: r.expected_description || r.description || r.alert_name || '(unnamed)',
      mttd: r.mttd_seconds ?? null,
      alertId: r.alert_id || null,
      observed: r.observed,        // true | false | null  (null = pending)
      notes: r.notes || '',
      executedAt: r.executed_at || null,
      observedAt: r.observed_at || null,
    }))
  }, [payload])

  const kpis = useMemo(() => {
    const total     = rows.length
    const detected  = rows.filter((r) => r.observed === true).length
    const missed    = rows.filter((r) => r.observed === false).length
    const pending   = rows.filter((r) => r.observed == null).length
    const mttdList  = rows.filter((r) => r.observed === true && typeof r.mttd === 'number')
                          .map((r) => r.mttd)
                          .sort((a, b) => a - b)
    const median = mttdList.length
      ? mttdList[Math.floor(mttdList.length / 2)]
      : null
    const coverage = total > 0 ? Math.round((detected / total) * 100) : 0
    const stitched = rows.filter((r) =>
      r.plane === 'ANALYTICS' && r.observed === true
    ).length

    return { total, detected, missed, pending, coverage, median, stitched }
  }, [rows])

  const validate = useCallback(async (resultId, observed, notes) => {
    if (resultId == null) return
    // Optimistic update
    setPayload((prev) => {
      if (!prev) return prev
      const list = (prev.results || prev)
      if (!Array.isArray(list)) return prev
      const next = list.map((r) =>
        (r.id === resultId || r.result_id === resultId)
          ? { ...r, observed, notes: notes ?? r.notes }
          : r
      )
      return Array.isArray(prev) ? next : { ...prev, results: next }
    })
    try {
      await validateResult(resultId, observed, notes)
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      // Refresh to pick up server-side mttd_seconds + observed_at
      refresh()
    }
  }, [refresh])

  return { rows, kpis, loading, error, validate, refresh }
}
