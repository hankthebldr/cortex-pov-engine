import React from 'react'

/**
 * TelemetryStrip — 40px strip showing active run state.
 *
 * Props:
 *   run      — { scenarioId, step, totalSteps, elapsed, detected, total, nextStep }
 *              elapsed is seconds; rendered as mm:ss
 *   onAbort  — () => void
 */
function fmtElapsed(seconds) {
  if (seconds == null) return '--:--'
  const s = Math.max(0, Math.floor(seconds))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

export default function TelemetryStrip({ run, onAbort }) {
  if (!run) return null

  const progress = run.totalSteps
    ? Math.min(100, Math.max(0, (run.step / run.totalSteps) * 100))
    : 0

  const detClass = run.detected >= run.total
    ? 'tel-value--detected'
    : run.detected > 0
    ? 'tel-value--signal'
    : 'tel-value--pending'

  return (
    <div className="telemetry">
      <span className="tel-label">Active</span>
      <span className="tel-value mono tel-value--signal">{run.scenarioId}</span>
      <span className="tel-sep">/</span>
      <span className="tel-label">Step</span>
      <span className="tel-value mono">{run.step} / {run.totalSteps}</span>
      <span className="tel-sep">/</span>
      <span className="tel-label">Elapsed</span>
      <span className="tel-value mono">{fmtElapsed(run.elapsed)}</span>
      <span className="tel-sep">/</span>
      <span className="tel-label">Detected</span>
      <span className={`tel-value mono ${detClass}`}>
        {run.detected} / {run.total}
      </span>

      <div className="tel-progress">
        <div className="tel-progress__fill" style={{ width: `${progress}%` }} />
      </div>

      {run.nextStep && (
        <>
          <span className="tel-label">Next</span>
          <span className="tel-value mono">{run.nextStep}</span>
        </>
      )}

      <button className="btn-abort" onClick={onAbort}>Abort</button>
    </div>
  )
}
