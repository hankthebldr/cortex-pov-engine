import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import OperationsView from '../components/console/OperationsView.jsx'
import ConsoleRail from '../components/console/ConsoleRail.jsx'
import LaunchView from '../components/console/LaunchView.jsx'
import TargetsView from '../components/console/TargetsView.jsx'
import RunDetailView from '../components/console/RunDetailView.jsx'
import MultiRunCompare from '../components/console/MultiRunCompare.jsx'
import CoverageView from '../components/console/CoverageView.jsx'
import TtpBrowserView from '../components/console/TtpBrowserView.jsx'
import ToolAdapterCatalog from '../components/console/ToolAdapterCatalog.jsx'
import UcTcIndexView from '../components/console/UcTcIndexView.jsx'
import LabView from '../components/console/LabView.jsx'
import TenantManager from '../components/console/TenantManager.jsx'
import EalConsole from '../components/EalConsole.jsx'

import { useEnvironment } from '../context/EnvironmentContext.jsx'
import { getScenario } from '../api/client.js'
import { isRunTerminal } from '../components/console/runStatus.js'
import { runIdOf, idMatches } from '../api/ids.js'

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
      style={{ display: 'grid', gridTemplateColumns: 'var(--w-rail) 1fr', minHeight: 0, height: '100%' }}
    >
      <ConsoleRail
        planes={railPlanes}
        pinned={pinned}
        onSelectPlane={handleSelectPlane}
        onSelectPinned={(id) => { armedRef.current = id; onNavigate('guided', { arm: id }) }}
        onUnpin={unpin}
      />
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

  useEffect(() => {
    if (!armId) { setScenario(null); return undefined }
    let cancelled = false
    getScenario(armId)
      .then((d) => { if (!cancelled) setScenario(d || null) })
      .catch(() => { if (!cancelled) setScenario(null) })
    return () => { cancelled = true }
  }, [armId])

  return (
    <div className="guided-flow" style={{ display: 'flex', flexDirection: 'column', gap: 16, padding: 4 }}>
      <div className="view-head">
        <div>
          <h1>New POV run</h1>
          <div className="view-head__meta">
            {scenario
              ? <>Armed: <strong className="mono">{scenario.scenario_id || scenario.id}</strong> · {scenario.name}</>
              : <>Arm a scenario from the <button className="linklike" onClick={() => onNavigate('library')} style={{ background: 'none', border: 'none', color: 'var(--cortex-teal, #00C0E8)', cursor: 'pointer', padding: 0 }}>Library</button> to begin.</>}
          </div>
        </div>
      </div>

      <TargetsView
        selectedTarget={selectedTarget}
        onSelectTarget={setSelectedTarget}
        onGoToLab={() => onNavigate('environments')}
      />

      {scenario && (
        <LaunchView
          scenario={scenario}
          selectedTarget={selectedTarget}
          onRunComplete={(run) => {
            refreshRuns()
            onNavigate('runs', { run: runIdOf(run), tab: 'live' })
          }}
          onError={() => {}}
          onGoLibrary={() => onNavigate('library')}
          onGoTargets={() => {}}
        />
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
        <MultiRunCompare />
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
    <RunDetailView
      runId={runId}
      run={selected}
      activeRun={activeRun}
      subTab={subTab}
      onSubTab={(tab) => setParams({ tab })}
      onBack={() => setParams({ run: null, tab: null }, { replace: true })}
      onError={() => {}}
    />
  )
}

function RunList({ runs = [], onOpen = () => {} }) {
  if (!runs.length) {
    return (
      <div style={{ padding: 48, textAlign: 'center', color: 'var(--c-text-muted)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
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
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr auto auto',
              gap: 12, alignItems: 'center', width: '100%',
              padding: '10px 14px', textAlign: 'left',
              background: 'var(--c-surface-raised, rgba(255,255,255,0.03))',
              border: '1px solid var(--c-hairline, rgba(255,255,255,0.08))',
              borderRadius: 3, marginBottom: 6, color: 'var(--c-text)', cursor: 'pointer',
            }}
          >
            <span className="mono" style={{ fontSize: 12 }}>{r.scenario_id || id}</span>
            <span className="mono" style={{ fontSize: 10, color: 'var(--c-text-muted)' }}>{id}</span>
            <span
              className="chip"
              style={{
                fontSize: 10,
                color: live ? 'var(--cortex-teal, #00C0E8)' : terminal ? 'var(--c-detected, #6ee7a8)' : 'var(--c-pending, #f5c451)',
              }}
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
function CoverageSurface() { return <CoverageView /> }
function TtpsSurface({ params = {} }) { return <TtpBrowserView initialTtpId={params.ttp || null} /> }
function AdaptersSurface() { return <ToolAdapterCatalog /> }
// Deep-linkable: #/uctc?tab=index&uc=UC-EDR&tc=TC-EDR-03
function UcTcSurface({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  return <UcTcIndexView params={params} setParams={setParams} onNavigate={onNavigate} />
}
function EalSurface() { return <EalConsole /> }
function EnvironmentsSurface() { return <LabView /> }
function AgentsSurface() { return <TargetsView /> }
function TenantsSurface() { return <TenantManager /> }

// ─── Registry ─────────────────────────────────────────────────────────────────
export const DESTINATIONS = [
  { id: 'library',      label: 'Library',       group: 'Operate',        icon: '▤', Component: LibrarySurface,      badge: 'scenarioCount' },
  { id: 'runs',         label: 'Runs & Proof',  group: 'Operate',        icon: '◈', Component: RunsSurface,         badge: 'live' },
  { id: 'coverage',     label: 'Coverage',      group: 'Analyze',        icon: '▦', Component: CoverageSurface },
  { id: 'ttps',         label: 'TTP Cards',     group: 'Analyze',        icon: '◆', Component: TtpsSurface },
  { id: 'uctc',         label: 'UC / TC Index', group: 'Analyze',        icon: '≣', Component: UcTcSurface },
  { id: 'adapters',     label: 'Tool Adapters', group: 'Analyze',        icon: '⚙', Component: AdaptersSurface },
  { id: 'eal',          label: 'Traffic / EAL', group: 'Traffic',        icon: '∿', Component: EalSurface },
  { id: 'environments', label: 'Environments',  group: 'Infrastructure', icon: '☁', Component: EnvironmentsSurface },
  { id: 'agents',       label: 'Agents',        group: 'Manage',         icon: '◉', Component: AgentsSurface },
  { id: 'tenants',      label: 'Tenants',       group: 'Manage',         icon: '⬡', Component: TenantsSurface },
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
