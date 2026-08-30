import React, { useState, useEffect } from 'react'
import { getScenario } from '../api/client.js'

// ─── Detection type badge ─────────────────────────────────────────────────────

// GAP-2: the detection-type vocabulary now spans BIOC | XQL | Analytics |
// Correlation | IOC. Correlation is the headline XSIAM differentiator —
// given a distinct violet treatment so it stands out in the UC/TC chain.
// Each key maps to a static CSS modifier class (uctc-badge--<key>) instead
// of an inline style object — the palette is a closed vocabulary, so the
// per-type variance is expressed as classes, not runtime style computation.
const DETECTION_BADGE_CLASS = {
  BIOC: 'uctc-badge--bioc',
  XQL: 'uctc-badge--xql',
  ANALYTICS: 'uctc-badge--analytics',
  CORRELATION: 'uctc-badge--correlation',
  IOC: 'uctc-badge--ioc',
}

function DetectionBadge({ type }) {
  // Match case-insensitively so a card emitting 'correlation' still resolves.
  const key = Object.keys(DETECTION_BADGE_CLASS).find((k) => k === (type || '').toUpperCase())
  const modifier = DETECTION_BADGE_CLASS[key] || 'uctc-badge--default'
  return (
    <span className={`uctc-badge ${modifier}`}>
      {type}
    </span>
  )
}

// ─── Step card ────────────────────────────────────────────────────────────────

function StepCard({ step, index, isLast }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className={`uctc-step${isLast ? ' uctc-step--last' : ''}`}>
      {/* Timeline spine */}
      <div className="uctc-step__spine">
        <div className="uctc-step__num">
          {index + 1}
        </div>
        {!isLast && <div className="uctc-step__connector" />}
      </div>

      {/* Step content */}
      <div className={`uctc-step__content${isLast ? ' uctc-step__content--last' : ''}`}>
        <button
          onClick={() => setExpanded(v => !v)}
          className="uctc-step__toggle"
          aria-expanded={expanded}
        >
          <span className="uctc-step__name">
            {step.name}
          </span>
          {step.mitre_technique && (
            <span className="uctc-step__mitre">
              {step.mitre_technique}
            </span>
          )}
          <span className={`uctc-step__chevron${expanded ? ' uctc-step__chevron--expanded' : ''}`}>
            &#9658;
          </span>
        </button>

        {/* Command preview */}
        {step.command && (
          <pre className="uctc-step__command">
            {step.command}
          </pre>
        )}

        {/* Expected detections — collapsible */}
        {expanded && step.expected_detections && step.expected_detections.length > 0 && (
          <div className="uctc-step__detections">
            <p className="uctc-step__detections-label">
              Expected Detections
            </p>
            <div className="uctc-step__detections-list">
              {step.expected_detections.map((det, di) => (
                <div key={di} className="uctc-detection-row">
                  <DetectionBadge type={det.type} />
                  <div className="uctc-detection-row__content">
                    <span className="uctc-detection-row__plane">
                      {det.plane}
                    </span>
                    <span className="uctc-detection-row__desc">
                      {det.description}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

export default function UCTCMapper({ scenario }) {
  const [detail, setDetail]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)

  const scenarioId = scenario?.scenario_id || scenario?.id

  useEffect(() => {
    if (!scenarioId) return
    setLoading(true)
    setError(null)
    // Try to fetch full scenario detail (may include steps not in list payload)
    getScenario(scenarioId)
      .then(data => setDetail(data))
      .catch(() => {
        // Fall back to the prop data if API fails
        setDetail(scenario)
      })
      .finally(() => setLoading(false))
  }, [scenarioId])

  const data = detail || scenario
  const steps = data?.steps || []

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>UC / TC Chain</h3>
        <div className="uctc-header__badges">
          {data?.uc_ref && <span className="badge badge-navy">{data.uc_ref}</span>}
          {data?.tc_ref && <span className="badge badge-teal">{data.tc_ref}</span>}
        </div>
      </div>

      <div className="panel-card-body">
        {loading ? (
          <div className="uctc-loading">
            <div className="spinner" />
            <span className="text-muted uctc-loading__text">Loading scenario detail…</span>
          </div>
        ) : (
          <>
            {/* UC Header */}
            <div className="uctc-uc-header">
              <div className="uctc-uc-header__row">
                <span className="uctc-uc-header__label">
                  Use Case
                </span>
                {data?.uc_ref && (
                  <span className="text-mono uctc-uc-header__ref">
                    {data.uc_ref}
                  </span>
                )}
              </div>
              <p className="uctc-uc-header__name">
                {data?.uc_name || data?.name || '—'}
              </p>
              {data?.tc_name && (
                <p className="uctc-uc-header__tc">
                  <strong className="uctc-uc-header__tc-strong">TC: </strong>{data.tc_name}
                </p>
              )}

              {/* MITRE info */}
              {(data?.mitre_tactic || data?.mitre_technique) && (
                <div className="uctc-mitre">
                  {data.mitre_tactic && (
                    <div>
                      <span className="uctc-mitre__label">
                        Tactic
                      </span>
                      <span className="text-mono uctc-mitre__value">
                        {data.mitre_tactic}
                      </span>
                      {data.mitre_tactic_name && (
                        <span className="uctc-mitre__name">
                          {data.mitre_tactic_name}
                        </span>
                      )}
                    </div>
                  )}
                  {data.mitre_technique && (
                    <div>
                      <span className="uctc-mitre__label">
                        Technique
                      </span>
                      <span className="text-mono uctc-mitre__value">
                        {data.mitre_technique}
                      </span>
                      {data.mitre_technique_name && (
                        <span className="uctc-mitre__name">
                          {data.mitre_technique_name}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Threat report ref */}
              {data?.threat_report && (
                <div className="uctc-threat">
                  <span className="uctc-threat__label">
                    Unit 42 ref:{' '}
                    {data.threat_report_url ? (
                      <a href={data.threat_report_url} target="_blank" rel="noopener noreferrer">
                        {data.threat_report}
                      </a>
                    ) : (
                      <span className="uctc-threat__value">{data.threat_report}</span>
                    )}
                  </span>
                </div>
              )}
            </div>

            {/* Steps timeline */}
            {steps.length === 0 ? (
              <div className="empty-state uctc-empty-state">
                <p>No execution steps defined for this scenario.</p>
              </div>
            ) : (
              <div>
                <p className="section-label uctc-steps-label">
                  Execution Steps ({steps.length})
                </p>
                {steps.map((step, i) => (
                  <StepCard
                    key={step.id || i}
                    step={step}
                    index={i}
                    isLast={i === steps.length - 1}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
