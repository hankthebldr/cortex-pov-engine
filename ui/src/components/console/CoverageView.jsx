import React, { useState, useEffect } from 'react'
import useMitreCoverage from './useMitreCoverage.js'
import StackCoverageView from './StackCoverageView.jsx'
import CompetitiveView from './CompetitiveView.jsx'
import { downloadLayer } from './exportNavigatorLayer.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import '../../styles/destinations/coverage.css'

/**
 * CoverageView — the Coverage destination. THREE analytical modes, each a
 * different lens over the same scenario corpus:
 *
 *   ATT&CK      — MITRE Navigator-style matrix (tactic × technique cells);
 *                 "which techniques does the library exercise?"
 *   PANW Stack  — product × tactic kill-chain matrix; "which Palo Alto
 *                 products carry the detection load?"
 *   Competitive — capability matrix vs. major EDR / SIEM / BAS competitors.
 *
 * The adapters / tools / TTP inventories that used to live here as extra
 * toggle modes have GRADUATED to their own first-class destinations
 * (Tool Adapters + TTP Cards). This surface is now purely analytical.
 *
 * Ambient scope (the active XSIAM tenant) comes from the EnvironmentProvider,
 * not local tab state — switching tenant in the global bar refetches the
 * coverage matrix automatically.
 *
 * Props (spine contract — all optional; the surface degrades gracefully when
 * mounted bare, e.g. in isolated tests):
 *   onNavigate  — (destinationId, params?) => void  cross-surface router jump.
 *                 Used to hop to the `ttps` destination when a sibling surface
 *                 emits a cortex:navigate-ttp event, and to the `library`
 *                 destination for a technique drill-down.
 */
export default function CoverageView({ onNavigate } = {}) {
  const { tenant } = useEnvironment()
  const { data, loading, error, refresh } = useMitreCoverage(tenant?.name || null)
  const [selectedTechnique, setSelectedTechnique] = useState(null)
  const [viewMode, setViewMode] = useState('attack') // 'attack' | 'stack' | 'competitive'

  // A sibling surface (the Tool Adapters destination's detail panel) can emit
  // a cortex:navigate-ttp custom event when a DC clicks a TTP-ref chip. The
  // TTP browser is no longer a sub-tab here — it graduated to its own
  // destination — so we hop the router to `ttps?ttp=<id>` instead of flipping
  // an internal mode. When the spine hands us `onNavigate` we use it; otherwise
  // we fall back to a direct hash write the zero-dep router reflects back.
  useEffect(() => {
    const handler = (e) => {
      const ttpId = e?.detail?.ttpId
      if (typeof ttpId !== 'string' || !ttpId) return
      if (typeof onNavigate === 'function') {
        onNavigate('ttps', { ttp: ttpId })
      } else {
        window.location.hash = `#/ttps?ttp=${encodeURIComponent(ttpId)}`
      }
    }
    window.addEventListener('cortex:navigate-ttp', handler)
    return () => window.removeEventListener('cortex:navigate-ttp', handler)
  }, [onNavigate])

  // Technique / stack-cell drill-down → the Library destination. Only wired
  // when the spine passes onNavigate; a bare mount degrades to a no-op.
  const onFilterByTechnique = (tid, scenarioIds) => {
    if (typeof onNavigate === 'function') {
      onNavigate('library', { technique: tid, scenarios: (scenarioIds || []).join(',') })
    }
  }

  if (viewMode === 'stack') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <CoverageKicker />
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

  if (viewMode === 'competitive') {
    return (
      <div className="coverage">
        <div className="view-head">
          <div>
            <CoverageKicker />
            <h1>Competitive Position</h1>
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

  return (
    <div className="coverage">
      <div className="view-head">
        <div>
          <CoverageKicker />
          <h1>ATT&amp;CK Coverage</h1>
          <div className="view-head__meta">
            {data?.summary ? (
              <>
                <span className="mono">{data.summary.total_techniques}</span> techniques
                {' · '}<span className="mono coverage-color-detected">
                  {data.summary.detected}
                </span> detected
                {' · '}<span className="mono coverage-color-pending">
                  {data.summary.run_not_detected}
                </span> run / no detection
                {' · '}<span className="mono coverage-color-muted">
                  {data.summary.not_run}
                </span> staged
              </>
            ) : (
              'click a cell to filter Operations to scenarios that exercise that technique'
            )}
          </div>
        </div>
        <div className="coverage__header-actions">
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
        <>
          <CoverageKpis summary={data.summary} />
          <CoverageSummaryBar summary={data.summary} />
        </>
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
        <div className="coverage__empty mono coverage__empty--error">
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

// Accent bar + eyebrow — the masthead pattern shared by every redesigned
// console destination ("Analyze" is this destination's real nav group,
// per app/destinations.jsx; there is no app-level "phase" stepper to
// echo the mockup's "· Phase 6" suffix, so it is dropped).
function CoverageKicker() {
  return (
    <div className="coverage-kicker">
      <span className="coverage-kicker__bar" aria-hidden="true" />
      <span className="coverage-kicker__label">Analyze</span>
    </div>
  )
}

// Four KPI tiles summarizing the same `data.summary` the segmented bar
// below already renders — no new data fetch, just a second, higher-
// contrast read of the ATT&CK matrix's headline numbers.
function CoverageKpis({ summary }) {
  const total = summary.total_techniques || 0
  const tiles = [
    {
      key: 'detected', tone: 'ac', value: `${summary.detected || 0} / ${total}`,
      label: 'Detected', note: 'techniques observed with a firing detection on this tenant',
    },
    {
      key: 'gap', tone: 'warn', value: String(summary.run_not_detected || 0),
      label: 'Open gaps', note: 'executed with no detection — close these first',
    },
    {
      key: 'staged', tone: '', value: String(summary.not_run || 0),
      label: 'Staged', note: 'scenario library covers this technique, not yet run',
    },
    {
      key: 'total', tone: '', value: String(total),
      label: 'Technique surface', note: 'MITRE ATT&CK techniques tracked by the library',
    },
  ]
  return (
    <div className="coverage__kpis">
      {tiles.map((t) => (
        <div key={t.key} className={'coverage__kpi' + (t.tone ? ` coverage__kpi--${t.tone}` : '')}>
          <div className="coverage__kpi-label">{t.label}</div>
          <div className="coverage__kpi-value mono">{t.value}</div>
          <div className="coverage__kpi-note">{t.note}</div>
        </div>
      ))}
    </div>
  )
}

function CoverageSummaryBar({ summary }) {
  const total = Math.max(1, summary.total_techniques || 0)
  const segs = [
    { key: 'detected',         label: 'Detected',            count: summary.detected || 0,         color: 'var(--c-detected)' },
    { key: 'run_not_detected', label: 'Run · no detection',  count: summary.run_not_detected || 0, color: 'var(--c-pending)'  },
    { key: 'not_run',          label: 'Staged',               count: summary.not_run || 0,          color: 'var(--c-hairline-strong)' },
  ]
  return (
    <>
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
      <div className="coverage__legend">
        {segs.map((s) => (
          <span key={s.key} className="coverage__legend-item">
            <span className="coverage__legend-dot" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </>
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
                <span key={p} className="chip cov-detail__plane-chip">{p}</span>
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
        title="MITRE ATT&CK technique coverage matrix"
      >
        ATT&amp;CK
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'stack'}
        className={viewMode === 'stack' ? 'is-active' : ''}
        onClick={() => onChange('stack')}
        title="PANW product × kill-chain coverage matrix"
      >
        PANW Stack
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={viewMode === 'competitive'}
        className={viewMode === 'competitive' ? 'is-active' : ''}
        onClick={() => onChange('competitive')}
        title="Capability matrix vs. major EDR / SIEM / BAS competitors"
      >
        Competitive
      </button>
    </div>
  )
}
