import React, { useMemo } from 'react'
import InflightView from './InflightView.jsx'
import EvidenceView from './EvidenceView.jsx'
import DetectionStoryline, { isRunUnproven } from '../DetectionStoryline.jsx'
import CausalityGraph from '../CausalityGraph.jsx'
import { runStatusToken, runStatusGlyph } from './runStatus.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { scenarioIdOf } from '../../api/ids.js'

/**
 * Last few lines of a run's captured output — the actual reason it died.
 *
 * SimCore stores the whole per-step transcript in `Run.output`; the operator
 * needs the tail (`runuser: may not be used by non-root users …
 * exit_code=1`), not a wall of it. Trim to the last N non-empty lines.
 */
function tailOutput(output, lines = 8) {
  if (!output) return null
  const kept = String(output)
    .split('\n')
    .map((l) => l.trimEnd())
    .filter((l) => l.length > 0)
    .slice(-lines)
  return kept.length ? kept.join('\n') : null
}

/**
 * RunDetailView — the single Run Detail surface, keyed by runId.
 *
 * Re-homes the four former top-level tabs (Live / Evidence / Storyline /
 * Causality) as SUB-tabs of one run so a run's whole proof story lives under
 * one deep-linkable URL (`#/runs?run=<id>&tab=<sub>`). Nothing here fetches the
 * run list — the parent RunsView owns selection + routing and passes the
 * resolved run record down. The four child views are re-homed unchanged; their
 * internal fetching (results, storyline, causality, telemetry) is untouched.
 *
 * Props:
 *   runId      — the selected run's id (string)
 *   run        — the resolved run record from the provider (or null if unknown)
 *   activeRun  — the provider's derived live run ({ runId, ... }) or null
 *   subTab     — 'live' | 'evidence' | 'storyline' | 'causality'
 *   onSubTab   — (tab) => void   switch sub-tab (writes ?tab=)
 *   onBack     — () => void      return to the run list
 *   onError    — (msg) => void
 */

export const RUN_SUBTABS = [
  { id: 'live',      label: 'Live'      },
  { id: 'evidence',  label: 'Evidence'  },
  { id: 'storyline', label: 'Storyline' },
  { id: 'causality', label: 'Causality' },
]

export default function RunDetailView({
  runId,
  run = null,
  activeRun = null,
  subTab = 'live',
  onSubTab = () => {},
  onBack = () => {},
  onError = () => {},
}) {
  const tab = RUN_SUBTABS.some((t) => t.id === subTab) ? subTab : 'live'
  const { scenarios } = useEnvironment()

  const descriptor = useMemo(() => ({
    runId,
    scenarioId: run?.scenario_id || null,
    status: run?.status || null,
  }), [runId, run])

  // Title the surface with what the run WAS, not its opaque uuid. A customer
  // reading "4ee09860-140d-…" as the heading learns nothing.
  const scenarioName = useMemo(() => {
    if (!descriptor.scenarioId) return null
    const s = scenarios.find((x) => scenarioIdOf(x) === descriptor.scenarioId)
    return s?.name || null
  }, [scenarios, descriptor.scenarioId])

  const isThisActive = !!(activeRun && activeRun.runId === runId)
  const statusToken = runStatusToken(run?.status)
  const failed = isRunUnproven(run?.status)
  const failureTail = failed ? tailOutput(run?.output) : null

  if (!runId) {
    return (
      <div className="runs-surface">
        <div className="view-head">
          <div><h1>Run detail</h1></div>
          <button className="btn" onClick={onBack}>← All runs</button>
        </div>
        <div className="run-detail__empty mono">no run selected</div>
      </div>
    )
  }

  return (
    <div className="runs-surface run-detail">
      <div className="view-head">
        <div>
          <h1 className={scenarioName ? undefined : 'mono'}>
            {scenarioName || descriptor.scenarioId || runId}
          </h1>
          <div className="view-head__meta">
            {scenarioName && descriptor.scenarioId && (
              <><span className="mono">{descriptor.scenarioId}</span>{' · '}</>
            )}
            run <span className="mono">{runId}</span>
            {run?.status && (
              <>
                {' · '}
                <span className={`run-status run-status--${statusToken}`}>
                  <span aria-hidden="true">{runStatusGlyph(run.status)}</span>{' '}{run.status}
                </span>
              </>
            )}
            {isThisActive && (
              <> · <span className="run-live-pill" aria-label="live run">LIVE</span></>
            )}
            {run?.mode && <> · <span className="mono">{run.mode}</span></>}
            {run?.target && <> · <span className="mono">{run.target}</span></>}
          </div>
        </div>
        <button className="btn" onClick={onBack}>← All runs</button>
      </div>

      {/* The run died and the console knew — say it here, once, at the top,
          before any proof surface invites a coverage reading. */}
      {failed && (
        <div className="run-failure" role="alert" data-testid="run-failure-banner">
          <div className="run-failure__head">
            <span className="run-failure__tag">RUN {String(run.status).toUpperCase()}</span>
            <span className="run-failure__lede">
              The simulation stopped before completing — coverage figures on this run
              are not a measurement of the tenant's detections.
            </span>
          </div>
          {failureTail ? (
            <pre className="run-failure__output" aria-label="Run failure output">{failureTail}</pre>
          ) : (
            <p className="run-failure__nooutput mono">
              no output captured — check the beacon's logs on the target host
            </p>
          )}
        </div>
      )}

      <div
        className="lab__segmented"
        role="tablist"
        aria-label="Run detail views"
        style={{ marginBottom: 12 }}
      >
        {RUN_SUBTABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? 'is-active' : ''}
            onClick={() => onSubTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="view run-detail__panel" role="tabpanel" key={tab}>
        {tab === 'live' && (
          <InflightView
            activeRun={isThisActive ? activeRun : null}
            lastRun={isThisActive ? null : descriptor}
            onError={onError}
          />
        )}
        {tab === 'evidence' && (
          <EvidenceView
            activeRun={isThisActive ? activeRun : null}
            lastRun={isThisActive ? null : descriptor}
            pinnedRun={descriptor}
            onError={onError}
          />
        )}
        {tab === 'storyline' && (
          <DetectionStoryline
            runId={runId}
            scenarioId={descriptor.scenarioId}
            onOpenEvidence={() => onSubTab('evidence')}
            onError={onError}
          />
        )}
        {tab === 'causality' && (
          <CausalityGraph
            runId={runId}
            scenarioId={descriptor.scenarioId}
            onOpenEvidence={() => onSubTab('evidence')}
            onError={onError}
          />
        )}
      </div>
    </div>
  )
}
