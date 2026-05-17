import React, { useState } from 'react'
import useMitreCoverage from './useMitreCoverage.js'

/**
 * CoverageView — the ATT&CK Coverage tab.
 *
 * Console-themed MITRE Navigator-style matrix:
 *   - Tactic columns scroll horizontally
 *   - Each technique cell shows TID, name, coverage chip
 *   - Cell click opens an inline detail panel with the scenario list
 *   - "Filter Operations" button on the detail panel emits a callback the
 *     parent uses to switch tabs and apply a technique filter
 *
 * Props:
 *   onFilterByTechnique  — (tid) => void
 *                          AppConsole switches to Operations tab + sets the filter
 */
export default function CoverageView({ onFilterByTechnique = () => {} }) {
  const { data, loading, error, refresh } = useMitreCoverage()
  const [selectedTechnique, setSelectedTechnique] = useState(null)

  return (
    <div className="coverage">
      <div className="view-head">
        <div>
          <h1>ATT&amp;CK Coverage</h1>
          <div className="view-head__meta">
            {data?.summary ? (
              <>
                <span className="mono">{data.summary.total_techniques}</span> techniques
                {' · '}<span className="mono" style={{ color: 'var(--c-detected)' }}>
                  {data.summary.detected}
                </span> detected
                {' · '}<span className="mono" style={{ color: 'var(--c-pending)' }}>
                  {data.summary.run_not_detected}
                </span> run / no detection
                {' · '}<span className="mono" style={{ color: 'var(--c-text-muted)' }}>
                  {data.summary.not_run}
                </span> staged
              </>
            ) : (
              'click a cell to filter Operations to scenarios that exercise that technique'
            )}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" onClick={refresh} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {data?.summary && (
        <CoverageSummaryBar summary={data.summary} />
      )}

      {selectedTechnique && (
        <TechniqueDetailPanel
          technique={selectedTechnique}
          onClose={() => setSelectedTechnique(null)}
          onFilterByTechnique={(tid) => {
            setSelectedTechnique(null)
            onFilterByTechnique(tid, selectedTechnique.scenarios || [])
          }}
        />
      )}

      {loading && !data ? (
        <div className="coverage__empty mono">loading MITRE coverage…</div>
      ) : error ? (
        <div className="coverage__empty mono" style={{ color: 'var(--c-missed)' }}>
          {error}
        </div>
      ) : !data || !data.by_tactic || data.by_tactic.length === 0 ? (
        <div className="coverage__empty mono">
          no MITRE technique data yet — load scenarios to populate the matrix
        </div>
      ) : (
        <div className="coverage__matrix">
          {data.by_tactic.map((tactic) => (
            <TacticColumn
              key={tactic.tactic_id}
              tactic={tactic}
              selectedTechniqueId={selectedTechnique?.technique_id || null}
              onSelectTechnique={setSelectedTechnique}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/* ─── Subcomponents ────────────────────────────────────────────────────── */

function CoverageSummaryBar({ summary }) {
  const total = Math.max(1, summary.total_techniques || 0)
  const segs = [
    { key: 'detected',         count: summary.detected || 0,         color: 'var(--c-detected)' },
    { key: 'run_not_detected', count: summary.run_not_detected || 0, color: 'var(--c-pending)'  },
    { key: 'not_run',          count: summary.not_run || 0,          color: 'var(--c-hairline-strong)' },
  ]
  return (
    <div className="coverage__summary-bar">
      {segs.map((s) =>
        s.count > 0 ? (
          <div
            key={s.key}
            className="coverage__summary-seg"
            style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
            title={`${s.key}: ${s.count}`}
          />
        ) : null
      )}
    </div>
  )
}

function TacticColumn({ tactic, selectedTechniqueId, onSelectTechnique }) {
  return (
    <div className="coverage__column">
      <div className="coverage__column-head">
        <div className="coverage__column-tid">{tactic.tactic_id}</div>
        <div className="coverage__column-name">{tactic.tactic_name}</div>
        <div className="coverage__column-count mono">
          {tactic.techniques.length}
        </div>
      </div>
      <div className="coverage__column-cells">
        {tactic.techniques.map((tech) => (
          <TechniqueCell
            key={tech.technique_id}
            technique={tech}
            isSelected={tech.technique_id === selectedTechniqueId}
            onClick={onSelectTechnique}
          />
        ))}
      </div>
    </div>
  )
}

const STATUS_CLASS = {
  detected:         'cov-cell--detected',
  run_not_detected: 'cov-cell--pending',
  not_run:          'cov-cell--staged',
  no_scenario:      'cov-cell--empty',
}

function TechniqueCell({ technique, isSelected, onClick }) {
  const cls = STATUS_CLASS[technique.status] || STATUS_CLASS.no_scenario
  const detected = technique.observed_detections || 0
  const totalDet = technique.total_detections || 0

  return (
    <button
      type="button"
      className={
        'cov-cell ' + cls + (isSelected ? ' cov-cell--selected' : '')
      }
      onClick={() => onClick(technique)}
      title={`${technique.technique_id} · ${technique.technique_name}\n${
        STATUS_LABEL[technique.status] || 'No scenario'
      }\nScenarios: ${technique.scenarios.join(', ') || 'none'}`}
    >
      <div className="cov-cell__tid mono">{technique.technique_id}</div>
      <div className="cov-cell__name">{technique.technique_name}</div>
      {totalDet > 0 && (
        <div className="cov-cell__chip mono">{detected}/{totalDet}</div>
      )}
    </button>
  )
}

const STATUS_LABEL = {
  detected:         'Detected',
  run_not_detected: 'Run — no detection',
  not_run:          'Scenario staged · not run',
  no_scenario:      'No scenario',
}

function TechniqueDetailPanel({ technique, onClose, onFilterByTechnique }) {
  return (
    <div className="cov-detail">
      <div className="cov-detail__head">
        <div>
          <span className="cov-detail__tid mono">{technique.technique_id}</span>
          <span className={
            'cov-detail__status mono cov-detail__status--' +
            (technique.status || 'no_scenario')
          }>
            {STATUS_LABEL[technique.status] || 'No scenario'}
          </span>
        </div>
        <button className="btn" onClick={onClose}>Close</button>
      </div>

      <div className="cov-detail__name">{technique.technique_name}</div>

      <div className="cov-detail__kv">
        <dt>Tactic</dt>
        <dd>
          <span className="mono">{technique.tactic_id}</span>
          {' · '}{technique.tactic_name}
        </dd>

        <dt>Scenarios</dt>
        <dd className="mono">
          {technique.scenarios && technique.scenarios.length > 0
            ? technique.scenarios.join(' · ')
            : 'None'}
        </dd>

        <dt>Detections</dt>
        <dd className="mono">
          {technique.observed_detections}/{technique.total_detections}
          {technique.coverage_pct > 0 && (
            <> · {technique.coverage_pct}%</>
          )}
        </dd>

        {technique.planes && technique.planes.length > 0 && (
          <>
            <dt>Planes</dt>
            <dd>
              {technique.planes.map((p) => (
                <span key={p} className="chip" style={{ marginRight: 4 }}>{p}</span>
              ))}
            </dd>
          </>
        )}
      </div>

      <div className="cov-detail__actions">
        <button
          className="btn btn--primary"
          onClick={() => onFilterByTechnique(technique.technique_id)}
          disabled={!technique.scenarios || technique.scenarios.length === 0}
          title="Switch to Operations and filter scenarios that exercise this technique"
        >
          Filter Operations <span className="kbd">→</span>
        </button>
      </div>
    </div>
  )
}
