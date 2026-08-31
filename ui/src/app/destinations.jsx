import React, { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import ConsoleRail from '../components/console/ConsoleRail.jsx'
import { SurfaceBoundary } from '../components/console/SurfaceError.jsx'

import { useEnvironment } from '../context/EnvironmentContext.jsx'
import { getScenario } from '../api/client.js'
import { isRunTerminal } from '../components/console/runStatus.js'
import { runIdOf, idMatches } from '../api/ids.js'

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
const LaunchView = makeLazySurface(() => import('../components/console/LaunchView.jsx'), 'New POV run')
const TargetsView = makeLazySurface(() => import('../components/console/TargetsView.jsx'), 'Targets')
const RunDetailView = makeLazySurface(() => import('../components/console/RunDetailView.jsx'), 'Runs & Proof')
const MultiRunCompare = makeLazySurface(() => import('../components/console/MultiRunCompare.jsx'), 'Runs & Proof · Compare')
const CoverageView = makeLazySurface(() => import('../components/console/CoverageView.jsx'), 'Coverage')
const TtpBrowserView = makeLazySurface(() => import('../components/console/TtpBrowserView.jsx'), 'TTP Cards')
const ToolAdapterCatalog = makeLazySurface(() => import('../components/console/ToolAdapterCatalog.jsx'), 'Tools & Payloads')
const UcTcIndexView = makeLazySurface(() => import('../components/console/UcTcIndexView.jsx'), 'UC / TC Index')
const LabView = makeLazySurface(() => import('../components/console/LabView.jsx'), 'Environments')
const TenantManager = makeLazySurface(() => import('../components/console/TenantManager.jsx'), 'Tenants')
const ReadinessView = makeLazySurface(() => import('../components/console/ReadinessView.jsx'), 'Readiness')
const EalConsole = makeLazySurface(() => import('../components/EalConsole.jsx'), 'Traffic / EAL')

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
  const { refreshRuns } = useEnvironment()
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
              : <>Arm a scenario from the <button className="linklike guided-flow__library-link" onClick={() => onNavigate('library')}>Library</button> to begin.</>}
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

// ─── Runs & Proof surface ────────────────────────────────────────────────────
// Run history list → single Run Detail surface keyed by runId with
// Live / Evidence / Storyline / Causality SUB-tabs (collapses the four former
// top-level tabs, extracted into RunDetailView). Multi-run compare via ?compare=1.
function RunsSurface({ params = {}, setParams = () => {} }) {
  const { runs, activeRun } = useEnvironment()
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
          <div><h1>Runs & Proof · Compare</h1></div>
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
            <h1>Runs &amp; Proof</h1>
            <div className="view-head__meta"><span className="mono">{runs.length}</span> runs</div>
          </div>
          <button className="btn" onClick={() => setParams({ compare: '1' }, { replace: true })}>Compare runs</button>
        </div>
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

function RunList({ runs = [], onOpen = () => {} }) {
  if (!runs.length) {
    return (
      <div className="run-list-empty">
        no runs yet — launch a scenario from the Library
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
function TtpsSurface({ params = {} }) {
  return (
    <Suspense fallback={<DestinationLoading />}>
      <TtpBrowserView initialTtpId={params.ttp || null} />
    </Suspense>
  )
}
// "Tools & Payloads" — the catalog plus the payload shelf. Staging state is a
// PROPERTY of an adapter, not a new noun, so it lives on this destination
// rather than an eleventh one: splitting them would list `linpeas.sh` in one
// place and `TOOL-LINPEAS` in another and make the DC hold the join.
const AdaptersSurface = withSuspense(ToolAdapterCatalog)
// Deep-linkable: #/uctc?tab=index&uc=UC-EDR&tc=TC-EDR-03
const UcTcSurface = withSuspense(UcTcIndexView)
const EalSurface = withSuspense(EalConsole)
const EnvironmentsSurface = withSuspense(LabView)
// Readiness is grouped under Manage, next to Agents and Tenants — the three
// things a DC configures before a POV. It is deliberately NOT the default
// destination: a health page that greets you every morning stops being read.
const ReadinessSurface = withSuspense(ReadinessView)
const AgentsSurface = withSuspense(TargetsView)
const TenantsSurface = withSuspense(TenantManager)

// ─── Registry ─────────────────────────────────────────────────────────────────
export const DESTINATIONS = [
  { id: 'library',      label: 'Library',       group: 'Operate',        icon: '▤', Component: LibrarySurface,      badge: 'scenarioCount' },
  { id: 'runs',         label: 'Runs & Proof',  group: 'Operate',        icon: '◈', Component: RunsSurface,         badge: 'live' },
  { id: 'coverage',     label: 'Coverage',      group: 'Analyze',        icon: '▦', Component: CoverageSurface },
  { id: 'ttps',         label: 'TTP Cards',     group: 'Analyze',        icon: '◆', Component: TtpsSurface },
  { id: 'uctc',         label: 'UC / TC Index', group: 'Analyze',        icon: '≣', Component: UcTcSurface },
  // id stays 'adapters' — it is the route (#/adapters), the data-testid and the
  // ⌘K entry. Only the label changed.
  { id: 'adapters',     label: 'Tools & Payloads', group: 'Analyze',     icon: '⚙', Component: AdaptersSurface, badge: 'targetEgress' },
  { id: 'eal',          label: 'Traffic / EAL', group: 'Traffic',        icon: '∿', Component: EalSurface },
  { id: 'environments', label: 'Environments',  group: 'Infrastructure', icon: '☁', Component: EnvironmentsSurface },
  { id: 'agents',       label: 'Agents',        group: 'Manage',         icon: '◉', Component: AgentsSurface },
  { id: 'tenants',      label: 'Tenants',       group: 'Manage',         icon: '⬡', Component: TenantsSurface },
  { id: 'readiness',    label: 'Readiness',     group: 'Manage',         icon: '✓', Component: ReadinessSurface, badge: 'degraded' },
  // Hidden route — the optional guided demo path, reachable from Library + ⌘K.
  { id: 'guided',       label: 'New POV run',   group: null, hidden: true, icon: '▸', Component: GuidedPovFlow },
]

export const DEFAULT_DESTINATION = 'library'

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
    if (!byLabel.has(d.group)) { byLabel.set(d.group, []); order.push(d.group) }
    const badgeVal = d.badge ? badges[d.badge] : null
    const isLive = badgeVal && typeof badgeVal === 'object' && badgeVal.variant === 'live'
    byLabel.get(d.group).push({
      id: d.id,
      label: d.label,
      icon: d.icon,
      badge: isLive ? badgeVal.text : badgeVal,
      badgeVariant: isLive ? 'live' : undefined,
    })
  }
  return order.map((label) => ({ label, items: byLabel.get(label) }))
}
