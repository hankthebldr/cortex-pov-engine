import React, { useState, useEffect, useMemo, useCallback } from 'react'
import ScenarioGrid from './ScenarioGrid.jsx'
import ScenarioInspector from './ScenarioInspector.jsx'
import FilterPalette from './FilterPalette.jsx'
import useLaunchScenario from './useLaunchScenario.js'
import useScenarioFilter from './useScenarioFilter.js'
import useScenarioRunHistory from './useScenarioRunHistory.js'
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
  onClearPlane = () => {},
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
  const [scenarios, setScenarios]       = useState([])
  const [loading, setLoading]           = useState(true)
  const [selected, setSelected]         = useState(null)
  const [drawerOpen, setDrawerOpen]     = useState(false)
  const [filterPaletteOpen, setFilterPaletteOpen] = useState(false)

  // Unified filter — plane + technique + multi-criteria from FilterPalette
  const scenarioFilter = useScenarioFilter()

  // Run history rollup — feeds the per-card history badge.
  const { historyByScenario } = useScenarioRunHistory()

  // History-based view mode: 'all' (default) | 'never' (never run) | 'run' (already run).
  // Sits on top of the unified filter — lets DCs target gaps without re-keying every
  // filter criterion through the palette.
  const [historyMode, setHistoryMode] = useState('all')

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

  // ── Sync external filter signals into the unified hook ──────────────
  // selectedPlane comes from the rail; techniqueFilter from the Coverage tab.
  // Both feed into useScenarioFilter so the chip strip is canonical.
  useEffect(() => {
    scenarioFilter.setPlane(selectedPlane)
  }, [selectedPlane]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (techniqueFilter) {
      const tid = typeof techniqueFilter === 'string'
        ? techniqueFilter
        : techniqueFilter.tid
      const ids = typeof techniqueFilter === 'string'
        ? []
        : (techniqueFilter.scenarioIds || [])
      scenarioFilter.setTechnique(tid, ids)
    } else {
      scenarioFilter.setTechnique(null)
    }
  }, [techniqueFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Apply unified filter, then layer history mode on top ────────────
  const visibleScenarios = useMemo(() => {
    const filtered = scenarioFilter.applyTo(scenarios)
    if (historyMode === 'all') return filtered
    return filtered.filter((s) => {
      const id = s.scenario_id || s.id
      const hasHistory = historyByScenario.has(id) && historyByScenario.get(id).count > 0
      return historyMode === 'never' ? !hasHistory : hasHistory
    })
  }, [scenarios, scenarioFilter.filter, historyMode, historyByScenario]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── ⌘F / Ctrl+F → open filter palette ────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      const key = e.key ? e.key.toLowerCase() : ''
      if ((e.metaKey || e.ctrlKey) && key === 'f') {
        // Preempt the browser's in-page find — DCs want scenario filter here.
        e.preventDefault()
        setFilterPaletteOpen((v) => !v)
      } else if (key === 'escape' && filterPaletteOpen) {
        setFilterPaletteOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [filterPaletteOpen])

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
          {!scenarioFilter.isEmpty && (
            <FilterChips
              filter={scenarioFilter.filter}
              onClearOne={(field) => {
                // Plane and technique cross-link state lives in the parent; the
                // hook's clearOne only clears local state. Mirror up.
                if (field === 'plane')     onClearPlane()
                if (field === 'technique') onClearTechniqueFilter()
                scenarioFilter.clearOne(field)
              }}
              onClearAll={() => {
                onClearPlane()
                onClearTechniqueFilter()
                scenarioFilter.clearAll()
              }}
            />
          )}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className={'btn' + (scenarioFilter.activeCount > 0 ? ' btn--primary' : '')}
            onClick={() => setFilterPaletteOpen(true)}
            title="Open filter palette"
          >
            <span>Filter</span>
            {scenarioFilter.activeCount > 0 && (
              <span className="kbd" style={{ background: 'rgba(5,10,20,0.25)', borderColor: 'rgba(5,10,20,0.25)' }}>
                {scenarioFilter.activeCount}
              </span>
            )}
            <span className="kbd">⌘F</span>
          </button>
          <button className="btn" disabled title="New scenario authoring — backlog">
            <span>New scenario</span>
            <span className="kbd">N</span>
          </button>
        </div>
      </div>

      <HistoryModeStrip
        mode={historyMode}
        onChange={setHistoryMode}
        counts={countByHistoryMode(scenarios, historyByScenario)}
      />

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
          historyByScenario={historyByScenario}
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

      <FilterPalette
        open={filterPaletteOpen}
        onClose={() => setFilterPaletteOpen(false)}
        scenarios={scenarios}
        filter={scenarioFilter.filter}
        onToggle={scenarioFilter.toggle}
        onClearAll={() => {
          onClearPlane()
          onClearTechniqueFilter()
          scenarioFilter.clearAll()
        }}
        matchCount={visibleScenarios.length}
        totalCount={scenarios.length}
      />
    </div>
  )
}

// ─── helpers ─────────────────────────────────────────────────────────────

/**
 * FilterChips — render one chip per active filter criterion in the header.
 * Each chip's × calls clearOne(field). Multi-value Sets render as a single
 * chip with the count rather than one chip per value (the palette already
 * shows the per-value detail).
 */
function FilterChips({ filter, onClearOne, onClearAll }) {
  const chips = []
  if (filter.plane) {
    chips.push({ field: 'plane', label: `Plane: ${filter.plane}` })
  }
  if (filter.technique) {
    chips.push({ field: 'technique', label: `Technique: ${filter.technique.tid}` })
  }
  const SET_LABELS = {
    tactics:      'Tactics',
    techniques:   'Techniques',
    actors:       'Actors',
    difficulties: 'Difficulty',
    identities:   'Identity',
    detTypes:     'Det type',
    tags:         'Tags',
  }
  for (const [field, label] of Object.entries(SET_LABELS)) {
    const set = filter[field]
    if (set && set.size > 0) {
      const preview = Array.from(set).slice(0, 2).join(', ')
      const more = set.size > 2 ? ` +${set.size - 2}` : ''
      chips.push({ field, label: `${label}: ${preview}${more}` })
    }
  }
  if (chips.length === 0) return null

  return (
    <div className="filter-chip-strip">
      {chips.map((c) => (
        <button
          key={c.field}
          type="button"
          className="chip chip--signal filter-chip-strip__chip"
          onClick={() => onClearOne(c.field)}
          aria-label={`Clear ${c.label}`}
          title={`Clear ${c.label}`}
        >
          {c.label}
          <span className="filter-chip-strip__x" aria-hidden="true">×</span>
        </button>
      ))}
      {chips.length > 1 && (
        <button
          type="button"
          className="filter-chip-strip__clear-all"
          onClick={onClearAll}
        >
          clear all
        </button>
      )}
    </div>
  )
}

/* ─── History-mode strip (Never run / Already run / All) ──────────── */

/**
 * Tally how many scenarios fall into each history bucket. Cheap O(n)
 * loop; runs every render but n is tiny.
 */
function countByHistoryMode(scenarios, history) {
  const counts = { all: scenarios.length, never: 0, run: 0 }
  if (!history || !history.get) return counts
  for (const s of scenarios) {
    const id = s.scenario_id || s.id
    const h = history.get(id)
    if (h && h.count > 0) counts.run += 1
    else counts.never += 1
  }
  return counts
}

function HistoryModeStrip({ mode, onChange, counts }) {
  const options = [
    { id: 'all',   label: 'All',         count: counts.all   },
    { id: 'never', label: 'Never run',   count: counts.never },
    { id: 'run',   label: 'Already run', count: counts.run   },
  ]
  return (
    <div className="lab__segmented ops-history-strip" role="tablist" aria-label="Filter by run history">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          role="tab"
          aria-selected={mode === o.id}
          className={mode === o.id ? 'is-active' : ''}
          onClick={() => onChange(o.id)}
          title={`Show ${o.label.toLowerCase()} scenarios`}
        >
          {o.label}
          <span className="kbd ops-history-strip__count">{o.count}</span>
        </button>
      ))}
    </div>
  )
}
