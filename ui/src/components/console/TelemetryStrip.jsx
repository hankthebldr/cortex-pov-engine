import React, { useState } from 'react'
import { useFirstUseHint } from '../onboarding/useFirstUseHint.js'
import FirstUseHint from '../onboarding/FirstUseHint.jsx'
import { Button } from './kit/index.js'

/**
 * TelemetryStrip — 40px strip showing the run in flight, on the Runs list.
 *
 * Runs shows what was attempted, so the strip says which scenario, how far
 * along (when SimCore reports a step count), how long, and offers Abort. It
 * carries no detection count: what Cortex saw is on Results.
 *
 * Props:
 *   run      — { scenarioId, step, totalSteps, elapsed, nextStep }
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
  // First-use hint on Abort (I6 / spec §5) — hooks must run unconditionally,
  // ahead of the `!run` early return below. Same "clears on USE, not on
  // dismiss" contract as Launch's — see LaunchView.jsx's handleLaunch.
  const abortHint = useFirstUseHint('abort')
  const [abortHintDismissed, setAbortHintDismissed] = useState(false)
  const handleAbort = () => { abortHint.onUse(); onAbort() }

  if (!run) return null

  // The run list's rows carry no step counter; say nothing rather than "0 / 0".
  const counted = Number(run.totalSteps) > 0
  const progress = counted
    ? Math.min(100, Math.max(0, (run.step / run.totalSteps) * 100))
    : 0

  return (
    <div
      className="telemetry"
      role="region"
      aria-label="Active run telemetry"
      aria-live="polite"
    >
      <span className="tel-label">Active</span>
      <span className="tel-value mono tel-value--signal">{run.scenarioId}</span>
      {counted && (
        <>
          <span className="tel-sep">/</span>
          <span className="tel-label">Step</span>
          <span className="tel-value mono">{run.step} / {run.totalSteps}</span>
        </>
      )}
      <span className="tel-sep">/</span>
      <span className="tel-label">Elapsed</span>
      <span className="tel-value mono">{fmtElapsed(run.elapsed)}</span>

      {counted ? (
        <div
          className="tel-progress"
          role="progressbar"
          aria-valuenow={Math.round(progress)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`Run progress: step ${run.step} of ${run.totalSteps}`}
        >
          <div className="tel-progress__fill" style={{ width: `${progress}%` }} />
        </div>
      ) : <span className="tel-spacer" />}

      {run.nextStep && (
        <>
          <span className="tel-label">Next</span>
          <span className="tel-value mono">{run.nextStep}</span>
        </>
      )}

      <Button verb="Abort run" className="telemetry__abort" onClick={handleAbort} />
      {abortHint.show && !abortHintDismissed && (
        <FirstUseHint
          show
          text="Abort stops the run in place — the beacon kills the in-flight process group."
          onDismiss={() => setAbortHintDismissed(true)}
        />
      )}
    </div>
  )
}
