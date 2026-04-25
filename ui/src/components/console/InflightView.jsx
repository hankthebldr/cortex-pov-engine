import React, { useMemo } from 'react'
import NarrativeTimeline from './NarrativeTimeline.jsx'
import useTimelineData from './useTimelineData.js'

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
export default function InflightView({ activeRun, lastRun, onScreenshot, onExport }) {
  // Prefer the live run; fall back to the most recent completed run.
  const targetRunId     = activeRun?.runId      || lastRun?.runId      || null
  const targetScenarioId = activeRun?.scenarioId || lastRun?.scenarioId || null
  const isLive = !!activeRun

  const { frames, scenario, run, stitches, loading } = useTimelineData(
    targetScenarioId,
    targetRunId
  )

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
      <div className="narrative" style={{ paddingTop: 80 }}>
        <h1 style={{
          fontFamily: 'var(--font-display)',
          fontSize: 28, fontWeight: 400, color: 'var(--c-text)', marginBottom: 12,
        }}>No run in progress</h1>
        <p style={{
          fontFamily: 'var(--font-narrative)',
          fontSize: 15, fontWeight: 300,
          color: 'var(--c-text-secondary)', lineHeight: 1.6, maxWidth: 640,
        }}>
          Launch a scenario from the Operations tab. The attack narrative
          timeline will render here as steps execute, and Cortex stitches arrive.
        </p>
      </div>
    )
  }

  return (
    <div className="narrative">
      <div className="narrative__header">
        <div className="narrative__eyebrow">{eyebrow}</div>
        <h1 className="narrative__title">
          {scenario?.name || 'loading…'}
        </h1>
        {summary && (
          <p className="narrative__summary">{summary}</p>
        )}
      </div>

      {loading && frames.length === 0 ? (
        <div style={{
          padding: 48,
          textAlign: 'center',
          color: 'var(--c-text-muted)',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          letterSpacing: '0.04em',
        }}>
          loading run telemetry…
        </div>
      ) : (
        <NarrativeTimeline frames={frames} stitches={stitches} />
      )}

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
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={onScreenshot} disabled={!onScreenshot}>
            Screenshot
          </button>
          <button
            className="btn btn--primary"
            onClick={onExport}
            disabled={!onExport}
          >
            Export
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
