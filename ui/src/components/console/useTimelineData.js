import { useState, useEffect, useMemo } from 'react'
import { getScenario, getRun, getResultsForRun } from '../../api/client.js'

/**
 * useTimelineData — fetches scenario detail + run results and folds them into
 * an array of "frames" the NarrativeTimeline can render directly.
 *
 * One frame per scenario step. Each frame:
 *   {
 *     id, index, tid, identity, name, status,
 *     timestamp,                    // when the step started (ISO string or null)
 *     elapsedMttd,                  // median MTTD across detected detections, null if none
 *     detections: [{
 *       plane, type, description,
 *       status: 'detected' | 'pending' | 'missed' | 'idle',
 *       mttd, alertId,
 *     }]
 *   }
 *
 * status semantics for the step itself:
 *   'done'    — all expected detections are detected or missed (terminal)
 *   'pending' — step is currently executing (matches run.current_step)
 *   'idle'    — step has not started yet
 *
 * @param {string|null} scenarioId
 * @param {string|null} runId
 * @returns {{ frames, scenario, run, loading, error, stitches }}
 *           stitches: array of { from, to } step indices to draw arcs between
 */
export default function useTimelineData(scenarioId, runId) {
  const [scenario, setScenario] = useState(null)
  const [run, setRun]           = useState(null)
  const [results, setResults]   = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)

  // ── Fetch scenario detail ────────────────────────────────────────────────
  useEffect(() => {
    if (!scenarioId) { setScenario(null); return }
    let cancelled = false
    setLoading(true)
    getScenario(scenarioId)
      .then((data) => { if (!cancelled) setScenario(data) })
      .catch((err) => { if (!cancelled) setError(err.message || String(err)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [scenarioId])

  // ── Fetch run + results (refresh on interval while pending) ─────────────
  useEffect(() => {
    if (!runId) { setRun(null); setResults(null); return }
    let cancelled = false
    let timer = null

    const fetchOnce = async () => {
      try {
        const [r, res] = await Promise.all([getRun(runId), getResultsForRun(runId)])
        if (cancelled) return
        setRun(r)
        setResults(res)
      } catch (err) {
        if (cancelled) return
        setError(err.message || String(err))
      }
    }

    fetchOnce()
    // Poll every 5s if the run is still active.
    const tick = () => {
      timer = setTimeout(async () => {
        await fetchOnce()
        if (cancelled) return
        const status = (run && run.status) || 'pending'
        if (status === 'pending' || status === 'running') tick()
      }, 5_000)
    }
    tick()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [runId]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Combine into frames ─────────────────────────────────────────────────
  const frames = useMemo(() => {
    if (!scenario || !scenario.steps) return []

    const currentStepIdx = (() => {
      if (!run) return null
      // Accept various shapes from /api/runs/:id
      if (typeof run.current_step === 'number') return run.current_step
      if (typeof run.step === 'number')         return run.step
      return null
    })()

    const resultRows = (results && results.results) || results || []

    return scenario.steps.map((step, idx) => {
      const stepStatus = (() => {
        if (currentStepIdx == null) return 'idle'
        if (idx + 1 < currentStepIdx) return 'done'
        if (idx + 1 === currentStepIdx) return 'pending'
        return 'idle'
      })()

      const detections = (step.expected_detections || []).map((d, di) => {
        const matches = resultRows.filter((r) =>
          (r.step_id === step.id || r.step_index === idx) &&
          (r.plane || '').toUpperCase() === (d.plane || '').toUpperCase() &&
          (r.detection_type || '').toUpperCase() === (d.type || '').toUpperCase()
        )
        const r = matches[0]
        let status = 'idle'
        if (stepStatus === 'pending') status = 'pending'
        if (r && r.observed === true)  status = 'detected'
        if (r && r.observed === false) status = 'missed'
        return {
          key: `${step.id || idx}-${di}`,
          plane: (d.plane || '').toUpperCase(),
          type: d.type || '',
          description: d.description || '',
          status,
          mttd: r?.mttd_seconds ?? null,
          alertId: r?.alert_id ?? null,
        }
      })

      const detected = detections.filter((d) => d.status === 'detected')
      const elapsedMttd = detected.length
        ? Math.round(detected.reduce((s, d) => s + (d.mttd || 0), 0) / detected.length)
        : null

      // Step start timestamp: prefer run-side telemetry if present.
      const stepResults = resultRows.filter((r) =>
        r.step_id === step.id || r.step_index === idx
      )
      const earliestExec = stepResults
        .map((r) => r.executed_at)
        .filter(Boolean)
        .sort()[0] || null

      return {
        id: step.id || `step-${idx + 1}`,
        index: idx,
        tid: step.mitre_technique || '—',
        identity: step.identity || '—',
        name: step.name || '(unnamed step)',
        timestamp: earliestExec,
        status: stepStatus,
        elapsedMttd,
        detections,
      }
    })
  }, [scenario, run, results])

  // ── Compute stitches: pairs of consecutive steps that share ANALYTICS / IOC
  //    detections — that's where XSIAM grouping draws a line between events.
  const stitches = useMemo(() => {
    if (frames.length < 2) return []
    const result = []
    for (let i = 0; i < frames.length - 1; i++) {
      const a = frames[i]
      const b = frames[i + 1]
      const aHasStitch = a.detections.some(
        (d) => d.plane === 'ANALYTICS' || d.type.toUpperCase() === 'IOC' || d.type.toLowerCase().includes('stitch')
      )
      const bHasStitch = b.detections.some(
        (d) => d.plane === 'ANALYTICS' || d.type.toUpperCase() === 'IOC' || d.type.toLowerCase().includes('stitch')
      )
      if (aHasStitch && bHasStitch) {
        result.push({ from: i, to: i + 1, label: 'XSIAM · stitched' })
      }
    }
    return result
  }, [frames])

  return { frames, scenario, run, loading, error, stitches }
}
