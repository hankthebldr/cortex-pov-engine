import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import '../../styles/destinations/library.css'
import ScenarioGrid from './ScenarioGrid.jsx'
import ScenarioInspector from './ScenarioInspector.jsx'
import FilterPalette from './FilterPalette.jsx'
import useLaunchScenario from './useLaunchScenario.js'
import useScenarioFilter from './useScenarioFilter.js'
import useScenarioRunHistory from './useScenarioRunHistory.js'
import FlowStepper from './FlowStepper.jsx'
import { getScenarios, getScenario, getDoorReadiness, getDoors, getStagedScenarios } from '../../api/client.js'
import { applyScenarioFilters, scenarioFilterOptions, EMPTY_SCENARIO_FILTERS } from './scenarioFilters.js'
import { unmappedCounts } from './scenarioMapping.js'
import { agentIdOf } from '../../api/ids.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'

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
 *   onError                  — (msg) => void  for a fault this view does NOT render
 *                              itself (a scenario detail that would not load).
 *                              Faults it renders inline as an alert — the list
 *                              load, a refused launch or download — are not
 *                              sent here too, or they would be announced twice.
 *   onSurfaceMessage         — (msg, tone='info') => void   polite notices,
 *                              including the echo of an inline launch refusal
 */
export default function OperationsView({
  onNavigate = () => {},
  selectedPlane = null,
  onClearPlane = () => {},
  onSelectPlane = null,
  techniqueFilter = null,
  onClearTechniqueFilter = () => {},
  requestOpenScenarioId = null,
  pinnedIds = [],
  isPinned = () => false,
  togglePin = () => {},
  onRunComplete = () => {},
  onError = () => {},
  onSurfaceMessage = () => {},
  onOpenRunEvidence = () => {},
  onArmScenario = () => {},
  onContinueToLaunch = null,
}) {
  // The provider already holds the corpus. Seed the "all planes" view from it
  // so the first paint shows the Library instead of an empty grid behind
  // "loading scenarios…"; the fetch below still runs — it is the only
  // plane-filtered source and the retry path — and replaces the seed when it
  // lands. A plane-scoped view has no seed: the provider list is unfiltered.
  const env = useEnvironment()
  const seedFor = (plane) =>
    (!plane && Array.isArray(env.scenarios) && env.scenarios.length ? env.scenarios : [])
  const [scenarios, setScenarios]       = useState(() => seedFor(selectedPlane))
  const [loading, setLoading]           = useState(() => seedFor(selectedPlane).length === 0)
  // A failed list fetch is NOT an empty library. It used to fold into
  // setScenarios([]) and report through `onError` — which the Library
  // destination wires to a no-op — so a 500 or a 20 s timeout on
  // /api/scenarios rendered "No scenarios match the current filter." on a
  // SimCore whose /api/health was fine (no global banner either).
  const [loadError, setLoadError]       = useState(null)
  const [reloadKey, setReloadKey]       = useState(0)
  const [selected, setSelected]         = useState(null)
  const [drawerOpen, setDrawerOpen]     = useState(false)
  const [filterPaletteOpen, setFilterPaletteOpen] = useState(false)

  // Unified filter — plane + technique + multi-criteria from FilterPalette
  const scenarioFilter = useScenarioFilter()

  // Run history rollup — feeds the per-card history badge and the
  // inspector's per-scenario run list. We expose refresh so the launch
  // hook can repoll after a successful run — otherwise the badge stays
  // "never run" until the user navigates away and back.
  const { historyByScenario, runsByScenario, refresh: refreshHistory } = useScenarioRunHistory()

  // The v26 filter row (§4.4): component · use case · test case · status. The
  // plane rides the unified filter (and the rail); these sit on top of it.
  // Status absorbs the old All / Never run / Already run strip.
  const [rowFilters, setRowFilters] = useState(EMPTY_SCENARIO_FILTERS)
  const setRowFilter = useCallback((key, value) => {
    setRowFilters((prev) => ({ ...prev, [key]: value || '' }))
  }, [])

  // ── Door readiness (advisory chip + opt-in "Runnable here") ─────────────
  // One fetch for the whole library, keyed on the selected target agent. A
  // failure leaves the map null and the error visible in each chip's tooltip:
  // an unreachable endpoint must not read as "nothing to check".
  const doorAgentId = agentIdOf(env.agent)
  const [doorState, setDoorState] = useState({ map: null, rows: null, error: null })
  useEffect(() => {
    let cancelled = false
    Promise.all([getDoorReadiness(doorAgentId), getDoors()])
      .then(([ready, doors]) => {
        if (!cancelled) setDoorState({ map: ready.scenarios, rows: doors.doors, error: null })
      })
      .catch((err) => {
        if (!cancelled) setDoorState({ map: null, rows: null, error: err.message || 'door readiness failed' })
      })
    return () => { cancelled = true }
  }, [doorAgentId, reloadKey])

  // ── Extended library (the staged draft pairs) ───────────────────────────
  // GET /api/scenarios/staged. Listed below the corpus, never mixed into it:
  // a staged pair is not loaded, not launchable and not coverage. `absent`
  // (the directory is not on this SimCore) and `error` are each said on the
  // page; neither reads as an empty extended library.
  const [staged, setStaged] = useState({ status: 'loading', rows: [], code: null, detail: null })
  useEffect(() => {
    let cancelled = false
    setStaged((prev) => ({ ...prev, status: 'loading' }))
    getStagedScenarios()
      .then((data) => {
        if (cancelled) return
        setStaged(data.loaded
          ? { status: 'ok', rows: data.scenarios, code: null, detail: null }
          : { status: 'absent', rows: [], code: data.code || null, detail: data.detail || null })
      })
      .catch((err) => {
        if (!cancelled) setStaged({ status: 'error', rows: [], code: err.code || null, detail: err.message || 'request failed' })
      })
    return () => { cancelled = true }
  }, [reloadKey])
  // Which library the page lists: both (default), the corpus, or the extended one.
  const [library, setLibrary] = useState('all')

  // ── Fetch scenario list ──────────────────────────────────────────────
  useEffect(() => {
    // Only the newest plane's answer may land: switching EDR → CDR while the
    // EDR fetch is still in flight otherwise shows EDR's list under CDR.
    let cancelled = false
    // A seeded list stays on screen while the refresh is in flight; the
    // loading note is for when there is nothing truthful to show.
    const seed = seedFor(selectedPlane)
    if (seed.length) setScenarios(seed)
    setLoading(seed.length === 0)
    setLoadError(null)
    const params = selectedPlane ? { plane: selectedPlane } : {}
    getScenarios(params)
      .then((data) => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : (data && data.scenarios) || []
        setScenarios(list)
      })
      .catch((err) => {
        if (cancelled) return
        const msg = err.message || 'Failed to load scenarios'
        setScenarios([])
        // Rendered below as the one alert for this fault (with its Retry). It
        // is deliberately NOT also sent to `onError`: the Library destination
        // renders that as a second alert at the top of the page, so the same
        // failure was announced twice.
        setLoadError(msg)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [selectedPlane, reloadKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handle card selection — hydrate full detail ──────────────────────
  // The newest selection wins. Without this, clicking card A (slow detail
  // fetch) and then card B (fast) let A's detail land LAST: the drawer showed
  // A, `selected` — and therefore useLaunchScenario and ⌘L — targeted A, while
  // the armed id and the operator's intent were B. The wrong scenario would be
  // launched against the customer environment.
  const selectSeqRef = useRef(0)
  const handleSelect = useCallback(async (summary) => {
    const id = summary.scenario_id || summary.id
    const seq = ++selectSeqRef.current
    setSelected(summary)
    setDrawerOpen(true)
    // Arm this scenario for the ③ Launch step (redesign v2).
    onArmScenario(id)
    try {
      const detail = await getScenario(id)
      if (seq !== selectSeqRef.current) return
      setSelected(detail || summary)
    } catch (err) {
      if (seq !== selectSeqRef.current) return
      onError(err.message || `Failed to load ${id}`)
    }
  }, [onError, onArmScenario])

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
      const seq = ++selectSeqRef.current
      getScenario(requestOpenScenarioId)
        .then((detail) => {
          if (!detail || seq !== selectSeqRef.current) return
          setSelected(detail)
          setDrawerOpen(true)
        })
        .catch((err) => {
          if (seq !== selectSeqRef.current) return
          onError(err.message || `Failed to load ${requestOpenScenarioId}`)
        })
    }
  }, [requestOpenScenarioId]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleClose = useCallback(() => {
    setDrawerOpen(false)
  }, [])

  const selectedId = selected?.scenario_id || selected?.id || null

  // ── Launch hook (lifted from inspector) ──────────────────────────────
  const launch = useLaunchScenario(selected, {
    onRunComplete: (run) => {
      onRunComplete(run)
      setDrawerOpen(false)
      // Re-fetch the run list so the per-card history badge picks up
      // the new run without requiring a tab switch.
      refreshHistory()
    },
    // A refused launch or download is already an alert beside the button that
    // was clicked (LaunchErrorNotice / DownloadOutcome in the inspector). The
    // parent gets a polite echo, not a second alert — the same contract the
    // guided Launch step uses.
    onError: (msg) => onSurfaceMessage(msg, 'warn'),
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

  // ── Apply unified filter, then the filter row on top ────────────────
  // The corpus and the extended library go through the same filters; the
  // library select decides which of the two the page lists.
  const showCorpus = library !== 'extended'
  const showStaged = library !== 'corpus'
  const scopedRows = useMemo(
    () => [...(showCorpus ? scenarios : []), ...(showStaged ? staged.rows : [])],
    [scenarios, staged.rows, showCorpus, showStaged],
  )
  const filterRows = useCallback((rows) => {
    const filtered = scenarioFilter.applyTo(rows, doorState.map)
    return applyScenarioFilters(filtered, rowFilters, { readiness: doorState.map, history: historyByScenario })
  }, [scenarioFilter.filter, rowFilters, historyByScenario, doorState.map]) // eslint-disable-line react-hooks/exhaustive-deps
  const visibleScenarios = useMemo(
    () => (showCorpus ? filterRows(scenarios) : []),
    [scenarios, showCorpus, filterRows],
  )
  const visibleStaged = useMemo(
    () => (showStaged ? filterRows(staged.rows) : []),
    [staged.rows, showStaged, filterRows],
  )
  const unmapped = useMemo(() => unmappedCounts(scopedRows, doorState.map), [scopedRows, doorState.map])

  const filterOptions = useMemo(
    () => scenarioFilterOptions(scopedRows, { readiness: doorState.map, doorRows: doorState.rows, history: historyByScenario }),
    [scopedRows, doorState.map, doorState.rows, historyByScenario],
  )
  const activePlane = selectedPlane || scenarioFilter.filter.plane || ''
  const choosePlane = useCallback((code) => {
    scenarioFilter.setPlane(code || null)
    if (onSelectPlane) onSelectPlane(code || null)
    else if (!code) onClearPlane()
  }, [onSelectPlane, onClearPlane]) // eslint-disable-line react-hooks/exhaustive-deps
  const rowFiltered = !!(activePlane || rowFilters.component || rowFilters.uc || rowFilters.tc
    || rowFilters.status || rowFilters.unmapped || library !== 'all')
  const clearRow = useCallback(() => {
    setRowFilters(EMPTY_SCENARIO_FILTERS)
    setLibrary('all')
    choosePlane(null)
  }, [choosePlane])

  // ── Card verbs (v26 §4.4) ─────────────────────────────────────────────
  // Run: arm the scenario and hand it to the launch step. Customize: open its
  // chain in Build a chain. Neither opens the sheet.
  const runScenario = useCallback((id) => {
    onArmScenario(id)
    if (onContinueToLaunch) onContinueToLaunch(id)
  }, [onArmScenario, onContinueToLaunch])
  const customizeScenario = useCallback((id) => onNavigate('composer', { open: id }), [onNavigate])

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
      {/* Compose → simulate: the scenario picked here rides into the next pages. */}
      <FlowStepper current="library" scenarioId={selectedId} onNavigate={onNavigate} />
      <div className="view-head">
        <div>
          <div className="ops-accent-bar" aria-hidden="true" />
          {/* Masthead eyebrow: the nav group alone, no "· Phase N" — this
              product has no app-level phase stepper to echo (M-4; mirrors
              CoverageView's CoverageKicker, the reference for this pattern). */}
          <h1>Scenarios</h1>
          <div className="view-head__meta">
            Plane: <strong>{headMeta.planeLabel}</strong>
            {' · '}<span className="mono">
              {/* A count is a fact about a list that ARRIVED. While the fetch
                  is in flight this read "0 scenarios · loading…" — a zero
                  rendered as a fact, which is the Gate A5 failure mode. */}
              <span data-testid="library-count">
                {loadError ? '—' : loading ? '…' : headMeta.count}
              </span>
              {techniqueFilter && headMeta.count !== headMeta.totalCount && (
                <span className="ops-head__total">
                  /{headMeta.totalCount}
                </span>
              )} scenarios
            </span>
            {' · '}<ExtendedCount staged={staged} />
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
        <div className="ops-toolbar">
          <input
            type="search"
            className="lab__input ops-search"
            placeholder="Search scenarios (name · id · technique)…"
            aria-label="Search scenarios"
            value={scenarioFilter.filter.query}
            onChange={(e) => scenarioFilter.setQuery(e.target.value)}
          />
          <button
            type="button"
            className={'btn' + (scenarioFilter.activeCount > 0 ? ' btn--primary' : '')}
            onClick={() => setFilterPaletteOpen(true)}
            title="Open filter palette"
          >
            <span>Filter</span>
            {scenarioFilter.activeCount > 0 && (
              <span className="kbd ops-filter-kbd">
                {scenarioFilter.activeCount}
              </span>
            )}
            <span className="kbd">⌘F</span>
          </button>
          <button
            type="button"
            className={'btn' + (scenarioFilter.filter.runnable ? ' btn--primary' : '')}
            aria-pressed={scenarioFilter.filter.runnable}
            onClick={() => scenarioFilter.setRunnable(!scenarioFilter.filter.runnable)}
            title="Show only scenarios with at least one detection whose ingestion door is open. Off by default: nothing is hidden."
            data-testid="filter-runnable"
          >
            <span>Runnable here</span>
          </button>
          <button className="btn" disabled title="New scenario authoring — backlog">
            <span>New scenario</span>
            <span className="kbd">N</span>
          </button>
        </div>
      </div>

      <ScenarioFilterRow
        options={filterOptions}
        plane={activePlane}
        onPlane={choosePlane}
        filters={rowFilters}
        onFilter={setRowFilter}
        library={library}
        onLibrary={setLibrary}
        staged={staged}
        unmapped={unmapped}
        shown={visibleScenarios.length + visibleStaged.length}
        total={scopedRows.length}
        filtered={rowFiltered}
        onClear={clearRow}
      />

      {!showCorpus ? null : loading ? (
        <div className="ops-loading-note">
          loading scenarios…
        </div>
      ) : loadError ? (
        <div className="ops-loading-note ops-load-error" role="alert" data-testid="library-load-error">
          <strong>Scenarios could not be loaded.</strong>{' '}
          <span className="mono">{loadError}</span>{' '}
          The library is not empty — SimCore did not answer the list request.{' '}
          <button type="button" className="btn btn--xs" onClick={() => setReloadKey((k) => k + 1)}>
            ↻ Retry
          </button>
        </div>
      ) : (
        <ScenarioGrid
          scenarios={visibleScenarios}
          selectedScenarioId={selectedId}
          onSelectScenario={handleSelect}
          onRunScenario={onContinueToLaunch ? runScenario : null}
          onCustomizeScenario={customizeScenario}
          isPinned={isPinned}
          onTogglePin={togglePin}
          historyByScenario={historyByScenario}
          doorReadiness={doorState.map}
          doorRows={doorState.rows}
          doorAgentId={doorAgentId}
          doorError={doorState.error}
        />
      )}

      {showStaged && (
        <ExtendedLibrary staged={staged} rows={visibleStaged} doorRows={doorState.rows} />
      )}

      <ScenarioInspector
        scenario={selected}
        open={drawerOpen}
        launch={launch}
        pinned={selectedId ? isPinned(selectedId) : false}
        onTogglePin={() => selectedId && togglePin(selectedId)}
        onClose={handleClose}
        runHistory={selectedId && runsByScenario.get
          ? (runsByScenario.get(selectedId) || [])
          : []}
        onOpenRunEvidence={onOpenRunEvidence}
        // Customize: Build a chain with this scenario's chain loaded.
        onCustomize={selectedId ? () => onNavigate('composer', { open: selectedId }) : null}
        // Run: the launch step with this scenario loaded. It is armed on
        // select; close the sheet and hand off.
        onRun={onContinueToLaunch
          ? () => {
              if (selectedId) onArmScenario(selectedId)
              setDrawerOpen(false)
              onContinueToLaunch(selectedId)
            }
          : null}
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

/* ─── Filter row (design v26 §4.4) ────────────────────────────────── */

/**
 * Library · Plane · Component · Use case · Test case · Status · Unmapped, then
 * "N of M shown" and Clear. Options come from `scenarioFilterOptions` — read
 * from the loaded scenarios, the door registry and the run history, never
 * authored here. Unmapped is a toggle that carries its count, so the gap is
 * visible before anyone opens it.
 */
function ScenarioFilterRow({
  options, plane, onPlane, filters, onFilter, library = 'all', onLibrary = () => {},
  staged = null, unmapped = { unmapped: 0, sourceUnknown: 0 }, shown, total, filtered, onClear,
}) {
  const uid = React.useId()
  const stagedLabel = !staged || staged.status === 'loading' ? 'Extended · …'
    : staged.status === 'ok' ? `Extended · ${staged.rows.length}`
      : staged.status === 'absent' ? 'Extended · not on this SimCore'
        : 'Extended · could not be loaded'
  const libraries = [{ v: 'corpus', label: 'Corpus' }, { v: 'extended', label: stagedLabel }]
  const unmappedTitle = 'Scenarios missing a UC/TC, a detection source or a MITRE technique, as SimCore reports them.'
    + (unmapped.sourceUnknown
      ? ` ${unmapped.sourceUnknown} more could not be checked for a detection source and are not counted.`
      : '')
  const field = (key, label, all, list, value, onChange) => (
    <span className="ops-filter" key={key}>
      <label className="ops-filter__label" htmlFor={`${uid}-${key}`}>{label}</label>
      <select
        id={`${uid}-${key}`}
        className={'ops-filter__select' + (value ? ' ops-filter__select--on' : '')}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{all}</option>
        {list.map((o) => (
          <option key={o.v} value={o.v} title={o.title}>{o.label}</option>
        ))}
      </select>
    </span>
  )
  return (
    <div className="ops-filter-row" data-testid="scenario-filter-row">
      {field('library', 'Library', 'All libraries', libraries, library === 'all' ? '' : library, (v) => onLibrary(v || 'all'))}
      {field('plane', 'Plane', 'All planes', options.planes.map((p) => ({ v: p, label: p })), plane, onPlane)}
      {field('component', 'Component', 'All components', options.components, filters.component, (v) => onFilter('component', v))}
      {field('uc', 'Use case', 'All use cases', options.useCases, filters.uc, (v) => onFilter('uc', v))}
      {field('tc', 'Test case', 'All test cases', options.testCases, filters.tc, (v) => onFilter('tc', v))}
      {field('status', 'Status', 'Any status', options.statuses, filters.status, (v) => onFilter('status', v))}
      <button
        type="button"
        className={'ops-filter-toggle' + (filters.unmapped ? ' ops-filter-toggle--on' : '')}
        aria-pressed={!!filters.unmapped}
        onClick={() => onFilter('unmapped', !filters.unmapped)}
        title={unmappedTitle}
        data-testid="filter-unmapped"
      >
        Unmapped <span className="ops-filter-toggle__count mono" data-testid="filter-unmapped-count">{unmapped.unmapped}</span>
      </button>
      <span className="ops-filter-row__spacer" />
      <span className="ops-filter-row__count mono" data-testid="scenario-filter-count">
        {shown} of {total} shown
      </span>
      {filtered && (
        <button type="button" className="btn btn--xs" aria-label="Clear filters" onClick={onClear}>Clear</button>
      )}
    </div>
  )
}

/* ─── Extended library (the staged draft pairs) ───────────────────── */

/** The masthead's extended-library count, or what stands in for it. */
function ExtendedCount({ staged }) {
  if (staged.status === 'ok') {
    return (
      <span className="mono">
        <span data-testid="library-staged-count">{staged.rows.length}</span> in the extended library
      </span>
    )
  }
  if (staged.status === 'loading') return <span className="mono">… in the extended library</span>
  return <span>extended library {staged.status === 'absent' ? 'not on this SimCore' : 'not loaded'}</span>
}

/**
 * The staged pairs under drafts/detection-library, listed below the corpus and
 * never mixed into it. Each card says what it is and what it still needs; none
 * can be opened, pinned or run. An absent directory and a failed request are
 * said as such, never as an empty list.
 */
function ExtendedLibrary({ staged, rows, doorRows }) {
  const headingId = React.useId()
  return (
    <section className="ops-extended" aria-labelledby={headingId} data-testid="extended-library">
      <div className="ops-extended__head">
        <h2 id={headingId} className="ops-extended__title">Extended library</h2>
        <p className="ops-extended__note">
          Staged pairs under <span className="mono">drafts/detection-library</span>: not loaded by SimCore,
          not launchable and not counted as coverage. Each still needs a review, a tested cleanup, UC/TC refs
          and a lab run before it is promoted into the library.
        </p>
      </div>
      {staged.status === 'loading' ? (
        <div className="ops-loading-note">loading the extended library…</div>
      ) : staged.status === 'absent' ? (
        <div className="ops-loading-note" role="status" data-testid="extended-library-absent">
          The extended library is not on this SimCore{staged.code ? <> (<span className="mono">{staged.code}</span>)</> : null}.
          {staged.detail ? <> {staged.detail}.</> : null}
        </div>
      ) : staged.status === 'error' ? (
        <div className="ops-loading-note ops-load-error" role="status" data-testid="extended-library-error">
          <strong>The extended library could not be loaded.</strong>{' '}
          <span className="mono">{staged.detail}</span>
        </div>
      ) : (
        <ScenarioGrid
          scenarios={rows}
          doorRows={doorRows}
          emptyText="No staged pair matches the current filter."
        />
      )}
    </section>
  )
}
