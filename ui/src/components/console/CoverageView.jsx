import React, { useEffect, useMemo, useState } from 'react'
import { getDoorReadiness, getDoors, getRuns, getScenarios } from '../../api/client.js'
import { agentIdOf } from '../../api/ids.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import useMitreCoverage from './useMitreCoverage.js'
import StackCoverageView from './StackCoverageView.jsx'
import CompetitiveView from './CompetitiveView.jsx'
import { downloadLayer } from './exportNavigatorLayer.js'
import { PageIcon, StatusIcon } from './kit/index.js'
import { planeLabel, planeTint } from './povdata/planes.js'
import {
  ALL, NO_DOOR, NO_SCENARIO, NO_SOURCE, NO_USE_CASE, NOT_CHECKED, STATE, STATE_ORDER,
  countStates, detectionSourcesOf, doorStatus, idOf, mitreMatrix, orderPlanes,
  planeDoorGrid, scenarioStates, sortByState, sourceUseCaseGrid, stateOf,
} from './dashboardModel.js'
import '../../styles/destinations/coverage.css'

/**
 * CoverageView — the Dashboards destination (route id `coverage`, design v33
 * C8). Every scenario in the library, counted from GET /api/scenarios, in
 * three views:
 *
 *   Detection source × Use case   use cases down, the planes the scenarios
 *                                 raise detections on across
 *   MITRE ATT&CK                  GET /api/mitre/coverage on the 14 Enterprise
 *                                 tactics; a technique no scenario in scope
 *                                 exercises is drawn dashed
 *   Planes × doors                GET /api/doors/readiness: scenarios on each
 *                                 plane waiting on each door that is not open
 *
 * plus the two lenses the old Coverage page carried (PANW stack, Competitive),
 * kept as they were.
 *
 * What a scenario has proven comes from the run list only (dashboardModel.js):
 * Detected means a read-back run of it detected something. A scenario that is
 * authored, or that ran and was not read back, is never coloured as covered.
 *
 * Every cell and tile opens the scenarios it counts; each scenario opens its
 * sheet on Scenarios (`#/library?open=<id>`). The tab rides `?tab=`.
 */
export const DASH_TABS = Object.freeze([
  ['sources', 'Detection source × Use case'],
  ['mitre', 'MITRE ATT&CK'],
  ['doors', 'Planes × doors'],
  ['stack', 'PANW stack'],
  ['competitive', 'Competitive'],
])
const TAB_IDS = DASH_TABS.map(([id]) => id)

const errText = (err, fallback) => (err && (err.message || String(err))) || fallback

export default function CoverageView({ onNavigate, params = {}, setParams } = {}) {
  const fromParams = TAB_IDS.includes(params.tab) ? params.tab : 'sources'
  const [tab, setTab] = useState(fromParams)
  useEffect(() => { setTab(fromParams) }, [fromParams])
  const choose = (id) => {
    setTab(id)
    if (typeof setParams === 'function') setParams({ tab: id })
  }

  // A sibling surface (the Packages detail panel) can emit cortex:navigate-ttp
  // when a DC clicks a TTP-ref chip; the TTP browser is its own destination.
  useEffect(() => {
    const handler = (e) => {
      const ttpId = e?.detail?.ttpId
      if (typeof ttpId !== 'string' || !ttpId) return
      if (typeof onNavigate === 'function') onNavigate('ttps', { ttp: ttpId })
      else window.location.hash = `#/ttps?ttp=${encodeURIComponent(ttpId)}`
    }
    window.addEventListener('cortex:navigate-ttp', handler)
    return () => window.removeEventListener('cortex:navigate-ttp', handler)
  }, [onNavigate])

  const data = useCorpus()
  const corpusTab = tab === 'sources' || tab === 'mitre' || tab === 'doors'

  return (
    <div className="coverage dash" data-testid="dashboards">
      <header className="dash__head">
        <div className="dash__title">
          <PageIcon icon="line-chart" size="tile" />
          <h1 className="pov-page__title">Dashboards</h1>
        </div>
        <p className="pov-page__lede">
          Every scenario in the library, counted three ways. Open any cell or technique to see the
          scenarios behind it and what they have proven so far. Detected means a run of the
          scenario was read back from the tenant and detected something.
        </p>
      </header>

      <CorpusSummary data={data} />

      <div className="dash__tabs" role="tablist" aria-label="Dashboards">
        {DASH_TABS.map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`dash-tab-${id}`}
            data-testid={`dash-tab-${id}`}
            aria-selected={tab === id}
            aria-controls="dash-panel"
            className={'dash__tab' + (tab === id ? ' dash__tab--on' : '')}
            onClick={() => choose(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id="dash-panel" aria-labelledby={`dash-tab-${tab}`} className="dash__panel">
        {corpusTab && !data.ready ? (
          <CorpusPending data={data} />
        ) : tab === 'sources' ? (
          <SourcesPanel scenarios={data.scenarios} states={data.states} />
        ) : tab === 'mitre' ? (
          <MitrePanel scenarios={data.scenarios} states={data.states} />
        ) : tab === 'doors' ? (
          <DoorsPanel scenarios={data.scenarios} states={data.states} />
        ) : tab === 'stack' ? (
          <StackCoverageView
            onFilterByCell={(_productId, _tactic, scenarioIds) => {
              if (typeof onNavigate === 'function') {
                onNavigate('library', { technique: 'STACK', scenarios: (scenarioIds || []).join(',') })
              }
            }}
          />
        ) : (
          <CompetitiveView />
        )}
      </div>
    </div>
  )
}

/* ─── Data ─────────────────────────────────────────────────────────────── */

/**
 * The scenario list and the run list, read once per visit. A failed run list
 * is NOT an empty one: it leaves `states` null, and every scenario reads
 * Can't check instead of Never run.
 */
function useCorpus() {
  const [scenarios, setScenarios] = useState(null)
  const [scenarioError, setScenarioError] = useState(null)
  const [runs, setRuns] = useState(null)
  const [runError, setRunError] = useState(null)

  useEffect(() => {
    let live = true
    getScenarios({})
      .then((list) => { if (live) setScenarios(Array.isArray(list) ? list : []) })
      .catch((err) => { if (live) setScenarioError(errText(err, 'the scenario list did not load')) })
    getRuns({ fields: 'summary' })
      .then((list) => { if (live) setRuns(list) })
      .catch((err) => { if (live) setRunError(errText(err, 'the run list did not load')) })
    return () => { live = false }
  }, [])

  const derived = useMemo(() => {
    if (runError || !runs) return { states: null, error: runError }
    try {
      return { states: scenarioStates(runs), error: null }
    } catch (err) {
      return { states: null, error: errText(err, 'the run list has an unrecognised shape') }
    }
  }, [runs, runError])

  const runsSettled = runs !== null || runError !== null
  return {
    scenarios,
    scenarioError,
    runError: derived.error,
    states: derived.states,
    loading: (scenarios === null && !scenarioError) || !runsSettled,
    ready: scenarios !== null && runsSettled && scenarios.length > 0,
  }
}

/* ─── Summary ──────────────────────────────────────────────────────────── */

function CorpusSummary({ data }) {
  if (data.loading || data.scenarioError || !data.scenarios || data.scenarios.length === 0) return null
  const ids = data.scenarios.map(idOf).filter(Boolean)
  const counts = countStates(ids, data.states)
  return (
    <>
      <div className="pov-stats dash__stats" data-testid="dash-stats">
        <div className="pov-stat">
          <span className="pov-stat__n mono" data-testid="dash-stat-total">{ids.length}</span>
          <span className="dash__stat-k">Scenarios counted</span>
          <span className="dash__stat-sub">every scenario in the library</span>
        </div>
        {STATE_ORDER.map((k) => (
          <div className="pov-stat" key={k}>
            <span className="pov-stat__n mono" data-testid={`dash-stat-${k}`}>{data.states ? counts[k] : '—'}</span>
            <span className="dash__stat-k"><StatusIcon status={STATE[k].status} word={STATE[k].word} /></span>
            <span className="dash__stat-sub">{STATE[k].sub}</span>
          </div>
        ))}
      </div>
      {data.runError && (
        <p className="dash__notice" role="status" data-testid="dash-runs-error">
          <StatusIcon status="down" word="Not loaded" />
          <span>
            The run list did not load ({data.runError}), so no scenario can be called detected or
            never run: every state below reads Can&apos;t check.
          </span>
        </p>
      )}
    </>
  )
}

function CorpusPending({ data }) {
  if (data.loading) return <p className="dash__empty" role="status">Loading scenarios and runs…</p>
  if (data.scenarioError) {
    return (
      <p className="dash__notice" role="alert">
        <StatusIcon status="down" word="Not loaded" />
        <span>The scenario list did not load ({data.scenarioError}), so there is nothing to count.</span>
      </p>
    )
  }
  return (
    <p className="dash__notice" role="status" data-testid="dash-no-scenarios">
      <StatusIcon status="degraded" word="No scenarios" />
      <span>SimCore returned no scenarios, so there is nothing to count. Load the scenario library and come back.</span>
    </p>
  )
}

/* ─── Shared pieces ────────────────────────────────────────────────────── */

const pct = (n, d) => `${d ? Math.round((n / d) * 100) : 0}%`

/** Detected (green), not seen yet (yellow), pending (grey); the rest of the track never ran. */
function StateBar({ counts, n }) {
  return (
    <span className="dash-bar" aria-hidden="true">
      <span className="dash-bar__seg dash-bar__seg--detected" style={{ width: pct(counts.detected, n) }} />
      <span className="dash-bar__seg dash-bar__seg--not_seen" style={{ width: pct(counts.not_seen, n) }} />
      <span className="dash-bar__seg dash-bar__seg--pending" style={{ width: pct(counts.pending, n) }} />
    </span>
  )
}

function countLine(ids, states) {
  if (!states) return `${ids.length} scenarios · states can't be checked`
  const c = countStates(ids, states)
  return `${ids.length} scenarios · ${c.detected} detected · ${c.not_seen} not seen yet · `
    + `${c.pending} pending · ${c.never} never run`
}

/** The cell or tile that is open, and the scenarios behind it. */
function PickPanel({ pick, hint, scenarios, states }) {
  const byId = useMemo(() => new Map((scenarios || []).map((s) => [idOf(s), s])), [scenarios])
  if (!pick) return <aside className="dash__card dash__pick" data-testid="dash-pick-hint">{hint}</aside>
  return (
    <aside className="dash__card dash__pick dash__pick--on" aria-label={pick.title} data-testid="dash-pick">
      <div className="mono dash__pick-title">{pick.title}</div>
      <div className="dash__pick-sub">{pick.sub}</div>
      {pick.ids.length === 0 ? (
        <>
          <p className="dash__pick-empty">{pick.emptyNote}</p>
          <a className="dash__link" href="#/composer">Open Build a chain →</a>
        </>
      ) : (
        <ul className="dash__pick-list">
          {sortByState(pick.ids, states).map((id) => {
            const st = STATE[stateOf(states, id)]
            return (
              <li key={id}>
                <a className="dash__pick-row" href={`#/library?open=${encodeURIComponent(id)}`} data-testid={`dash-open-${id}`}>
                  <span className="dash__pick-id">
                    <span className="mono">{id}</span>
                    <span className="dash__pick-name">{byId.get(id)?.name || ''}</span>
                  </span>
                  <StatusIcon status={st.status} word={st.word} />
                </a>
              </li>
            )
          })}
        </ul>
      )}
    </aside>
  )
}

function CountCell({ ids, states, on, label, onPick, testId }) {
  const counts = countStates(ids, states)
  return (
    <button
      type="button"
      className={'dash-cell' + (on ? ' dash-cell--on' : '')}
      aria-pressed={on}
      aria-label={label}
      data-testid={testId}
      onClick={onPick}
    >
      <span className="mono dash-cell__n">{ids.length}</span>
      <StateBar counts={counts} n={ids.length} />
    </button>
  )
}

/* ─── Detection source × Use case ──────────────────────────────────────── */

function SourcesPanel({ scenarios, states }) {
  const grid = useMemo(() => sourceUseCaseGrid(scenarios), [scenarios])
  const [pickKey, setPickKey] = useState(null)

  let pick = null
  if (pickKey) {
    const row = grid.rows.find((r) => r.id === pickKey.row)
    const ids = row?.cells[pickKey.col] || []
    pick = {
      title: `${pickKey.row} × ${pickKey.col === NO_SOURCE ? 'no detection' : pickKey.col}`,
      sub: countLine(ids, states),
      ids,
    }
  }

  return (
    <div className="dash__split">
      <div className="dash__card dash__scroll">
        <table className="dash-grid" data-testid="dash-sources-grid">
          <caption className="sr-only">
            Scenarios per use case and detection source. A scenario with detections on two planes is
            counted under both.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="dash-grid__corner">Use case</th>
              {grid.columns.map((c) => (
                <th scope="col" key={c} className="dash-grid__col">
                  <span className="mono">{c === NO_SOURCE ? 'None' : c}</span>
                  <span className="dash-grid__sub">{c === NO_SOURCE ? 'no detection' : planeLabel(c)}</span>
                </th>
              ))}
              <th scope="col" className="dash-grid__col">Total</th>
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((r) => (
              <tr key={r.id}>
                <th scope="row" className="dash-grid__row">
                  <span className="mono">{r.id === NO_USE_CASE ? 'No use case' : r.id}</span>
                  <span className="dash-grid__sub">{r.name}</span>
                </th>
                {grid.columns.map((c) => {
                  const ids = r.cells[c] || []
                  if (!ids.length) return <td key={c} className="dash-grid__none"><span aria-label="none">—</span></td>
                  const on = pickKey?.row === r.id && pickKey?.col === c
                  const detected = states ? countStates(ids, states).detected : null
                  return (
                    <td key={c}>
                      <CountCell
                        ids={ids}
                        states={states}
                        on={on}
                        testId={`dash-cell-${r.id}-${c}`}
                        label={`${r.id} through ${c === NO_SOURCE ? 'no detection' : c}: ${ids.length} scenarios`
                          + (detected === null ? '' : `, ${detected} detected`)}
                        onPick={() => setPickKey({ row: r.id, col: c })}
                      />
                    </td>
                  )
                })}
                <td className="mono dash-grid__total">{r.ids.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <PickPanel
        pick={pick}
        scenarios={scenarios}
        states={states}
        hint={
          'Open a cell to list its scenarios. The bar under each count is detected (green), '
          + 'not seen yet (yellow) and pending (grey); the rest of the track never ran.'
        }
      />
    </div>
  )
}

/* ─── MITRE ATT&CK ─────────────────────────────────────────────────────── */

const SHOW_GAPS = 'Gaps only'

function FilterSelect({ label, value, options, onChange, testId }) {
  return (
    <label className="dash__filter">
      {label}
      <select
        className={'dash__select' + (value !== ALL ? ' dash__select--on' : '')}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      >
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  )
}

function MitrePanel({ scenarios, states }) {
  const { data, loading, error } = useMitreCoverage()
  const [filters, setFilters] = useState({ plane: ALL, source: ALL, show: ALL })
  const [pickId, setPickId] = useState(null)
  const set = (k) => (v) => { setFilters((f) => ({ ...f, [k]: v })); setPickId(null) }

  const planes = useMemo(() => orderPlanes(scenarios.map((s) => String(s.plane || '').toUpperCase()).filter(Boolean)), [scenarios])
  const sources = useMemo(() => orderPlanes(scenarios.flatMap(detectionSourcesOf)), [scenarios])

  const built = useMemo(() => {
    if (!data) return null
    try {
      return { matrix: mitreMatrix(data, scenarios, states, { ...filters, gapsOnly: filters.show === SHOW_GAPS }) }
    } catch (err) {
      return { error: errText(err, 'unrecognised coverage shape') }
    }
  }, [data, scenarios, states, filters])

  if (loading && !data) return <p className="dash__empty" role="status">Loading MITRE ATT&amp;CK coverage…</p>
  if (error) {
    return (
      <p className="dash__notice" role="alert">
        <StatusIcon status="down" word="Not loaded" />
        <span>GET /api/mitre/coverage failed ({error}).</span>
      </p>
    )
  }
  if (!built) return null
  if (built.error) {
    return (
      <p className="dash__notice" role="alert" data-testid="dash-mitre-error">
        <StatusIcon status="down" word="Unreadable" />
        <span>{built.error}</span>
      </p>
    )
  }

  const { matrix } = built
  let pick = null
  if (pickId) {
    const tile = matrix.columns.flatMap((c) => c.tiles).find((t) => t.id === pickId)
    if (tile) {
      pick = {
        title: `${tile.id} · ${tile.name}`,
        sub: countLine(tile.ids, states),
        ids: tile.ids,
        emptyNote: 'No scenario in this scope exercises this technique yet.',
      }
    }
  }

  return (
    <>
      <div className="dash__filters">
        <FilterSelect label="Plane" value={filters.plane} options={[ALL, ...planes]} onChange={set('plane')} testId="dash-filter-plane" />
        <FilterSelect label="Detection source" value={filters.source} options={[ALL, ...sources]} onChange={set('source')} testId="dash-filter-source" />
        <FilterSelect label="Show" value={filters.show} options={[ALL, SHOW_GAPS]} onChange={set('show')} testId="dash-filter-show" />
        <span className="dash__spacer" />
        <ul className="dash__legend" aria-label="Legend">
          {[...STATE_ORDER, ...(states ? [] : ['unknown']), NO_SCENARIO.key].map((k) => {
            const meta = k === NO_SCENARIO.key ? NO_SCENARIO : STATE[k]
            return (
              <li key={k} className="dash__legend-item">
                <span className={`dash-swatch dash-swatch--${k}`} aria-hidden="true" />
                <StatusIcon status={meta.status} word={meta.word} />
                <span className="mono dash__legend-n" data-testid={`dash-legend-${k}`}>{matrix.tallies[k]}</span>
              </li>
            )
          })}
        </ul>
      </div>
      <p className="dash__note">
        {matrix.total} techniques the scenario library reaches, placed by GET /api/mitre/coverage, which
        files a step&apos;s technique under its scenario&apos;s tactic.{' '}
        <button
          type="button"
          className="dash__link"
          onClick={() => downloadLayer(data)}
          title="MITRE ATT&CK Navigator v4.5 layer JSON, coloured by SimCore's coverage status"
        >
          Download Navigator layer
        </button>
      </p>
      <div className="dash__split">
        <div className="dash__card dash__scroll">
          <div className="dash-attack" data-testid="dash-attack">
            {matrix.columns.map((col) => (
              <section key={col.id} className="dash-attack__col" aria-label={col.name}>
                <header className="dash-attack__head">
                  <span className="dash-attack__tactic">{col.name}</span>
                  <span className="mono dash-attack__sum">{col.detected} / {col.count} detected</span>
                </header>
                {col.tiles.map((t) => {
                  const meta = t.state === NO_SCENARIO.key ? NO_SCENARIO : STATE[t.state]
                  const on = pickId === t.id
                  return (
                    <button
                      key={t.id}
                      type="button"
                      className={`dash-tile dash-tile--${t.state}` + (on ? ' dash-tile--on' : '')}
                      aria-pressed={on}
                      aria-label={`${t.id} ${t.name}: `
                        + (t.ids.length ? `${t.ids.length} scenarios, best ${meta.word.toLowerCase()}` : 'no scenario')}
                      data-testid={`dash-tile-${t.id}`}
                      data-state={t.state}
                      onClick={() => setPickId(t.id)}
                    >
                      <span className="dash-tile__top">
                        <span className="mono">{t.id}</span>
                        <span className="mono">{t.ids.length || ''}</span>
                      </span>
                      <span className="dash-tile__name">{t.name}</span>
                      <StatusIcon status={meta.status} word={meta.word} />
                    </button>
                  )
                })}
                {col.tiles.length === 0 && (
                  <p className="dash-attack__none">{col.count ? 'No gaps' : 'No technique reached'}</p>
                )}
              </section>
            ))}
          </div>
        </div>
        <PickPanel
          pick={pick}
          scenarios={scenarios}
          states={states}
          hint="Open a technique to list the scenarios that exercise it. The number on a tile is how many scenarios do; a dashed tile has none in this scope."
        />
      </div>
    </>
  )
}

/* ─── Planes × doors ───────────────────────────────────────────────────── */

const DOOR_SUB = {
  [NO_DOOR]: 'ready, collector route, or none judged',
  [NOT_CHECKED]: 'readiness not reported or not judged',
}

function DoorsPanel({ scenarios, states }) {
  // The agent the header has selected, as on Scenarios: agent doors are read
  // for it, and are not evaluated when there is none.
  const { agent } = useEnvironment()
  const agentId = agentIdOf(agent)
  const [doors, setDoors] = useState({ loading: true })
  const [pickKey, setPickKey] = useState(null)

  useEffect(() => {
    let live = true
    setDoors({ loading: true })
    Promise.all([getDoorReadiness(agentId), getDoors()])
      .then(([readiness, rows]) => { if (live) setDoors({ readiness, rows }) })
      .catch((err) => { if (live) setDoors({ error: errText(err, 'the doors did not load') }) })
    return () => { live = false }
  }, [agentId])

  const built = useMemo(() => {
    if (!doors.readiness) return null
    try {
      return { grid: planeDoorGrid(scenarios, doors.readiness.scenarios, doors.rows?.doors, agentId) }
    } catch (err) {
      return { error: errText(err, 'unrecognised door readiness') }
    }
  }, [doors, scenarios, agentId])

  if (doors.loading) return <p className="dash__empty" role="status">Loading doors…</p>
  if (doors.error || built?.error) {
    return (
      <p className="dash__notice" role="alert">
        <StatusIcon status="down" word="Not loaded" />
        <span>The doors did not load ({doors.error || built.error}).</span>
      </p>
    )
  }
  if (doors.readiness.registry_loaded === false) {
    return (
      <p className="dash__notice" role="status" data-testid="dash-doors-registry">
        <StatusIcon status="degraded" word="Degraded" />
        <span>
          The dataset requirement registry is not loaded on this SimCore, so no door can be derived
          for any scenario.
        </span>
      </p>
    )
  }

  const { grid } = built
  let pick = null
  if (pickKey) {
    const row = grid.rows.find((r) => r.id === pickKey.row)
    const cell = row?.cells[pickKey.col]
    const col = grid.columns.find((c) => c.id === pickKey.col)
    if (cell && col) {
      pick = {
        title: `${pickKey.row} × ${col.label}`,
        sub: countLine(cell.ids, states) + (cell.detections ? ` · ${cell.detections} detections waiting` : ''),
        ids: cell.ids,
      }
    }
  }

  return (
    <>
      <p className="dash__note">
        Scenarios on each plane waiting on each door that is not open. A door nobody has checked is
        not open either, and a scenario waiting on two doors is counted under both. Doors decide
        whether a detection can fire, never whether it is scored.
        {doors.readiness.agent_scoped_evaluated === false && (
          <> No agent is selected, so agent doors such as the XDR agent are not evaluated and the
            scenarios that need them read Not checked.</>
        )}
      </p>
      <div className="dash__split">
        <div className="dash__card dash__scroll">
          <table className="dash-grid" data-testid="dash-doors-grid">
            <caption className="sr-only">Scenarios per plane waiting on each ingestion door.</caption>
            <thead>
              <tr>
                <th scope="col" className="dash-grid__corner">Plane</th>
                {grid.columns.map((c) => {
                  const st = c.state === null ? null : doorStatus(c.state)
                  return (
                    <th scope="col" key={c.id} className="dash-grid__col">
                      <span>{c.label}</span>
                      {st ? <StatusIcon status={st.status} word={st.word} /> : <span className="dash-grid__sub">{DOOR_SUB[c.id]}</span>}
                    </th>
                  )
                })}
                <th scope="col" className="dash-grid__col">Total</th>
              </tr>
            </thead>
            <tbody>
              {grid.rows.map((r) => (
                <tr key={r.id}>
                  <th scope="row" className="dash-grid__row">
                    <span className="dash-grid__plane">
                      <span className="dash-grid__tint" style={{ background: planeTint(r.id) }} aria-hidden="true" />
                      <span className="mono">{r.id === NO_SOURCE ? 'No plane' : r.id}</span>
                    </span>
                    <span className="dash-grid__sub">{r.id === NO_SOURCE ? '' : planeLabel(r.id)}</span>
                  </th>
                  {grid.columns.map((c) => {
                    const cell = r.cells[c.id]
                    if (!cell) return <td key={c.id} className="dash-grid__none"><span aria-label="none">—</span></td>
                    return (
                      <td key={c.id}>
                        <CountCell
                          ids={cell.ids}
                          states={states}
                          on={pickKey?.row === r.id && pickKey?.col === c.id}
                          testId={`dash-door-${r.id}-${c.id}`}
                          label={`${r.id} · ${c.label}: ${cell.ids.length} scenarios`}
                          onPick={() => setPickKey({ row: r.id, col: c.id })}
                        />
                      </td>
                    )
                  })}
                  <td className="mono dash-grid__total">{r.ids.length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <PickPanel
          pick={pick}
          scenarios={scenarios}
          states={states}
          hint="Open a cell to list the scenarios on that plane waiting on that door."
        />
      </div>
    </>
  )
}
