/**
 * RunTopologyView — the Topology tab on a run.
 *
 * Five lane bands — the ingestion doors the Composer bands by — with each of
 * the run's steps drawn in its execution-order column inside the lane
 * `composerLanes.laneOf` derives for it, and the Cortex Engine pinned as the
 * terminal on the right holding the Correlation / Analytics / ABIOC rows and
 * the run verdict. A lane no step launched through is drawn dimmed and says
 * so; a step whose door cannot be derived is listed, never guessed.
 *
 * Live records only: the scenario's steps (door + names), the run record via
 * `useRunStepTail` (host rc / termination, beacon identity + duration, polled
 * while the run is live), and the run's `Result` rows (tones).
 */
import React, { useEffect, useMemo, useState } from 'react'
import { getResultsForRun, getScenario } from '../../api/client.js'
import useRunStepTail, { STEP_STATUS_LABEL } from './useRunStepTail.js'
import { buildTopologyModel, toLaneStep } from './runTopology.js'
import { isRunUnproven } from './runStatus.js'

const EVIDENCE_SENTENCE =
  'Lanes are the doors the scenario’s steps declare; status is the host’s record; tones are this SimCore’s Result rows. Authored is not proven.'

function rowLabel(r) {
  return r.detection_name || r.detection_id || r.signal_type || `result ${r.id}`
}

export default function RunTopologyView({
  runId,
  run: runProp = null,
  onOpenEvidence = () => {},
  onOpenCausality = () => {},
  onError = () => {},
}) {
  const [scenario, setScenario] = useState(null)
  const [scenarioError, setScenarioError] = useState(null)
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)

  const laneSteps = useMemo(
    () => (Array.isArray(scenario?.steps) ? scenario.steps.map(toLaneStep) : []),
    [scenario],
  )
  const tail = useRunStepTail(runId, laneSteps)
  const run = tail.run || runProp
  const scenarioId = run?.scenario_id || null

  // The scenario, for doors and names. A run of a draft the corpus no longer
  // holds 404s here: the lanes are then rebuilt from the Result rows and the
  // page says so, rather than pretending the scenario was read.
  useEffect(() => {
    if (!scenarioId) return undefined
    let cancelled = false
    setScenario(null); setScenarioError(null)
    getScenario(scenarioId)
      .then((s) => { if (!cancelled) setScenario(s) })
      .catch((err) => { if (!cancelled) setScenarioError(err?.message || 'request failed') })
    return () => { cancelled = true }
  }, [scenarioId])

  // Results on mount and once more when the run goes terminal, so the final
  // outcomes (not_reached, the host's kills) land on the board.
  useEffect(() => {
    if (!runId) return undefined
    let cancelled = false
    getResultsForRun(runId)
      .then((res) => {
        if (cancelled) return
        setResults(Array.isArray(res) ? res : res?.results || [])
        setError(null)
      })
      .catch((err) => {
        if (cancelled) return
        const msg = err?.message || 'could not load results'
        setError(msg)
        onError(msg)
      })
    return () => { cancelled = true }
  }, [runId, tail.terminal])

  const model = useMemo(
    () => (results ? buildTopologyModel({ scenario, run, results, stepRows: tail.rows }) : null),
    [scenario, run, results, tail.rows],
  )

  if (!runId) return <div className="run-topo__empty mono">no run selected</div>
  if (error) return <div className="run-topo__empty mono" data-testid="topo-error">could not load results: {error}</div>
  if (!model) return <div className="run-topo__empty mono" role="status">loading topology…</div>

  const unproven = isRunUnproven(model.runStatus)
  const verdict = model.engine.tcVerdict || 'not scored'
  const verdictKey = model.engine.tcVerdict || 'unscored'
  const cols = Math.max(1, model.columns.length)
  const track = { gridTemplateColumns: `repeat(${cols}, minmax(136px, 1fr))` }

  return (
    <div className="run-topo" data-testid="run-topology" data-proven={model.proven ? 'true' : 'false'} data-steps-from={model.stepsFrom}>
      <div className="run-topo__head">
        <div className="run-topo__totals mono" data-testid="topo-totals">
          {model.totals.steps} step{model.totals.steps === 1 ? '' : 's'} · {model.totals.hits} of {model.totals.denominator} rows detected
          {' · '}{model.lanes.filter((l) => !l.empty).length} of {model.lanes.length} doors used
          {model.unplaced.length ? ` · ${model.unplaced.length} unplaced` : ''}
          {' · tenant-verified: 0'}
        </div>
        <p className="run-topo__sentence">{EVIDENCE_SENTENCE}</p>
        {model.stepsFrom === 'run' && (
          <p className="run-topo__note mono" data-testid="topo-scenario-note">
            scenario {scenarioId || '(unknown)'} not loaded{scenarioError ? `: ${scenarioError}` : ''} — lanes rebuilt from this run's Result rows, so an EAL step reads by its plane's door and step names are absent.
          </p>
        )}
        {unproven && (
          <p className="run-topo__unproven" data-testid="topo-unproven">
            This run {model.runStatus}: an un-fired detection is <b>pending</b>, not missed — the signal may never have been generated.
          </p>
        )}
      </div>

      <div className="run-topo__board">
        <div className="run-topo__lanes" role="table" aria-label="Lane topology">
          {model.lanes.map((l) => (
            <div
              key={l.key}
              className={`run-topo__lane run-topo__lane--${l.tone}${l.empty ? ' run-topo__lane--empty' : ''}`}
              role="row"
              data-testid="topo-lane"
              data-lane={l.key}
              data-empty={l.empty ? 'true' : 'false'}
              data-tone={l.tone}
            >
              <div className="run-topo__lane-head" role="rowheader">
                <span className="run-topo__lane-key mono">{l.key}</span>
                <span className="run-topo__lane-label">{l.label}</span>
                <span className="run-topo__lane-sub">{l.sub}</span>
                <span className="run-topo__lane-count mono">{l.empty ? '—' : `${l.hits}/${l.denominator}`}</span>
              </div>
              <div className="run-topo__track" style={track} role="cell">
                {l.empty ? (
                  <span className="run-topo__lane-none mono" style={{ gridColumn: `1 / span ${cols}` }}>
                    no step launched through this door
                  </span>
                ) : l.cells.map((c) => (
                  <div
                    key={c.id}
                    className={`run-topo__cell run-topo__cell--${c.tone} run-topo__cell--st-${c.status}`}
                    style={{ gridColumn: c.index }}
                    data-testid="topo-cell"
                    data-step-id={c.id}
                    data-lane={c.lane}
                    data-status={c.status}
                    data-tone={c.tone}
                    title={c.name || c.id}
                  >
                    <span className="run-topo__cell-id mono">{c.index}. {c.id}</span>
                    {c.name && <span className="run-topo__cell-name">{c.name}</span>}
                    <span className="run-topo__cell-meta mono">
                      <span className={`run-topo__st run-topo__st--${c.status}`}>{STEP_STATUS_LABEL[c.status] || c.status}</span>
                      {c.rc != null ? ` rc=${c.rc}` : ''}
                      {c.duration ? ` · ${c.duration}` : ''}
                    </span>
                    <span className="run-topo__cell-meta mono">
                      {c.identity ? `identity=${c.identity}` : 'identity —'}
                      {c.identityNote ? ' · NOT HONOURED' : ''}
                    </span>
                    <span className={`run-topo__cell-det mono run-topo__tone--${c.tone}`}>
                      {c.rows.length ? `${c.hits}/${c.denominator} detected` : 'no expectation'}
                      {c.engineRows.length ? ` · ${c.engineRows.length} → engine` : ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        <aside
          className={`run-topo__engine run-topo__engine--${model.engine.tone}${model.engine.empty ? ' run-topo__engine--empty' : ''}`}
          data-testid="topo-engine"
          data-tone={model.engine.tone}
          data-empty={model.engine.empty ? 'true' : 'false'}
          aria-label="Cortex Engine terminal"
        >
          <div className="run-topo__engine-head">
            <span className="run-topo__lane-key mono">{model.engine.key}</span>
            <span className="run-topo__lane-label">{model.engine.label}</span>
            <span className="run-topo__lane-sub">{model.engine.sub} · pinned terminal</span>
          </div>
          <div className="run-topo__engine-verdict">
            <span className="run-topo__k">Run verdict</span>
            <span className={`chip run-topo__verdict-chip run-topo__verdict-chip--${verdictKey}`} data-testid="topo-verdict">{verdict}</span>
          </div>
          {model.engine.tcVerdictDetail?.reason && (
            <div className="run-topo__engine-detail mono">{String(model.engine.tcVerdictDetail.reason)}</div>
          )}
          <div className="run-topo__engine-count mono">
            {model.engine.empty ? 'no Correlation / Analytics / ABIOC row on this run' : `${model.engine.hits}/${model.engine.denominator} engine rows detected`}
          </div>
          <ul className="run-topo__engine-rows">
            {model.engine.rows.map((r) => {
              const col = model.columns.find((c) => c.id === r.step_id)
              return (
                <li key={r.id} className={`mono run-topo__engine-row run-topo__tone--${col ? col.tone : 'pending'}`} data-testid="topo-engine-row" data-result-id={r.id}>
                  <span className="run-topo__engine-row-name">{rowLabel(r)}</span>
                  <span className="run-topo__engine-row-meta">{String(r.signal_type || '').toUpperCase()} · {r.step_id || '—'} · {r.outcome || (r.observed ? 'observed' : 'pending')}</span>
                </li>
              )
            })}
          </ul>
          <div className="run-topo__engine-foot mono">rows here are counted once, in their step's cell</div>
        </aside>
      </div>

      <div className="run-topo__crossings mono" data-testid="topo-crossings">
        {model.crossings.length
          ? <>door crossings: {model.crossings.map((x) => `${x.from} → ${x.to} at ${x.at}`).join(' · ')}</>
          : model.columns.length ? 'the chain stayed behind one door' : 'no step placed'}
      </div>

      {model.unplaced.length > 0 && (
        <div className="run-topo__unplaced" data-testid="topo-unplaced">
          <div className="run-topo__k">Unplaced — not drawn in a lane, with the reason</div>
          <ul>
            {model.unplaced.map((u) => (
              <li key={u.id} className="mono" data-testid="topo-unplaced-row">
                <span className="run-topo__unplaced-id">{u.id}</span> · {u.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="run-topo__foot">
        <span className="mono">lane = composerLanes.laneOf(step) · status = host step_outcomes · tenant-verified: 0</span>
        <span className="run-topo__foot-actions">
          <button type="button" className="btn btn--xs" onClick={onOpenCausality}>Causality ▸</button>
          <button type="button" className="btn btn--xs" onClick={onOpenEvidence}>Open the evidence ▸</button>
        </span>
      </div>
    </div>
  )
}
