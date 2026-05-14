import React, { useState, useEffect, useCallback } from 'react'
import PlaneSelector from './components/PlaneSelector.jsx'
import ScenarioBrowser from './components/ScenarioBrowser.jsx'
import UCTCMapper from './components/UCTCMapper.jsx'
import LaunchPanel from './components/LaunchPanel.jsx'
import ToolStatusPanel from './components/ToolStatusPanel.jsx'
import ResultsViewer from './components/ResultsViewer.jsx'
import MitreHeatmap from './components/MitreHeatmap.jsx'
import InfraGenerator from './components/InfraGenerator.jsx'
import EalConsole from './components/EalConsole.jsx'
import ResultsValidationWizard from './components/ResultsValidationWizard.jsx'
import { getHealth, getRuns, getEalRuns } from './api/client.js'

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Pick the most recent run_id across classic /api/runs and EAL
 * /api/eal/runs. Both record types expose ``run_id`` and ``started_at``.
 * Returns ``null`` if both lists are empty.
 */
function pickFreshestRunId(classicRuns, ealRuns) {
  const candidates = []
  for (const r of classicRuns || []) {
    if (r?.run_id) candidates.push({ id: r.run_id, ts: r.started_at })
  }
  for (const r of ealRuns || []) {
    if (r?.run_id) candidates.push({ id: r.run_id, ts: r.started_at })
  }
  if (candidates.length === 0) return null
  candidates.sort((a, b) => {
    const ta = a.ts ? Date.parse(a.ts) : 0
    const tb = b.ts ? Date.parse(b.ts) : 0
    return tb - ta
  })
  return candidates[0].id
}

// ─── Cortex Logo SVG ─────────────────────────────────────────────────────────

function CortexLogo() {
  return (
    <svg
      width="32"
      height="32"
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Cortex by Palo Alto Networks"
    >
      {/* Navy background shield */}
      <rect width="32" height="32" rx="6" fill="#003366" />
      {/* Teal geometric accent — stylised C/X mark */}
      <path d="M8 10 L16 6 L24 10 L24 18 L16 26 L8 18 Z" stroke="#00C0E8" strokeWidth="2" fill="none" />
      <path d="M12 16 L16 12 L20 16 L16 20 Z" fill="#00C0E8" />
    </svg>
  )
}

// ─── Header ───────────────────────────────────────────────────────────────────

function AppHeader({ hostname, version, onToggleResults, showResults,
                   onToggleMitre, showMitre, onToggleDeploy, showDeploy,
                   onToggleEal, showEal, onToggleValidate, showValidate }) {
  return (
    <header className="app-header">
      <CortexLogo />

      <div className="flex-row gap-3 flex-1">
        <span style={{
          fontSize: '18px',
          fontWeight: 700,
          color: 'var(--cortex-white)',
          letterSpacing: '-0.02em',
        }}>
          Cortex<span style={{ color: 'var(--cortex-teal)' }}>Sim</span>
        </span>

        <span style={{
          fontSize: '11px',
          fontWeight: 600,
          padding: '2px 8px',
          borderRadius: '3px',
          background: 'rgba(0,192,232,0.18)',
          color: 'var(--cortex-teal)',
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}>
          {version || 'v1.0'}
        </span>

        <span style={{
          fontSize: '11px',
          color: 'rgba(255,255,255,0.45)',
          fontFamily: 'var(--font-mono)',
          marginLeft: '4px',
        }}>
          Detection Simulation Engine
        </span>
      </div>

      {/* Hostname display */}
      <div style={{
        fontSize: '12px',
        color: 'rgba(255,255,255,0.55)',
        fontFamily: 'var(--font-mono)',
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
      }}>
        <span style={{ color: 'var(--cortex-success)', fontSize: '8px' }}>&#9679;</span>
        {hostname || window.location.hostname}
      </div>

      {/* View toggles */}
      <button
        className={`btn btn-sm ${showMitre ? 'btn-navy' : 'btn-secondary'}`}
        onClick={onToggleMitre}
        style={{
          marginLeft: '8px',
          border: '1px solid rgba(255,255,255,0.15)',
          background: showMitre ? 'rgba(0,192,232,0.2)' : 'rgba(255,255,255,0.08)',
          color: 'var(--cortex-white)',
        }}
      >
        &#9635; MITRE
      </button>
      <button
        className={`btn btn-sm ${showDeploy ? 'btn-navy' : 'btn-secondary'}`}
        onClick={onToggleDeploy}
        style={{
          border: '1px solid rgba(255,255,255,0.15)',
          background: showDeploy ? 'rgba(0,192,232,0.2)' : 'rgba(255,255,255,0.08)',
          color: 'var(--cortex-white)',
        }}
      >
        &#x2630; Deploy
      </button>
      <button
        className={`btn btn-sm ${showEal ? 'btn-navy' : 'btn-secondary'}`}
        onClick={onToggleEal}
        style={{
          border: '1px solid rgba(255,255,255,0.15)',
          background: showEal ? 'rgba(0,192,232,0.2)' : 'rgba(255,255,255,0.08)',
          color: 'var(--cortex-white)',
        }}
      >
        &#9881; EAL
      </button>
      <button
        className={`btn btn-sm ${showValidate ? 'btn-navy' : 'btn-secondary'}`}
        onClick={onToggleValidate}
        style={{
          border: '1px solid rgba(255,255,255,0.15)',
          background: showValidate ? 'rgba(0,192,232,0.2)' : 'rgba(255,255,255,0.08)',
          color: 'var(--cortex-white)',
        }}
      >
        &#10003; Validate
      </button>
      <button
        className={`btn btn-sm ${showResults ? 'btn-navy' : 'btn-secondary'}`}
        onClick={onToggleResults}
        style={{
          border: '1px solid rgba(255,255,255,0.15)',
          background: showResults ? 'rgba(0,192,232,0.2)' : 'rgba(255,255,255,0.08)',
          color: 'var(--cortex-white)',
        }}
      >
        &#9776; Runs
      </button>

      {/* PANW branding */}
      <div style={{
        fontSize: '10px',
        color: 'rgba(255,255,255,0.3)',
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        paddingLeft: '12px',
        borderLeft: '1px solid rgba(255,255,255,0.12)',
      }}>
        Palo Alto Networks
      </div>
    </header>
  )
}

// ─── App Root ─────────────────────────────────────────────────────────────────

export default function App() {
  // ── Core UI state ──────────────────────────────────────────────────────────
  const [selectedPlane, setSelectedPlane]         = useState(null)   // string | null
  const [selectedScenario, setSelectedScenario]   = useState(null)   // object | null
  const [runs, setRuns]                           = useState([])
  const [ealRuns, setEalRuns]                     = useState([])
  const [showResults, setShowResults]             = useState(false)
  const [showMitre, setShowMitre]                 = useState(false)
  const [showDeploy, setShowDeploy]               = useState(false)
  const [showEal, setShowEal]                     = useState(false)
  const [showValidate, setShowValidate]           = useState(false)
  const [validateRunId, setValidateRunId]         = useState(null)

  // ── Meta state ─────────────────────────────────────────────────────────────
  const [hostname, setHostname]   = useState(window.location.hostname)
  const [version, setVersion]     = useState('v1.0')
  const [toast, setToast]         = useState(null)  // { message, type }

  // ── Fetch health on mount ──────────────────────────────────────────────────
  useEffect(() => {
    getHealth()
      .then(data => {
        if (data?.version) setVersion(`v${data.version}`)
        if (data?.hostname) setHostname(data.hostname)
      })
      .catch(() => {/* silently degrade — use defaults */})
  }, [])

  // ── Fetch run history ─────────────────────────────────────────────────────
  // Classic scenarios run via /api/runs; EAL campaigns run via /api/eal/runs.
  // Both are needed so the Validate toggle can find the freshest run from
  // either source — without this an EAL-only POV environment opens the
  // wizard with "No run to validate yet" even when EAL runs exist.
  const refreshRuns = useCallback(() => {
    getRuns()
      .then(data => setRuns(Array.isArray(data) ? data : []))
      .catch(() => {})
    getEalRuns()
      .then(data => setEalRuns(Array.isArray(data?.runs) ? data.runs : []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    refreshRuns()
  }, [refreshRuns])

  // ── Toast helper ──────────────────────────────────────────────────────────
  const showToast = useCallback((message, type = 'info') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 4000)
  }, [])

  // ── Plane selection ───────────────────────────────────────────────────────
  const handleSelectPlane = useCallback((plane) => {
    setSelectedPlane(prev => prev === plane ? null : plane)
    setSelectedScenario(null)
  }, [])

  // ── Scenario selection ────────────────────────────────────────────────────
  const handleSelectScenario = useCallback((scenario) => {
    setSelectedScenario(scenario)
    setShowResults(false)
  }, [])

  // ── Launch callback ───────────────────────────────────────────────────────
  const handleRunComplete = useCallback((run) => {
    showToast(`Run ${run?.id || ''} started successfully`, 'success')
    refreshRuns()
  }, [showToast, refreshRuns])

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="app-layout">
      <AppHeader
        hostname={hostname}
        version={version}
        onToggleResults={() => {
          setShowResults(v => !v); setShowMitre(false); setShowDeploy(false); setShowEal(false); setShowValidate(false)
        }}
        showResults={showResults}
        onToggleMitre={() => {
          setShowMitre(v => !v); setShowResults(false); setShowDeploy(false); setShowEal(false); setShowValidate(false)
        }}
        showMitre={showMitre}
        onToggleDeploy={() => {
          setShowDeploy(v => !v); setShowResults(false); setShowMitre(false); setShowEal(false); setShowValidate(false)
        }}
        showDeploy={showDeploy}
        onToggleEal={() => {
          setShowEal(v => !v); setShowResults(false); setShowMitre(false); setShowDeploy(false); setShowValidate(false)
        }}
        showEal={showEal}
        onToggleValidate={() => {
          // Validate needs a run_id; if none chosen yet, fall back to the
          // most recent run across BOTH classic /api/runs and EAL
          // /api/eal/runs (EAL-only environments have empty classic list).
          if (!showValidate && !validateRunId) {
            const freshest = pickFreshestRunId(runs, ealRuns)
            if (freshest) setValidateRunId(freshest)
          }
          setShowValidate(v => !v); setShowResults(false); setShowMitre(false); setShowDeploy(false); setShowEal(false)
        }}
        showValidate={showValidate}
      />

      {/* LEFT RAIL — Detection Plane Selector */}
      <aside className="app-left-rail">
        <PlaneSelector
          selectedPlane={selectedPlane}
          onSelectPlane={handleSelectPlane}
        />
      </aside>

      {/* MAIN PANEL */}
      <main className="app-main-panel">
        {showEal ? (
          <EalConsole
            onMessage={showToast}
            onClose={() => setShowEal(false)}
          />
        ) : showValidate ? (
          validateRunId ? (
            <ResultsValidationWizard
              runId={validateRunId}
              onClose={() => setShowValidate(false)}
              onMessage={showToast}
            />
          ) : (
            <div className="empty-state" style={{ padding: '24px' }}>
              <p>No run to validate yet.</p>
              <p className="muted small">
                Launch a campaign from the <strong>EAL</strong> view first,
                then pick a run from the <strong>Runs</strong> list.
              </p>
            </div>
          )
        ) : showDeploy ? (
          <InfraGenerator />
        ) : showMitre ? (
          <MitreHeatmap />
        ) : showResults ? (
          <ResultsViewer
            runs={runs}
            onClose={() => setShowResults(false)}
            onValidate={(runId) => {
              setValidateRunId(runId)
              setShowResults(false)
              setShowValidate(true)
            }}
          />
        ) : (
          <>
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
                  onError={(msg) => showToast(msg, 'error')}
                />
              </>
            )}
          </>
        )}
      </main>

      {/* RIGHT RAIL — Tool Status */}
      <aside className="app-right-rail">
        <ToolStatusPanel onMessage={showToast} />
      </aside>

      {/* Toast notification */}
      {toast && (
        <div className={`toast toast-${toast.type}`}>
          {toast.message}
        </div>
      )}
    </div>
  )
}
