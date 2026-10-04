import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import ConsoleRail from '../components/console/ConsoleRail.jsx'
import TelemetryStrip from '../components/console/TelemetryStrip.jsx'
import { SurfaceBoundary } from '../components/console/SurfaceError.jsx'

import { useEnvironment } from '../context/EnvironmentContext.jsx'
import { getScenario } from '../api/client.js'
import { isRunTerminal } from '../components/console/runStatus.js'
import { runIdOf, idMatches } from '../api/ids.js'
import { agentIsOnline } from '../components/console/setupProgress.js'

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
const OperationsView = makeLazySurface(() => import('../components/console/OperationsView.jsx'), 'Library')
const ComposerView = makeLazySurface(() => import('../components/console/ComposerView.jsx'), 'Composer')
const LaunchView = makeLazySurface(() => import('../components/console/LaunchView.jsx'), 'New POV run')
const TargetsView = makeLazySurface(() => import('../components/console/TargetsView.jsx'), 'Agents')
const RunDetailView = makeLazySurface(() => import('../components/console/RunDetailView.jsx'), 'Runs')
const MultiRunCompare = makeLazySurface(() => import('../components/console/MultiRunCompare.jsx'), 'Runs · Compare')
const CoverageView = makeLazySurface(() => import('../components/console/CoverageView.jsx'), 'Coverage')
const TtpBrowserView = makeLazySurface(() => import('../components/console/TtpBrowserView.jsx'), 'TTP Cards')
const ToolAdapterCatalog = makeLazySurface(() => import('../components/console/ToolAdapterCatalog.jsx'), 'Packages')
const UcTcIndexView = makeLazySurface(() => import('../components/console/UcTcIndexView.jsx'), 'UC / TC Index')
const LabView = makeLazySurface(() => import('../components/console/LabView.jsx'), 'Lab')
const TenantView = makeLazySurface(() => import('../components/console/TenantView.jsx'), 'Tenant')
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

/** A surface that only forwards to another destination (replacing the
 *  history entry), for ids whose page was retired. */
function redirectTo(target) {
  return function Redirect({ onNavigate = () => {} }) {
    useEffect(() => { onNavigate(target) }, [onNavigate])
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
 *     ({ params, setParams, onNavigate }) => JSX
 *   - params      — surface-local query params from the hash router
 *   - setParams   — merge/replace this surface's params (deep-linkable)
 *   - onNavigate  — (destinationId, params?) => void  cross-surface jumps
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
        <OperationsView
          onNavigate={onNavigate}
          selectedPlane={selectedPlane}
          onClearPlane={() => setSelectedPlane(null)}
          techniqueFilter={techniqueFilter}
          onClearTechniqueFilter={() => setTechniqueFilter(null)}
          requestOpenScenarioId={params.open || null}
          pinnedIds={pinnedIds}
          isPinned={isPinned}
          togglePin={togglePin}
          onArmScenario={(sid) => { armedRef.current = sid }}
          onContinueToLaunch={() => onNavigate('guided', { arm: armedRef.current })}
          onOpenRunEvidence={(run) =>
            onNavigate('runs', { run: runIdOf(run), tab: 'evidence' })}
          onRunComplete={(run) =>
            onNavigate('runs', { run: runIdOf(run), tab: 'live' })}
          onError={() => {}}
          onSurfaceMessage={() => {}}
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

  useEffect(() => {
    if (!armId) { setScenario(null); return undefined }
    let cancelled = false
    getScenario(armId)
      .then((d) => { if (!cancelled) setScenario(d || null) })
      .catch(() => { if (!cancelled) setScenario(null) })
    return () => { cancelled = true }
  }, [armId])

  return (
    <div className="guided-flow">
      <div className="view-head">
        <div>
          <h1>New POV run</h1>
          <div className="view-head__meta">
            {scenario
              ? <>Armed: <strong className="mono">{scenario.scenario_id || scenario.id}</strong> · {scenario.name}</>
              : <>Arm a scenario from <button className="linklike guided-flow__library-link" onClick={() => onNavigate('library')}>Simulate</button> to begin.</>}
          </div>
        </div>
      </div>

      <Suspense fallback={<DestinationLoading />}>
        <TargetsView
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
              refreshRuns()
              onNavigate('runs', { run: runIdOf(run), tab: 'live' })
            }}
            onError={() => {}}
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
function RunsSurface({ params = {}, setParams = () => {} }) {
  const { runs, activeRun } = useEnvironment()
  // The live-run telemetry used to be a permanent shell row above every
  // destination. It is a property of the RUN, so it belongs on the run's own
  // surface — the header pill already answers "is something in flight" from
  // anywhere, and it reads the same derived activeRun this does, so the two
  // cannot disagree.
  const runId = params.run || null
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
        {activeRun && <TelemetryStrip run={activeRun} />}
        {/* "What ran last" leads the page: the first question on this surface
            is never "list every run", it is "what is happening / what just
            happened". The run list stays below, unchanged. */}
        <LastRunCard
          onOpen={(id, tab) => setParams({ run: id, tab }, { replace: true })}
        />
        <ScopeHealthStrip />
        <RunList runs={runs} onOpen={(id) => setParams({ run: id, tab: 'live' }, { replace: true })} />
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
        No run has completed on this SimCore yet. Compose a chain, or open a scenario
        from Simulate and launch it.
      </div>
    )
  }

  const id = runIdOf(shown)
  const isLive = !!running
  const stats = [
    ['Status', shown.status || 'unknown'],
    ['Detections', shown.detected_count != null && shown.expected_detections != null
      ? `${shown.detected_count} / ${shown.expected_detections}` : '—'],
    ['Started', shown.started_at || '—'],
    ['Agent', shown.target_agent_id || shown.agent_id || '—'],
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

function RunList({ runs = [], onOpen = () => {} }) {
  if (!runs.length) {
    return (
      <div className="run-list-empty">
        no runs yet — launch a scenario from Simulate
      </div>
    )
  }
  return (
    <div className="run-list" role="table" aria-label="Run history">
      {runs.map((r) => {
        const id = runIdOf(r)
        const terminal = isRunTerminal(r.status)
        const live = r.status === 'running'
        return (
          <button
            key={id}
            type="button"
            role="row"
            className="run-list__row"
            onClick={() => onOpen(id)}
          >
            <span className="mono run-list__scenario">{r.scenario_id || id}</span>
            <span className="mono run-list__id">{id}</span>
            <span
              className={'chip run-list__status-chip' + (live ? ' run-list__status-chip--live' : terminal ? ' run-list__status-chip--done' : ' run-list__status-chip--pending')}
            >
              {r.status || 'unknown'}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ─── Thin re-home wrappers for self-sourcing surfaces ────────────────────────
// Each of these mounts exactly one lazy component with a straight props
// pass-through, so `withSuspense` covers both the lazy-load and the boundary.
const CoverageSurface = withSuspense(CoverageView)
function TtpsSurface({ params = {}, onNavigate = () => {} }) {
  return (
    <Suspense fallback={<DestinationLoading />}>
      <TtpBrowserView initialTtpId={params.ttp || null} onNavigate={onNavigate} />
    </Suspense>
  )
}
// "Packages" — the tool catalog plus the payload shelf. Staging state is a
// PROPERTY of a package, not a new noun, so it lives on this destination
// rather than its own: splitting them would list `linpeas.sh` in one place and
// `TOOL-LINPEAS` in another and make the DC hold the join. Renamed from
// "Tools & Payloads" because the rail now names it by what a DC deploys.
const AdaptersSurface = withSuspense(ToolAdapterCatalog)
// Deep-linkable: #/uctc?tab=index&uc=UC-EDR&tc=TC-EDR-03
const UcTcSurface = withSuspense(UcTcIndexView)
const EalSurface = withSuspense(EalConsole)
const DataStreamsSurface = withSuspense(DataStreamsView)
const EnvironmentsSurface = withSuspense(LabView)
// The Launch Gate: "will this chain actually reach the target?". Deliberately
// NOT the default destination — a health page that greets you every morning
// stops being read.
const ReadinessSurface = withSuspense(ReadinessView)
const AgentsSurface = withSuspense(TargetsView)
// One tenant per instance, in four tabs. The registration wizard is not gone
// — it is the Binding tab inside TenantView, because binding a tenant is a real
// operation that has to happen once, it just stopped being the whole page.
const TenantsSurface = withSuspense(TenantView)
// Proof & Export is its own destination now. It used to be a sub-tab of a run,
// which meant the artefact a POV actually delivers was two clicks inside the
// thing that produced it. EvidenceView takes its run as props rather than
// reading the provider, so this wrapper supplies the same active/last run the
// header pill and the telemetry read — the three cannot disagree — and lets a
// `?run=` deep link pin a specific one.
function ProofSurface({ params = {} }) {
  const { runs, activeRun, lastRun } = useEnvironment()
  const pinnedRun = useMemo(() => {
    if (!params.run) return null
    return runs.find((r) => idMatches(runIdOf(r), params.run)) || null
  }, [runs, params.run])
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
// THE RAIL IS A TASK LIST, NOT A MAP OF THE PRODUCT.
//
// The previous rail carried 17 destinations grouped by POV phase. Every one of
// them was real, and together they answered "what does this console contain"
// instead of "what do I do next" — which is the only question a DC opening it
// cold actually has. So the rail is now five tasks plus two management pages:
//
//   Get started   the setup checklist, built on real state (default home)
//   Simulate      pick or build a chain, check it, launch it
//   Runs          what is executing and what just finished
//   Results       the report, the coverage it adds up to, tenant read-back
//   Catalog       reference content: TTP cards, packages, CLI, streams, UC/TC
//   ── Manage ──  Agents · Tenant
//
// NOTHING WAS DELETED. Every former destination is still routable (deep links,
// bookmarks and ⌘K resolve) and the ones that belong to a task are reachable
// as tabs inside it. `navParent` says which rail item lights up while a hidden
// route is open, and `SECTIONS` below is the one place the tab strips are
// defined — the rail, the tab strip and the router all read this file.
//
// `icon` is a real PANW line icon under /icons — never a Unicode glyph.
export const DESTINATIONS = [
  // ── The five tasks ──
  { id: 'start',    label: 'Get started',  group: 'Tasks',  icon: 'keyboard',        Component: GetStartedView, badge: 'setupLeft' },
  // Library keeps its id: it is the route, the data-testid and the e2e key.
  { id: 'library',  label: 'Simulate',     group: 'Tasks',  icon: 'line-chart',      Component: LibrarySurface },
  { id: 'runs',     label: 'Runs',         group: 'Tasks',  icon: 'donut-chart',     Component: RunsSurface,    badge: 'live' },
  { id: 'proof',    label: 'Results',      group: 'Tasks',  icon: 'bar-chart',       Component: ProofSurface },
  { id: 'ttps',     label: 'Catalog',      group: 'Tasks',  icon: 'threat-warning',  Component: TtpsSurface },

  // ── Manage ──
  { id: 'agents',   label: 'Agents',       group: 'Manage', icon: 'fingerprint-scan', Component: AgentsSurface, badge: 'agentCount' },
  { id: 'tenants',  label: 'Tenant',       group: 'Manage', icon: 'people',          Component: TenantsSurface },

  // ── Off-rail, reached as tabs or links ──
  { id: 'composer',  label: 'Build a chain',     hidden: true, navParent: 'library', icon: 'apps-grid',      Component: ComposerView },
  { id: 'preflight', label: 'Launch gate',       hidden: true, navParent: 'library', icon: 'user-lock',      Component: ReadinessSurface },
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
  { id: 'environments', label: 'Lab',            hidden: true, navParent: 'agents',  icon: 'building',       Component: EnvironmentsSurface },
  { id: 'eal',       label: 'Traffic / EAL',     hidden: true, navParent: 'library', icon: 'threat-network', Component: EalSurface },
  // Legacy id for the Launch Gate, aliased rather than broken.
  { id: 'readiness', label: 'Launch gate',       hidden: true, navParent: 'library', icon: 'user-lock',      Component: ReadinessSurface },
]

/**
 * The tab strip each task shows across the top of its pages. A tab IS a
 * destination, so the strip navigates and holds no state of its own — the rail
 * and the strip cannot disagree about where you are.
 */
export const SECTIONS = {
  library: [['library', 'Scenarios'], ['composer', 'Build a chain'], ['preflight', 'Launch gate']],
  proof: [['proof', 'Report'], ['coverage', 'Coverage']],
  ttps: [['ttps', 'TTP cards'], ['adapters', 'Packages'], ['streams', 'Data streams'], ['uctc', 'UC / TC index']],
}

/** The rail item that represents a destination (itself, or its parent). */
export function navOwner(id) {
  const d = BY_ID.get(id)
  if (!d) return null
  return d.navParent || d.id
}

/** The tab set for a destination, or null when its task has no tabs. */
export function sectionFor(id) {
  const owner = id === 'readiness' ? 'library' : navOwner(id)
  const tabs = SECTIONS[owner]
  if (!tabs) return null
  return { owner, tabs, active: id === 'readiness' ? 'preflight' : id }
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

export function isValidDestination(id) {
  return BY_ID.has(id)
}

/** Grouped, nav-visible destinations in registry order. */
export function navGroups(badges = {}) {
  const order = []
  const byLabel = new Map()
  for (const d of DESTINATIONS) {
    if (d.hidden || !d.group) continue
    if (!byLabel.has(d.group)) {
      byLabel.set(d.group, { items: [] })
      order.push(d.group)
    }
    const badgeVal = d.badge ? badges[d.badge] : null
    const isLive = badgeVal && typeof badgeVal === 'object' && badgeVal.variant === 'live'
    byLabel.get(d.group).items.push({
      id: d.id,
      label: d.label,
      icon: d.icon,
      iconUrl: iconUrl(d.icon),
      badge: isLive ? badgeVal.text : badgeVal,
      badgeVariant: isLive ? 'live' : undefined,
    })
  }
  // The task group is the rail's body and carries no heading; only the
  // secondary group is labelled, which is what makes it read as secondary.
  return order.map((label) => ({ label: label === 'Tasks' ? '' : label, items: byLabel.get(label).items }))
}
