import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AppShell from './components/console/AppShell.jsx'
import ConfirmDialog from './components/console/ConfirmDialog.jsx'
import { Toast, TOAST_MS } from './components/console/kit/index.js'
import { SurfaceBoundary } from './components/console/SurfaceError.jsx'
import { EnvironmentProvider, useEnvironment } from './context/EnvironmentContext.jsx'
import useConsoleRouter from './app/useConsoleRouter.js'
import {
  DESTINATIONS,
  DEFAULT_DESTINATION,
  getDestination,
  isValidDestination,
  navGroups,
  navOwner,
} from './app/destinations.jsx'
import { exitTarget, nextReturnTarget } from './app/immersiveReturn.js'
import SectionTabs from './components/console/SectionTabs.jsx'
import useSetupProgress from './components/console/useSetupProgress.js'
import useComponentStatus from './components/console/useComponentStatus.js'
import { componentKeyForRoute } from './app/componentRoutes.js'
import { downloadReportBundle, getToolAdapters, getTtps } from './api/client.js'
import useShelf from './components/console/useShelf.js'
import { agentIdOf } from './api/ids.js'
import { instanceStatus } from './components/console/instanceStatus.js'
import { ToastContext } from './components/console/useToast.js'
import { ReadBackProvider } from './components/console/useReadBack.jsx'
import { buildSearchItems, componentDestination } from './components/console/searchIndex.js'
import { ChainTrayProvider } from './context/ChainTrayContext.jsx'
import ChainTray from './components/console/ChainTray.jsx'

// Where the "Your chain" bar shows: the pages you add to a chain from (v26 §4.4).
const TRAY_PAGES = new Set(['library', 'ttps', 'adapters', 'streams', 'uctc'])

/**
 * AppConsole — Mission Ops Console root.
 *
 * Thin router shell. The 678-line monolith's state has been lifted into the
 * EnvironmentProvider (tenant/agent/health/scenarios/planes/runs/activeRun/
 * pins) and the 12-branch if/else replaced by a destination registry mounted
 * through a zero-dep hash router. Each destination is a self-contained surface
 * that reads ambient scope from the provider.
 *
 * The legacy light-themed App.jsx remains reachable via `?theme=legacy`.
 */
export default function AppConsole() {
  return (
    <EnvironmentProvider>
      <ConsoleShell />
    </EnvironmentProvider>
  )
}

function ConsoleShell() {
  const env = useEnvironment()
  const router = useConsoleRouter({
    defaultDestination: DEFAULT_DESTINATION,
    isValid: isValidDestination,
  })

  const [toast, setToast] = useState(null)
  const [abortConfirmOpen, setAbortConfirmOpen] = useState(false)
  // The adapter catalog is the denominator for the shelf badge. Fetched once
  // here; a failure leaves it empty and the badge simply does not render — a
  // missing badge is honest, a badge of "0" would read as "nothing to worry
  // about" when we could not check.
  const [toolAdapters, setToolAdapters] = useState([])
  useEffect(() => {
    let cancelled = false
    getToolAdapters()
      .then((d) => { if (!cancelled) setToolAdapters(Array.isArray(d?.adapters) ? d.adapters : []) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  const surfaceToast = useCallback((message, type = 'info', ms = TOAST_MS) => {
    setToast({ message, type })
    setTimeout(() => setToast(null), ms)
  }, [])

  // ⌘E — global POV briefing export. Picks the active run, else the last
  // completed run. Downloads the full bundle.
  const handleExportPOV = useCallback(async () => {
    const targetRunId = env.activeRun?.runId || env.lastRun?.runId || null
    if (!targetRunId) {
      surfaceToast('No run to export — launch a scenario first', 'warn')
      return
    }
    try {
      const blob = await downloadReportBundle(targetRunId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cortexsim-pov-${targetRunId}.tar.gz`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      surfaceToast(`Exported POV briefing for ${targetRunId}`, 'success')
    } catch (err) {
      surfaceToast(err.message || 'Export failed', 'error', 4000)
    }
  }, [env.activeRun, env.lastRun, surfaceToast])

  // Abort — POST /api/runs/:id/abort for ONE named run. Re-reading the run
  // list afterwards is what makes the run read Aborted everywhere and takes the
  // header's Running pill away (design v26 §4.6).
  const abortRunNow = useCallback(async (runId) => {
    if (!runId) return
    try {
      const r = await fetch(`/api/runs/${runId}/abort`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
      })
      if (r.ok) {
        surfaceToast('Run aborted — the agent stops its process tree on its next check', 'success')
        env.refreshRuns()
      } else if (r.status === 404) {
        surfaceToast('Abort endpoint not yet implemented on this SimCore', 'warn', 4000)
      } else {
        surfaceToast(`Abort failed: HTTP ${r.status}`, 'error', 4000)
      }
    } catch (err) {
      surfaceToast(err.message || 'Abort failed', 'error', 4000)
    }
  }, [env, surfaceToast])

  // The modal confirm (the live strip on Runs) aborts the live run.
  const handleAbortConfirmed = useCallback(() => {
    setAbortConfirmOpen(false)
    abortRunNow(env.activeRun?.runId)
  }, [abortRunNow, env.activeRun])

  // What a surface's Abort asks for. A run's own page confirms inline and
  // sends { runId, confirmed: true }; anything else opens the modal confirm.
  const requestAbort = useCallback((req) => {
    if (req && req.confirmed === true && req.runId) abortRunNow(req.runId)
    else setAbortConfirmOpen(true)
  }, [abortRunNow])

  // ── Nav badges ────────────────────────────────────────────────────────────
  // The Tools & Payloads badge counts adapters whose tool the TARGET must fetch
  // from the internet mid-run. A number on the nav is the only thing that makes
  // a DC open that surface BEFORE the customer meeting rather than during it.
  const shelf = useShelf({ adapters: toolAdapters })

  const setup = useSetupProgress()
  // Every component's status from real state: the rail's dots and the
  // "N issues" on Components (design v26 §5).
  const componentStatus = useComponentStatus()

  // Real counts only. The rail used to badge nearly every item with a number
  // from the prototype's seed catalogs ("12 components", "0/11 verified"),
  // which a DC reasonably read as facts about THEIR instance. A badge now
  // appears only when it says something true and actionable.
  const badges = useMemo(() => ({
    // Required setup steps still open — the reason to open Get started.
    setupLeft: setup.requiredLeft > 0 ? String(setup.requiredLeft) : null,
    // Components that are Degraded or Down. Off and Unknown are not issues.
    componentIssues: componentStatus.issues > 0
      ? `${componentStatus.issues} issue${componentStatus.issues === 1 ? '' : 's'}`
      : null,
    live: env.activeRun ? { text: 'LIVE', variant: 'live' } : null,
  }), [setup.requiredLeft, componentStatus.issues, env.activeRun])

  const groups = useMemo(
    () => navGroups(badges, { components: componentStatus.statuses }),
    [badges, componentStatus.statuses],
  )

  // ── Header: this instance's status, uptime and live KPIs (v26 §4.5) ───────
  const instance = useMemo(() => instanceStatus({
    healthModel: env.healthModel,
    runs: env.runs,
    agents: env.agents,
    loading: env.loading,
    errors: env.errors,
  }), [env.healthModel, env.runs, env.agents, env.loading, env.errors])
  // The instance status opens the POVengine component wherever the rail opens
  // it: componentRoutes.js is the one table, read by the rail, the Components
  // page, this header and ⌘K, so they move together. The Launch gate is only
  // the fallback for a build that has no such page.
  const instanceRoute = useMemo(
    () => componentDestination('pov', isValidDestination) || { destination: 'preflight' },
    [],
  )
  const instanceTargetLabel = getDestination(instanceRoute.destination)?.label || 'Launch gate'
  const routerNavigate = router.navigate
  const openInstance = useCallback(
    () => routerNavigate(instanceRoute.destination, instanceRoute.params),
    [routerNavigate, instanceRoute],
  )

  // TTP cards for ⌘K, fetched the first time search opens — never on load.
  // A failed fetch leaves search without cards and tries again next time.
  const [ttpCards, setTtpCards] = useState(null)
  const ttpLoadRef = useRef(false)
  const loadSearchCards = useCallback(() => {
    if (ttpLoadRef.current || ttpCards) return
    ttpLoadRef.current = true
    getTtps()
      .then((d) => setTtpCards(Array.isArray(d?.ttps) ? d.ttps : []))
      .catch(() => {})
      .finally(() => { ttpLoadRef.current = false })
  }, [ttpCards])

  // ── ⌘K: pages, components, scenarios, TTP cards, then actions ────────────
  const paletteItems = useMemo(() => {
    const found = buildSearchItems({
      destinations: DESTINATIONS,
      isValid: isValidDestination,
      scenarios: env.scenarios,
      ttps: ttpCards || [],
      navigate: router.navigate,
    })

    const pinnedRuns = env.pinnedIds
      .map((pid) => {
        const s = env.scenarios.find((x) => (x.scenario_id || x.id) === pid)
        if (!s) return null
        return {
          id: `run-pinned-${pid}`,
          title: `Run ${pid}${s.name ? ` · ${s.name}` : ''}`,
          kind: 'Pinned',
          icon: getDestination('preflight')?.icon || null,
          shortcut: ['⌘', 'L'],
          onSelect: () => router.navigate('guided', { arm: pid }),
        }
      })
      .filter(Boolean)

    const actions = [
      {
        id: 'stage-tool',
        title: 'Make a tool ready',
        meta: 'fetch a public tool onto this instance',
        kind: 'Action',
        icon: getDestination('adapters')?.icon || null,
        onSelect: () => router.navigate('adapters', { supply: 'unstaged' }),
      },
      {
        id: 'global-export',
        title: 'Export POV report',
        meta: 'active or most recent run',
        kind: 'Action',
        icon: getDestination('proof')?.icon || null,
        shortcut: ['⌘', 'E'],
        onSelect: handleExportPOV,
      },
    ]

    const tenantSwitches = env.tenants.map((t) => {
      const id = t.name || t.id
      return {
        id: `tenant-${id}`,
        title: `Point at ${id}`,
        meta: t.config?.region || t.region || 'XSIAM tenant',
        kind: 'Tenant',
        icon: getDestination('tenants')?.icon || null,
        onSelect: () => { env.setTenant(id); surfaceToast(`Tenant: ${id}`, 'success', 2000) },
      }
    })

    const agentSwitches = env.agents.map((a) => {
      const id = agentIdOf(a)
      return {
        id: `agent-${id}`,
        title: `Use ${a.hostname || id}`,
        meta: [a.os, a.status].filter(Boolean).join(' · ') || 'beacon',
        kind: 'Agent',
        icon: getDestination('agents')?.icon || null,
        onSelect: () => { env.setAgent(id); surfaceToast(`Agent: ${a.hostname || id}`, 'success', 2000) },
      }
    })

    return [
      ...found.pages,
      ...found.components,
      ...pinnedRuns,
      ...actions,
      ...found.scenarios,
      ...found.ttps,
      ...agentSwitches,
      ...tenantSwitches,
    ]
  }, [env, router, handleExportPOV, surfaceToast, ttpCards])

  // ── Resolve + mount the current destination surface ───────────────────────
  const dest = getDestination(router.destination) || getDestination(DEFAULT_DESTINATION)
  const Surface = dest.Component
  // Immersive mode is DERIVED from the route on every render and never stored:
  // it is a property of the page that is open, not a preference.
  const immersive = !!dest.immersive

  // The last ordinary page (and its params) — what an immersive surface's Exit
  // returns to. A ref, not state: recording it must not re-render the console.
  const returnRef = useRef(null)
  useEffect(() => {
    returnRef.current = nextReturnTarget(returnRef.current, dest, router.params)
  }, [dest, router.params])

  // Keyed on `router.navigate` (stable), not `router` (a fresh object every
  // render), so a surface that lists onExit in an effect's deps does not
  // re-subscribe on every console render.
  const navigate = router.navigate
  const exitImmersive = useCallback(() => {
    const { destination, params } = exitTarget(returnRef.current, dest, DEFAULT_DESTINATION)
    navigate(destination, params)
  }, [dest, navigate])

  return (
    // The chain tray wraps the whole shell: AppShell keys each page by its
    // destination, so a tray inside the page would empty on the navigation
    // it exists to survive ("Add to chain" here, Customize there, v26 §4.4).
    <ToastContext.Provider value={surfaceToast}>
    <ReadBackProvider>
    <ChainTrayProvider notify={surfaceToast}>
      <AppShell
        destination={router.destination}
        onNavigate={router.navigate}
        navGroups={groups}
        activeRun={env.activeRun}
        health={env.health}
        tenant={env.tenant}
        agent={env.agent}
        activeNav={navOwner(dest.id)}
        activeChild={componentKeyForRoute(dest.id, router.params)}
        immersive={immersive}
        // Drives the safety banner's per-tenant acknowledgement: switching to a
        // customer tenant re-arms the blast-radius warning that was
        // acknowledged against a lab one. `tenant` is the resolved object, so
        // fall back to the raw name when the pointer has not resolved yet.
        tenantId={env.tenant ? (env.tenant.name || env.tenant.id) : null}
        onAbortRun={requestAbort}
        onExportPOV={handleExportPOV}
        paletteItems={paletteItems}
        onPaletteOpen={loadSearchCards}
        instance={instance}
        onOpenInstance={openInstance}
        instanceTargetLabel={instanceTargetLabel}
      >
        {/* Every fetcher below swallows its failure into an empty list, so a
            dead SimCore reads as "nothing configured" on all ten destinations.
            Say it once, globally, above whichever surface is mounted —
            immersive ones included: an unreachable SimCore is never hidden. */}
        {env.apiError && (
          <div className="api-down" role="alert" data-testid="api-down-banner">
            <span className="api-down__tag">SIMCORE UNREACHABLE</span>
            <span className="api-down__msg mono">{env.apiError}</span>
            <span className="api-down__hint">
              Views below may look empty because nothing could be loaded — not because
              nothing exists. Retrying automatically.
            </span>
            <button type="button" className="btn btn--xs" onClick={env.refreshHealth}>↻ Retry now</button>
          </div>
        )}

        {/* An immersive surface owns everything below the header; its own
            Exit (onExit) replaces the tab strip as the way back. */}
        {!immersive && <SectionTabs destination={dest.id} onNavigate={router.navigate} />}

        {TRAY_PAGES.has(dest.id) && <ChainTray onNavigate={router.navigate} />}

        <SurfaceBoundary resetKey={router.destination} title={dest.label}>
          <Surface
            params={router.params}
            setParams={router.setParams}
            onNavigate={router.navigate}
            onAbortRun={requestAbort}
            onExit={immersive ? exitImmersive : undefined}
          />
        </SurfaceBoundary>
        {/* Inside the shell, like the palette and help overlays: every
            .confirm-dialog rule is scoped to .theme-console, so mounted after
            </AppShell> it rendered as bare text at the foot of the page. */}
        <ConfirmDialog
          open={abortConfirmOpen}
          onClose={() => setAbortConfirmOpen(false)}
          onConfirm={handleAbortConfirmed}
          title="Abort active run?"
          body={
            env.activeRun ? (
              <>
                <p>
                  Aborting <strong className="mono">{env.activeRun.scenarioId}</strong>{' '}
                  (step {env.activeRun.step} of {env.activeRun.totalSteps}) will:
                </p>
                <ul>
                  <li>Stop the agent from executing remaining steps</li>
                  <li>Trigger the scenario's cleanup block on the target</li>
                  <li>Mark the run as <span className="mono">aborted</span> in Evidence</li>
                </ul>
                <p className="abort-dialog__note">
                  Already-fired detections remain valid for POV evidence.
                </p>
              </>
            ) : null
          }
          confirmLabel="Abort run"
          confirmVariant="danger"
        />
        {/* Bottom-centre confirmation (v26 §4.1 Toast). Inside the shell so it
            takes the theme's tokens; the live region stays mounted so a screen
            reader announces each message. */}
        <Toast toast={toast} />
      </AppShell>
    </ChainTrayProvider>
    </ReadBackProvider>
    </ToastContext.Provider>
  )
}
