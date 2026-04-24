import React, { useState, useEffect, useCallback } from 'react'
import { getMitreCoverage } from '../api/client.js'

// --- Status colors ---

const STATUS_CONFIG = {
  detected:         { bg: 'var(--cortex-success)', fg: 'white',              label: 'Detected' },
  run_not_detected: { bg: 'var(--cortex-warning)', fg: 'white',              label: 'Run — Not Detected' },
  not_run:          { bg: 'var(--cortex-steel)',   fg: 'white',              label: 'Scenario Exists' },
  no_scenario:      { bg: '#E8ECF0',              fg: 'var(--cortex-steel)', label: 'No Scenario' },
}

// --- Technique cell ---

function TechniqueCell({ technique, onClick }) {
  const cfg = STATUS_CONFIG[technique.status] || STATUS_CONFIG.no_scenario
  const [hovered, setHovered] = useState(false)

  return (
    <div
      onClick={() => onClick(technique)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={`${technique.technique_id}: ${technique.technique_name}\nStatus: ${cfg.label}\nScenarios: ${technique.scenarios.join(', ') || 'none'}\nDetections: ${technique.observed_detections}/${technique.total_detections}`}
      style={{
        background: cfg.bg,
        color: cfg.fg,
        borderRadius: '4px',
        padding: '6px 8px',
        fontSize: '11px',
        fontFamily: 'var(--font-mono)',
        cursor: 'pointer',
        minWidth: '90px',
        textAlign: 'center',
        lineHeight: 1.3,
        border: hovered ? '2px solid var(--cortex-teal)' : '2px solid transparent',
        transition: 'border 0.15s ease',
        position: 'relative',
      }}
    >
      <div style={{ fontWeight: 700, fontSize: '11px' }}>{technique.technique_id}</div>
      <div style={{
        fontSize: '9px',
        opacity: 0.9,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        maxWidth: '100px',
      }}>
        {technique.technique_name}
      </div>
      {technique.total_detections > 0 && (
        <div style={{
          fontSize: '9px',
          marginTop: '2px',
          fontWeight: 600,
        }}>
          {technique.observed_detections}/{technique.total_detections}
        </div>
      )}
    </div>
  )
}

// --- Technique detail popup ---

function TechniqueDetail({ technique, onClose }) {
  if (!technique) return null
  const cfg = STATUS_CONFIG[technique.status]

  return (
    <div style={{
      background: 'white',
      border: '1px solid var(--cortex-border)',
      borderRadius: 'var(--radius-md)',
      padding: '16px 20px',
      marginBottom: '16px',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
        <div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: '16px',
              fontWeight: 700, color: 'var(--cortex-navy)',
            }}>
              {technique.technique_id}
            </span>
            <span style={{
              padding: '2px 8px', borderRadius: '10px', fontSize: '11px',
              fontWeight: 600, background: cfg.bg, color: cfg.fg,
            }}>
              {cfg.label}
            </span>
          </div>
          <div style={{ fontSize: '14px', color: 'var(--cortex-navy)', fontWeight: 500 }}>
            {technique.technique_name}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            fontSize: '18px', color: 'var(--cortex-steel)', padding: '0 4px',
          }}
        >
          ✕
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '12px' }}>
        <div>
          <span style={{ color: 'var(--cortex-steel)' }}>Tactic:</span>{' '}
          <span style={{ fontWeight: 500 }}>{technique.tactic_id} — {technique.tactic_name}</span>
        </div>
        <div>
          <span style={{ color: 'var(--cortex-steel)' }}>Planes:</span>{' '}
          {technique.planes.map(p => (
            <span key={p} className="badge badge-navy" style={{ fontSize: '10px', marginRight: '4px' }}>{p}</span>
          ))}
        </div>
        <div>
          <span style={{ color: 'var(--cortex-steel)' }}>Scenarios:</span>{' '}
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 500 }}>
            {technique.scenarios.length > 0 ? technique.scenarios.join(', ') : 'None'}
          </span>
        </div>
        <div>
          <span style={{ color: 'var(--cortex-steel)' }}>Detections:</span>{' '}
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: technique.observed_detections > 0 ? 'var(--cortex-success)' : 'var(--cortex-steel)' }}>
            {technique.observed_detections}/{technique.total_detections}
          </span>
          {technique.coverage_pct > 0 && (
            <span style={{ marginLeft: '6px', fontWeight: 600 }}>({technique.coverage_pct}%)</span>
          )}
        </div>
      </div>
    </div>
  )
}

// --- Summary bar ---

function SummaryBar({ summary }) {
  const total = summary.total_techniques || 1
  const segments = [
    { label: 'Detected', count: summary.detected, color: 'var(--cortex-success)' },
    { label: 'Run — No Detection', count: summary.run_not_detected, color: 'var(--cortex-warning)' },
    { label: 'Scenario Exists', count: summary.not_run, color: 'var(--cortex-steel)' },
  ]

  return (
    <div style={{ marginBottom: '20px' }}>
      {/* Stacked bar */}
      <div style={{ display: 'flex', height: '24px', borderRadius: '6px', overflow: 'hidden', marginBottom: '8px' }}>
        {segments.map(seg => (
          seg.count > 0 && (
            <div
              key={seg.label}
              style={{
                width: `${(seg.count / total) * 100}%`,
                background: seg.color,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'white', fontSize: '11px', fontWeight: 600, fontFamily: 'var(--font-mono)',
                minWidth: seg.count > 0 ? '30px' : 0,
              }}
            >
              {seg.count}
            </div>
          )
        ))}
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: '16px', fontSize: '11px' }}>
        {segments.map(seg => (
          <div key={seg.label} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
            <div style={{ width: '10px', height: '10px', borderRadius: '2px', background: seg.color }} />
            <span>{seg.label} ({seg.count})</span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '2px', background: '#E8ECF0' }} />
          <span style={{ color: 'var(--cortex-steel)' }}>
            {summary.total_techniques} techniques total
          </span>
        </div>
      </div>
    </div>
  )
}

// --- Main Component ---

export default function MitreHeatmap() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedTechnique, setSelectedTechnique] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    getMitreCoverage()
      .then(d => setData(d))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  return (
    <div className="panel-card">
      <div className="panel-card-header">
        <h3>MITRE ATT&CK Coverage</h3>
        <button
          className="btn btn-secondary btn-sm"
          onClick={refresh}
          disabled={loading}
        >
          {loading ? <span className="spinner" /> : '⟳ Refresh'}
        </button>
      </div>

      <div className="panel-card-body">
        {loading ? (
          <div className="empty-state">
            <div className="spinner" style={{ margin: '0 auto var(--space-4)' }} />
            <p>Loading MITRE coverage…</p>
          </div>
        ) : error ? (
          <div className="empty-state">
            <p style={{ color: 'var(--cortex-danger)' }}>{error}</p>
          </div>
        ) : !data || !data.by_tactic || data.by_tactic.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon" style={{ fontSize: '28px' }}>🛡️</div>
            <p>No MITRE technique data yet. Load scenarios and run simulations to populate the matrix.</p>
          </div>
        ) : (
          <>
            <SummaryBar summary={data.summary} />

            {selectedTechnique && (
              <TechniqueDetail
                technique={selectedTechnique}
                onClose={() => setSelectedTechnique(null)}
              />
            )}

            {/* Tactic columns — matrix layout */}
            <div style={{
              display: 'flex', gap: '8px', overflowX: 'auto',
              paddingBottom: '12px',
            }}>
              {data.by_tactic.map(tactic => (
                <div key={tactic.tactic_id} style={{ minWidth: '120px', flex: '0 0 auto' }}>
                  {/* Tactic header */}
                  <div style={{
                    background: 'var(--cortex-navy)', color: 'white',
                    padding: '8px', borderRadius: '6px 6px 0 0',
                    textAlign: 'center', fontSize: '10px', fontWeight: 700,
                    textTransform: 'uppercase', letterSpacing: '0.3px',
                    lineHeight: 1.3,
                  }}>
                    <div>{tactic.tactic_id}</div>
                    <div style={{ fontSize: '9px', opacity: 0.8, fontWeight: 400, marginTop: '2px' }}>
                      {tactic.tactic_name}
                    </div>
                  </div>

                  {/* Technique cells */}
                  <div style={{
                    display: 'flex', flexDirection: 'column', gap: '4px',
                    padding: '4px', background: 'var(--cortex-light-bg)',
                    borderRadius: '0 0 6px 6px',
                    border: '1px solid var(--cortex-border)',
                    borderTop: 'none',
                  }}>
                    {tactic.techniques.map(tech => (
                      <TechniqueCell
                        key={tech.technique_id}
                        technique={tech}
                        onClick={setSelectedTechnique}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
