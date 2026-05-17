import React, { useState, useEffect, useMemo, useCallback } from 'react'
import ScenarioGrid from './ScenarioGrid.jsx'
import ScenarioInspector from './ScenarioInspector.jsx'
import useLaunchScenario from './useLaunchScenario.js'
import { getScenarios, getScenario } from '../../api/client.js'

/**
 * OperationsView — the default tab in AppConsole.
 *
 * Composes:
 *   - a head with title + plane/filter context
 *   - a scenario card grid
 *   - the right-side ScenarioInspector drawer (pinned launch CTA at top)
 *
 * Owns:
 *   - the scenario list fetch
 *   - the selected/drawer state
 *   - the launch hook (lifted up from inspector so ⌘L can trigger it)
 *   - the global ⌘L / Ctrl+L launch handler
 *
 * Props:
 *   selectedPlane            — uppercase plane code or null (filter)
 *   techniqueFilter          — TID string or null (Coverage→Operations cross-link)
 *   onClearTechniqueFilter   — () => void
 *   requestOpenScenarioId    — when this string prop changes, open that scenario
 *                              (rail click → AppConsole sets this)
 *   pinnedIds                — array of pinned scenario IDs
 *   isPinned                 — (id) => boolean
 *   togglePin                — (id) => void
 *   onRunComplete            — (run) => void  forwarded from parent
 *   onError                  — (msg) => void  forwarded from parent
 *   onSurfaceMessage         — (msg, type='info') => void   for non-error toasts
 */
export default function OperationsView({
  selectedPlane = null,
  techniqueFilter = null,
  onClearTechniqueFilter = () => {},
  requestOpenScenarioId = null,
  pinnedIds = [],
  isPinned = () => false,
  togglePin = () => {},
  onRunComplete = () => {},
  onError = () => {},
  onSurfaceMessage = () => {},
}) {
  const [scenarios, setScenarios]   = useState([])
  const [loading, setLoading]       = useState(true)
  const [selected, setSelected]     = useState(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  // ── Fetch scenario list ──────────────────────────────────────────────
  useEffect(() => {
    setLoading(true)
    const params = selectedPlane ? { plane: selectedPlane } : {}
    getScenarios(params)
      .then((data) => {
        const list = Array.isArray(data) ? data : (data && data.scenarios) || []
        setScenarios(list)
      })
      .catch((err) => {
        onError(err.message || 'Failed to load scenarios')
        setScenarios([])
      })
      .finally(() => setLoading(false))
  }, [selectedPlane]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handle card selection — hydrate full detail ──────────────────────
  const handleSelect = useCallback(async (summary) => {
    const id = summary.scenario_id || summary.id
    setSelected(summary)
    setDrawerOpen(true)
    try {
      const detail = await getScenario(id)
      setSelected(detail || summary)
    } catch (err) {
      onError(err.message || `Failed to load ${id}`)
    }
  }, [onError])

  // ── React to external open-by-id requests (rail click, palette) ──────
  useEffect(() => {
    if (!requestOpenScenarioId) return
    // Try to satisfy from the current list first; otherwise fetch.
    const existing = scenarios.find(
      (s) => (s.scenario_id || s.id) === requestOpenScenarioId
    )
    if (existing) {
      handleSelect(existing)
    } else {
      getScenario(requestOpenScenarioId)
        .then((detail) => {
          if (!detail) return
          setSelected(detail)
          setDrawerOpen(true)
        })
        .catch((err) => onError(err.message || `Failed to load ${requestOpenScenarioId}`))
    }
  }, [requestOpenScenarioId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleClose = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  const selectedId = selected?.scenario_id || selected?.id || null

  // ── Launch hook (lifted from inspector) ──────────────────────────────
  const launch = useLaunchScenario(selected, {
    onRunComplete: (run) => { onRunComplete(run); setDrawerOpen(false) },
    onError,
  })

  // ── ⌘L / Ctrl+L → launch the currently selected scenario ────────────
  useEffect(() => {
    const handler = (e) => {
      const key = e.key ? e.key.toLowerCase() : ''
      if (!(e.metaKey || e.ctrlKey) || key !== 'l') return
      // Browser default for Ctrl/Cmd+L is the address bar — preempt only when we
      // actually have something to launch.
      if (selected && drawerOpen && !launch.launchDisabled) {
        e.preventDefault()
        launch.launch()
      } else if (selected) {
        e.preventDefault()
        onSurfaceMessage('Open the scenario drawer to launch (⌘K to find it)', 'warn')
      } else {
        // No selection — surface a hint, don't preempt
        onSurfaceMessage('Select a scenario first to use ⌘L', 'warn')
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [selected, drawerOpen, launch, onSurfaceMessage])

  // ── Apply client-side filters (technique) ────────────────────────────
  // techniqueFilter accepts:
  //   { tid: 'T1552.001', scenarioIds: ['SIM-MP-004', ...] }   (preferred — from Coverage tab)
  //   string 'T1552.001'                                       (fallback — palette etc.)
  const visibleScenarios = useMemo(() => {
    if (!techniqueFilter) return scenarios
    const ids = (techniqueFilter.scenarioIds || []).map((x) => String(x))
    const tid = (techniqueFilter.tid || techniqueFilter).toString().toUpperCase()
    return scenarios.filter((s) => {
      const sid = String(s.scenario_id || s.id || '')
      if (ids.length && ids.includes(sid)) return true
      const tids = collectTids(s).map((t) => t.toUpperCase())
      return tids.includes(tid)
    })
  }, [scenarios, techniqueFilter])

  const headMeta = useMemo(() => ({
    planeLabel: selectedPlane || 'all planes',
    count: visibleScenarios.length,
    totalCount: scenarios.length,
    lastUpdated: new Date().toISOString().substring(11, 19) + 'Z',
  }), [selectedPlane, visibleScenarios, scenarios])

  return (
    <div className="operations grid-bg">
      <div className="view-head">
        <div>
          <h1>Operations</h1>
          <div className="view-head__meta">
            Plane: <strong>{headMeta.planeLabel}</strong>
            {' · '}<span className="mono">
              {headMeta.count}
              {techniqueFilter && headMeta.count !== headMeta.totalCount && (
                <span style={{ color: 'var(--c-text-muted)' }}>
                  /{headMeta.totalCount}
                </span>
              )} scenarios
            </span>
            {' · '}<span className="mono">{pinnedIds.length} pinned</span>
            {' · '}last updated <span className="mono">{headMeta.lastUpdated}</span>
          </div>
          {techniqueFilter && (
            <div style={{ marginTop: 10 }}>
              <span
                className="chip chip--signal"
                style={{ paddingRight: 6 }}
              >
                Technique: {techniqueFilter.tid || techniqueFilter}
                <button
                  type="button"
                  onClick={onClearTechniqueFilter}
                  style={{
                    marginLeft: 6,
                    background: 'transparent',
                    border: 0,
                    color: 'inherit',
                    cursor: 'pointer',
                    fontFamily: 'var(--font-mono)',
                    fontSize: 12,
                    lineHeight: 1,
                  }}
                  aria-label="Clear technique filter"
                  title="Clear filter"
                >×</button>
              </span>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" disabled title="Filter palette — backlog">
            <span>Filter</span>
            <span className="kbd">F</span>
          </button>
          <button className="btn" disabled title="New scenario authoring — backlog">
            <span>New scenario</span>
            <span className="kbd">N</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{
          padding: 48,
          textAlign: 'center',
          color: 'var(--c-text-muted)',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          letterSpacing: '0.04em',
        }}>
          loading scenarios…
        </div>
      ) : (
        <ScenarioGrid
          scenarios={visibleScenarios}
          selectedScenarioId={selectedId}
          onSelectScenario={handleSelect}
          isPinned={isPinned}
          onTogglePin={togglePin}
        />
      )}

      <ScenarioInspector
        scenario={selected}
        open={drawerOpen}
        launch={launch}
        pinned={selectedId ? isPinned(selectedId) : false}
        onTogglePin={() => selectedId && togglePin(selectedId)}
        onClose={handleClose}
      />
    </div>
  )
}

// ─── helpers ─────────────────────────────────────────────────────────────

function collectTids(scenario) {
  const tids = new Set()
  if (scenario.mitre_technique) tids.add(scenario.mitre_technique)
  ;(scenario.additional_techniques || []).forEach((t) => {
    if (t && t.technique) tids.add(t.technique)
  })
  ;(scenario.steps || []).forEach((step) => {
    if (step.mitre_technique) tids.add(step.mitre_technique)
  })
  return Array.from(tids)
}
