import React, { useMemo, useRef, useState, useCallback } from 'react'
import NarrativeTimeline from './NarrativeTimeline.jsx'
import EventStream from './EventStream.jsx'
import useTimelineData from './useTimelineData.js'
import { toPng } from 'html-to-image'
import { downloadReport } from '../../api/client.js'

/**
 * InflightView — the In-Flight tab.
 *
 * Layout:
 *   ▸ eyebrow ID + ETA
 *   ▸ narrative title (display font)
 *   ▸ summary prose (Fraunces / narrative serif) — the "why this scenario matters"
 *   ▸ NarrativeTimeline — the hero artifact
 *   ▸ footer card with quick stats + Screenshot / Export CTAs
 *
 * Data:
 *   activeRun  — { scenarioId, runId, step, totalSteps, ... }
 *   lastRun    — most recent completed run (used as fallback when nothing live)
 */
export default function InflightView({ activeRun, lastRun, onError }) {
  // Prefer the live run; fall back to the most recent completed run.
  const targetRunId     = activeRun?.runId      || lastRun?.runId      || null
  const targetScenarioId = activeRun?.scenarioId || lastRun?.scenarioId || null
  const isLive = !!activeRun

  const captureRef = useRef(null)
  const [capturing, setCapturing] = useState(false)
  const [exporting, setExporting] = useState(false)

  const { frames, scenario, run, stitches, loading } = useTimelineData(
    targetScenarioId,
    targetRunId
  )

  const handleScreenshot = useCallback(async () => {
    if (!captureRef.current) return
    setCapturing(true)
    try {
      const dataUrl = await toPng(captureRef.current, {
        backgroundColor: '#050A14',
        pixelRatio: 2,
        cacheBust: true,
        filter: (node) => {
          // strip the action buttons themselves from the screenshot
          if (node && node.classList && node.classList.contains('narrative__footer-actions')) {
            return false
          }
          return true
        },
      })
      const a = document.createElement('a')
      a.href = dataUrl
      a.download = `cortexsim-narrative-${targetRunId || 'preview'}.png`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } catch (err) {
      if (onError) onError(err.message || 'Screenshot failed')
    } finally {
      setCapturing(false)
    }
  }, [targetRunId, onError])

  const handleExport = useCallback(async () => {
    if (!targetRunId) {
      if (onError) onError('No run selected for export')
      return
    }
    setExporting(true)
    try {
      const blob = await downloadReport(targetRunId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-pov-${targetRunId}.md`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      if (onError) onError(err.message || 'Export failed')
    } finally {
      setExporting(false)
    }
  }, [targetRunId, onError])

  const summary = useMemo(() => {
    if (!scenario) return ''
    // Compose a one-paragraph operator summary from scenario fields.
    const actor = scenario.threat_report
      ? scenario.threat_report.split(/\s*[—\-]\s*/)[0].trim()
      : null
    const technique = scenario.mitre_technique_name || scenario.mitre_technique
    const tactic    = scenario.mitre_tactic_name    || scenario.mitre_tactic
    const planes = Array.from(
      new Set(
        (scenario.steps || []).flatMap((s) =>
          (s.expected_detections || []).map((d) => (d.plane || '').toUpperCase())
        )
      )
    ).filter(Boolean)
    const planeText = planes.length > 1
      ? `Cortex stitches signal from ${planes.join(', ')} into a single incident.`
      : `Cortex surfaces ${planes[0] || 'detection'} signal as a single incident.`
    return `${actor ? actor + '. ' : ''}${tactic} via ${technique}. ${planeText}`
  }, [scenario])

  const eyebrow = useMemo(() => {
    const id = targetScenarioId || '—'
    if (isLive) {
      const total = activeRun.totalSteps || 0
      const cur   = activeRun.step || 0
      return `${id} · in progress · step ${cur} of ${total}`
    }
    if (lastRun) {
      return `${id} · most recent run · ${lastRun.status || 'complete'}`
    }
    return id
  }, [targetScenarioId, isLive, activeRun, lastRun])

  const stats = useMemo(() => {
    const totalDetections = frames.reduce((n, f) => n + f.detections.length, 0)
    const detected = frames.reduce(
      (n, f) => n + f.detections.filter((d) => d.status === 'detected').length, 0
    )
    const stitchCount = stitches.length
    return { totalDetections, detected, stitchCount }
  }, [frames, stitches])

  if (!targetScenarioId) {
    return (
      <div className="narrative inflight-empty">
        <h1 className="inflight-empty__title">No run in progress</h1>
        <p className="inflight-empty__desc">
          Launch a scenario from the Library. The attack narrative
          timeline will render here as steps execute, and Cortex stitches arrive.
        </p>
      </div>
    )
  }

  return (
    // The shell (.theme-console, AppShell.jsx) is theme-aware and sets
    // [data-theme] on the root, so the PANW token set (--ac/--s1/--tx/…)
    // resolves correctly here without pinning a local override.
    <div className="narrative">
      <div className="narrative__capture" ref={captureRef}>
        <div className="narrative__header">
          <div className={`narrative__eyebrow${isLive ? ' narrative__eyebrow--live' : ''}`}>
            {eyebrow}
          </div>
          <h1 className="narrative__title">
            {scenario?.name || 'loading…'}
          </h1>
          {summary && (
            <p className="narrative__summary">{summary}</p>
          )}
        </div>

        {loading && frames.length === 0 ? (
          <div className="inflight-loading-note">
            loading run telemetry…
          </div>
        ) : (
          <NarrativeTimeline frames={frames} stitches={stitches} />
        )}
      </div>

      <EventStream runId={targetRunId} compact />

      <div className="narrative__footer">
        <p>
          {stats.detected > 0 ? (
            <>
              In <strong>
                {fmtElapsed(activeRun?.elapsed)}
              </strong>{' '}
              Cortex has detected {stats.detected} of {stats.totalDetections} expected signals
              {stats.stitchCount > 0 && (
                <>{' '}across {stats.stitchCount} stitched event pair{stats.stitchCount === 1 ? '' : 's'}</>
              )}
              .
            </>
          ) : isLive ? (
            <>
              Run started. The timeline will update as detections arrive in
              the Cortex Data Lake (typical ingest latency: 30–120s).
            </>
          ) : (
            <>
              Showing the most recent completed run. Click <strong>Screenshot</strong>{' '}
              to capture the timeline as a POV artifact.
            </>
          )}
        </p>
        <div className="narrative__footer-actions inflight-footer-actions">
          <button className="btn" onClick={handleScreenshot} disabled={capturing || frames.length === 0}>
            {capturing ? 'Capturing…' : 'Screenshot'}
          </button>
          <button
            className="btn btn--primary"
            onClick={handleExport}
            disabled={exporting || !targetRunId}
          >
            {exporting ? 'Exporting…' : 'Export POV'}
          </button>
        </div>
      </div>
    </div>
  )
}

function fmtElapsed(seconds) {
  if (seconds == null) return '—'
  const s = Math.max(0, Math.floor(seconds))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}
