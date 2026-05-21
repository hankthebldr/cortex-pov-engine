import React, { useState, useEffect, useCallback, useMemo } from 'react'
import AppShell from './components/console/AppShell.jsx'
import OperationsView from './components/console/OperationsView.jsx'
import InflightView from './components/console/InflightView.jsx'
import EvidenceView from './components/console/EvidenceView.jsx'
import CoverageView from './components/console/CoverageView.jsx'
import LabView from './components/console/LabView.jsx'
import ConfirmDialog from './components/console/ConfirmDialog.jsx'
import usePinnedScenarios from './components/console/usePinnedScenarios.js'
import { getHealth, getRuns, getScenarios, downloadReportBundle } from './api/client.js'

/**
 * AppConsole — Mission Ops Console root.
 *
 * The default shell. Legacy light-themed App.jsx remains reachable via
 * `?theme=legacy` as an escape hatch during the soak period — see
 * docs/design/console-redesign.md for the deprecation schedule.
 *
 * Migration: all 9 steps shipped + extensive enterprise-grade
 * enhancements layered on top.
 *
 * Migration plan (complete):
 *   [x] 1 · tokens + Google Fonts + .theme-console scope
 *   [x] 2 · AppShell chrome (header, telemetry, rail, tabs, strip)
 *   [x] 3 · modes-as-buttons → proper tabs (Ops/In-Flight/Evidence/Lab/Coverage)
 *   [x] 4 · Inspector drawer with pinned launch CTA
 *   [x] 5 · TelemetryStrip (always-visible live run state)
 *   [x] 6 · Attack Narrative Timeline (animated SVG stitch arcs — the hero)
 *   [x] 7 · Evidence redesign + Screenshot PNG + POV report markdown export
 *   [x] 8 · CoverageView (ATT&CK matrix + PANW Stack toggle) + LabView (IaC)
 *   [x] 9 · console is the default; ?theme=legacy is the opt-out
 *
 * Enterprise-grade enhancements:
 *   • ⌘K command palette — fuzzy scenario search + jump-to-tab + actions
 *   • ⌘F filter palette — multi-criteria slicing across 7 facet groups
 *   • ⌘L global quick-launch — preempts browser default
 *   • ⌘E global POV report export from any tab
 *   • ⌘/ help overlay — keyboard reference + tab cheatsheet + PANW stack
 *     map; surfaces automatically on first browser visit
 *   • Pinned scenarios — localStorage-backed, cross-tab sync, rail + palette
 *   • Detection drill-down — click any scorecard row → side panel with
 *     timing, alert ID copy, operator notes, validate-with-notes
 *   • PANW Stack Coverage view — product × kill chain matrix; "wow"
 *     visualization for security architects
 *   • A11y: skip link, ARIA landmarks, aria-live regions on telemetry +
 *     ticker, role=progressbar with valuenow, prefers-reduced-motion
 *     respect, focus-visible outlines
 *   • Tier A + Tier B static analysis CI gates (every TTP script +
 *     every generated push bundle)
 */

const PLANE_META = [
  { code: 'EDR',  name: 'Endpoint'     },
  { code: 'CDR',  name: 'Cloud'        },
  { code: 'NDR',  name: 'Network'      },
  { code: 'ITDR', name: 'Identity'     },
  { code: 'CLOUD_APP', name: 'Cloud App' },
  { code: 'ANALYTICS', name: 'Multi-plane' },
]

export default function AppConsole() {
  const [activeTab, setActiveTab]                       = useState('operations')
  const [selectedPlane, setSelectedPlane]               = useState(null)
  const [techniqueFilter, setTechniqueFilter]           = useState(null)
  const [runs, setRuns]                                 = useState([])
  const [scenarioList, setScenarioList]                 = useState([])
  const [health, setHealth]                             = useState({})
  const [toast, setToast]                               = useState(null)
  const [requestOpenScenarioId, setRequestOpenScenarioId] = useState(null)

  // Pinned scenarios — localStorage-backed
  const { pinnedIds, isPinned, toggle: togglePin, unpin } = usePinnedScenarios()

  // ── Health fetch ──────────────────────────────────────────────────────────
  useEffect(() => {
    getHealth()
      .then((d) => {
        if (!d) return
        setHealth({
          hostname: d.hostname || window.location.hostname,
          version:  d.version ? `v${d.version}` : 'v1.0',
          // The /api/health endpoint doesn't yet expose sensor status — filled
          // with placeholders; the env pill will show muted until we wire the
          // aggregated health endpoint (see open question #2 in design doc).
          sensors: { xdr: 'healthy', cdr: 'healthy', ndr: 'healthy' },
        })
      })
      .catch(() => {
        setHealth({ hostname: window.location.hostname, version: 'v1.0', sensors: {} })
      })
  }, [])

  // ── Runs fetch (poll every 10s while on inflight/evidence) ────────────────
  const refreshRuns = useCallback(() => {
    getRuns()
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    refreshRuns()
  }, [refreshRuns])

  useEffect(() => {
    const needsPoll = activeTab === 'inflight' || activeTab === 'evidence'
    if (!needsPoll) return undefined
    const t = setInterval(refreshRuns, 10_000)
    return () => clearInterval(t)
  }, [activeTab, refreshRuns])

  // ── Scenarios (for plane counts + palette items) ──────────────────────────
  useEffect(() => {
    getScenarios({})
      .then((data) => {
        // API shape varies — accept either array or { scenarios: [...] }
        const list = Array.isArray(data) ? data : (data && data.scenarios) || []
        setScenarioList(list)
      })
      .catch(() => setScenarioList([]))
  }, [])

  // ── Derive active run (most recent running run) ──────────────────────────
  const activeRun = useMemo(() => {
    const running = runs.find((r) => r && (r.status === 'running' || r.status === 'pending'))
    if (!running) return null
    const totalSteps = running.total_steps ?? running.steps?.length ?? 0
    const currentStep = running.current_step ?? running.step ?? 0
    const elapsedSec = running.started_at
      ? Math.floor((Date.now() - new Date(running.started_at).getTime()) / 1000)
      : running.elapsed_seconds ?? 0
    const detected = running.detected_count ?? 0
    const total = running.expected_detections ?? 0
    const nextStep = running.next_technique ?? running.next_step ?? null
    return {
      runId: running.id || running.run_id,
      scenarioId: running.scenario_id,
      step: currentStep,
      totalSteps,
      elapsed: elapsedSec,
      detected,
      total,
      nextStep,
    }
  }, [runs])

  // ── Derive last completed run (fallback for InflightView) ───────────────
  const lastRun = useMemo(() => {
    const finished = runs.find(
      (r) => r && (r.status === 'completed' || r.status === 'failed' || r.status === 'aborted')
    )
    if (!finished) return null
    return {
      runId: finished.id || finished.run_id,
      scenarioId: finished.scenario_id,
      status: finished.status,
    }
  }, [runs])

  // ── Rail data ────────────────────────────────────────────────────────────
  const planes = useMemo(() => {
    const counts = scenarioList.reduce((acc, s) => {
      const p = (s.plane || '').toUpperCase()
      acc[p] = (acc[p] || 0) + 1
      return acc
    }, {})
    return PLANE_META.map((p) => ({
      ...p,
      count: counts[p.code] || 0,
      isActive: selectedPlane === p.code,
    }))
  }, [scenarioList, selectedPlane])

  // Resolve pinned IDs against the live scenario list. If a scenario was
  // pinned but is no longer present in the list (e.g. plane filter or
  // deletion), fall back to a name derived from the ID.
  const pinned = useMemo(() => {
    if (!pinnedIds.length) return []
    const byId = new Map(
      scenarioList.map((s) => [s.scenario_id || s.id, s])
    )
    return pinnedIds.map((id) => {
      const s = byId.get(id)
      return { id, name: s?.name || id }
    })
  }, [pinnedIds, scenarioList])

  // ── Open-scenario helper (used by rail and palette) ──────────────────────
  const handleOpenScenario = useCallback((scenarioId) => {
    if (!scenarioId) return
    setActiveTab('operations')
    // Bump a fresh value so the same scenario re-opens if clicked twice.
    setRequestOpenScenarioId(`${scenarioId}::${Date.now()}`)
  }, [])

  const requestOpenIdForView = useMemo(() => {
    if (!requestOpenScenarioId) return null
    const idx = requestOpenScenarioId.indexOf('::')
    return idx > 0 ? requestOpenScenarioId.slice(0, idx) : requestOpenScenarioId
  }, [requestOpenScenarioId])

  // ── Command palette items ────────────────────────────────────────────────
  const paletteItems = useMemo(() => {
    const scenarios = scenarioList.slice(0, 12).map((s) => ({
      section: 'Scenarios',
      id: s.scenario_id || s.id,
      title: s.name || '(unnamed)',
      meta: `${s.scenario_id || s.id} \u00b7 ${s.plane || '?'}${
        s.steps ? ' \u00b7 ' + s.steps.length + ' steps' : ''
      }${isPinned(s.scenario_id || s.id) ? ' \u00b7 \u25fc pinned' : ''}`,
      icon: '\u25b8',
      onSelect: () => handleOpenScenario(s.scenario_id || s.id),
    }))
    const actions = [
      {
        section: 'Actions',
        id: 'tab-operations',
        title: 'Go to Operations',
        meta: 'browse and launch scenarios',
        icon: '\u26a1',
        shortcut: ['G', 'O'],
        onSelect: () => setActiveTab('operations'),
      },
      {
        section: 'Actions',
        id: 'tab-inflight',
        title: 'Go to In-Flight',
        meta: 'attack narrative timeline',
        icon: '\u26a1',
        shortcut: ['G', 'I'],
        onSelect: () => setActiveTab('inflight'),
      },
      {
        section: 'Actions',
        id: 'tab-evidence',
        title: 'Go to Evidence',
        meta: 'scorecard · validate · export',
        icon: '\u26a1',
        shortcut: ['G', 'E'],
        onSelect: () => setActiveTab('evidence'),
      },
      {
        section: 'Actions',
        id: 'tab-lab',
        title: 'Go to Lab',
        meta: 'IaC bundle generator',
        icon: '\u26a1',
        shortcut: ['G', 'L'],
        onSelect: () => setActiveTab('lab'),
      },
      {
        section: 'Actions',
        id: 'tab-coverage',
        title: 'Go to ATT&CK Coverage',
        meta: 'MITRE + PANW Stack matrix',
        icon: '\u26a1',
        shortcut: ['G', 'C'],
        onSelect: () => setActiveTab('coverage'),
      },
      {
        section: 'Actions',
        id: 'global-export',
        title: 'Export POV report',
        meta: 'markdown \u00b7 active or most recent run',
        icon: '\u2197',
        shortcut: ['\u2318', 'E'],
        onSelect: handleExportPOV,
      },
    ]

    // Pinned quick-launch actions appear FIRST in Actions when present.
    const pinnedActions = pinnedIds
      .map((pid) => {
        const s = scenarioList.find((x) => (x.scenario_id || x.id) === pid)
        if (!s) return null
        return {
          section: 'Pinned \u00b7 launch',
          id: `quick-launch-${pid}`,
          title: `Open ${s.name || pid}`,
          meta: `${pid} \u00b7 ${s.plane || '?'}`,
          icon: '\u25fc',
          shortcut: ['\u2318', 'L'],
          onSelect: () => handleOpenScenario(pid),
        }
      })
      .filter(Boolean)

    return [...pinnedActions, ...scenarios, ...actions]
  }, [scenarioList, pinnedIds, isPinned, handleOpenScenario, handleExportPOV])

  // ── Tab badges ──────────────────────────────────────────────────────────
  const tabBadges = useMemo(() => {
    const b = {
      operations: scenarioList.length ? String(scenarioList.length) : null,
      inflight:   activeRun ? { text: 'LIVE', variant: 'live' } : null,
      evidence:   activeRun ? `${activeRun.detected}/${activeRun.total}` : null,
      lab:        null,
      coverage:   null,
    }
    return b
  }, [scenarioList, activeRun])

  // ── Ticker (most recent event) ──────────────────────────────────────────
  const ticker = useMemo(() => {
    const latest = runs[0]
    if (!latest) return 'idle'
    const ts = latest.last_event_at || latest.updated_at || latest.started_at
    return `${ts ? new Date(ts).toISOString().substring(11, 19) + 'Z' : 'now'} \u00b7 ${
      latest.scenario_id || latest.id
    } \u00b7 ${latest.status || 'unknown'}`
  }, [runs])

  // ── Callbacks ───────────────────────────────────────────────────────────
  const handleSelectPlane = useCallback((planeCode) => {
    setSelectedPlane((prev) => (prev === planeCode ? null : planeCode))
  }, [])

  const handleRunComplete = useCallback((run) => {
    setToast({ message: `Run ${run?.id || ''} started`, type: 'success' })
    refreshRuns()
    setActiveTab('inflight')
    setTimeout(() => setToast(null), 4000)
  }, [refreshRuns])

  // Abort flow — confirmation dialog → POST /api/runs/:id/abort with
  // graceful fallback when the backend doesn't yet implement the
  // endpoint (older SimCore builds). Friendly toast in either case.
  const [abortConfirmOpen, setAbortConfirmOpen] = useState(false)

  const handleAbortRun = useCallback(() => {
    setAbortConfirmOpen(true)
  }, [])

  const handleAbortConfirmed = useCallback(async () => {
    setAbortConfirmOpen(false)
    if (!activeRun?.runId) return
    try {
      const r = await fetch(`/api/runs/${activeRun.runId}/abort`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (r.ok) {
        setToast({ message: `Run ${activeRun.runId} aborted`, type: 'success' })
        refreshRuns()
      } else if (r.status === 404) {
        setToast({
          message: 'Abort endpoint not yet implemented on this SimCore — escalate to lab admin',
          type: 'warn',
        })
      } else {
        setToast({ message: `Abort failed: HTTP ${r.status}`, type: 'error' })
      }
    } catch (err) {
      setToast({ message: err.message || 'Abort failed', type: 'error' })
    }
    setTimeout(() => setToast(null), 4000)
  }, [activeRun, refreshRuns])

  // ⌘E — global POV briefing export. Picks the most relevant run: active
  // if any, else last completed. Downloads the full bundle (narrative +
  // matrix + Navigator layer + manifest) — the artifact a DC actually
  // hands the customer at the end of a POV. Friendly toast if no run.
  const handleExportPOV = useCallback(async () => {
    const targetRunId = activeRun?.runId || lastRun?.runId || null
    if (!targetRunId) {
      setToast({ message: 'No run to export — launch a scenario first', type: 'warn' })
      setTimeout(() => setToast(null), 3000)
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
      setToast({ message: `Exported POV briefing for ${targetRunId}`, type: 'success' })
      setTimeout(() => setToast(null), 3000)
    } catch (err) {
      setToast({ message: err.message || 'Export failed', type: 'error' })
      setTimeout(() => setToast(null), 4000)
    }
  }, [activeRun, lastRun])

  // ── Render tab content ──────────────────────────────────────────────────
  let tabContent = null
  if (activeTab === 'operations') {
    tabContent = (
      <OperationsView
        selectedPlane={selectedPlane}
        onClearPlane={() => setSelectedPlane(null)}
        techniqueFilter={techniqueFilter}
        onClearTechniqueFilter={() => setTechniqueFilter(null)}
        requestOpenScenarioId={requestOpenIdForView}
        pinnedIds={pinnedIds}
        isPinned={isPinned}
        togglePin={togglePin}
        onRunComplete={handleRunComplete}
        onError={(msg) => setToast({ message: msg, type: 'error' })}
        onSurfaceMessage={(msg, type = 'info') => {
          setToast({ message: msg, type })
          setTimeout(() => setToast(null), 3000)
        }}
      />
    )
  } else if (activeTab === 'inflight') {
    tabContent = (
      <InflightView
        activeRun={activeRun}
        lastRun={lastRun}
        onError={(msg) => setToast({ message: msg, type: 'error' })}
      />
    )
  } else if (activeTab === 'evidence') {
    tabContent = (
      <EvidenceView
        activeRun={activeRun}
        lastRun={lastRun}
        onError={(msg) => setToast({ message: msg, type: 'error' })}
      />
    )
  } else if (activeTab === 'lab') {
    tabContent = (
      <LabView
        onError={(msg) => setToast({ message: msg, type: 'error' })}
      />
    )
  } else if (activeTab === 'coverage') {
    tabContent = (
      <CoverageView
        onFilterByTechnique={(tid, scenarioIds) => {
          setTechniqueFilter({ tid, scenarioIds: scenarioIds || [] })
          setActiveTab('operations')
          setToast({
            message: `Filtered Operations to ${tid} (${(scenarioIds || []).length} scenarios)`,
            type: 'info',
          })
          setTimeout(() => setToast(null), 3000)
        }}
      />
    )
  }

  return (
    <>
      <AppShell
        activeTab={activeTab}
        onTabChange={setActiveTab}
        activeRun={activeRun}
        health={health}
        planes={planes}
        onSelectPlane={handleSelectPlane}
        pinned={pinned}
        onSelectPinned={handleOpenScenario}
        onUnpinScenario={unpin}
        onAbortRun={handleAbortRun}
        onExportPOV={handleExportPOV}
        tabBadges={tabBadges}
        paletteItems={paletteItems}
        ticker={ticker}
      >
        {tabContent}
      </AppShell>

      <ConfirmDialog
        open={abortConfirmOpen}
        onClose={() => setAbortConfirmOpen(false)}
        onConfirm={handleAbortConfirmed}
        title="Abort active run?"
        body={
          activeRun ? (
            <>
              <p>
                Aborting <strong className="mono">{activeRun.scenarioId}</strong>{' '}
                (step {activeRun.step} of {activeRun.totalSteps}) will:
              </p>
              <ul>
                <li>Stop the agent from executing remaining steps</li>
                <li>Trigger the scenario's cleanup block on the target</li>
                <li>Mark the run as <span className="mono">aborted</span> in Evidence</li>
              </ul>
              <p style={{ color: 'var(--c-pending)', fontSize: 12 }}>
                Already-fired detections remain valid for POV evidence.
              </p>
            </>
          ) : null
        }
        confirmLabel="Abort run"
        confirmVariant="danger"
      />

      {toast && (
        <div className={`toast toast-${toast.type}`} style={{
          position: 'fixed', bottom: 48, right: 20, zIndex: 200,
          padding: '10px 14px',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
          background: 'var(--c-surface-modal)',
          border: '1px solid var(--c-hairline-strong)',
          borderRadius: 3,
          color: toast.type === 'error' ? 'var(--c-missed)'
               : toast.type === 'warn'  ? 'var(--c-pending)'
                                        : 'var(--c-detected)',
        }}>
          {toast.message}
        </div>
      )}
    </>
  )
}

// In-flight rendering moved to components/console/InflightView.jsx (step 6).
// Empty-state handling is now intrinsic to InflightView when no run is active.
