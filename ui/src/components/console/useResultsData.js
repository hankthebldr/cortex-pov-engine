import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { getResultsForRun, validateResult } from '../../api/client.js'
import { resultStatus, isHitStatus, isInDenominator, isAttestable } from '../../outcomes.js'

/**
 * useResultsData — fetches and folds /api/results/:runId into:
 *   - rows: render-ready rows for the scorecard
 *   - kpis: summary metrics (coverage %, MTTD median, stitch count, pending count)
 *
 * Coverage is the SERVER's (`/api/results` → `coverage`, computed with
 * `engine.outcome.is_in_denominator`): the console never re-derives the
 * denominator. Row status comes from `resultStatus` (outcomes.js), so a
 * `not_reached` / soak-pending row is never counted as missed.
 *
 * Provides validate(resultId, observed, notes) which optimistically updates
 * local state and refetches on completion.
 *
 * @param {string|null} runId
 * @returns {{ rows, kpis, loading, loaded, error, validate, refresh }}
 */
export default function useResultsData(runId) {
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  // Monotonic request id. Only the newest request may write state: an 8 s poll
  // against a slow SimCore, or a switch from run A to run B while A's fetch is
  // still in flight, otherwise lets an OLDER answer land last — run A's
  // scorecard under run B's header, or a pre-validate snapshot overwriting the
  // row the DC just marked.
  const seqRef = useRef(0)
  // Whether `payload` currently holds an answer for THIS runId. A failed first
  // fetch leaves it false, which is what lets the view say "could not load"
  // rather than render an empty scorecard as 0% coverage.
  const [loaded, setLoaded]   = useState(false)

  const refresh = useCallback(async () => {
    const seq = ++seqRef.current
    if (!runId) { setPayload(null); setLoaded(false); return }
    setLoading(true)
    try {
      const data = await getResultsForRun(runId)
      if (seq !== seqRef.current) return
      setPayload(data)
      setLoaded(true)
      setError(null)
    } catch (err) {
      if (seq !== seqRef.current) return
      setError(err.message || String(err))
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [runId])

  // A different run starts from nothing — never from the previous run's rows.
  useEffect(() => {
    setPayload(null)
    setLoaded(false)
    setError(null)
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
      // `expected_detection` is the column /api/results actually returns
      // (Result.expected_detection). The other three are legacy/aspirational
      // names and matched nothing, so every scorecard row read "(unnamed)".
      alert: r.expected_detection || r.expected_description || r.description
        || r.alert_name || '(unnamed)',
      mttd: r.mttd_seconds ?? null,
      alertId: r.alert_id || null,
      observed: r.observed,        // true | false | null  (null = pending)
      outcome: r.outcome ?? null,
      outcome_reason: r.outcome_reason ?? null,
      expectedOutcome: r.expected_outcome || null,
      status: resultStatus(r),     // the rendered status — outcome first
      notes: r.notes || '',
      executedAt: r.executed_at || null,
      observedAt: r.observed_at || null,
    }))
  }, [payload])

  const kpis = useMemo(() => {
    const seeded    = rows.length
    const missed    = rows.filter((r) => r.status === 'missed').length
    const pending   = rows.filter((r) => r.status === 'pending').length
    // What "Validate all" may act on: exactly the rows the server accepts as a
    // new attestation (outcome none / pending / missed, not yet observed).
    // `skipped` = unobserved rows it will not touch (never ran / prevented),
    // shown next to the button so the skip is never silent.
    const unvalidated = rows.filter((r) => isAttestable(r)).length
    const skipped = rows.filter((r) => r.observed !== true && !isAttestable(r)).length
    const mttdList  = rows.filter((r) => isHitStatus(r.status) && typeof r.mttd === 'number')
                          .map((r) => r.mttd)
                          .sort((a, b) => a - b)
    const median = mttdList.length
      ? mttdList[Math.floor(mttdList.length / 2)]
      : null
    // The server's honest denominator. Absent (a server that predates it) is
    // reported as unknown, never as a measured figure.
    const cov = (payload && !Array.isArray(payload) && payload.coverage) || null
    const denominator = cov && typeof cov.denominator === 'number' ? cov.denominator : null
    const detected = denominator != null ? cov.observed_in_denominator : null
    const excluded = cov && cov.excluded
      ? Object.values(cov.excluded).reduce((n, v) => n + (Number(v) || 0), 0)
      : 0
    // 0 of 0 is not 0 % — it is "nothing to score", and must not render as a
    // measured coverage figure.
    const coverage = denominator ? Math.round(cov.pct) : null
    const stitched = rows.filter((r) =>
      r.plane === 'ANALYTICS' && isHitStatus(r.status)
    ).length
    // The tile's denominator: ANALYTICS-plane rows (the numerator's own
    // population) that pass the one denominator rule -- a row that never ran
    // or is still soaking is not something XSIAM could have stitched. Zero is
    // degraded, rendered "—", never "0 / 0".
    const stitchDenominator = rows.filter((r) =>
      r.plane === 'ANALYTICS' && isInDenominator(r.outcome, r.outcome_reason)
    ).length

    return {
      total: denominator, seeded, detected, missed, pending, unvalidated, skipped, excluded,
      coverage, coverageUnknown: seeded > 0 && denominator == null, median, stitched,
      stitchDenominator,
    }
  }, [rows, payload])

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

  return { rows, kpis, loading, loaded, error, validate, refresh }
}
