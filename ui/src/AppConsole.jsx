import React, { useCallback, useEffect, useMemo, useState } from 'react'
import AppShell from './components/console/AppShell.jsx'
import ConfirmDialog from './components/console/ConfirmDialog.jsx'
import { SurfaceBoundary } from './components/console/SurfaceError.jsx'
import ReadinessBanner from './components/console/ReadinessBanner.jsx'
import { EnvironmentProvider, useEnvironment } from './context/EnvironmentContext.jsx'
import useConsoleRouter from './app/useConsoleRouter.js'
import {
  DESTINATIONS,
  DEFAULT_DESTINATION,
  getDestination,
  isValidDestination,
  navGroups,
} from './app/destinations.jsx'
import { downloadReportBundle, getToolAdapters } from './api/client.js'
import useShelf from './components/console/useShelf.js'
import { agentIdOf } from './api/ids.js'

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

  const surfaceToast = useCallback((message, type = 'info', ms = 3000) => {
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

  // Abort flow — confirmation → POST /api/runs/:id/abort.
  const handleAbortConfirmed = useCallback(async () => {
    setAbortConfirmOpen(false)
    const runId = env.activeRun?.runId
    if (!runId) return
    try {
      const r = await fetch(`/api/runs/${runId}/abort`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
      })
      if (r.ok) {
        surfaceToast(`Run ${runId} aborted`, 'success')
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

  // ── Nav badges ────────────────────────────────────────────────────────────
  // The Tools & Payloads badge counts adapters whose tool the TARGET must fetch
  // from the internet mid-run. A number on the nav is the only thing that makes
  // a DC open that surface BEFORE the customer meeting rather than during it.
  const shelf = useShelf({ adapters: toolAdapters })
  const degradedCount = env.healthModel ? env.healthModel.degraded.length : 0
  const badges = useMemo(() => ({
    scenarioCount: env.scenarios.length ? String(env.scenarios.length) : null,
    live: env.activeRun ? { text: 'LIVE', variant: 'live' } : null,
    targetEgress: shelf.counts.target_egress > 0 ? String(shelf.counts.target_egress) : null,
    // A count, never a dot: "3" sends a DC to the page, a coloured pip does not.
    degraded: degradedCount > 0 ? String(degradedCount) : null,
  }), [env.scenarios.length, env.activeRun, shelf.counts.target_egress, degradedCount])

  const groups = useMemo(() => navGroups(badges), [badges])

  // ── Command palette items (destinations + context switches + scenario jumps) ─
  const paletteItems = useMemo(() => {
    const pinnedLaunch = env.pinnedIds
      .map((pid) => {
        const s = env.scenarios.find((x) => (x.scenario_id || x.id) === pid)
        if (!s) return null
        return {
          section: 'Pinned · launch',
          id: `launch-${pid}`,
          title: `Arm ${s.name || pid}`,
          meta: `${pid} · ${s.plane || '?'}`,
          icon: '◼',
          shortcut: ['⌘', 'L'],
          onSelect: () => router.navigate('guided', { arm: pid }),
        }
      })
      .filter(Boolean)

    const scenarios = env.scenarios.map((s) => {
      const id = s.scenario_id || s.id
      return {
        section: 'Scenarios',
        id: `scn-${id}`,
        title: s.name || '(unnamed)',
        meta: `${id} · ${s.plane || '?'}${s.steps ? ' · ' + s.steps.length + ' steps' : ''}`,
        icon: '▸',
        onSelect: () => router.navigate('library', { open: id }),
      }
    })

    const tenantSwitches = env.tenants.map((t) => {
      const id = t.name || t.id
      return {
        section: 'Switch tenant',
        id: `tenant-${id}`,
        title: `Point at ${id}`,
        meta: t.config?.region || t.region || 'XSIAM tenant',
        icon: '⬡',
        onSelect: () => { env.setTenant(id); surfaceToast(`Tenant: ${id}`, 'success', 2000) },
      }
    })

    const agentSwitches = env.agents.map((a) => {
      const id = agentIdOf(a)
      return {
        section: 'Switch agent',
        id: `agent-${id}`,
        title: `Use ${a.hostname || id}`,
        meta: [a.os, a.status].filter(Boolean).join(' · ') || 'beacon',
        icon: '◉',
        onSelect: () => { env.setAgent(id); surfaceToast(`Agent: ${a.hostname || id}`, 'success', 2000) },
      }
    })

    const destinationActions = DESTINATIONS
      .filter((d) => !d.hidden && d.group)
      .map((d) => ({
        section: 'Go to',
        id: `go-${d.id}`,
        title: `Go to ${d.label}`,
        meta: d.group,
        icon: d.icon || '⚡',
        onSelect: () => router.navigate(d.id),
      }))

    const utility = [
      {
        section: 'Actions',
        id: 'new-pov',
        title: 'New POV run',
        meta: 'guided target → launch flow',
        icon: '▸',
        onSelect: () => router.navigate('guided'),
      },
      {
        section: 'Actions',
        id: 'stage-tool',
        title: 'Stage a tool payload',
        meta: 'pull a public tool onto this SimCore',
        icon: '⇩',
        onSelect: () => router.navigate('adapters', { supply: 'unstaged' }),
      },
      {
        section: 'Actions',
        id: 'global-export',
        title: 'Export POV report',
        meta: 'active or most recent run',
        icon: '↗',
        shortcut: ['⌘', 'E'],
        onSelect: handleExportPOV,
      },
    ]

    return [
      ...pinnedLaunch,
      ...destinationActions,
      ...scenarios,
      ...tenantSwitches,
      ...agentSwitches,
      ...utility,
    ]
  }, [env, router, handleExportPOV, surfaceToast])

  // ── Ticker (most recent event) ────────────────────────────────────────────
  const ticker = useMemo(() => {
    const latest = env.runs[0]
    if (!latest) return 'idle'
    const ts = latest.last_event_at || latest.updated_at || latest.started_at
    return `${ts ? new Date(ts).toISOString().substring(11, 19) + 'Z' : 'now'} · ${
      latest.scenario_id || latest.id
    } · ${latest.status || 'unknown'}`
  }, [env.runs])

  // ── Resolve + mount the current destination surface ───────────────────────
  const dest = getDestination(router.destination) || getDestination(DEFAULT_DESTINATION)
  const Surface = dest.Component

  return (
    <>
      <AppShell
        destination={router.destination}
        onNavigate={router.navigate}
        navGroups={groups}
        activeRun={env.activeRun}
        lastRun={env.lastRun}
        health={env.health}
        // Drives the safety banner's per-tenant acknowledgement: switching to a
        // customer tenant re-arms the blast-radius warning that was
        // acknowledged against a lab one. `tenant` is the resolved object, so
        // fall back to the raw name when the pointer has not resolved yet.
        tenantId={env.tenant ? (env.tenant.name || env.tenant.id) : null}
        onAbortRun={() => setAbortConfirmOpen(true)}
        onExportPOV={handleExportPOV}
        paletteItems={paletteItems}
        ticker={ticker}
      >
        {/* Every fetcher below swallows its failure into an empty list, so a
            dead SimCore reads as "nothing configured" on all ten destinations.
            Say it once, globally, above whichever surface is mounted. */}
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

        {/* SimCore answered, but is not whole. The two deployments that break
            hardest — booted without tools/ or without scenarios/ — both report
            `status: "ok"`, so the first signal a DC gets today is an empty
            Library that reads as "this product has no content". */}
        {router.destination !== 'readiness' && (
          <ReadinessBanner model={env.healthModel} onNavigate={router.navigate} />
        )}

        <SurfaceBoundary resetKey={router.destination} title={dest.label}>
          <Surface
            params={router.params}
            setParams={router.setParams}
            onNavigate={router.navigate}
          />
        </SurfaceBoundary>
      </AppShell>

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

      {toast && (
        <div className={`toast toast-${toast.type} app-toast`}>
          {toast.message}
        </div>
      )}
    </>
  )
}
