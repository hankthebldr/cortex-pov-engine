import React, { useEffect, useMemo, useRef, useState } from 'react'
import InflightView, { runCaveatLines } from './InflightView.jsx'
import EvidenceView from './EvidenceView.jsx'
import DetectionStoryline, { isRunUnproven } from '../DetectionStoryline.jsx'
import CausalityGraph from '../CausalityGraph.jsx'
import RunTechniquesView from './RunTechniquesView.jsx'
import RunTopologyView from './RunTopologyView.jsx'
import RunCasePanel from './RunCasePanel.jsx'
import { runStatusToken, runStatusGlyph } from './runStatus.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { scenarioIdOf, idMatches } from '../../api/ids.js'
import { getRun } from '../../api/client.js'
import { hasAttestations, attestationLines } from '../../outcomes.js'
import { parseStepTranscript } from './useRunStepTail.js'
import { launchWarningsFor } from './launchWarnings.js'
import { runLifecycle, PHASE, NOTHING, NOT_EXECUTED_COPY } from './runLifecycle.js'
import RunLifecycleStrip from './RunLifecycleStrip.jsx'
import ReadBackNotice from './ReadBackNotice.jsx'
import ReadBackButton from './ReadBackButton.jsx'
import TenantEvidencePanel from './TenantEvidencePanel.jsx'
import RunChain from './RunChain.jsx'
import RunVerdictLine from './RunVerdictLine.jsx'
import useReadBack from './useReadBack.jsx'
import { resultCell } from './RunList.jsx'
import { absoluteTime, runAgent } from './runRows.js'
import { trapTabKey } from './focusTrap.js'
import { Button } from './kit/index.js'
import '../../styles/destinations/run-detail.css'

// Launch/host markers that change what a missing detection MEANS. Both carry
// `step=<id>` and may sit outside a step's === STEP === block (the variant
// marker is written at launch, before any step runs), so they are matched by
// line rather than through the step parser — which stays the one reader of
// the per-step grammar (identity, exit code, duration).
const VARIANT_MARKER = /^!! PLATFORM VARIANT:\s*step=(\S+)\s*(.*)$/
const NOT_RUN_MARKER = /^!! STEP NOT RUN: PLATFORM_UNSATISFIABLE\s+step=(\S+)\s*(.*)$/
// Launch-time reservations about the TARGET, not about one step. SimCore writes
// TARGET_AGENT_OS_UNKNOWN into the transcript with the `!!` prefix; it returns
// TARGET_AGENT_OFFLINE on the launch response without one. Both spellings are
// read, so the day the offline marker is written to Run.output it shows here.
const LAUNCH_MARKER = /^(?:!!\s*)?(TARGET_AGENT_[A-Z0-9_]+):\s*(.*)$/

/**
 * What in this run's transcript says its steps did not run as authored.
 * Pure. Identity comes from `parseStepTranscript` (useRunStepTail.js) — reused,
 * not re-implemented, so a beacon format change fails one parser test.
 */
export function runDegradations(output) {
  const parsed = parseStepTranscript(output)
  const identity = parsed.order
    .map((id) => ({ id, ...parsed.steps[id] }))
    .filter((s) => s.identityNote)
  const total = parsed.order.reduce((m, id) => Math.max(m, parsed.steps[id].total || 0), 0)
    || parsed.order.length

  const variant = []
  const notRun = []
  const launch = []
  const seen = { variant: new Set(), notRun: new Set(), launch: new Set() }
  if (typeof output === 'string') {
    for (const raw of output.split('\n')) {
      const line = raw.replace(/\r$/, '').trim()
      let m = VARIANT_MARKER.exec(line)
      if (m && !seen.variant.has(m[1])) {
        seen.variant.add(m[1])
        variant.push({ id: m[1], note: m[2] || '' })
        continue
      }
      m = NOT_RUN_MARKER.exec(line)
      if (m && !seen.notRun.has(m[1])) {
        seen.notRun.add(m[1])
        notRun.push({ id: m[1], note: m[2] || '' })
        continue
      }
      m = LAUNCH_MARKER.exec(line)
      if (m && !seen.launch.has(m[1])) {
        seen.launch.add(m[1])
        launch.push({ code: m[1], text: m[2] || '', source: 'record' })
      }
    }
  }
  return { identity, total, variant, notRun, launch }
}

/**
 * The launch warnings to show for a run: what the run RECORD carries, then
 * anything this browser session heard on the launch response that the record
 * does not (`source: 'session'`). One row per code. Step-level markers
 * (PLATFORM VARIANT) are left to their own banner.
 */
export function mergeLaunchWarnings(fromRecord, fromSession) {
  const out = [...(fromRecord || [])]
  const have = new Set(out.map((w) => w.code))
  for (const w of fromSession || []) {
    if (!w || !w.code || !/^TARGET_AGENT_/.test(w.code) || have.has(w.code)) continue
    have.add(w.code)
    out.push({ code: w.code, text: w.text || '', source: 'session' })
  }
  return out
}

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
 *   subTab     — 'live' | 'evidence' | 'storyline' | 'techniques' | 'topology' | 'causality'
 *   onSubTab   — (tab) => void   switch sub-tab (writes ?tab=)
 *   onBack     — () => void      return to the run list
 *   onAbort    — ({ runId, confirmed: true }) => void   abort THIS run. The
 *                                confirm is inline on this page ("Stop this run
 *                                on the agent?" · Keep running · Abort now), so
 *                                the shell receives an already-confirmed request.
 *   onNavigate — (destination, params?) => void   Open results / Run
 *   onError    — (msg) => void
 *
 * Where the run is (design v26 §4.6) leads the page: the five-stage strip, one
 * line saying what it means, and the one action for the phase — Read back on a
 * finished run nobody has read back, Open results once it has been. Before the
 * read back there is no detection claim on this page: the Evidence tab shows
 * why there are no results and offers Read back instead of a scorecard.
 */

/**
 * The three ways a step can finish without having run as the scenario authored
 * it, each said ONCE at the top of the run — before any proof surface invites a
 * coverage reading of the steps it names.
 *
 *   identity  — `!! IDENTITY NOT HONOURED`: the step ran as the beacon's own
 *               account. On Windows that is every non-direct identity.
 *   variant   — `!! PLATFORM VARIANT`: the step ran its per-OS variant, and its
 *               expected detections were authored against the primary command.
 *   not run   — `!! STEP NOT RUN: PLATFORM_UNSATISFIABLE`: the host refused the
 *               step; its detections cannot have fired.
 *
 * `partial`: only the summary row's output tail was readable, so a step named
 * early in the transcript may be missing from these counts — said, not hidden.
 */
function RunDegradationBanners({ degraded, launchWarnings = [], partial = false }) {
  const { identity, total, variant, notRun } = degraded
  if (!identity.length && !variant.length && !notRun.length && !launchWarnings.length) return null
  const partialNote = partial && (
    <p className="run-attest__lede">
      Read from the last 2 KB of this run&apos;s output only — the full transcript could not be
      loaded, so these counts may be low.
    </p>
  )
  const sessionOnly = launchWarnings.some((w) => w.source === 'session')
  return (
    <>
      {launchWarnings.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-launch-warnings-banner">
          <div>
            {`SimCore accepted this launch with ${launchWarnings.length === 1 ? 'a warning' : `${launchWarnings.length} warnings`} `
              + 'about the target — read before treating a silent run as missed detections'}
          </div>
          <ul className="run-attest__list">
            {launchWarnings.map((w) => (
              <li key={w.code}>
                <span className="mono">{w.code}</span>{w.text ? ` — ${w.text}` : ''}
                {w.source === 'session' ? ' (from the launch response)' : ''}
              </li>
            ))}
          </ul>
          {sessionOnly && (
            <p className="run-attest__lede">
              A warning marked “from the launch response” is not in the run record: this browser
              session kept it from the launch. Another browser, or an export of this run, will not show it.
            </p>
          )}
          {partialNote}
        </div>
      )}
      {identity.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-identity-banner">
          <div>
            {`${identity.length} of ${Math.max(total, identity.length)} steps ran as the beacon account, `
              + 'not the authored identity — lineage-keyed misses on these steps are not a coverage '
              + 'measurement'}
          </div>
          <ul className="run-attest__list">
            {identity.map((s) => (
              <li key={s.id}>
                <span className="mono">{s.id}</span>
                {s.identity ? <> · authored <span className="mono">{s.identity}</span></> : null}
                {` — ${s.identityNote}`}
              </li>
            ))}
          </ul>
          {partialNote}
        </div>
      )}
      {variant.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-platform-variant-banner">
          <div>
            {`${variant.length} step${variant.length === 1 ? '' : 's'} ran a platform variant, not the `
              + 'authored command — expected detections unverified for this OS'}
          </div>
          <ul className="run-attest__list">
            {variant.map((s) => (
              <li key={s.id}><span className="mono">{s.id}</span>{s.note ? ` — ${s.note}` : ''}</li>
            ))}
          </ul>
          {partialNote}
        </div>
      )}
      {notRun.length > 0 && (
        <div className="run-severed run-degraded" role="status" data-testid="run-platform-notrun-banner">
          <div>
            {`${notRun.length} step${notRun.length === 1 ? '' : 's'} never ran on this host `
              + '(PLATFORM_UNSATISFIABLE) — their detections could not fire, so a miss on them is not '
              + 'a coverage measurement'}
          </div>
          <ul className="run-attest__list">
            {notRun.map((s) => (
              <li key={s.id}><span className="mono">{s.id}</span>{s.note ? ` — ${s.note}` : ''}</li>
            ))}
          </ul>
          {partialNote}
        </div>
      )}
    </>
  )
}

/** One line under each of the five stages on the run's page. */
function stageSubs(run, lifecycle, { activeRun, isThisActive, detail }) {
  const reported = Array.isArray(detail?.step_outcomes) ? detail.step_outcomes.length : null
  const running = lifecycle.phase === PHASE.RUNNING
  const res = resultCell(run, lifecycle)
  return [
    absoluteTime(run?.started_at) || 'start unknown',
    running
      ? (isThisActive && activeRun?.totalSteps ? `step ${activeRun.step} of ${activeRun.totalSteps}` : 'in progress')
      : lifecycle.phase === PHASE.NOTHING
        ? (lifecycle.nothing === NOTHING.NOT_EXECUTED ? NOT_EXECUTED_COPY.step : 'no step ran')
        : reported ? `${reported} step${reported === 1 ? '' : 's'} reported` : 'ran',
    lifecycle.ended ? (absoluteTime(run?.completed_at) || lifecycle.ended.toLowerCase()) : 'not yet',
    lifecycle.phase === PHASE.READY ? `from ${lifecycle.readBack.from}`
      : lifecycle.phase === PHASE.READING ? 'reading…' : 'not yet',
    lifecycle.phase === PHASE.READY ? res.text : 'none yet',
  ]
}

/** What the phase means, in one sentence. */
function lifecycleMessage(run, lifecycle) {
  const agent = runAgent(run)
  switch (lifecycle.phase) {
    case PHASE.RUNNING:
      return `Running on ${agent}. It can be read back from the tenant once it finishes.`
    case PHASE.NOTHING:
      return lifecycle.nothing === NOTHING.NOT_EXECUTED
        ? NOT_EXECUTED_COPY.message
        : 'No step reached its agent, so there is nothing to read back. Check the agent and run it again.'
    case PHASE.READING:
      return 'Reading alerts back from the tenant and matching them to what this run expected…'
    case PHASE.UNREAD:
      return `${lifecycle.ended || 'Ended'}. Nothing has been read back from the tenant yet, so there are no results.`
    default: {
      const res = resultCell(run, lifecycle)
      if (res.tone === 'unproven') {
        return `The run did not complete, so what was read back from ${lifecycle.readBack.from} is not a measurement of the tenant.`
      }
      const expected = run?.expected_detections
      if (typeof expected === 'number' && expected > 0 && typeof run.detected_count === 'number') {
        return `${run.detected_count} of ${expected} expected detections were seen in the tenant (read back from ${lifecycle.readBack.from}).`
      }
      return `Read back from ${lifecycle.readBack.from}.`
    }
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

export const RUN_SUBTABS = [
  { id: 'live',      label: 'Live'      },
  { id: 'evidence',  label: 'Evidence'  },
  { id: 'storyline', label: 'Storyline' },
  // Techniques and the Storyline are the SAME run in two vocabularies. The
  // storyline is the narrative a DC walks a room through; this is the table a
  // customer's detection engineer audits. Collapsing them gave a page that was
  // too narrative to audit and too tabular to follow.
  { id: 'techniques', label: 'Techniques' },
  // The generated lane topology: which ingestion doors this run actually
  // touched, and where the causality crossed between them.
  { id: 'topology', label: 'Topology' },
  { id: 'causality', label: 'Causality' },
]

export default function RunDetailView({
  runId,
  run = null,
  activeRun = null,
  subTab = 'live',
  onSubTab = () => {},
  onBack = () => {},
  onAbort = () => {},
  onNavigate = () => {},
  onError = () => {},
}) {
  const tab = RUN_SUBTABS.some((t) => t.id === subTab) ? subTab : 'live'
  const { scenarios } = useEnvironment()
  const { reading, outcomes, readBack } = useReadBack()

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

  // The run RECORD, for its full output. The list row this view is handed comes
  // from the console's 10 s poll, which asks for `fields=summary` and so carries
  // no `output` (OPS-06) — reading the transcript off it would show "no output
  // captured" for a run that captured plenty, and no degradation banners at all.
  // An older SimCore returns the full row; that still works, because a fetched
  // record only replaces the row's output when it is recognisably this run's.
  const [detail, setDetail] = useState(null)
  useEffect(() => { setDetail(null) }, [runId])
  useEffect(() => {
    if (!runId) return undefined
    let cancelled = false
    Promise.resolve()
      .then(() => getRun(runId))
      .then((d) => {
        if (cancelled) return
        const isThisRun = d && typeof d === 'object' && typeof d.output === 'string'
          && (d.run_id == null || idMatches(d.run_id, runId))
        // Keep the previous record on an unusable answer: a transient bad read
        // must not blank banners the operator was already reading.
        if (isThisRun) setDetail(d)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [runId, run?.status, run?.output_bytes])

  const { output, outputFrom } = useMemo(() => {
    const rowOutput = typeof run?.output === 'string' ? run.output : null
    if (typeof detail?.output === 'string') {
      // The transcript only ever grows. On an older SimCore the polled row
      // still carries full output and can be newer than the record fetched at
      // mount — take whichever has more, never the stale one.
      if (rowOutput !== null && rowOutput.length > detail.output.length) {
        return { output: rowOutput, outputFrom: 'row' }
      }
      return { output: detail.output, outputFrom: 'record' }
    }
    if (rowOutput !== null) return { output: rowOutput, outputFrom: 'row' }
    if (typeof run?.output_tail === 'string' && run.output_tail) {
      return { output: run.output_tail, outputFrom: 'tail' }
    }
    return { output: null, outputFrom: null }
  }, [detail, run])
  const degraded = useMemo(() => runDegradations(output), [output])
  const launchWarnings = useMemo(
    () => mergeLaunchWarnings(degraded.launch, launchWarningsFor(runId)),
    [degraded.launch, runId],
  )
  // The Live tab's Screenshot captures only its own region, so the same
  // degradations ride inside it as short lines (UI-09).
  const caveats = useMemo(() => runCaveatLines(degraded, launchWarnings), [degraded, launchWarnings])
  const partial = outputFrom === 'tail'

  const isThisActive = !!(activeRun && activeRun.runId === runId)
  const statusToken = runStatusToken(run?.status)
  // The polled row, filled in from the fetched record (delivery stamp, output)
  // where the row is thin, so the phase is read from everything SimCore said.
  const lcRun = useMemo(() => (run ? { ...(detail || {}), ...run } : detail), [run, detail])
  const lifecycle = useMemo(
    () => (lcRun ? runLifecycle(lcRun, { reading: reading.has(runId) }) : null),
    [lcRun, reading, runId],
  )
  const canAbort = isThisActive || statusToken === 'running'
  const failed = isRunUnproven(run?.status)
  const failureTail = failed ? tailOutput(output) : null
  const attest = hasAttestations(run) ? attestationLines(run) : null

  if (!runId) {
    return (
      <div className="runs-surface run-detail run-detail--empty">
        <div className="view-head">
          <div><h1>Run detail</h1></div>
          <button type="button" className="run-detail__back" onClick={onBack}>← All runs</button>
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
      <button type="button" className="run-detail__back" onClick={onBack}>← All runs</button>

      <div className="view-head run-detail__head">
        <div className="run-detail__head-main">
          <div className="run-detail__accent-bar" aria-hidden="true" />
          {/* Masthead eyebrow: the nav group alone ("Operate", per this
              destination's registry entry), matching every other redesigned
              destination — not the destination's own name (M-4). */}
          {/* NOT a phase eyebrow. Every other masthead names its phase,
              because a phase is a property of the SURFACE. A run detail's
              eyebrow has a better job: it carries the facts that differ
              between two runs of the same workflow, so two attempts are
              distinguishable at a glance. A generic "PHASE 5 · OBSERVE" here
              told a DC something they already knew from the rail. */}
          <div className="run-detail__eyebrow">
            {descriptor.scenarioId || 'run'} · {run?.started_at || 'start unknown'}
          </div>
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
        {/* The run's own page is where a DC watches it go wrong, so it is
            where they stop it — confirmed in place, then handed to the shell's
            abort path. Beside it, the one action for the run's phase. */}
        <div className="run-detail__actions">
          {canAbort && <AbortControl runId={runId} onAbort={onAbort} />}
          {lifecycle?.phase === PHASE.UNREAD && (
            <Button variant="primary" onClick={() => readBack(runId)}>Read back</Button>
          )}
          {lifecycle?.phase === PHASE.READING && (
            <Button variant="secondary" disabled>Reading…</Button>
          )}
          {lifecycle?.phase === PHASE.READY && (
            <Button variant="primary" onClick={() => onNavigate('proof', { run: runId })}>Open results</Button>
          )}
          {lifecycle?.phase === PHASE.NOTHING && descriptor.scenarioId && (
            <Button variant="primary" onClick={() => onNavigate('preflight', { arm: descriptor.scenarioId })}>Run</Button>
          )}
        </div>
      </div>

      {lifecycle && (
        <div className="run-lifecycle" data-testid="run-lifecycle">
          <RunLifecycleStrip
            lifecycle={lifecycle}
            variant="full"
            subs={stageSubs(lcRun, lifecycle, { activeRun, isThisActive, detail })}
          />
          <p className="run-lifecycle__msg" data-testid="run-lifecycle-message">
            {lifecycleMessage(lcRun, lifecycle)}
          </p>
          {lifecycle.phase === PHASE.UNREAD && outcomes.get(runId) && !outcomes.get(runId).ok && (
            <p className="run-lifecycle__why" role="status">{outcomes.get(runId).message}</p>
          )}
          <RunVerdictLine run={lcRun} variant="header" />
        </div>
      )}

      {/* The chain leads the page (design v26 §9.3): ChainStage, live, fed by
          this run's steps, the host's record and — once read back — its
          results. */}
      {lifecycle && (
        <RunChain runId={runId} run={lcRun} lifecycle={lifecycle} onNavigate={onNavigate} />
      )}
      <RunVerdictLine run={lcRun} variant="engine" />

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

      {run?.severed_at_step && (
        <div className="run-severed" role="status" data-testid="run-severed-banner">
          {`Chain severed at ${run.severed_at_step}`}
        </div>
      )}

      <RunDegradationBanners degraded={degraded} launchWarnings={launchWarnings} partial={partial} />

      {attest && (
        <section className="run-attest" aria-label="Attestations" data-testid="run-attestations">
          <h2 className="run-attest__h">Attestations</h2>
          <p className="run-attest__lede">
            Operator declarations at launch. SimCore did not observe or verify any of these.
          </p>
          <ul className="run-attest__list mono">
            <li>{attest.profile}</li>
            <li>{attest.bas}</li>
            <li>{attest.soakless}</li>
            <li>{attest.canary}</li>
          </ul>
        </section>
      )}

      {/* Read back here only where a verdict is already on screen, and not on the
          Evidence tab, whose tenant evidence panel carries the one button. */}
      <RunCasePanel
        ledger={run?.case_ledger ?? null}
        reconcileAction={lifecycle?.phase === PHASE.READY && tab !== 'evidence'
          ? <ReadBackButton runId={runId} /> : null}
      />

      <div
        className="lab__segmented run-detail__subtabs"
        role="tablist"
        aria-label="Run detail views"
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
            caveats={caveats}
            onError={onError}
          />
        )}
        {tab === 'evidence' && (lifecycle && lifecycle.phase !== PHASE.READY ? (
          <>
            <ReadBackNotice
              lifecycle={lifecycle}
              outcome={outcomes.get(runId)}
              onReadBack={() => readBack(runId)}
            />
            <TenantEvidencePanel runId={runId} showCaseLedger={false} />
          </>
        ) : (
          <EvidenceView
            activeRun={isThisActive ? activeRun : null}
            lastRun={isThisActive ? null : descriptor}
            pinnedRun={descriptor}
            showCaseLedger={false}
            onError={onError}
          />
        ))}
        {tab === 'storyline' && (
          <DetectionStoryline
            runId={runId}
            scenarioId={descriptor.scenarioId}
            onOpenEvidence={() => onSubTab('evidence')}
            onError={onError}
          />
        )}
        {tab === 'techniques' && (
          <RunTechniquesView
            runId={runId}
            run={run}
            onOpenEvidence={() => onSubTab('evidence')}
            onError={onError}
          />
        )}
        {tab === 'topology' && (
          <RunTopologyView
            runId={runId}
            run={run}
            onOpenEvidence={() => onSubTab('evidence')}
            onOpenCausality={() => onSubTab('causality')}
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
