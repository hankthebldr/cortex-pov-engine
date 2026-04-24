import React, { useState, useEffect, useCallback, useMemo } from 'react'
import AppShell from './components/console/AppShell.jsx'
import ScenarioBrowser from './components/ScenarioBrowser.jsx'
import UCTCMapper from './components/UCTCMapper.jsx'
import LaunchPanel from './components/LaunchPanel.jsx'
import ResultsViewer from './components/ResultsViewer.jsx'
import MitreHeatmap from './components/MitreHeatmap.jsx'
import InfraGenerator from './components/InfraGenerator.jsx'
import { getHealth, getRuns, getScenarios } from './api/client.js'

/**
 * AppConsole — Mission Ops Console root.
 *
 * Wraps the existing scenario/launch/evidence/lab/coverage components in the
 * new 4-region AppShell layout. This is the migration target for the UI;
 * the legacy light-themed App.jsx remains available via the URL flag
 * (?theme=console toggles this root on; default stays on the legacy App).
 *
 * Migration status (aligned with docs/design/console-redesign.md):
 *   [x] Shell chrome (header, telemetry, rail, tabs, strip, ⌘K)
 *   [x] Tabs wired to existing components as content
 *   [ ] Operations tab redesigned (scenario card grid) — next
 *   [ ] In-Flight tab (attack narrative timeline) — next
 *   [ ] Evidence tab redesigned (KPI row + scorecard) — next
 *   [ ] Inspector drawer (pinned launch CTA) — next
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
  const [activeTab, setActiveTab]               = useState('operations')
  const [selectedPlane, setSelectedPlane]       = useState(null)
  const [selectedScenario, setSelectedScenario] = useState(null)
  const [runs, setRuns]                         = useState([])
  const [scenarioList, setScenarioList]         = useState([])
  const [health, setHealth]                     = useState({})
  const [toast, setToast]                       = useState(null)

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
      scenarioId: running.scenario_id || running.id,
      step: currentStep,
      totalSteps,
      elapsed: elapsedSec,
      detected,
      total,
      nextStep,
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

  const pinned = useMemo(() => {
    // Show the 3 most-recent scenarios as pinned for now — a proper
    // per-user pin list is a follow-up.
    return scenarioList.slice(0, 3).map((s) => ({
      id: s.scenario_id || s.id,
      name: s.name || '',
    }))
  }, [scenarioList])

  // ── Command palette items ────────────────────────────────────────────────
  const paletteItems = useMemo(() => {
    const scenarios = scenarioList.slice(0, 12).map((s) => ({
      section: 'Scenarios',
      id: s.scenario_id || s.id,
      title: s.name || '(unnamed)',
      meta: `${s.scenario_id || s.id} \u00b7 ${s.plane || '?'} \u00b7 ${
        s.steps ? `${s.steps.length} steps` : ''
      }`,
      icon: '\u25b8',
      onSelect: () => {
        setSelectedScenario(s)
        setActiveTab('operations')
      },
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
        meta: 'MITRE heatmap',
        icon: '\u26a1',
        shortcut: ['G', 'C'],
        onSelect: () => setActiveTab('coverage'),
      },
    ]
    return [...scenarios, ...actions]
  }, [scenarioList])

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
    setSelectedScenario(null)
  }, [])

  const handleSelectScenario = useCallback((scenario) => {
    setSelectedScenario(scenario)
  }, [])

  const handleRunComplete = useCallback((run) => {
    setToast({ message: `Run ${run?.id || ''} started`, type: 'success' })
    refreshRuns()
    setActiveTab('inflight')
    setTimeout(() => setToast(null), 4000)
  }, [refreshRuns])

  const handleAbortRun = useCallback(() => {
    // TODO: wire to POST /api/runs/:id/abort once endpoint is available
    setToast({ message: 'Abort not yet implemented (see migration step 5)', type: 'warn' })
    setTimeout(() => setToast(null), 4000)
  }, [])

  // ── Render tab content ──────────────────────────────────────────────────
  let tabContent = null
  if (activeTab === 'operations') {
    tabContent = (
      <div>
        <ScenarioBrowser
          selectedPlane={selectedPlane}
          selectedScenario={selectedScenario}
          onSelectScenario={handleSelectScenario}
        />
        {selectedScenario && (
          <>
            <UCTCMapper scenario={selectedScenario} />
            <LaunchPanel
              scenario={selectedScenario}
              onRunComplete={handleRunComplete}
              onError={(msg) => setToast({ message: msg, type: 'error' })}
            />
          </>
        )}
      </div>
    )
  } else if (activeTab === 'inflight') {
    tabContent = activeRun ? (
      <InflightPlaceholder run={activeRun} />
    ) : (
      <EmptyState
        title="No run in progress"
        body="Launch a scenario from the Operations tab. The attack narrative timeline will render here as steps execute."
      />
    )
  } else if (activeTab === 'evidence') {
    tabContent = <ResultsViewer runs={runs} onClose={() => setActiveTab('operations')} />
  } else if (activeTab === 'lab') {
    tabContent = <InfraGenerator />
  } else if (activeTab === 'coverage') {
    tabContent = <MitreHeatmap />
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
        onAbortRun={handleAbortRun}
        tabBadges={tabBadges}
        paletteItems={paletteItems}
        ticker={ticker}
      >
        {tabContent}
      </AppShell>

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

// ─── Placeholder: In-flight narrative ────────────────────────────────────
function InflightPlaceholder({ run }) {
  return (
    <div style={{
      maxWidth: 960, margin: '0 auto', paddingTop: 32,
    }}>
      <div style={{
        fontFamily: 'var(--font-mono)', fontSize: 10,
        letterSpacing: '0.08em', textTransform: 'uppercase',
        color: 'var(--c-signal)', marginBottom: 8,
      }}>
        {run.scenarioId} · step {run.step} of {run.totalSteps}
      </div>
      <h1 style={{
        fontFamily: 'var(--font-display)', fontSize: 36, fontWeight: 400,
        letterSpacing: '-0.02em', color: 'var(--c-text)', marginBottom: 12,
      }}>
        Run in progress
      </h1>
      <p style={{
        fontFamily: 'var(--font-narrative)', fontSize: 16, fontWeight: 300,
        color: 'var(--c-text-secondary)', lineHeight: 1.6, maxWidth: 720,
      }}>
        The attack narrative timeline will render here in a follow-up migration
        step (see <em>docs/design/console-redesign.md</em> → migration step 6).
        Until then, use the <strong>Evidence</strong> tab to validate detections
        as they arrive.
      </p>
    </div>
  )
}

function EmptyState({ title, body }) {
  return (
    <div style={{
      maxWidth: 560, margin: '0 auto', paddingTop: 80, textAlign: 'left',
    }}>
      <h1 style={{
        fontFamily: 'var(--font-display)', fontSize: 28, fontWeight: 400,
        color: 'var(--c-text)', marginBottom: 12,
      }}>{title}</h1>
      <p style={{
        fontFamily: 'var(--font-narrative)', fontSize: 15, fontWeight: 300,
        color: 'var(--c-text-secondary)', lineHeight: 1.6,
      }}>{body}</p>
    </div>
  )
}
