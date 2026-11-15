/**
 * ResultsView — Results for one run: what Cortex saw.
 *
 * Runs shows what was attempted; this page shows what came of it, always for
 * the run named in the "Result of" bar:
 *
 *   Result of bar   which run, and the switcher across reviewed runs
 *   caveats         what changes how the results read (collapsed, one line)
 *   verdict line    the run's verdict in words, with its reason
 *   tabs            Detections · Storyline · Techniques · Topology ·
 *                   Causality · Tenant evidence (`?tab=`, resultTabs.js)
 *   downloads       the report artifacts, one secondary Download
 *
 * A run that has not been reviewed has no results: the verdict line is the
 * Review notice, and every detection tab says "Not reviewed" — never a
 * scorecard, and never "missed". Tenant evidence still shows the stored read.
 *
 * The views are the ones that were tabs of a run's page, moved here unchanged.
 *
 * Props:
 *   run           the run row (newest first list's entry)
 *   readBackRuns  the reviewed runs, for the switcher
 *   tab           the `?tab=` value, or null
 *   onTab         (tabId) => void     writes `?tab=`
 *   onSwitch      (runId) => void     writes `?run=`
 *   onNavigate    (destination, params?) => void
 */
import React, { useMemo } from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { runIdOf, scenarioIdOf } from '../../api/ids.js'
import { RESULT_TABS, resultTab } from '../../app/resultTabs.js'
import { runLifecycle, PHASE } from './runLifecycle.js'
import { verdictReason } from './verdictReason.js'
import useReadBack from './useReadBack.jsx'
import useRunRecord from './useRunRecord.js'
import useToast from './useToast.js'
import ResultOfBar from './ResultOfBar.jsx'
import ReadBackNotice from './ReadBackNotice.jsx'
import ReadBackButton from './ReadBackButton.jsx'
import ResultVerdictLine from './ResultVerdictLine.jsx'
import RunCaveats from './RunCaveats.jsx'
import ExportMenu from './ExportMenu.jsx'
import EvidenceView from './EvidenceView.jsx'
import DetectionStoryline from '../DetectionStoryline.jsx'
import CausalityGraph from '../CausalityGraph.jsx'
import RunTechniquesView from './RunTechniquesView.jsx'
import RunTopologyView from './RunTopologyView.jsx'
import TenantEvidencePanel from './TenantEvidencePanel.jsx'
import { StatusIcon } from './kit/index.js'
import '../../styles/destinations/run-detail.css'
import '../../styles/destinations/results.css'

void React

const noop = () => {}

export default function ResultsView({
  run,
  readBackRuns = [],
  tab = null,
  onTab = noop,
  onSwitch = noop,
  onNavigate = noop,
}) {
  const { scenarios } = useEnvironment()
  const { reading, outcomes, readBack } = useReadBack()
  const toast = useToast()
  const id = runIdOf(run)
  const lifecycle = runLifecycle(run, { reading: reading.has(id) })
  // Reviewed is what SimCore recorded on the run, so a second review in flight
  // does not blank results that are already there.
  const reviewed = !!lifecycle.readBack
  // A run nobody has reviewed has no verdict to show — except the reason it is
  // pending while its detectors soak, which a review later can still score.
  const showVerdict = reviewed
    || (lifecycle.phase !== PHASE.NOTHING && !!verdictReason(run.tc_verdict_detail))
  const active = resultTab(tab)
  const scenario = scenarios.find((x) => scenarioIdOf(x) === run.scenario_id) || null

  // The run's record: its transcript is what the caveats are read from.
  const { record } = useRunRecord(id, {
    settled: true,
    refreshKey: `${run.status || ''}|${run.output_bytes ?? ''}`,
  })
  const merged = useMemo(() => ({ ...(record || {}), ...run }), [record, run])
  const { output, partial } = useMemo(() => {
    if (typeof record?.output === 'string') return { output: record.output, partial: false }
    if (typeof run.output === 'string') return { output: run.output, partial: false }
    if (typeof run.output_tail === 'string' && run.output_tail) return { output: run.output_tail, partial: true }
    return { output: null, partial: false }
  }, [record, run])

  const pinned = useMemo(
    () => ({ runId: id, scenarioId: run.scenario_id, status: run.status }),
    [id, run.scenario_id, run.status],
  )
  const toDetections = () => onTab('detections')

  let panel
  if (active === 'tenant') {
    panel = reviewed ? (
      <TenantEvidencePanel
        runId={id}
        reconcileAction={({ onDone }) => <ReadBackButton runId={id} onDone={onDone} />}
      />
    ) : (
      <TenantEvidencePanel runId={id} showCaseLedger={false} />
    )
  } else if (!reviewed) {
    panel = (
      <p className="results__unreviewed" data-testid="results-not-reviewed">
        <StatusIcon status="off" word="Not reviewed" />
      </p>
    )
  } else if (active === 'storyline') {
    panel = (
      <DetectionStoryline runId={id} scenarioId={run.scenario_id} onOpenEvidence={toDetections} onError={noop} />
    )
  } else if (active === 'techniques') {
    panel = <RunTechniquesView runId={id} run={run} onOpenEvidence={toDetections} onError={noop} />
  } else if (active === 'topology') {
    panel = (
      <RunTopologyView
        runId={id}
        run={run}
        onOpenEvidence={toDetections}
        onOpenCausality={() => onTab('causality')}
        onError={noop}
      />
    )
  } else if (active === 'causality') {
    panel = <CausalityGraph runId={id} scenarioId={run.scenario_id} onOpenEvidence={toDetections} onError={noop} />
  } else {
    panel = <EvidenceView pinnedRun={pinned} />
  }

  return (
    <div className="evidence results" data-testid="results" data-run-id={id}>
      <div className="view-head">
        <div><h1>Results</h1></div>
        {reviewed && (
          <div className="results__actions">
            <ExportMenu runId={id} onError={(msg) => toast(msg, 'error', 4000)} />
          </div>
        )}
      </div>

      <ResultOfBar
        run={run}
        lifecycle={lifecycle}
        scenarioName={scenario?.name || null}
        readBackRuns={readBackRuns}
        onSwitch={onSwitch}
        onOpenRun={() => onNavigate('runs', { run: id })}
      />

      <RunCaveats runId={id} run={merged} output={output} partial={partial} />

      {!reviewed && (
        <ReadBackNotice lifecycle={lifecycle} outcome={outcomes.get(id)} onReadBack={() => readBack(id)} />
      )}
      {showVerdict && <ResultVerdictLine run={run} tcRef={scenario?.tc_ref || null} />}

      <div className="pov-tabs results__tabs" role="tablist" aria-label="Results views">
        {RESULT_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`results-tab-${t.id}`}
            data-testid={`results-tab-${t.id}`}
            aria-selected={active === t.id}
            aria-controls="results-panel"
            className={'pov-tab' + (active === t.id ? ' pov-tab--on' : '')}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        className="run-detail results__panel"
        role="tabpanel"
        id="results-panel"
        aria-labelledby={`results-tab-${active}`}
        key={active}
      >
        {panel}
      </div>
    </div>
  )
}
