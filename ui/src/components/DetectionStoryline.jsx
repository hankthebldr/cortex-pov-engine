import React, { useCallback, useEffect, useRef, useState } from 'react'
import { getStoryline, storylineEventsUrl } from '../api/storyline.js'
import Term from './onboarding/Term.jsx'
import './DetectionStoryline.css'

/**
 * DetectionStoryline — presenter-grade vertical kill-chain timeline.
 *
 * Renders the per-run Detection Storyline (core/engine/storyline.py) as a
 * large, projector-friendly sequence of steps. Each step shows its TTP,
 * and for every expected detection: the expected logic → the observed
 * Cortex alert (or an "awaiting detection…" state) → a real MTTD badge →
 * a status pill (detected / pending / missed) → an evidence affordance.
 *
 * This is the "wow" surface: the moment a real XSIAM alert reconciles, the
 * targeted detection flashes pending→detected, stamped with the Cortex
 * engine that caught it (BIOC | XQL | Analytics | Correlation | IOC | ABIOC)
 * and the true time-to-detect. No competitor can attribute a catch to a
 * named Cortex detection engine — that's the wedge.
 *
 * Live-updating: polls GET /api/runs/:id/storyline every ~2s, and (when the
 * browser supports it) also attaches an EventSource to /api/runs/:id/events
 * so a reconcile triggers an immediate refetch — the catch lands instantly
 * instead of on the next poll tick.
 *
 * Props:
 *   runId            — string (required)
 *   scenarioId       — string (optional; used only for the header label)
 *   pollIntervalMs   — number (default 2000)
 *   onOpenEvidence   — (detection) => void  evidence-drawer affordance
 *   onError          — (message: string) => void
 */

// ── Formatting helpers ──────────────────────────────────────────────────────

function formatMttd(seconds) {
  if (seconds == null) return null
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatClock(iso) {
  if (!iso) return null
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  } catch {
    return iso
  }
}

// The six-value Cortex-native detection vocabulary. Each engine gets a
// distinct treatment so a presenter can attribute a catch at a glance.
const ENGINE_VARIANT = {
  bioc:        'bioc',
  xql:         'xql',
  analytics:   'analytics',
  correlation: 'correlation',
  ioc:         'ioc',
  abioc:       'abioc',
}

function engineLabel(type) {
  const t = (type || '').toLowerCase().trim()
  const labels = {
    bioc: 'BIOC', xql: 'XQL', analytics: 'Analytics',
    correlation: 'Correlation', ioc: 'IOC', abioc: 'ABIOC',
  }
  return labels[t] || (type || '').toString().toUpperCase() || '—'
}

function EngineBadge({ type }) {
  const t = (type || '').toLowerCase().trim()
  const variant = ENGINE_VARIANT[t] || 'unknown'
  const label = engineLabel(type)
  return (
    <span
      className={`storyline-engine storyline-engine--${variant}`}
      title={`${label} — Cortex detection engine`}
    >
      {label}
    </span>
  )
}

const STATUS_META = {
  detected:      { cls: 'detected',      label: 'Detected' },
  pending:       { cls: 'pending',       label: 'Awaiting' },
  missed:        { cls: 'missed',        label: 'Missed' },
  'not-executed': { cls: 'not-executed', label: 'Not executed' },
}

function StatusPill({ status }) {
  const meta = STATUS_META[status] || { cls: 'idle', label: status || 'Idle' }
  return (
    <span className={`storyline-pill storyline-pill--${meta.cls}`}>
      <span className="storyline-pill__dot" aria-hidden="true" />
      {meta.label}
    </span>
  )
}

// Run states in which NO claim about the customer's detection posture is
// defensible: the simulation itself did not finish, so an un-fired detection
// means "we never generated the signal", not "Cortex missed it".
const UNPROVEN_RUN_STATUSES = new Set(['failed', 'aborted'])

export function isRunUnproven(runStatus) {
  return UNPROVEN_RUN_STATUSES.has(String(runStatus || '').toLowerCase())
}

/**
 * Reconcile the server's per-detection status with the run's own outcome.
 *
 * The storyline builder marks every un-reviewed detection `missed` once a run
 * reaches ANY terminal state — including `failed`. Rendering that as a
 * detection gap tells the customer their Cortex tenant missed alerts that were
 * never generated: a false-negative claim about their own product, made on our
 * evidence surface. A `detected` row survives (the signal demonstrably fired
 * before the run died); an unfired one is downgraded to `not-executed`.
 */
export function reconcileStatus(status, runStatus) {
  const s = status || 'pending'
  if (s === 'detected') return s
  return isRunUnproven(runStatus) ? 'not-executed' : s
}

// Stable key for a detection within a step (used for flash tracking + React key).
function detectionKey(det, stepId, di) {
  return `${stepId || det.step_id || 'step'}::${det.detection_type || '?'}::${det.plane || '?'}::${di}`
}

// ── Detection row ───────────────────────────────────────────────────────────

function DetectionRow({ det, dkey, flashing, runStatus, onOpenEvidence }) {
  const status = reconcileStatus(det.status, runStatus)
  const mttd = formatMttd(det.mttd_seconds)
  const detected = status === 'detected'
  const evidenceCount = det.evidence_count || 0
  const observedClock = formatClock(det.observed_at)

  return (
    <div
      className={
        'storyline-detection' +
        ` storyline-detection--${status}` +
        (flashing ? ' storyline-detection--flash' : '')
      }
      data-testid="storyline-detection"
    >
      <div className="storyline-detection__head">
        <div className="storyline-detection__tags">
          {det.plane && <span className="storyline-plane">{det.plane}</span>}
          <EngineBadge type={det.detection_type} />
        </div>
        <div className="storyline-detection__status">
          {mttd && detected && (
            <span className="storyline-mttd" title="Time to detect">
              MTTD {mttd}
            </span>
          )}
          <StatusPill status={status} />
        </div>
      </div>

      {/* Expected detection logic */}
      <p className="storyline-detection__expected">
        <span className="storyline-detection__caption">Expected</span>
        {det.expected_description || '—'}
      </p>

      {/* Observed Cortex alert — or the live "awaiting" state */}
      {detected ? (
        <div className="storyline-detection__observed">
          <span className="storyline-detection__caption">Observed</span>
          <span className="storyline-observed__engine">
            {engineLabel(det.detection_type)} fired
          </span>
          {det.alert_external_id && (
            <span className="storyline-observed__alert text-mono">
              {det.alert_external_id}
            </span>
          )}
          {observedClock && (
            <span className="storyline-observed__clock text-mono">{observedClock}</span>
          )}
          {det.deep_link && (
            <a
              className="storyline-observed__link"
              href={det.deep_link}
              target="_blank"
              rel="noreferrer"
            >
              View in tenant ↗
            </a>
          )}
        </div>
      ) : status === 'not-executed' ? (
        <div className="storyline-detection__awaiting storyline-detection__awaiting--unproven">
          Signal never generated — the run did not complete, so this detection was
          never put to the test.
        </div>
      ) : status === 'missed' ? (
        <div className="storyline-detection__awaiting storyline-detection__awaiting--missed">
          No alert reconciled — detection gap
        </div>
      ) : (
        <div className="storyline-detection__awaiting">
          <span className="storyline-scan" aria-hidden="true" />
          Cortex analyzing — awaiting detection…
        </div>
      )}

      {/* Evidence affordance — chain of custody */}
      {(evidenceCount > 0 || detected) && (
        <button
          type="button"
          className="storyline-detection__evidence"
          onClick={() => onOpenEvidence && onOpenEvidence(det)}
          disabled={!onOpenEvidence}
          title="Open evidence — raw alert record, matched-on rationale, deep link"
        >
          Evidence{evidenceCount ? ` (${evidenceCount})` : ''}
        </button>
      )}
    </div>
  )
}

// ── Step node ───────────────────────────────────────────────────────────────

function StepNode({ step, index, flashing, runStatus, onOpenEvidence }) {
  const detections = step.detections || []
  const anyDetected = detections.some((d) => d.status === 'detected')
  const anyPending = detections.some(
    (d) => reconcileStatus(d.status, runStatus) === 'pending',
  )
  const nodeState = anyDetected ? 'detected' : anyPending ? 'pending' : 'idle'
  const stepId = step.step_id || step.id

  return (
    <li className={`storyline-step storyline-step--${nodeState}`}>
      <div className="storyline-step__rail" aria-hidden="true">
        <span className="storyline-step__node">{(step.index ?? index) + 1}</span>
      </div>
      <div className="storyline-step__card">
        <div className="storyline-step__header">
          <div className="storyline-step__title">
            {step.mitre_technique && (
              <span className="storyline-tid text-mono">{step.mitre_technique}</span>
            )}
            <h4>{step.name || `Step ${index + 1}`}</h4>
          </div>
          {step.stitch_group && (
            <span className="storyline-stitch" title="XSIAM correlation group">
              ⛓ {step.stitch_group}
            </span>
          )}
        </div>

        <div className="storyline-step__detections">
          {detections.length === 0 ? (
            <p className="storyline-step__empty">No expected detections for this step.</p>
          ) : (
            detections.map((det, di) => {
              const dkey = detectionKey(det, stepId, di)
              return (
                <DetectionRow
                  key={dkey}
                  dkey={dkey}
                  det={det}
                  flashing={flashing.has(dkey)}
                  runStatus={runStatus}
                  onOpenEvidence={onOpenEvidence}
                />
              )
            })
          )}
        </div>
      </div>
    </li>
  )
}

// ── KPI header ──────────────────────────────────────────────────────────────

function StorylineHeader({ storyline, live, lastUpdated }) {
  const coverage = storyline.coverage || {}
  const mttd = storyline.mttd || {}
  const prov = storyline.provenance || {}
  const runStatus = storyline.run_status || null
  const unproven = isRunUnproven(runStatus)
  const pct = coverage.pct != null
    ? Math.round(coverage.pct)
    : coverage.expected
      ? Math.round(((coverage.detected || 0) / coverage.expected) * 100)
      : 0

  return (
    <header className="storyline-header">
      <div className="storyline-header__title">
        <h3>Detection Storyline</h3>
        <span className="storyline-header__scenario text-mono">
          {storyline.scenario_id || storyline.run_id}
        </span>
        {/* The SSE channel stays open on a dead run, so an open socket alone is
            not liveness — only the run's own status is. */}
        {live && runStatus === 'running' && (
          <span className="storyline-live" aria-live="polite">
            <span className="storyline-live__dot" aria-hidden="true" />
            LIVE
          </span>
        )}
      </div>

      <div className="storyline-kpis">
        <div className="storyline-kpi">
          <span className="storyline-kpi__label">Coverage</span>
          {unproven ? (
            // A percentage here is a claim about the customer's tenant. Refuse
            // to make one when the simulation never produced the signal.
            <span
              className="storyline-kpi__value storyline-kpi__value--unproven"
              data-testid="storyline-coverage-unproven"
            >
              n/a
              <span className="storyline-kpi__sub">run {runStatus}</span>
            </span>
          ) : (
            <span className="storyline-kpi__value">
              {pct}%
              <span className="storyline-kpi__sub">
                {coverage.detected || 0}/{coverage.expected || 0}
              </span>
            </span>
          )}
        </div>
        <div className="storyline-kpi">
          <span className="storyline-kpi__label"><Term k="mttd">MTTD</Term> p50</span>
          <span className="storyline-kpi__value">
            {formatMttd(mttd.p50) || '—'}
          </span>
        </div>
        <div className="storyline-kpi">
          <span className="storyline-kpi__label"><Term k="mttd">MTTD</Term> p90</span>
          <span className="storyline-kpi__value">
            {formatMttd(mttd.p90) || '—'}
          </span>
        </div>
        <div className="storyline-kpi">
          <span className="storyline-kpi__label">Provenance</span>
          <span className="storyline-kpi__value storyline-kpi__value--prov">
            <span title="Machine-verified via tenant reconciliation">
              {prov.machine_verified || 0} verified
            </span>
            <span className="storyline-kpi__sub" title="Operator-attested (manual)">
              {prov.attested || 0} attested
            </span>
          </span>
        </div>
      </div>

      {lastUpdated && (
        <div className="storyline-header__updated text-mono">
          updated {formatClock(lastUpdated)}
        </div>
      )}
    </header>
  )
}

// ── Main component ──────────────────────────────────────────────────────────

export default function DetectionStoryline({
  runId,
  scenarioId,
  pollIntervalMs = 2000,
  onOpenEvidence,
  onError,
}) {
  const [storyline, setStoryline] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [live, setLive] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)

  // Flash tracking: detection keys that just transitioned → detected.
  const [flashing, setFlashing] = useState(() => new Set())
  const prevStatuses = useRef(new Map())
  const flashTimers = useRef([])

  const applyStoryline = useCallback((next) => {
    if (!next) return
    // Diff statuses to detect fresh catches for the flash animation.
    const fresh = []
    const nextStatuses = new Map()
    for (const step of next.steps || []) {
      const stepId = step.step_id || step.id
      ;(step.detections || []).forEach((det, di) => {
        const key = detectionKey(det, stepId, di)
        const status = det.status || 'pending'
        nextStatuses.set(key, status)
        const before = prevStatuses.current.get(key)
        if (status === 'detected' && before && before !== 'detected') {
          fresh.push(key)
        }
      })
    }
    prevStatuses.current = nextStatuses

    if (fresh.length) {
      setFlashing((prev) => {
        const merged = new Set(prev)
        fresh.forEach((k) => merged.add(k))
        return merged
      })
      const timer = setTimeout(() => {
        setFlashing((prev) => {
          const merged = new Set(prev)
          fresh.forEach((k) => merged.delete(k))
          return merged
        })
      }, 1800)
      flashTimers.current.push(timer)
    }

    setStoryline(next)
    setLastUpdated(new Date().toISOString())
  }, [])

  // Polling + SSE-triggered refetch.
  useEffect(() => {
    if (!runId) {
      setStoryline(null)
      return undefined
    }

    let cancelled = false
    let pollTimer = null
    let eventSource = null

    const fetchOnce = async () => {
      try {
        const data = await getStoryline(runId)
        if (cancelled) return
        applyStoryline(data)
        setError(null)
      } catch (err) {
        if (cancelled) return
        const msg = err.message || String(err)
        setError(msg)
        if (onError) onError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    setLoading(true)
    fetchOnce()

    // Interval poll — the robust backstop.
    pollTimer = setInterval(fetchOnce, pollIntervalMs)

    // SSE — instant refetch on any run event (jsdom has no EventSource,
    // so guard; polling still covers those environments).
    if (typeof EventSource !== 'undefined') {
      try {
        eventSource = new EventSource(storylineEventsUrl(runId))
        eventSource.onopen = () => { if (!cancelled) setLive(true) }
        eventSource.onmessage = () => { if (!cancelled) fetchOnce() }
        eventSource.onerror = () => {
          if (!cancelled) setLive(false)
          if (eventSource) { eventSource.close(); eventSource = null }
        }
      } catch {
        /* fall back to polling only */
      }
    }

    return () => {
      cancelled = true
      if (pollTimer) clearInterval(pollTimer)
      if (eventSource) eventSource.close()
      flashTimers.current.forEach(clearTimeout)
      flashTimers.current = []
      prevStatuses.current = new Map()
    }
  }, [runId, pollIntervalMs, applyStoryline, onError])

  // ── Render states ─────────────────────────────────────────────────────────
  if (!runId) {
    return (
      <div className="storyline storyline--empty">
        <div className="storyline-empty__icon" aria-hidden="true">◎</div>
        <p>No run selected. Launch a scenario to build its Detection Storyline.</p>
      </div>
    )
  }

  if (loading && !storyline) {
    return (
      <div className="storyline storyline--loading">
        <span className="spinner" />
        <span className="text-muted">Building Detection Storyline…</span>
      </div>
    )
  }

  if (error && !storyline) {
    return (
      <div className="storyline storyline--error">
        <p className="storyline-error__msg">Could not load storyline: {error}</p>
      </div>
    )
  }

  if (!storyline) return null

  const steps = storyline.steps || []
  const runStatus = storyline.run_status || null
  const unproven = isRunUnproven(runStatus)

  return (
    <section className="storyline" aria-label="Detection storyline">
      <StorylineHeader storyline={storyline} live={live} lastUpdated={lastUpdated} />

      {unproven && (
        <div className="storyline-unproven" role="status" data-testid="storyline-unproven-banner">
          <span className="storyline-unproven__tag">RUN {String(runStatus).toUpperCase()}</span>
          <p>
            The simulation did not complete, so nothing below is a verdict on this
            tenant's detection posture — the signal was never generated. Fix the run
            (see the failure detail on the Live tab), then re-launch to measure coverage.
          </p>
        </div>
      )}

      {steps.length === 0 ? (
        <div className="storyline--empty">
          <p>This run has no steps to narrate yet.</p>
        </div>
      ) : (
        <ol className="storyline-steps">
          {steps.map((step, idx) => (
            <StepNode
              key={step.step_id || step.id || idx}
              step={step}
              index={idx}
              flashing={flashing}
              runStatus={runStatus}
              onOpenEvidence={onOpenEvidence}
            />
          ))}
        </ol>
      )}
    </section>
  )
}
