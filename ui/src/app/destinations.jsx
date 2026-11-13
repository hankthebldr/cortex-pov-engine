import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { openParam, uctcOpen } from './openParam.js'

import ConsoleRail from '../components/console/ConsoleRail.jsx'
import TelemetryStrip from '../components/console/TelemetryStrip.jsx'
import { SurfaceBoundary } from '../components/console/SurfaceError.jsx'

import { useEnvironment } from '../context/EnvironmentContext.jsx'
import { getScenario } from '../api/client.js'
import { isRunTerminal } from '../components/console/runStatus.js'
import RunList from '../components/console/RunList.jsx'
import { absoluteTime, detectionCell, runAgent } from '../components/console/runRows.js'
import { runIdOf, idMatches } from '../api/ids.js'
import { agentIsOnline } from '../components/console/setupProgress.js'
import { COMPONENTS } from '../components/console/componentModel.js'
import { componentRoute } from './componentRoutes.js'

/**
 * A surface's last reported failure, visible and dismissable.
 *
 * The Library and guided-flow mounts used to pass `onError={() => {}}`, so a
 * failure reported through it — a scenario that would not load, a refused push
 * bundle, a launch the server turned down — reached nothing on screen. The
 * views that own a button also render its refusal beside it; this notice is the
 * floor for everything else (a scenario-detail fetch that failed) and the
 * record of the last failure when the drawer that showed it has closed.
 */
export function useSurfaceNotice() {
  const [notice, setNotice] = useState(null) // { message, tone }
  const report = useCallback((message, tone = 'error') => {
    const text = typeof message === 'string' ? message : (message?.message || String(message || ''))
    if (text) setNotice({ message: text, tone })
  }, [])
  const clear = useCallback(() => setNotice(null), [])
  return { notice, report, clear }
}

export function SurfaceNotice({ notice, onDismiss, testId = 'surface-notice' }) {
  if (!notice) return null
  const isError = notice.tone !== 'info' && notice.tone !== 'warn'
  return (
    <div
      className={`launch-result launch-result--${isError ? 'error' : 'pending'} surface-notice`}
      role={isError ? 'alert' : 'status'}
      data-testid={testId}
    >
      <span className="surface-notice__message">{notice.message}</span>{' '}
      <button type="button" className="btn btn--xs" onClick={onDismiss} aria-label="Dismiss message">
        Dismiss
      </button>
    </div>
  )
}

export function DestinationLoading() {
  return (
    <div className="destination-loading" role="status" aria-live="polite">
      loading…
    </div>
  )
}

/**
 * Tags a rejected `factory()` as a chunk-load failure. The ONLY thing that
 * can reject this specific promise is the `import()` itself — a 404 on the
 * hashed chunk URL, a network failure, a parse error in the fetched module
 * — never a render error from inside the resolved component (that happens
 * later, outside this try/catch, and is caught by SurfaceBoundary same as
 * any other throw). That makes the tag exact, unlike sniffing the
 * browser-specific message text ("Failed to fetch dynamically imported
 * module" / "error loading dynamically imported module" / "Importing a
 * module script failed").
 */
function lazyRetriable(factory) {
  return lazy(() =>
    factory().catch((err) => {
      const wrapped = err instanceof Error ? err : new Error(String(err))
      wrapped.isChunkLoadError = true
      throw wrapped
    }),
  )
}

/**
 * A lazy destination chunk whose failure is RECOVERABLE, not a dead end.
 *
 * React caches a `lazy()` component's outcome — success OR rejection —
 * forever. Before this, `SurfaceBoundary`'s "Retry" cleared its own local
 * error state and re-rendered the SAME `lazy()` object; on a rejected
 * import that replayed the identical cached error every time, since
 * nothing ever called the import factory again (verified: the loader's
 * call count stayed at 1 across repeated Retry clicks).
 *
 * `attempt` fixes that by forcing `useMemo` to build a brand-new `lazy()` —
 * and therefore make a brand-new `import()` call — on every retry. The
 * inner `SurfaceBoundary` sees `isChunkLoadError` and offers "Try again"
 * (re-invoke the import; recovers a one-off network blip) alongside
 * "Reload app" (full page reload; the actual fix when this tab's
 * index.html is stale after a redeploy, which "Try again" alone cannot
 * repair — the new chunk hash isn't in this tab's manifest at all). An
 * ordinary render throw from an already-loaded surface still gets the
 * plain "Retry" copy, unchanged.
 */
export function makeLazySurface(loader, title) {
  return function LazySurfaceMount(props) {
    const [attempt, setAttempt] = useState(0)
    const Comp = useMemo(() => lazyRetriable(loader), [attempt])
    return (
      <SurfaceBoundary
        key={attempt}
        resetKey={attempt}
        title={title}
        onRetryImport={() => setAttempt((a) => a + 1)}
      >
        <Suspense fallback={<DestinationLoading />}>
          <Comp {...props} />
        </Suspense>
      </SurfaceBoundary>
    )
  }
}

/**
 * Destination-level code splitting.
 *
 * The console has 14 destinations and a session typically visits two or
 * three of them, so eagerly bundling every surface into the entry chunk pays
 * — in parse/eval time on every load — for content most sessions never open.
 * Each surface below is a lazy `import()` chunk instead of a static import,
 * wrapped by `makeLazySurface` so a mount site gets its own loading state
 * AND a recoverable failure state, without having to know whether the
 * component behind it is lazy.
 *
 * `ConsoleRail` stays a static import: it is the Library-scoped plane/pinned
 * filter rail, mounted in the same paint as the default destination, so
 * splitting it out would only add a chunk round-trip with no benefit.
 */
const OperationsView = makeLazySurface(() => import('../components/console/OperationsView.jsx'), 'Scenarios')
const ComposerView = makeLazySurface(() => import('../components/console/ComposerView.jsx'), 'Composer')
const LaunchView = makeLazySurface(() => import('../components/console/LaunchView.jsx'), 'New POV run')
const TargetsView = makeLazySurface(() => import('../components/console/TargetsView.jsx'), 'Agents')
const RunDetailView = makeLazySurface(() => import('../components/console/RunDetailView.jsx'), 'Runs')
const MultiRunCompare = makeLazySurface(() => import('../components/console/MultiRunCompare.jsx'), 'Runs · Compare')
const CoverageView = makeLazySurface(() => import('../components/console/CoverageView.jsx'), 'Coverage')
const TtpBrowserView = makeLazySurface(() => import('../components/console/TtpBrowserView.jsx'), 'TTP cards')
const ToolAdapterCatalog = makeLazySurface(() => import('../components/console/ToolAdapterCatalog.jsx'), 'Packages')
const UcTcIndexView = makeLazySurface(() => import('../components/console/UcTcIndexView.jsx'), 'UC / TC Index')
const LabView = makeLazySurface(() => import('../components/console/LabView.jsx'), 'Lab')
const ReadinessView = makeLazySurface(() => import('../components/console/ReadinessView.jsx'), 'Launch Gate')
const EalConsole = makeLazySurface(() => import('../components/EalConsole.jsx'), 'Traffic / EAL')
const DataStreamsView = makeLazySurface(() => import('../components/console/DataStreamsView.jsx'), 'Data Streams')
const EvidenceView = makeLazySurface(() => import('../components/console/EvidenceView.jsx'), 'Proof & Export')

// ── Surfaces the redesign adds ───────────────────────────────────────────────
// These five had no destination before: the front door, the guided setup, the
// component inventory, the authored CLI items, and the tenant read-back. Each
// was a thing the console already implied and never gave you a place to do.
const OverviewView = makeLazySurface(() => import('../components/console/OverviewView.jsx'), 'Overview')
const GetStartedView = makeLazySurface(() => import('../components/console/GetStartedView.jsx'), 'Get started')
// The hub for the eight components (design v26 §9.2), rebuilt on real state.
const ComponentsView = makeLazySurface(() => import('../components/console/ComponentsView.jsx'), 'Components')
// One page shape per component (v26 §4.2): Overview · Setup · management tab.
const AgentsComponent = makeLazySurface(() => import('../components/console/component/AgentsComponent.jsx'), 'Agents')
const TenantComponent = makeLazySurface(() => import('../components/console/component/TenantComponent.jsx'), 'Tenant')
const FirewallComponent = makeLazySurface(() => import('../components/console/component/FirewallComponent.jsx'), 'Firewall')
const PovengineComponent = makeLazySurface(() => import('../components/console/component/PovengineComponent.jsx'), 'POVengine')
const HttpCollectorComponent = makeLazySurface(() => import('../components/console/component/HttpCollectorComponent.jsx'), 'HTTP collector')
// The three SimCore cannot see share one module; each is its own page.
const confirmedPage = (name) => () => import('../components/console/component/ConfirmedComponents.jsx').then((m) => ({ default: m[name] }))
const BrokerVmComponent = makeLazySurface(confirmedPage('BrokerVmComponent'), 'Broker VM')
const XdrCollectorComponent = makeLazySurface(confirmedPage('XdrCollectorComponent'), 'XDR Collector')
const CortexEngineComponent = makeLazySurface(confirmedPage('CortexEngineComponent'), 'Cortex Engine')

/** A surface that only forwards to another destination, for ids whose page
 *  was retired. `extra` params are merged over the ones the old link carried,
 *  so `#/agents?open=x` lands on the Agents component's Fleet tab with `open`
 *  intact. */
function redirectTo(target, extra = null) {
  return function Redirect({ params = {}, onNavigate = () => {} }) {
    useEffect(() => {
      const next = { ...(params || {}), ...(extra || {}) }
      if (Object.keys(next).length) onNavigate(target, next)
      else onNavigate(target)
    }, [onNavigate]) // eslint-disable-line react-hooks/exhaustive-deps
    return null
  }
}

/** Wrap a lazily-loaded surface in its own Suspense boundary, so a mount
 * site never has to know whether the component behind it is lazy.
 * `makeLazySurface` surfaces already carry their own Suspense + retry
 * boundary, so this is now a passthrough kept for call-site stability. */
function withSuspense(LazyComponent) {
  return LazyComponent
}

/**
 * The composed payload plan rides the URL from Tools & Payloads (see
 * `ToolAdapterCatalog.jsx::encodePlan`). Decoding it needs that module's
 * `decodePlan` export — resolved via the same dynamic import used for the
 * lazy component above, so a guided-flow deep link that carries no `plan`
 * param (the common case) never pulls the 700-line catalog module in at all.
 *
 * The dynamic import means the plan is NOT available on first paint even when
 * `encoded` is present — there is a real window where the chunk is still in
 * flight. Callers must be able to tell "no plan was ever composed" apart from
 * "a plan was composed and is still loading": collapsing the two would let a
 * consultant launch mid-race with the payload plan silently dropped (I-1). So
 * this returns `{ plan, resolving }` rather than a bare, ambiguous `plan` —
 * a zero here is degraded, not ok.
 */
export function useDecodedPlan(encoded) {
  const [state, setState] = useState(() => (
    encoded ? { plan: null, resolving: true } : { plan: null, resolving: false }
  ))
  useEffect(() => {
    if (!encoded) { setState({ plan: null, resolving: false }); return undefined }
    let cancelled = false
    setState({ plan: null, resolving: true })
    import('../components/console/ToolAdapterCatalog.jsx').then((mod) => {
      if (!cancelled) setState({ plan: mod.decodePlan(encoded), resolving: false })
    })
    return () => { cancelled = true }
  }, [encoded])
  return state
}

/**
 * destinations.js — the single destination REGISTRY.
 *
 * One place that defines every first-class console destination: id, label,
 * group, icon, default route, and the surface component to mount. It drives
 * the DestinationNav, the router shell, AND the ⌘K command palette — so adding
 * a destination is a one-file edit.
 *
 * Surface components are mounted with a stable prop contract:
 *     ({ params, setParams, onNavigate, onExit? }) => JSX
 *   - params      — surface-local query params from the hash router
 *   - setParams   — merge/replace this surface's params (deep-linkable)
 *   - onNavigate  — (destinationId, params?) => void  cross-surface jumps
 *   - onExit      — OPTIONAL. () => void, passed only to immersive
 *                   destinations (`immersive: true` below); returns to the
 *                   previous non-immersive destination, with the params it
 *                   had (fallback: the destination's `exitTo`, else its
 *                   `navParent`, for a cold deep link with no previous page). Undefined for every
 *                   other surface, so a surface can render its Exit control
 *                   only when it is actually in a mode there is a way out of.
 *
 * `immersive: true` puts the shell in immersive mode while that destination is
 * open: the header and the banners above/below it stay (consent and SimCore
 * health are never hidden), the nav rail and the task tab strip are not
 * rendered, and the surface fills the workspace edge to edge with no padding
 * and no page scroll. It is derived from the route on every render and is
 * never persisted — reloading a non-immersive page can never land in it.
 *
 * Surfaces read ambient scope (tenant/agent/scenarios/runs/…) from the
 * EnvironmentProvider via useEnvironment — never prop-drilled. Where a surface
 * will be upgraded later by a dedicated agent, it currently mounts its existing
 * view component (re-homed, not rewritten).
 */

// ─── Library surface ────────────────────────────────────────────────────────
// FLAGSHIP scale surface. Re-homes OperationsView (self-fetching scenario grid
// + inspector + launch hook) and demotes ConsoleRail into the Library-scoped
// plane/pinned FILTER column (no longer the global nav).
function LibrarySurface({ params = {}, onNavigate = () => {} }) {
  const { planes, pinnedIds, isPinned, togglePin, unpin, scenarios } = useEnvironment()
  const [selectedPlane, setSelectedPlane] = useState(null)
  const [techniqueFilter, setTechniqueFilter] = useState(null)
  const armedRef = useRef(null)
  const surface = useSurfaceNotice()

  const railPlanes = useMemo(
    () => planes.map((p) => ({ ...p, isActive: selectedPlane === p.code })),
    [planes, selectedPlane],
  )

  const pinned = useMemo(() => {
    if (!pinnedIds.length) return []
    const byId = new Map(scenarios.map((s) => [s.scenario_id || s.id, s]))
    return pinnedIds.map((id) => ({ id, name: byId.get(id)?.name || id }))
  }, [pinnedIds, scenarios])

  const handleSelectPlane = useCallback((code) => {
    setSelectedPlane((prev) => (prev === code ? null : code))
  }, [])

  return (
    <div
      className="library-layout"
    >
      <ConsoleRail
        planes={railPlanes}
        pinned={pinned}
        onSelectPlane={handleSelectPlane}
        onSelectPinned={(id) => { armedRef.current = id; onNavigate('guided', { arm: id }) }}
        onUnpin={unpin}
      />
      <Suspense fallback={<DestinationLoading />}>
        <SurfaceNotice notice={surface.notice} onDismiss={surface.clear} testId="library-notice" />
        <OperationsView
          onNavigate={onNavigate}
          selectedPlane={selectedPlane}
          onClearPlane={() => setSelectedPlane(null)}
          techniqueFilter={techniqueFilter}
          onClearTechniqueFilter={() => setTechniqueFilter(null)}
          requestOpenScenarioId={openParam(params)}
          pinnedIds={pinnedIds}
          isPinned={isPinned}
          togglePin={togglePin}
          onArmScenario={(sid) => { armedRef.current = sid }}
          onContinueToLaunch={() => onNavigate('guided', { arm: armedRef.current })}
          onOpenRunEvidence={(run) =>
            onNavigate('runs', { run: runIdOf(run), tab: 'evidence' })}
          onRunComplete={(run) =>
            onNavigate('runs', { run: runIdOf(run), tab: 'live' })}
          onError={(msg) => surface.report(msg, 'error')}
          onSurfaceMessage={(msg, tone = 'info') => surface.report(msg, tone)}
        />
      </Suspense>
    </div>
  )
}

// ─── Guided "New POV run" flow (optional, not in primary nav) ────────────────
// The valuable first-run demo path: pick a target, then fire the armed
// scenario. Reachable from Library's Arm / Continue-to-Launch and from ⌘K.
function GuidedPovFlow({ params = {}, onNavigate = () => {} }) {
  const armId = params.arm || null
  const [scenario, setScenario] = useState(null)
  const [selectedTarget, setSelectedTarget] = useState(null)
  const { refreshRuns, agent } = useEnvironment()
  // Default the target to the active agent when it is online. Arriving from
  // Get started with a healthy beacon and being met by "Target: BLOCKED — no
  // target selected" put a red blocker one click after the console said what
  // to do next. The DC can still pick a different target below; this only
  // fills the empty state, it never overrides a choice.
  useEffect(() => {
    if (selectedTarget || !agent) return
    const id = agent.agent_id || agent.id
    if (id && agentIsOnline(agent)) {
      setSelectedTarget({ kind: 'agent', id, label: agent.hostname || id })
    }
  }, [agent, selectedTarget])
  // A composed payload plan arrives in the URL from Tools & Payloads. Decoding
  // here (rather than re-composing) keeps the launch reload-safe and means the
  // exact digests the DC saw are the ones the launch carries. `resolving` is a
  // DISTINCT state from "no plan" — see useDecodedPlan (I-1) — and must reach
  // LaunchView so Launch stays disabled until the decode settles.
  const { plan: payloadPlan, resolving: payloadPlanResolving } = useDecodedPlan(params.plan || null)
  const surface = useSurfaceNotice()

  useEffect(() => {
    if (!armId) { setScenario(null); return undefined }
    let cancelled = false
    getScenario(armId)
      .then((d) => { if (!cancelled) setScenario(d || null) })
      .catch((err) => {
        if (cancelled) return
        setScenario(null)
        // Without this the page said "Arm a scenario from Simulate to begin"
        // for a scenario that WAS armed and failed to load — the wrong fix.
        surface.report(`Could not load armed scenario ${armId}: ${err?.message || 'request failed'}`)
      })
    return () => { cancelled = true }
  }, [armId]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="guided-flow">
      <div className="view-head">
        <div>
          <h1>New POV run</h1>
          <div className="view-head__meta">
            {scenario
              ? <>Armed: <strong className="mono">{scenario.scenario_id || scenario.id}</strong> · {scenario.name}</>
              : <>Arm a scenario from <button className="linklike guided-flow__library-link" onClick={() => onNavigate('library')}>Scenarios</button> to begin.</>}
          </div>
        </div>
      </div>

      <SurfaceNotice notice={surface.notice} onDismiss={surface.clear} testId="guided-notice" />

      <Suspense fallback={<DestinationLoading />}>
        <TargetsView
          embedded
          selectedTarget={selectedTarget}
          onSelectTarget={setSelectedTarget}
          onGoToLab={() => onNavigate('environments')}
        />
      </Suspense>

      {scenario && (
        <Suspense fallback={<DestinationLoading />}>
          <LaunchView
            scenario={scenario}
            payloadPlan={payloadPlan}
            payloadPlanResolving={payloadPlanResolving}
            selectedTarget={selectedTarget}
            onRunComplete={(run) => {
              surface.clear()
              refreshRuns()
              onNavigate('runs', { run: runIdOf(run), tab: 'live' })
            }}
            // LaunchView already renders the refusal as an alert beside the
            // button that was clicked; the notice is its polite echo at the
            // top of the flow, so it is not announced twice.
            onError={(msg) => surface.report(msg, 'warn')}
            onGoLibrary={() => onNavigate('library')}
            onGoTargets={() => {}}
          />
        </Suspense>
      )}
    </div>
  )
}

// ─── Runs surface ────────────────────────────────────────────────────────────
// Run history list → single Run Detail surface keyed by runId with
// Live / Evidence / Storyline / Causality SUB-tabs (collapses the four former
// top-level tabs, extracted into RunDetailView). Multi-run compare via ?compare=1.
function RunsSurface({ params = {}, setParams = () => {}, onAbortRun = () => {} }) {
  const { runs, activeRun } = useEnvironment()
  // The live-run telemetry used to be a permanent shell row above every
  // destination. It is a property of the RUN, so it belongs on the run's own
  // surface — the header pill already answers "is something in flight" from
  // anywhere, and it reads the same derived activeRun this does, so the two
  // cannot disagree.
  const runId = openParam(params, 'run')
  const subTab = params.tab || 'live'
  const compare = params.compare === '1'

  const selected = useMemo(() => {
    if (!runId) return null
    return runs.find((r) => idMatches(runIdOf(r), runId)) || null
  }, [runs, runId])

  if (compare) {
    return (
      <div className="runs-surface">
        <div className="view-head">
          <div><h1>Runs · Compare</h1></div>
          <button className="btn" onClick={() => setParams({ compare: null }, { replace: true })}>← Back to runs</button>
        </div>
        <Suspense fallback={<DestinationLoading />}>
          <MultiRunCompare />
        </Suspense>
      </div>
    )
  }

  if (!runId) {
    return (
      <div className="runs-surface">
        <div className="view-head">
          <div>
            {/* "Runs", not "Runs & Proof". Proof & Export is its own
                destination now, so a title claiming both would disagree with
                the rail entry that opened this page. */}
            <h1>Runs</h1>
            <div className="view-head__meta"><span className="mono">{runs.length}</span> runs</div>
          </div>
          <button className="btn" onClick={() => setParams({ compare: '1' }, { replace: true })}>Compare runs</button>
        </div>
        {activeRun && <TelemetryStrip run={activeRun} onAbort={onAbortRun} />}
        {/* "What ran last" leads the page: the first question on this surface
            is never "list every run", it is "what is happening / what just
            happened". The run list stays below, unchanged. */}
        <LastRunCard
          onOpen={(id, tab) => setParams({ run: id, tab }, { replace: true })}
        />
        <ScopeHealthStrip />
        <RunList
          runs={runs}
          params={params}
          setParams={setParams}
          onOpen={(id) => setParams({ run: id, tab: 'live', scenario: null, status: null, page: null }, { replace: true })}
        />
      </div>
    )
  }

  return (
    <Suspense fallback={<DestinationLoading />}>
      <RunDetailView
        runId={runId}
        run={selected}
        activeRun={activeRun}
        subTab={subTab}
        onSubTab={(tab) => setParams({ tab })}
        onBack={() => setParams({ run: null, tab: null }, { replace: true })}
        onAbort={onAbortRun}
        onError={() => {}}
      />
    </Suspense>
  )
}

/**
 * LastRunCard — "Running now" or "Last run", above the run table.
 *
 * Reads the SAME derived `activeRun` / `runs` the header pill and the telemetry
 * strip read, so the three cannot disagree about what is in flight. Every field
 * is a real value off the run record; where SimCore did not report one (a run
 * with no `started_at`, a detection count the API omits) the card prints an
 * explicit dash rather than a zero — a fabricated "0 detections" on a run that
 * actually detected things is the kind of number that ends up in a customer
 * readout.
 */
function LastRunCard({ onOpen = () => {} }) {
  const { runs, activeRun } = useEnvironment()

  const running = activeRun
    ? runs.find((r) => idMatches(runIdOf(r), activeRun.runId)) || null
    : null
  const shown = running || runs.find((r) => r && isRunTerminal(r.status)) || null

  if (!shown) {
    return (
      <div className="last-run last-run--empty" data-testid="last-run-card">
        No run has completed on this SimCore yet. Build a chain, or open a scenario
        from Scenarios and launch it.
      </div>
    )
  }

  const id = runIdOf(shown)
  const isLive = !!running
  const stats = [
    ['Status', shown.status || 'unknown'],
    // `detectionCell` carries the truthfulness rule: a failed run with no hit
    // reads `unproven`, a run with no Result rows reads a dash.
    ['Detections', detectionCell(shown).text],
    ['Started', absoluteTime(shown.started_at) || '—'],
    // The agent is the run's `target` column — this card used to read two
    // field names that no response ever carried.
    ['Agent', runAgent(shown)],
  ]

  return (
    <button
      type="button"
      className={'last-run' + (isLive ? ' last-run--live' : '')}
      data-testid="last-run-card"
      onClick={() => onOpen(id, isLive ? 'live' : 'evidence')}
    >
      <div className="last-run__head">
        <span className="last-run__pulse" aria-hidden="true" />
        <span className="last-run__eyebrow">{isLive ? 'Running now' : 'Last run'}</span>
        <span className="mono last-run__id">{id}</span>
        <span className="last-run__spacer" />
        <span className="mono last-run__cta">
          {isLive ? 'Follow the live run →' : 'Open the evidence →'}
        </span>
      </div>
      <div className="last-run__title">
        {shown.scenario_id || '(scenario unknown)'}
      </div>
      <div className="last-run__stats">
        {stats.map(([k, v]) => (
          <span className="last-run__stat" key={k}>
            <span className="last-run__stat-k">{k}</span>
            <span className="mono last-run__stat-v">{v}</span>
          </span>
        ))}
      </div>
    </button>
  )
}

/**
 * ScopeHealthStrip — tenant · agent · component health, on the surface where
 * they matter.
 *
 * The redesign's argument for putting these here rather than only in the global
 * bar: scope is a property of the RUN, and this is the page where a DC asks
 * "why did that not detect?" — at which point "which tenant was that against,
 * and was the cloud sensor up?" is the next question. The header switchers stay
 * where they are; this is a read-only echo of the same provider state, not a
 * second place to change it.
 */
function ScopeHealthStrip() {
  const { tenant, agent, healthModel } = useEnvironment()

  const degraded = healthModel?.degraded?.length ?? null
  const tiles = [
    { k: 'Tenant', v: tenant ? (tenant.name || tenant.id) : 'none selected',
      note: tenant?.config?.region || tenant?.region || 'no region reported',
      ok: !!tenant },
    { k: 'Agent', v: agent ? (agent.hostname || agentIdOf(agent)) : 'none selected',
      note: agent ? [agent.os, agent.status].filter(Boolean).join(' · ') || 'beacon' : 'pull-mode launches need one',
      ok: !!agent },
    { k: 'Readiness',
      v: healthModel == null ? 'not probed'
        : !healthModel.reachable ? 'unreachable'
          : degraded === 0 ? 'all components ok' : `${degraded} degraded`,
      note: healthModel == null ? 'no /api/health answer yet'
        : healthModel.gaps?.length ? `${healthModel.gaps.length} not reported by this build` : 'reported by /api/health',
      ok: healthModel?.reachable === true && degraded === 0 },
  ]

  return (
    <div className="scope-health" data-testid="scope-health-strip">
      {tiles.map((t) => (
        <div className="scope-health__tile" key={t.k}>
          <div className="scope-health__label">
            <span className={'scope-health__dot' + (t.ok ? ' scope-health__dot--ok' : '')} />
            {t.k}
          </div>
          <div className="mono scope-health__value">{t.v}</div>
          <div className="scope-health__note">{t.note}</div>
        </div>
      ))}
    </div>
  )
}

// ─── Thin re-home wrappers for self-sourcing surfaces ────────────────────────
// Each of these mounts exactly one lazy component with a straight props
// pass-through, so `withSuspense` covers both the lazy-load and the boundary.
const CoverageSurface = withSuspense(CoverageView)
// `params` + `setParams` carry the grid's organization (`?group=&sort=&include=`)
// so "by subdomain" survives a trip into the UC / TC index and back.
function TtpsSurface({ params = {}, setParams, onNavigate = () => {} }) {
  return (
    <Suspense fallback={<DestinationLoading />}>
      <TtpBrowserView
        initialTtpId={openParam(params, 'ttp')}
        onNavigate={onNavigate}
        params={params}
        setParams={setParams}
      />
    </Suspense>
  )
}
// "Packages" — the tool catalog plus the payload shelf. Staging state is a
// PROPERTY of a package, not a new noun, so it lives on this destination
// rather than its own: splitting them would list `linpeas.sh` in one place and
// `TOOL-LINPEAS` in another and make the DC hold the join. Renamed from
// "Tools & Payloads" because the rail now names it by what a DC deploys.
const AdaptersSurface = withSuspense(ToolAdapterCatalog)
// Deep-linkable: #/uctc?tab=index&uc=UC-EDR&tc=TC-EDR-03 — and, like every
// surface, `?open=<id>`, told apart by prefix because the index has two id
// spaces (see `uctcOpen`).
function UcTcSurface({ params = {}, ...rest }) {
  const { uc, tc } = uctcOpen(params)
  return (
    <Suspense fallback={<DestinationLoading />}>
      <UcTcIndexView {...rest} params={{ ...params, uc, tc }} />
    </Suspense>
  )
}
const EalSurface = withSuspense(EalConsole)
const DataStreamsSurface = withSuspense(DataStreamsView)
const EnvironmentsSurface = withSuspense(LabView)
// The Launch Gate: "will this chain actually reach the target?". Deliberately
// NOT the default destination — a health page that greets you every morning
// stops being read.
const ReadinessSurface = withSuspense(ReadinessView)
// Agents, Tenant and Firewall are component pages now (component/*.jsx); their
// former surfaces live on as those pages' management tabs: TargetsView is
// Agents › Fleet, TenantManager + CollectorTargetsPanel are Tenant ›
// Read-back & integrations, and the firewall steps are Firewall's Setup and
// Sources (docs/superpowers/specs/2026-11-10-ngfw-connection-wizard-design.md).
// Proof & Export is its own destination now. It used to be a sub-tab of a run,
// which meant the artefact a POV actually delivers was two clicks inside the
// thing that produced it. EvidenceView takes its run as props rather than
// reading the provider, so this wrapper supplies the same active/last run the
// header pill and the telemetry read — the three cannot disagree — and lets a
// `?run=` deep link pin a specific one.
function ProofSurface({ params = {} }) {
  const { runs, activeRun, lastRun } = useEnvironment()
  const runParam = openParam(params, 'run')
  const pinnedRun = useMemo(() => {
    if (!runParam) return null
    return runs.find((r) => idMatches(runIdOf(r), runParam)) || null
  }, [runs, runParam])
  return (
    <Suspense fallback={<DestinationLoading />}>
      <EvidenceView
        activeRun={activeRun}
        lastRun={lastRun}
        pinnedRun={pinnedRun ? { runId: runIdOf(pinnedRun), scenarioId: pinnedRun.scenario_id, status: pinnedRun.status } : null}
        onError={() => {}}
      />
    </Suspense>
  )
}

// ─── Registry ─────────────────────────────────────────────────────────────────
//
// THE RAIL IS THE THREE JOBS A DC DOES, IN ORDER (design v26 §1).
//
//   1  Onboard & connect    Get started · Components ▸ (the eight components)
//   2  Compose & simulate   Scenarios · Build a chain · Launch gate
//   3  Run & observe        Runs · Results
//      Reference            Catalog
//
// Every page belongs to exactly one rail item. Simulate's three tabs became
// rail items of their own, and the Manage group became Components: an
// expandable submenu of all eight components, each with its status.
//
// NOTHING WAS DELETED. Every former destination is still routable (deep links,
// bookmarks and ⌘K resolve), retired ids forward to the page that replaced
// them, and `navParent` says which rail item lights up while an off-rail page
// is open. `SECTIONS` below is the one place the tab strips are defined — the
// rail, the tab strip and the router all read this file.
//
// `icon` is a real PANW line icon under /icons — never a Unicode glyph.
export const JOURNEYS = [
  { id: 'onboard',   num: '1', label: 'Onboard & connect' },
  { id: 'compose',   num: '2', label: 'Compose & simulate' },
  { id: 'observe',   num: '3', label: 'Run & observe' },
  // Not a step in the journey: what the scenarios are made of.
  { id: 'reference', num: '',  label: 'Reference' },
]

export const DESTINATIONS = [
  // ── 1 · Onboard & connect ──
  { id: 'start',      label: 'Get started',   group: 'onboard',   icon: 'keyboard',       Component: GetStartedView, badge: 'setupLeft' },
  // `children: 'components'` makes this rail item expand into the eight
  // components (componentModel.js), each with its live status.
  { id: 'components', label: 'Components',    group: 'onboard',   icon: 'settings-edit',  Component: ComponentsView, badge: 'componentIssues', children: 'components' },

  // ── 2 · Compose & simulate ──
  // Library keeps its id: it is the route, the data-testid and the e2e key.
  { id: 'library',    label: 'Scenarios',     group: 'compose',   icon: 'line-chart',     Component: LibrarySurface },
  // Immersive: the Composer is a full-screen node-graph editor, not a page
  // inside the rail (docs/superpowers/specs/2026-10-21-composer-mode-design.md).
  // The rail is not drawn while it is open; a cold deep link's Exit lands on
  // Scenarios (`exitTo`), where the chains it forks from live.
  { id: 'composer',   label: 'Build a chain', group: 'compose',   icon: 'apps-grid',      Component: ComposerView, immersive: true, exitTo: 'library' },
  { id: 'preflight',  label: 'Launch gate',   group: 'compose',   icon: 'user-lock',      Component: ReadinessSurface },

  // ── 3 · Run & observe ──
  { id: 'runs',       label: 'Runs',          group: 'observe',   icon: 'donut-chart',    Component: RunsSurface,    badge: 'live' },
  { id: 'proof',      label: 'Results',       group: 'observe',   icon: 'bar-chart',      Component: ProofSurface },

  // ── Reference ──
  { id: 'ttps',       label: 'Catalog',       group: 'reference', icon: 'threat-warning', Component: TtpsSurface },

  // ── The components' own pages, reached from the Components submenu ──
  // One page shape each (v26 §4.2), in the submenu's order. The label is the
  // h1 and the submenu name.
  { id: 'component-pov',      label: 'POVengine',      hidden: true, navParent: 'components', icon: 'settings-edit',    Component: PovengineComponent },
  { id: 'component-agents',   label: 'Agents',         hidden: true, navParent: 'components', icon: 'fingerprint-scan', Component: AgentsComponent },
  { id: 'component-tenant',   label: 'Tenant',         hidden: true, navParent: 'components', icon: 'people',           Component: TenantComponent },
  { id: 'component-http',     label: 'HTTP collector', hidden: true, navParent: 'components', icon: 'doc-search',       Component: HttpCollectorComponent },
  { id: 'component-bvm',      label: 'Broker VM',      hidden: true, navParent: 'components', icon: 'building',         Component: BrokerVmComponent },
  // No shield icon ships under /icons; threat-network is the existing network glyph.
  { id: 'component-firewall', label: 'Firewall',       hidden: true, navParent: 'components', icon: 'threat-network',   Component: FirewallComponent },
  { id: 'component-xdrc',     label: 'XDR Collector',  hidden: true, navParent: 'components', icon: 'apps-grid',        Component: XdrCollectorComponent },
  { id: 'component-engine',   label: 'Cortex Engine',  hidden: true, navParent: 'components', icon: 'keyboard',         Component: CortexEngineComponent },
  // The Manage pages they replace (v26 §5). A bookmark lands on the
  // management tab the old page was, keeping its own params.
  { id: 'agents',    label: 'Agents',   hidden: true, redirect: true, navParent: 'components', icon: 'fingerprint-scan', Component: redirectTo('component-agents', { tab: 'fleet' }) },
  { id: 'tenants',   label: 'Tenant',   hidden: true, redirect: true, navParent: 'components', icon: 'people',           Component: redirectTo('component-tenant', { tab: 'readback' }) },
  { id: 'firewall',  label: 'Firewall', hidden: true, redirect: true, navParent: 'components', icon: 'threat-network',   Component: redirectTo('component-firewall', { tab: 'sources' }) },

  // ── Off-rail, reached as tabs or links ──
  { id: 'guided',    label: 'New POV run',       hidden: true, navParent: 'library', icon: 'apps-grid',      Component: GuidedPovFlow },
  { id: 'coverage',  label: 'Coverage',          hidden: true, navParent: 'proof',   icon: 'donut-chart',    Component: CoverageSurface },
  // id stays 'adapters' deliberately — it is the route; only the label moved.
  { id: 'adapters',  label: 'Packages',          hidden: true, navParent: 'ttps',    icon: 'settings-edit',  Component: AdaptersSurface },
  { id: 'streams',   label: 'Data streams',      hidden: true, navParent: 'ttps',    icon: 'threat-network', Component: DataStreamsSurface },
  { id: 'uctc',      label: 'UC / TC index',     hidden: true, navParent: 'ttps',    icon: 'bar-chart',      Component: UcTcSurface },
  { id: 'overview',  label: 'About POVengine',   hidden: true, navParent: 'start',   icon: 'doc-search',     Component: OverviewView },
  // Retired seed-only pages. The ids stay routable so an old link lands
  // somewhere sensible instead of silently falling back to the default.
  { id: 'setup',      label: 'Get started', hidden: true, redirect: true, navParent: 'start', icon: 'keyboard', Component: redirectTo('start') },
  { id: 'scope',      label: 'Get started', hidden: true, redirect: true, navParent: 'start', icon: 'keyboard', Component: redirectTo('start') },
  { id: 'cli',        label: 'Catalog',     hidden: true, redirect: true, navParent: 'ttps',  icon: 'password', Component: redirectTo('ttps') },
  { id: 'validation', label: 'Results',     hidden: true, redirect: true, navParent: 'proof', icon: 'person',   Component: redirectTo('proof') },
  { id: 'environments', label: 'Lab',            hidden: true, navParent: 'components', icon: 'building',     Component: EnvironmentsSurface },
  { id: 'eal',       label: 'Traffic / EAL',     hidden: true, navParent: 'library',   icon: 'threat-network', Component: EalSurface },
  // Legacy id for the Launch Gate, aliased rather than broken.
  { id: 'readiness', label: 'Launch gate',       hidden: true, navParent: 'preflight', icon: 'user-lock',      Component: ReadinessSurface },
]

/**
 * The tab strip a rail item shows across the top of its pages. A tab IS a
 * destination, so the strip navigates and holds no state of its own — the rail
 * and the strip cannot disagree about where you are.
 */
export const SECTIONS = {
  proof: [['proof', 'Report'], ['coverage', 'Coverage']],
  ttps: [['ttps', 'TTP cards'], ['adapters', 'Packages'], ['streams', 'Data streams'], ['uctc', 'UC / TC index']],
}

/** The rail item that represents a destination (itself, or its parent). */
export function navOwner(id) {
  const d = BY_ID.get(id)
  if (!d) return null
  return d.navParent || d.id
}

/** The tab set for a destination, or null when its rail item has no tabs. */
export function sectionFor(id) {
  const owner = navOwner(id)
  const tabs = SECTIONS[owner]
  if (!tabs) return null
  return { owner, tabs, active: id }
}

export const DEFAULT_DESTINATION = 'start'

/** Path to a destination's rail icon. One guarded place, because interpolating
 *  a missing id emitted `url("icons/undefined")` and fired a 404 per render. */
export function iconUrl(icon) {
  return icon ? `/icons/${icon}.png` : null
}

const BY_ID = new Map(DESTINATIONS.map((d) => [d.id, d]))

export function getDestination(id) {
  return BY_ID.get(id) || null
}

/** True only when `id` resolves to a destination flagged `immersive: true`. */
export function isImmersive(id) {
  return getDestination(id)?.immersive === true
}

export function isValidDestination(id) {
  return BY_ID.has(id)
}

/**
 * The rail: one group per journey, in journey order, each holding its
 * nav-visible destinations in registry order.
 *
 * @param {Object} badges       badge key → string | { text, variant: 'live' }
 * @param {Object} [opts]
 * @param {Object} [opts.components]  componentModel statuses by key; a child
 *                                    with none reads Unknown, never a guess
 */
export function navGroups(badges = {}, { components = null } = {}) {
  const byGroup = new Map()
  for (const d of DESTINATIONS) {
    if (d.hidden || !d.group) continue
    if (!byGroup.has(d.group)) byGroup.set(d.group, [])
    const badgeVal = d.badge ? badges[d.badge] : null
    const isLive = badgeVal && typeof badgeVal === 'object' && badgeVal.variant === 'live'
    const item = {
      id: d.id,
      label: d.label,
      icon: d.icon,
      iconUrl: iconUrl(d.icon),
      badge: isLive ? badgeVal.text : badgeVal,
      badgeVariant: isLive ? 'live' : undefined,
    }
    if (d.children === 'components') {
      item.children = COMPONENTS.map((c) => {
        const route = componentRoute(c.key)
        return {
          key: c.key,
          label: c.name,
          destination: route.destination,
          params: route.params,
          status: components?.[c.key] || null,
        }
      })
    }
    byGroup.get(d.group).push(item)
  }
  return JOURNEYS
    .filter((j) => byGroup.has(j.id))
    .map((j) => ({ num: j.num, label: j.label, items: byGroup.get(j.id) }))
}
