import React, { useState, useEffect } from 'react'
import { getScenario } from '../api/client.js'

// ─── Detection type badge ─────────────────────────────────────────────────────

function DetectionBadge({ type }) {
  const styles = {
    BIOC:      { bg: 'rgba(0,51,102,0.1)',     color: 'var(--cortex-navy)' },
    IOC:       { bg: 'rgba(243,156,18,0.12)',  color: '#c47d00' },
    Analytics: { bg: 'rgba(0,192,232,0.12)',   color: '#007da3' },
  }
  const s = styles[type] || { bg: 'rgba(107,126,142,0.1)', color: 'var(--cortex-steel)' }
  return (
    <span style={{
      fontSize: '10px',
      fontWeight: 700,
      padding: '2px 7px',
      borderRadius: '3px',
      background: s.bg,
      color: s.color,
      textTransform: 'uppercase',
      letterSpacing: '0.04em',
    }}>
      {type}
    </span>
  )
}

// ─── Step card ────────────────────────────────────────────────────────────────

function StepCard({ step, index, isLast }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div style={{ display: 'flex', gap: '12px', paddingBottom: isLast ? 0 : '4px' }}>
      {/* Timeline spine */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
        <div style={{
          width: '28px',
          height: '28px',
          borderRadius: '50%',
          background: 'var(--cortex-navy)',
          color: 'var(--cortex-white)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '11px',
          fontWeight: 700,
          flexShrink: 0,
          zIndex: 1,
        }}>
          {index + 1}
        </div>
        {!isLast && (
          <div style={{
            width: '2px',
            flex: 1,
            background: 'var(--cortex-border)',
            marginTop: '4px',
            marginBottom: '4px',
          }} />
        )}
      </div>

      {/* Step content */}
      <div style={{ flex: 1, marginBottom: isLast ? 0 : '12px' }}>
        <button
          onClick={() => setExpanded(v => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            width: '100%',
            background: 'transparent',
            border: 'none',
            textAlign: 'left',
            cursor: 'pointer',
            padding: '2px 0',
            marginBottom: '6px',
          }}
          aria-expanded={expanded}
        >
          <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--cortex-navy)', flex: 1 }}>
            {step.name}
          </span>
          {step.mitre_technique && (
            <span style={{
              fontSize: '11px',
              fontFamily: 'var(--font-mono)',
              color: 'var(--cortex-steel)',
              flexShrink: 0,
            }}>
              {step.mitre_technique}
            </span>
          )}
          <span style={{
            fontSize: '10px',
            color: 'var(--cortex-steel)',
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform var(--transition-fast)',
          }}>
            &#9658;
          </span>
        </button>

        {/* Command preview */}
        {step.command && (
          <pre style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            background: '#0d1f2d',
            color: '#a8d8ea',
            padding: '8px 12px',
            borderRadius: 'var(--radius-md)',
            overflowX: 'auto',
            margin: '0 0 8px 0',
            border: '1px solid rgba(255,255,255,0.06)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}>
            {step.command}
          </pre>
        )}

        {/* Expected detections — collapsible */}
        {expanded && step.expected_detections && step.expected_detections.length > 0 && (
          <div style={{ marginTop: '8px' }}>
            <p style={{
              fontSize: '11px',
              fontWeight: 700,
              color: 'var(--cortex-steel)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: '6px',
            }}>
              Expected Detections
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {step.expected_detections.map((det, di) => (
                <div key={di} style={{
                  background: 'var(--cortex-light-bg)',
                  border: '1px solid var(--cortex-border)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '8px 12px',
                  display: 'flex',
                  gap: '8px',
                  alignItems: 'flex-start',
                }}>
                  <DetectionBadge type={det.type} />
                  <div style={{ flex: 1 }}>
                    <span style={{
                      fontSize: '11px',
                      fontWeight: 600,
                      color: 'var(--cortex-steel)',
                      textTransform: 'uppercase',
                      letterSpacing: '0.04em',
                      marginRight: '6px',
                    }}>
                      {det.plane}
                    </span>
                    <span style={{ fontSize: '12px', color: '#1A2B3C' }}>
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
        <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
          {data?.uc_ref && <span className="badge badge-navy">{data.uc_ref}</span>}
          {data?.tc_ref && <span className="badge badge-teal">{data.tc_ref}</span>}
        </div>
      </div>

      <div className="panel-card-body">
        {loading ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '12px 0' }}>
            <div className="spinner" />
            <span className="text-muted" style={{ fontSize: '13px' }}>Loading scenario detail…</span>
          </div>
        ) : (
          <>
            {/* UC Header */}
            <div style={{
              background: 'rgba(0,51,102,0.04)',
              border: '1px solid rgba(0,51,102,0.12)',
              borderRadius: 'var(--radius-md)',
              padding: '12px 16px',
              marginBottom: '20px',
            }}>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
                <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--cortex-steel)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Use Case
                </span>
                {data?.uc_ref && (
                  <span className="text-mono" style={{ fontSize: '11px', color: 'var(--cortex-teal)' }}>
                    {data.uc_ref}
                  </span>
                )}
              </div>
              <p style={{ fontSize: '14px', fontWeight: 600, color: 'var(--cortex-navy)', marginBottom: '4px' }}>
                {data?.uc_name || data?.name || '—'}
              </p>
              {data?.tc_name && (
                <p style={{ fontSize: '12px', color: 'var(--cortex-steel)' }}>
                  <strong style={{ fontWeight: 600 }}>TC: </strong>{data.tc_name}
                </p>
              )}

              {/* MITRE info */}
              {(data?.mitre_tactic || data?.mitre_technique) && (
                <div style={{
                  marginTop: '10px',
                  paddingTop: '10px',
                  borderTop: '1px solid rgba(0,51,102,0.1)',
                  display: 'flex',
                  gap: '16px',
                  flexWrap: 'wrap',
                }}>
                  {data.mitre_tactic && (
                    <div>
                      <span style={{ fontSize: '10px', color: 'var(--cortex-steel)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block' }}>
                        Tactic
                      </span>
                      <span className="text-mono" style={{ fontSize: '12px', color: 'var(--cortex-navy)', fontWeight: 600 }}>
                        {data.mitre_tactic}
                      </span>
                      {data.mitre_tactic_name && (
                        <span style={{ fontSize: '12px', color: '#1A2B3C', marginLeft: '6px' }}>
                          {data.mitre_tactic_name}
                        </span>
                      )}
                    </div>
                  )}
                  {data.mitre_technique && (
                    <div>
                      <span style={{ fontSize: '10px', color: 'var(--cortex-steel)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block' }}>
                        Technique
                      </span>
                      <span className="text-mono" style={{ fontSize: '12px', color: 'var(--cortex-navy)', fontWeight: 600 }}>
                        {data.mitre_technique}
                      </span>
                      {data.mitre_technique_name && (
                        <span style={{ fontSize: '12px', color: '#1A2B3C', marginLeft: '6px' }}>
                          {data.mitre_technique_name}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Threat report ref */}
              {data?.threat_report && (
                <div style={{ marginTop: '8px' }}>
                  <span style={{ fontSize: '11px', color: 'var(--cortex-steel)' }}>
                    Unit 42 ref:{' '}
                    {data.threat_report_url ? (
                      <a href={data.threat_report_url} target="_blank" rel="noopener noreferrer">
                        {data.threat_report}
                      </a>
                    ) : (
                      <span style={{ color: '#1A2B3C' }}>{data.threat_report}</span>
                    )}
                  </span>
                </div>
              )}
            </div>

            {/* Steps timeline */}
            {steps.length === 0 ? (
              <div className="empty-state" style={{ padding: '20px 0' }}>
                <p>No execution steps defined for this scenario.</p>
              </div>
            ) : (
              <div>
                <p className="section-label" style={{ marginBottom: '16px' }}>
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
