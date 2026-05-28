import React, { useState, useEffect } from 'react'
import useMitreCoverage from './useMitreCoverage.js'
import StackCoverageView from './StackCoverageView.jsx'
import CompetitiveView from './CompetitiveView.jsx'
import AdapterRegistryView from './AdapterRegistryView.jsx'
import ToolAdapterCatalog from './ToolAdapterCatalog.jsx'
import TtpBrowserView from './TtpBrowserView.jsx'
import { downloadLayer } from './exportNavigatorLayer.js'

/**
 * CoverageView — the Coverage tab. Two view modes:
 *
 *   ATT&CK   — MITRE Navigator-style matrix (tactic × technique cells)
 *   PANW Stack — product × tactic kill-chain matrix
 *
 * Both pivot the same scenario library through different lenses. The
 * ATT&CK view answers "which techniques do we exercise?"; the Stack view
 * answers "which Palo Alto products carry the detection load?"
 *
 * Props:
 *   onFilterByTechnique  — (tid, scenarioIds) => void
 *                          AppConsole switches to Operations tab + applies
 *                          the filter for either view's cell click.
 */
export default function CoverageView({ onFilterByTechnique = () => {} }) {
  const { data, loading, error, refresh } = useMitreCoverage()
  const [selectedTechnique, setSelectedTechnique] = useState(null)
  const [viewMode, setViewMode] = useState('attack') // 'attack' | 'stack' | 'advantage' | 'adapters' | 'tools' | 'ttps'

  // When an adapter detail panel emits a cortex:navigate-ttp custom
  // event (PR #49's TTP-ref chip click), flip to the TTP browser sub-
  // tab and pre-select the TTP whose id rode the event payload.
  const [pendingTtpId, setPendingTtpId] = useState(null)
  useEffect(() => {
    const handler = (e) => {
      const ttpId = e?.detail?.ttpId
      if (typeof ttpId === 'string' && ttpId) {
        setPendingTtpId(ttpId)
        setViewMode('ttps')
      }
    }
    window.addEventListener('cortex:navigate-ttp', handler)
    return () => window.removeEventListener('cortex:navigate-ttp', handler)
  }, [])

  if (viewMode === 'stack') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <h1>PANW Stack Coverage</h1>
            <div className="view-head__meta">
              product × kill chain · click a cell to drill into scenarios
            </div>
          </div>
          <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
        </div>
        <StackCoverageView
          onFilterByCell={(_productId, _tactic, scenarioIds) => {
            // Re-use the existing Operations filter cross-link by passing
            // scenarioIds; the receiving end ignores the tid when
            // scenarioIds is populated.
            onFilterByTechnique('STACK', scenarioIds)
          }}
        />
      </div>
    )
  }

  if (viewMode === 'advantage') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <h1>PANW Advantage</h1>
            <div className="view-head__meta">
              capability matrix · Cortex vs. major EDR / SIEM / BAS competitors
            </div>
          </div>
          <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
        </div>
        <CompetitiveView />
      </div>
    )
  }

  if (viewMode === 'adapters') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <h1>EAL Plugins</h1>
            <div className="view-head__meta">
              attack vectors shipped with this build · the plugins SimCore
              invokes to generate Cortex-bound signal
            </div>
          </div>
          <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
        </div>
        <AdapterRegistryView />
      </div>
    )
  }

  if (viewMode === 'tools') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <h1>Tool Adapters</h1>
            <div className="view-head__meta">
              static catalog of offensive + defensive tools a scenario can
              reference via <span className="mono">external_tools[].adapter_ref</span>
            </div>
          </div>
          <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
        </div>
        <ToolAdapterCatalog />
      </div>
    )
  }

  if (viewMode === 'ttps') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <h1>TTP Browser</h1>
            <div className="view-head__meta">
              detection cards under <span className="mono">detection_scanner/ttps/</span> —
              BIOC + XQL + correlation logic that catches each technique
            </div>
          </div>
          <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
        </div>
        <TtpBrowserView initialTtpId={pendingTtpId} />
      </div>
    )
  }

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
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <ViewModeToggle viewMode={viewMode} onChange={setViewMode} />
          <button
            className="btn"
            onClick={() => downloadLayer(data)}
            disabled={loading || !data?.by_tactic || data.by_tactic.length === 0}
            title="Download MITRE ATT&CK Navigator v4.5 layer JSON for executive briefings"
          >
            ↗ Navigator layer
          </button>
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

/* ─── ViewMode toggle ────────────────────────────────────────────── */

function ViewModeToggle({ viewMode, onChange }) {
  return (
    <div className="lab__segmented" role="tablist" aria-label="Coverage view mode">
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'attack'}
        className={viewMode === 'attack' ? 'is-active' : ''}
        onClick={() => onChange('attack')}
      >
        ATT&amp;CK
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'stack'}
        className={viewMode === 'stack' ? 'is-active' : ''}
        onClick={() => onChange('stack')}
      >
        PANW Stack
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'advantage'}
        className={viewMode === 'advantage' ? 'is-active' : ''}
        onClick={() => onChange('advantage')}
        title="Capability matrix vs. major competitors"
      >
        Advantage
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'adapters'}
        className={viewMode === 'adapters' ? 'is-active' : ''}
        onClick={() => onChange('adapters')}
        title="Installed EAL attack-vector plugins + parameter schemas"
      >
        EAL Plugins
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'tools'}
        className={viewMode === 'tools' ? 'is-active' : ''}
        onClick={() => onChange('tools')}
        title="Static catalog of tool adapters scenarios can reference"
      >
        Tool Adapters
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'ttps'}
        className={viewMode === 'ttps' ? 'is-active' : ''}
        onClick={() => onChange('ttps')}
        title="TTP corpus — BIOC + XQL + correlation detection cards"
      >
        TTP Browser
      </button>
    </div>
  )
}
