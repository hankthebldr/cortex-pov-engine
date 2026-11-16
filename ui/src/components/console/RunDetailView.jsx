import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { agentIdOf, idMatches, scenarioIdOf } from '../../api/ids.js'
import { getScenario } from '../../api/client.js'
import { isRunSettled } from './runStatus.js'
import { launchWarningsFor } from './launchWarnings.js'
import { runLifecycle, PHASE, NOTHING, NOT_EXECUTED_COPY } from './runLifecycle.js'
import { runState } from './runState.js'
import { buildRuntimeLog } from './runtimeLog.js'
import { liveOutput } from './liveOutput.js'
import { runNotices, isNoticeLine } from './runNotices.js'
import useRunRecord from './useRunRecord.js'
import RunLifecycleStrip from './RunLifecycleStrip.jsx'
import RunChain from './RunChain.jsx'
import RuntimeLog from './RuntimeLog.jsx'
import RunNotices from './RunNotices.jsx'
import useReadBack from './useReadBack.jsx'
import { absoluteTime, runAgent } from './runRows.js'
import { trapTabKey } from './focusTrap.js'
import { Button, Icon, StatusIcon } from './kit/index.js'
import '../../styles/destinations/run-detail.css'

/**
 * RunDetailView — one run's page: what was attempted.
 *
 * Runs shows what was attempted; Results shows what Cortex saw. So this page
 * carries the run's lifecycle, its chain, Abort, and ONE panel — the runtime
 * log (a block per step: the command, how it went on the host, what it
 * printed). It says nothing about a detection: no verdict, no count, no
 * detection view. Those are Results', one click away once the run has been
 * reviewed, and a link that still names one of the old tabs
 * (`#/runs?run=<id>&tab=evidence`) is forwarded there by the Runs surface.
 *
 * Nothing here fetches the run list — the parent RunsView owns selection +
 * routing and passes the resolved run row down. The page reads the run's own
 * record (`useRunRecord`): the list rows carry no output, and a live run is
 * re-read as its event stream moves, so the chain and the log follow it.
 * Output a step streams while it runs is read from the record's tail and the
 * stream's chunks (liveOutput.js), and gives way to the step's stored frame.
 *
 * What SimCore said about the launch (an offline agent, a missing
 * interpreter, a beacon that does not run cleanup) is one line above the
 * chain (RunNotices), not raw lines in the log.
 *
 * Props:
 *   runId      — the selected run's id (string)
 *   run        — the resolved run row from the provider (or null if unknown)
 *   activeRun  — the provider's derived live run ({ runId, ... }) or null
 *   onBack     — () => void      return to the run list
 *   onAbort    — ({ runId, confirmed: true }) => void   abort THIS run. The
 *                                confirm is inline on this page ("Stop this run
 *                                on the agent?" · Keep running · Abort now), so
 *                                the shell receives an already-confirmed request.
 *   onNavigate — (destination, params?) => void   Open results / Run
 *
 * Where the run is (design v26 §4.6) leads the page: the five-stage strip, one
 * line saying what it means, and the one action for the phase — Review on a
 * finished run nobody has reviewed, Open results once it has been.
 */

/** When the run started: SimCore's timestamp as UTC, anything else as given. */
function startedLabel(value) {
  if (!value) return 'start unknown'
  return (/^\d{4}-\d{2}-\d{2}T/.test(String(value)) && absoluteTime(value)) || String(value)
}

/** One line under each of the five stages on the run's page. */
function stageSubs(run, lifecycle, { activeRun, isThisActive, reported }) {
  const running = lifecycle.phase === PHASE.RUNNING
  return [
    absoluteTime(run?.started_at) || 'start unknown',
    running
      ? (isThisActive && activeRun?.totalSteps ? `step ${activeRun.step} of ${activeRun.totalSteps}` : 'in progress')
      : lifecycle.phase === PHASE.NOTHING
        ? (lifecycle.nothing === NOTHING.NOT_EXECUTED ? NOT_EXECUTED_COPY.step : 'no step ran')
        : reported ? `${reported} step${reported === 1 ? '' : 's'} reported` : 'ran',
    lifecycle.ended ? (absoluteTime(run?.completed_at) || lifecycle.ended.toLowerCase()) : 'not yet',
    lifecycle.phase === PHASE.READY ? `via ${lifecycle.readBack.from}`
      : lifecycle.phase === PHASE.READING ? 'reviewing…' : 'not yet',
    lifecycle.phase === PHASE.READY ? 'ready' : 'none yet',
  ]
}

/** What the phase means, in one line. Never a detection count. */
function lifecycleMessage(run, lifecycle, state) {
  switch (lifecycle.phase) {
    case PHASE.RUNNING:
      return `Running on ${runAgent(run)}`
    case PHASE.NOTHING:
      return lifecycle.nothing === NOTHING.NOT_EXECUTED
        ? NOT_EXECUTED_COPY.message
        : 'Nothing reached the agent — check it'
    case PHASE.READING:
      return 'Reviewing against the tenant…'
    case PHASE.UNREAD:
      return `${state.word} · not reviewed`
    default:
      return `${state.word} · reviewed via ${lifecycle.readBack.from}`
  }
}

/**
 * Abort run, confirmed in place (design v26 §4.6, §8): the button becomes the
 * question and its two answers. The safe answer takes focus, Tab stays between
 * the two, and Escape backs out.
 */
function AbortControl({ runId, onAbort }) {
  const [asking, setAsking] = useState(false)
  const groupRef = useRef(null)
  useEffect(() => {
    if (!asking || !groupRef.current) return
    const keep = groupRef.current.querySelector('[data-abort-keep]')
    if (keep) keep.focus()
  }, [asking])
  if (!asking) {
    return <Button variant="secondary" className="run-abort" onClick={() => setAsking(true)}>Abort run</Button>
  }
  const onKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      setAsking(false)
    } else if (e.key === 'Tab') {
      trapTabKey(e, groupRef.current)
    }
  }
  return (
    <div className="run-abort-confirm" role="group" aria-label="Abort this run" ref={groupRef} onKeyDown={onKeyDown}>
      <span className="run-abort-confirm__q">Stop this run on the agent?</span>
      <Button variant="secondary" data-abort-keep="" onClick={() => setAsking(false)}>Keep running</Button>
      <Button
        variant="destructive"
        onClick={() => { setAsking(false); onAbort({ runId, confirmed: true }) }}
      >
        Abort now
      </Button>
    </div>
  )
}

export default function RunDetailView({
  runId,
  run = null,
  activeRun = null,
  onBack = () => {},
  onAbort = () => {},
  onNavigate = () => {},
}) {
  const { scenarios, agents } = useEnvironment()
  const { reading, outcomes, readBack } = useReadBack()
  const scenarioId = run?.scenario_id || null

  // Title the surface with what the run WAS, not its opaque uuid. A customer
  // reading "4ee09860-140d-…" as the heading learns nothing.
  const scenarioName = useMemo(() => {
    if (!scenarioId) return null
    const s = scenarios.find((x) => scenarioIdOf(x) === scenarioId)
    return s?.name || null
  }, [scenarios, scenarioId])

  // The run RECORD: its output and the host's step record, neither of which the
  // polled list row carries (OPS-06). Followed live until the run settles.
  const { record, failed: recordFailed, stream, events } = useRunRecord(runId, {
    settled: isRunSettled(run?.status),
    refreshKey: `${run?.status || ''}|${run?.output_bytes ?? ''}`,
  })

  // The run's scenario: the chain and the log both read its steps.
  const [scenario, setScenario] = useState(null)
  const [scenarioError, setScenarioError] = useState(null)
  useEffect(() => {
    setScenario(null)
    setScenarioError(null)
    if (!scenarioId) return undefined
    let cancelled = false
    getScenario(scenarioId)
      .then((s) => { if (!cancelled) setScenario(s || null) })
      .catch((e) => { if (!cancelled) setScenarioError(e?.message || 'could not load the scenario') })
    return () => { cancelled = true }
  }, [scenarioId])

  const { stored, partial } = useMemo(() => {
    const rowOutput = typeof run?.output === 'string' ? run.output : null
    if (typeof record?.output === 'string') {
      // The transcript only ever grows. On an older SimCore the polled row
      // still carries full output and can be newer than the record that was
      // read — take whichever has more, never the stale one.
      if (rowOutput !== null && rowOutput.length > record.output.length) return { stored: rowOutput, partial: false }
      return { stored: record.output, partial: false }
    }
    if (rowOutput !== null) return { stored: rowOutput, partial: false }
    if (typeof run?.output_tail === 'string' && run.output_tail) return { stored: run.output_tail, partial: true }
    return { stored: null, partial: false }
  }, [record, run])

  // What running steps have streamed so far, and the stored frames the stream
  // delivered since the record was last read.
  const streamed = useMemo(
    () => liveOutput({ tail: record?.live_tail, events, output: stored || '' }),
    [record, events, stored],
  )
  // A frame the stream delivered is in the transcript at once; the next read
  // of the record carries it too. Not onto a 2 KB tail: that is not the
  // transcript, so there is nothing to tell what it already holds.
  const output = stored !== null && !partial && streamed.stored ? stored + streamed.stored : stored

  // One run to read everything from: the record under the row (the row is the
  // newer word on the review), with whichever status is further along and the
  // fullest output.
  const liveRun = useMemo(() => {
    if (!run && !record) return null
    const merged = { ...(record || {}), ...(run || {}) }
    if (record && isRunSettled(record.status) && !isRunSettled(run?.status)) merged.status = record.status
    if (record && !merged.completed_at && record.completed_at) merged.completed_at = record.completed_at
    if (output !== null) merged.output = output
    return merged
  }, [run, record, output])

  const lifecycle = useMemo(
    () => (liveRun ? runLifecycle(liveRun, { reading: reading.has(runId) }) : null),
    [liveRun, reading, runId],
  )
  const state = useMemo(() => runState(liveRun, lifecycle), [liveRun, lifecycle])

  // A warning this browser heard on the launch response and the record does
  // not carry leads the log, so nothing SimCore said about the launch is lost.
  // The ones the page states as its notice are left to the notice.
  const log = useMemo(() => {
    const built = buildRuntimeLog({
      steps: scenario?.steps || [],
      run: liveRun,
      starts: [...(Array.isArray(record?.step_starts) ? record.step_starts : []), ...streamed.starts],
      live: streamed.text,
      cleanupCommands: scenario?.cleanup?.commands,
    })
    const said = built.preface.flat().join('\n')
    const heard = launchWarningsFor(runId)
      .filter((w) => w && w.code && !isNoticeLine(`${w.code}: `) && !said.includes(w.code))
      .map((w) => `${w.code}${w.text ? `: ${w.text}` : ''}`)
    return heard.length ? { ...built, preface: [heard, ...built.preface] } : built
  }, [scenario, liveRun, record, streamed, runId])

  // What SimCore said about the launch, and whether this beacon can stream.
  const notices = useMemo(() => {
    const pull = String(liveRun?.mode || '').toLowerCase() !== 'push'
    const agent = pull && liveRun?.target
      ? (agents || []).find((a) => idMatches(agentIdOf(a), liveRun.target)) || null
      : null
    return runNotices({
      output: typeof liveRun?.output === 'string' ? liveRun.output : '',
      heard: launchWarningsFor(runId),
      live: lifecycle?.phase === PHASE.RUNNING,
      agent,
    })
  }, [liveRun, lifecycle, agents, runId])

  const isThisActive = !!(activeRun && activeRun.runId === runId)
  const canAbort = lifecycle?.phase === PHASE.RUNNING
    && (isThisActive || String(liveRun?.status || '').toLowerCase() === 'running')
  const reported = Array.isArray(record?.step_outcomes) ? record.step_outcomes.length : null
  // Until the record is read there is nothing to say about a step — unless the
  // row itself carries output (an older SimCore, or its 2 KB tail).
  const loaded = !!record || output !== null

  if (!runId) {
    return (
      <div className="runs-surface run-detail run-detail--empty">
        <div className="view-head">
          <div><h1>Run</h1></div>
          <button type="button" className="run-detail__back" onClick={onBack}>
            <Icon name="back" size={12} />All runs
          </button>
        </div>
        <div className="run-detail__empty mono">no run selected</div>
      </div>
    )
  }

  return (
    // The shell (.theme-console, AppShell.jsx) is theme-aware and sets
    // [data-theme] on the root, so the PANW token set (--ac/--s1/--tx/…)
    // resolves correctly here without pinning a local override.
    <div className="runs-surface run-detail">
      <button type="button" className="run-detail__back" onClick={onBack}>
        <Icon name="back" size={12} />All runs
      </button>

      <div className="view-head run-detail__head">
        <div className="run-detail__head-main">
          <div className="run-detail__accent-bar" aria-hidden="true" />
          {/* NOT a phase eyebrow. A run's eyebrow carries the facts that differ
              between two runs of the same scenario, so two attempts are
              distinguishable at a glance. */}
          <div className="run-detail__eyebrow">
            {scenarioId || 'run'} · {startedLabel(liveRun?.started_at)}
          </div>
          <h1 className={scenarioName ? undefined : 'mono'}>
            {scenarioName || scenarioId || runId}
          </h1>
          <div className="view-head__meta">
            {scenarioName && scenarioId && (
              <><span className="mono">{scenarioId}</span>{' · '}</>
            )}
            run <span className="mono">{runId}</span>
            {liveRun?.mode && <> · <span className="mono">{liveRun.mode}</span></>}
            {liveRun?.target && <> · <span className="mono">{liveRun.target}</span></>}
          </div>
          {liveRun?.status && (
            <div className="run-detail__state" data-testid="run-state">
              <StatusIcon status={state.status} word={state.word} />
            </div>
          )}
        </div>
        {/* The run's own page is where a DC watches it go wrong, so it is
            where they stop it — confirmed in place, then handed to the shell's
            abort path. Beside it, the one action for the run's phase. */}
        <div className="run-detail__actions">
          {canAbort && <AbortControl runId={runId} onAbort={onAbort} />}
          {lifecycle?.phase === PHASE.UNREAD && (
            <Button variant="primary" verb="Review" onClick={() => readBack(runId)} />
          )}
          {lifecycle?.phase === PHASE.READING && (
            <Button variant="secondary" verb="Review" disabled>Reviewing…</Button>
          )}
          {lifecycle?.phase === PHASE.READY && (
            <Button variant="primary" verb="Open results" onClick={() => onNavigate('proof', { run: runId })} />
          )}
          {lifecycle?.phase === PHASE.NOTHING && scenarioId && (
            <Button variant="primary" onClick={() => onNavigate('preflight', { arm: scenarioId })}>Run</Button>
          )}
        </div>
      </div>

      {lifecycle && (
        <div className="run-lifecycle" data-testid="run-lifecycle">
          <RunLifecycleStrip
            lifecycle={lifecycle}
            variant="full"
            subs={stageSubs(liveRun, lifecycle, { activeRun, isThisActive, reported })}
          />
          <p className="run-lifecycle__msg" data-testid="run-lifecycle-message">
            {lifecycleMessage(liveRun, lifecycle, state)}
          </p>
          {lifecycle.phase === PHASE.UNREAD && outcomes.get(runId) && !outcomes.get(runId).ok && (
            <p className="run-lifecycle__why" role="status">{outcomes.get(runId).message}</p>
          )}
        </div>
      )}

      <RunNotices notices={notices} />

      {/* The chain leads the page (design v26 §9.3): ChainStage, live, each
          card saying how its step went on the host. */}
      {scenarioId && (
        <RunChain
          runId={runId}
          scenario={scenario}
          scenarioError={scenarioError}
          blocks={log.blocks}
          live={loaded}
          onNavigate={onNavigate}
        />
      )}

      <RuntimeLog log={log} loaded={loaded} failed={recordFailed} stream={stream} partial={partial} />
    </div>
  )
}
