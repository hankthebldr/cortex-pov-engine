import React, { useState, useEffect, useCallback } from 'react'
import { getMitreCoverage } from '../api/client.js'
import '../styles/destinations/coverage.css'

// --- Status colors ---
// Status → CSS modifier class (see .mitre-heatmap-status--* in cortex-console.css).
// Background/foreground colors live in CSS now; only the label stays here.

const STATUS_CONFIG = {
  detected:         { statusClass: 'mitre-heatmap-status--detected',          label: 'Detected' },
  run_not_detected: { statusClass: 'mitre-heatmap-status--run-not-detected',  label: 'Run — Not Detected' },
  not_run:          { statusClass: 'mitre-heatmap-status--not-run',           label: 'Scenario Exists' },
  no_scenario:      { statusClass: 'mitre-heatmap-status--no-scenario',       label: 'No Scenario' },
}

// --- Technique cell ---

function TechniqueCell({ technique, onClick }) {
  const cfg = STATUS_CONFIG[technique.status] || STATUS_CONFIG.no_scenario

  return (
    <div
      onClick={() => onClick(technique)}
      title={`${technique.technique_id}: ${technique.technique_name}\nStatus: ${cfg.label}\nScenarios: ${technique.scenarios.join(', ') || 'none'}\nDetections: ${technique.observed_detections}/${technique.total_detections}`}
      className={`mitre-heatmap__cell ${cfg.statusClass}`}
    >
      <div className="mitre-heatmap__cell-id">{technique.technique_id}</div>
      <div className="mitre-heatmap__cell-name">
        {technique.technique_name}
      </div>
      {technique.total_detections > 0 && (
        <div className="mitre-heatmap__cell-detections">
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
    <div className="mitre-heatmap__detail">
      <div className="mitre-heatmap__detail-head">
        <div>
          <div className="mitre-heatmap__detail-title-row">
            <span className="mitre-heatmap__detail-id">
              {technique.technique_id}
            </span>
            <span className={`mitre-heatmap__detail-badge ${cfg.statusClass}`}>
              {cfg.label}
            </span>
          </div>
          <div className="mitre-heatmap__detail-name">
            {technique.technique_name}
          </div>
        </div>
        <button
          onClick={onClose}
          className="mitre-heatmap__detail-close"
        >
          ✕
        </button>
      </div>

      <div className="mitre-heatmap__detail-grid">
        <div>
          <span className="mitre-heatmap__detail-label">Tactic:</span>{' '}
          <span className="mitre-heatmap__detail-tactic-value">{technique.tactic_id} — {technique.tactic_name}</span>
        </div>
        <div>
          <span className="mitre-heatmap__detail-label">Planes:</span>{' '}
          {technique.planes.map(p => (
            <span key={p} className="badge badge-navy mitre-heatmap__detail-plane-badge">{p}</span>
          ))}
        </div>
        <div>
          <span className="mitre-heatmap__detail-label">Scenarios:</span>{' '}
          <span className="mitre-heatmap__detail-scenarios">
            {technique.scenarios.length > 0 ? technique.scenarios.join(', ') : 'None'}
          </span>
        </div>
        <div>
          <span className="mitre-heatmap__detail-label">Detections:</span>{' '}
          <span className={`mitre-heatmap__detail-detections ${technique.observed_detections > 0 ? 'mitre-heatmap__detail-detections--positive' : 'mitre-heatmap__detail-detections--zero'}`}>
            {technique.observed_detections}/{technique.total_detections}
          </span>
          {technique.coverage_pct > 0 && (
            <span className="mitre-heatmap__detail-coverage">({technique.coverage_pct}%)</span>
          )}
        </div>
      </div>
    </div>
  )
}

// --- Summary bar ---

const SUMMARY_SEGMENTS = [
  { key: 'detected', label: 'Detected', field: 'detected' },
  { key: 'run-not-detected', label: 'Run — No Detection', field: 'run_not_detected' },
  { key: 'not-run', label: 'Scenario Exists', field: 'not_run' },
]

function SummaryBar({ summary }) {
  const total = summary.total_techniques || 1
  const segments = SUMMARY_SEGMENTS.map(seg => ({ ...seg, count: summary[seg.field] }))

  return (
    <div className="mitre-heatmap__summary">
      {/* Stacked bar */}
      <div className="mitre-heatmap__summary-bar">
        {segments.map(seg => (
          seg.count > 0 && (
            <div
              key={seg.label}
              className={`mitre-heatmap__summary-segment mitre-heatmap-status--${seg.key}`}
              style={{ width: `${(seg.count / total) * 100}%` }}
            >
              {seg.count}
            </div>
          )
        ))}
      </div>

      {/* Legend */}
      <div className="mitre-heatmap__summary-legend">
        {segments.map(seg => (
          <div key={seg.label} className="mitre-heatmap__summary-legend-item">
            <div className={`mitre-heatmap__summary-swatch mitre-heatmap-status--${seg.key}`} />
            <span>{seg.label} ({seg.count})</span>
          </div>
        ))}
        <div className="mitre-heatmap__summary-legend-item">
          <div className="mitre-heatmap__summary-swatch mitre-heatmap-status--no-scenario" />
          <span className="mitre-heatmap__summary-total-label">
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
    <div className="panel-card coverage-heat-legacy">
      <div className="panel-card-header">
        <div>
          <div className="coverage-heat-legacy__bar" aria-hidden="true" />
          <h3>MITRE ATT&CK Coverage</h3>
        </div>
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
            <div className="spinner mitre-heatmap__spinner" />
            <p>Loading MITRE coverage…</p>
          </div>
        ) : error ? (
          <div className="empty-state">
            <p className="mitre-heatmap__error-text">{error}</p>
          </div>
        ) : !data || !data.by_tactic || data.by_tactic.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon mitre-heatmap__empty-icon">🛡️</div>
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
            <div className="mitre-heatmap__matrix">
              {data.by_tactic.map(tactic => (
                <div key={tactic.tactic_id} className="mitre-heatmap__tactic-col">
                  {/* Tactic header */}
                  <div className="mitre-heatmap__tactic-head">
                    <div>{tactic.tactic_id}</div>
                    <div className="mitre-heatmap__tactic-name">
                      {tactic.tactic_name}
                    </div>
                  </div>

                  {/* Technique cells */}
                  <div className="mitre-heatmap__tactic-body">
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
