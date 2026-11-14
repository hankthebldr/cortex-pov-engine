/**
 * Dashboards (design v33 C8) — the `coverage` route as a rail item with three
 * views over the real corpus, and per-scenario state from run READ-BACK only.
 *
 * What these pin:
 *   - every tab draws from its SimCore endpoint, not from sample data;
 *   - a scenario is Detected only when a read-back run of it detected
 *     something: a finished run that was never read back reads Pending even
 *     when SimCore counted detections on it, and an authored scenario reads
 *     Never run;
 *   - a run list that did not load is Can't check, never Never run;
 *   - a technique no scenario in scope exercises is drawn dashed;
 *   - a cell or tile opens the scenarios it counts on their sheets.
 */
import React from 'react'
import fs from 'node:fs'
import path from 'node:path'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import CoverageView, { DASH_TABS } from '../CoverageView.jsx'
import {
  ALL, NO_DOOR, NOT_CHECKED, OTHER_TACTIC, detectionSourcesOf, mitreMatrix, planeDoorGrid,
  runState, scenarioStates, sourceUseCaseGrid, stateOf, useCaseOf,
} from '../dashboardModel.js'
import { navGroups, getDestination } from '../../../app/destinations.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../../context/EnvironmentContext.jsx'

void React

const det = (...planes) => planes.map((plane) => ({ plane, type: 'BIOC' }))

const SCENARIOS = [
  { scenario_id: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR', uc_ref: 'UCS-EDR-02', uc_name: 'Endpoint Detection & Response', steps: [{ id: 's1', expected_detections: det('EDR', 'ANALYTICS') }] },
  { scenario_id: 'SIM-EDR-002', name: 'Reverse shell', plane: 'EDR', uc_ref: 'UCS-EDR-02', uc_name: 'Endpoint Detection & Response', steps: [{ id: 's1', expected_detections: det('EDR') }] },
  { scenario_id: 'SIM-NDR-001', name: 'C2 beacon', plane: 'NDR', uc_ref: 'UCS-NDR-01', uc_name: 'Network Detection & Response', steps: [{ id: 's1', expected_detections: det('NDR') }] },
  { scenario_id: 'SIM-ITDR-001', name: 'Kerberoast', plane: 'ITDR', uc_ref: 'UC-ITDR', uc_name: 'Identity Threat Detection', steps: [{ id: 's1', expected_detections: det('ITDR') }] },
]

const READ_BACK = { source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T10:00:00' }
const RUNS = [
  // Read back, SimCore counted detections: the only Detected.
  { run_id: 'r-1', scenario_id: 'SIM-EDR-002', status: 'completed', mode: 'pull', delivered_at: '2026-11-13T09:00:00', tc_verdict_detail: READ_BACK, expected_detections: 2, detected_count: 2 },
  // Finished with detections counted, but NEVER read back: Pending, not Detected.
  { run_id: 'r-2', scenario_id: 'SIM-EDR-001', status: 'completed', mode: 'pull', delivered_at: '2026-11-13T09:00:00', tc_verdict_detail: { source: 'completion' }, expected_detections: 3, detected_count: 3 },
  // Read back, nothing detected.
  { run_id: 'r-3', scenario_id: 'SIM-NDR-001', status: 'completed', mode: 'pull', delivered_at: '2026-11-13T09:00:00', tc_verdict_detail: READ_BACK, expected_detections: 1, detected_count: 0 },
  // SIM-ITDR-001 has no run: authored, never run.
]

const COVERAGE = {
  summary: { total_techniques: 4, detected: 0, run_not_detected: 0, not_run: 4 },
  by_tactic: [
    { tactic_id: 'TA0006', tactic_name: 'Credential Access', techniques: [
      { technique_id: 'T1003', technique_name: 'OS Credential Dumping', tactic_id: 'TA0006', scenarios: ['SIM-EDR-001'], status: 'detected' },
      { technique_id: 'T1558.003', technique_name: 'Kerberoasting', tactic_id: 'TA0006', scenarios: ['SIM-ITDR-001'], status: 'not_run' },
    ] },
    { tactic_id: 'TA0002', tactic_name: 'Execution', techniques: [
      { technique_id: 'T1059.004', technique_name: 'Unix Shell', tactic_id: 'TA0002', scenarios: ['SIM-EDR-002'], status: 'not_run' },
    ] },
    { tactic_id: 'TA0011', tactic_name: 'Command and Control', techniques: [
      { technique_id: 'T1071', technique_name: 'Application Layer Protocol', tactic_id: 'TA0011', scenarios: ['SIM-NDR-001'], status: 'not_run' },
    ] },
  ],
}

function routes(extra = {}) {
  return installRoutes({
    'GET /api/scenarios': { scenarios: SCENARIOS, total: SCENARIOS.length, projection: 'summary' },
    'GET /api/runs': { runs: RUNS, total: RUNS.length, fields: 'summary' },
    'GET /api/mitre/coverage': COVERAGE,
    'GET /api/doors/readiness': {
      registry_loaded: true,
      scenarios: {
        'SIM-EDR-001': { scenario_id: 'SIM-EDR-001', state: 'needs_doors', scorable: 0, total: 2, not_open: [{ door_id: 'xdr_agent', detections: 2 }] },
        'SIM-EDR-002': { scenario_id: 'SIM-EDR-002', state: 'needs_doors', scorable: 0, total: 1, not_open: [{ door_id: 'xdr_agent', detections: 1 }] },
        'SIM-NDR-001': { scenario_id: 'SIM-NDR-001', state: 'needs_doors', scorable: 0, total: 1, not_open: [{ door_id: 'ngfw_traffic', detections: 1 }] },
        // SIM-ITDR-001 has no readiness entry: Not checked, never ready.
      },
    },
    'GET /api/doors': {
      registry_loaded: true,
      doors: [
        { key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', state: 'unknown', rung: 'authored' },
        { key: 'ngfw_traffic', door_id: 'ngfw_traffic', label: 'NGFW traffic logs', state: 'unknown', rung: 'authored' },
      ],
    },
    ...extra,
  })
}

async function mountOn(tab, extra) {
  routes(extra)
  render(<CoverageView params={tab ? { tab } : {}} />)
  await screen.findByTestId('dash-stats')
}

// ─── The rail ────────────────────────────────────────────────────────────────

describe('Dashboards on the rail', () => {
  it('is a visible rail item named Dashboards, after Results, on the coverage route', () => {
    const observe = navGroups().find((g) => g.label === 'Run & observe')
    expect(observe.items.map((i) => [i.id, i.label])).toEqual([['runs', 'Runs'], ['proof', 'Results'], ['coverage', 'Dashboards']])
    expect(getDestination('coverage').label).toBe('Dashboards')
  })

  it('names the page Dashboards and offers the three views first', async () => {
    await mountOn()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Dashboards')
    const tabs = within(screen.getByRole('tablist', { name: 'Dashboards' })).getAllByRole('tab')
    expect(tabs.slice(0, 3).map((t) => t.textContent)).toEqual(['Detection source × Use case', 'MITRE ATT&CK', 'Planes × doors'])
    expect(tabs.map((t) => t.textContent)).toEqual(DASH_TABS.map(([, label]) => label))
  })
})

// ─── State: read-back only ──────────────────────────────────────────────────

describe('a scenario is detected only by a read-back run', () => {
  it('runState: detections on a run that was never read back are Pending, not Detected', () => {
    expect(runState(RUNS[0])).toBe('detected')
    expect(runState(RUNS[1])).toBe('pending')
    expect(runState(RUNS[2])).toBe('not_seen')
    // A read back with no count reported claims nothing either way.
    expect(runState({ ...RUNS[0], detected_count: undefined })).toBe('pending')
    // A pull run that never reached its agent executed nothing.
    expect(runState({ run_id: 'r-x', scenario_id: 'X', status: 'failed', mode: 'pull' })).toBe('never')
  })

  it('scenarioStates takes the best run, and an authored scenario reads Never run', () => {
    const states = scenarioStates([...RUNS, { ...RUNS[1], run_id: 'r-4', scenario_id: 'SIM-EDR-002' }])
    expect(stateOf(states, 'SIM-EDR-002')).toBe('detected')
    expect(stateOf(states, 'SIM-EDR-001')).toBe('pending')
    expect(stateOf(states, 'SIM-NDR-001')).toBe('not_seen')
    expect(stateOf(states, 'SIM-ITDR-001')).toBe('never')
    expect(stateOf(null, 'SIM-ITDR-001')).toBe('unknown')
  })

  it('the summary counts one Detected — the read-back run — and never the unread one', async () => {
    await mountOn()
    expect(screen.getByTestId('dash-stat-total')).toHaveTextContent('4')
    expect(screen.getByTestId('dash-stat-detected')).toHaveTextContent('1')
    expect(screen.getByTestId('dash-stat-not_seen')).toHaveTextContent('1')
    expect(screen.getByTestId('dash-stat-pending')).toHaveTextContent('1')
    expect(screen.getByTestId('dash-stat-never')).toHaveTextContent('1')
  })

  it('every status is an icon and a word', async () => {
    await mountOn()
    const stats = screen.getByTestId('dash-stats')
    for (const word of ['Detected', 'Not seen yet', 'Pending', 'Never run']) {
      expect(within(stats).getByRole('img', { name: word })).toBeInTheDocument()
      expect(within(stats).getByText(word)).toBeInTheDocument()
    }
  })

  it("a run list that did not load is Can't check, never Never run", async () => {
    await mountOn(null, { 'GET /api/runs': () => new Response('{}', { status: 500 }) })
    expect(screen.getByTestId('dash-runs-error')).toHaveTextContent(/run list did not load/i)
    expect(screen.getByTestId('dash-stat-never')).toHaveTextContent('—')
    expect(screen.getByTestId('dash-stat-detected')).toHaveTextContent('—')
    fireEvent.click(screen.getByTestId('dash-cell-UC-EDR-EDR'))
    const pick = screen.getByTestId('dash-pick')
    expect(within(pick).getAllByRole('img', { name: "Can't check" })).toHaveLength(2)
    expect(within(pick).queryByRole('img', { name: 'Never run' })).toBeNull()
  })
})

// ─── Detection source × Use case ────────────────────────────────────────────

describe('Detection source × Use case', () => {
  it('derives use cases and detection sources from the scenario rows', () => {
    expect(useCaseOf(SCENARIOS[0])).toEqual({ id: 'UC-EDR', name: 'Endpoint Detection & Response' })
    expect(useCaseOf(SCENARIOS[3]).id).toBe('UC-ITDR')
    expect(useCaseOf({}).id).toBe('(no use case)')
    expect(detectionSourcesOf(SCENARIOS[0])).toEqual(['EDR', 'ANALYTICS'])
    const grid = sourceUseCaseGrid(SCENARIOS)
    expect(grid.rows.map((r) => r.id)).toEqual(['UC-EDR', 'UC-ITDR', 'UC-NDR'])
    expect(grid.columns).toEqual(['EDR', 'NDR', 'ITDR', 'ANALYTICS'])
    expect(grid.rows[0].cells.EDR).toEqual(['SIM-EDR-001', 'SIM-EDR-002'])
    expect(grid.rows[0].cells.ANALYTICS).toEqual(['SIM-EDR-001'])
  })

  it('renders the grid from GET /api/scenarios and a cell opens the scenarios it counts', async () => {
    await mountOn()
    const grid = screen.getByTestId('dash-sources-grid')
    expect(within(grid).getByRole('rowheader', { name: /UC-EDR/ })).toBeInTheDocument()
    expect(within(grid).getByRole('columnheader', { name: /NDR/ })).toBeInTheDocument()
    const cell = screen.getByTestId('dash-cell-UC-EDR-EDR')
    expect(cell).toHaveAccessibleName('UC-EDR through EDR: 2 scenarios, 1 detected')
    fireEvent.click(cell)
    expect(cell).toHaveAttribute('aria-pressed', 'true')
    const pick = screen.getByTestId('dash-pick')
    expect(pick).toHaveTextContent('UC-EDR × EDR')
    // Best first, each opening its sheet on Scenarios.
    const links = within(pick).getAllByRole('link')
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#/library?open=SIM-EDR-002', '#/library?open=SIM-EDR-001'])
    expect(within(links[0]).getByRole('img', { name: 'Detected' })).toBeInTheDocument()
    expect(within(links[1]).getByRole('img', { name: 'Pending' })).toBeInTheDocument()
  })

  it('an empty library is said, not drawn as a grid of zeros', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [] }, 'GET /api/runs': { runs: [], total: 0 } })
    render(<CoverageView />)
    expect(await screen.findByTestId('dash-no-scenarios')).toHaveTextContent(/no scenarios/i)
    expect(screen.queryByTestId('dash-sources-grid')).toBeNull()
  })
})

// ─── MITRE ATT&CK ──────────────────────────────────────────────────────────

describe('MITRE ATT&CK', () => {
  it('draws the 14 Enterprise tactics from GET /api/mitre/coverage, coloured by read-back state', async () => {
    const fetchMock = routes()
    render(<CoverageView params={{ tab: 'mitre' }} />)
    const attack = await screen.findByTestId('dash-attack')
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/api/mitre/coverage'))).toBe(true)
    expect(within(attack).getAllByRole('region')).toHaveLength(14)
    expect(within(attack).getByRole('region', { name: 'Credential Access' })).toBeInTheDocument()
    // The endpoint says `detected` for T1003; its only scenario was never read back.
    expect(screen.getByTestId('dash-tile-T1003')).toHaveAttribute('data-state', 'pending')
    expect(screen.getByTestId('dash-tile-T1059.004')).toHaveAttribute('data-state', 'detected')
    expect(screen.getByTestId('dash-tile-T1071')).toHaveAttribute('data-state', 'not_seen')
    expect(screen.getByTestId('dash-tile-T1558.003')).toHaveAttribute('data-state', 'never')
    expect(within(screen.getByTestId('dash-tile-T1003')).getByRole('img', { name: 'Pending' })).toBeInTheDocument()
  })

  it('a technique no scenario in scope exercises is drawn dashed and says so', async () => {
    routes()
    render(<CoverageView params={{ tab: 'mitre' }} />)
    await screen.findByTestId('dash-attack')
    fireEvent.change(screen.getByTestId('dash-filter-plane'), { target: { value: 'NDR' } })
    const tile = screen.getByTestId('dash-tile-T1003')
    expect(tile).toHaveAttribute('data-state', 'none')
    expect(tile).toHaveClass('dash-tile--none')
    expect(tile).toHaveAccessibleName('T1003 OS Credential Dumping: no scenario')
    expect(within(tile).getByRole('img', { name: 'No scenario' })).toBeInTheDocument()
    expect(screen.getByTestId('dash-tile-T1071')).toHaveAttribute('data-state', 'not_seen')
    fireEvent.click(tile)
    expect(screen.getByTestId('dash-pick')).toHaveTextContent(/No scenario in this scope exercises this technique/)
    expect(within(screen.getByTestId('dash-pick')).getByRole('link', { name: /Build a chain/ })).toHaveAttribute('href', '#/composer')

    // The dashed line is the stylesheet's, so the class has to keep carrying it.
    const css = fs.readFileSync(path.resolve(__dirname, '../../../styles/destinations/coverage.css'), 'utf8')
    expect(css).toMatch(/\.dash-tile--none\s*\{[^}]*border-style:\s*dashed/)
  })

  it('filters by detection source and shows only gaps', async () => {
    routes()
    render(<CoverageView params={{ tab: 'mitre' }} />)
    await screen.findByTestId('dash-attack')
    fireEvent.change(screen.getByTestId('dash-filter-source'), { target: { value: 'ANALYTICS' } })
    expect(screen.getByTestId('dash-filter-source')).toHaveClass('dash__select--on')
    expect(screen.getByTestId('dash-tile-T1003')).toHaveAttribute('data-state', 'pending')
    expect(screen.getByTestId('dash-tile-T1059.004')).toHaveAttribute('data-state', 'none')
    fireEvent.change(screen.getByTestId('dash-filter-source'), { target: { value: ALL } })
    fireEvent.change(screen.getByTestId('dash-filter-show'), { target: { value: 'Gaps only' } })
    expect(screen.queryByTestId('dash-tile-T1059.004')).toBeNull()
    expect(screen.getByTestId('dash-tile-T1003')).toBeInTheDocument()
  })

  it('a tile opens the scenarios that exercise it', async () => {
    routes()
    render(<CoverageView params={{ tab: 'mitre' }} />)
    await screen.findByTestId('dash-attack')
    fireEvent.click(screen.getByTestId('dash-tile-T1059.004'))
    expect(within(screen.getByTestId('dash-pick')).getByRole('link', { name: /SIM-EDR-002/ }))
      .toHaveAttribute('href', '#/library?open=SIM-EDR-002')
  })

  it('an unrecognised coverage shape raises instead of drawing an empty matrix', () => {
    expect(() => mitreMatrix({ tactics: [] }, SCENARIOS, null)).toThrow(/unrecognised shape/)
    expect(() => mitreMatrix({ by_tactic: [{ techniques: [{ technique_id: 'T1' }] }] }, SCENARIOS, null)).toThrow(/unrecognised shape/)
  })

  it('a technique filed under an unknown tactic gets its own column instead of vanishing', () => {
    const m = mitreMatrix({ by_tactic: [{ tactic_id: null, techniques: [{ technique_id: 'T9999', scenarios: [] }] }] }, SCENARIOS, new Map())
    expect(m.columns).toHaveLength(15)
    expect(m.columns[14].id).toBe(OTHER_TACTIC)
    expect(m.columns[14].tiles[0].state).toBe('none')
  })
})

// ─── Planes × doors ────────────────────────────────────────────────────────

describe('Planes × doors', () => {
  it('counts scenarios per plane waiting on each door, and absent readiness as Not checked', () => {
    const readiness = {
      A: { state: 'needs_doors', not_open: [{ door_id: 'xdr_agent', detections: 2 }] },
      B: { state: 'ready', not_open: [] },
    }
    const grid = planeDoorGrid([
      { scenario_id: 'A', plane: 'EDR' }, { scenario_id: 'B', plane: 'EDR' }, { scenario_id: 'C', plane: 'NDR' },
    ], readiness, [{ key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', state: 'unknown' }])
    expect(grid.columns.map((c) => c.id)).toEqual(['xdr_agent', NO_DOOR, NOT_CHECKED])
    const edr = grid.rows.find((r) => r.id === 'EDR')
    expect(edr.cells.xdr_agent).toEqual({ ids: ['A'], detections: 2 })
    expect(edr.cells[NO_DOOR].ids).toEqual(['B'])
    expect(grid.rows.find((r) => r.id === 'NDR').cells[NOT_CHECKED].ids).toEqual(['C'])
  })

  it('renders from GET /api/doors/readiness and GET /api/doors', async () => {
    await mountOn('doors')
    const grid = await screen.findByTestId('dash-doors-grid')
    // Doors in the registry's order, as GET /api/doors lists them.
    expect(within(grid).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Plane', 'XDR agent?Not checked', 'NGFW traffic logs?Not checked', 'Not checkedreadiness not reported or not judged', 'Total'])
    // No door event is written yet, so no door claims to be open.
    expect(within(grid).getAllByRole('img', { name: 'Not checked' }).length).toBe(2)
    fireEvent.click(screen.getByTestId('dash-door-EDR-xdr_agent'))
    expect(screen.getByTestId('dash-pick')).toHaveTextContent('3 detections waiting')
    expect(screen.getByTestId('dash-door-ITDR-(not checked)')).toHaveAccessibleName('ITDR · Not checked: 1 scenarios')
  })

  it('reads agent doors for the agent the header selected, and says so when there is none', async () => {
    const fetchMock = routes()
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, agent: { agent_id: 'jb-01' } }}>
        <CoverageView params={{ tab: 'doors' }} />
      </EnvironmentContext.Provider>,
    )
    await screen.findByTestId('dash-doors-grid')
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/api/doors/readiness?agent_id=jb-01'))).toBe(true)

    routes({
      'GET /api/doors/readiness': { registry_loaded: true, agent_scoped_evaluated: false, scenarios: {} },
    })
    render(<CoverageView params={{ tab: 'doors' }} />)
    expect(await screen.findByText(/No agent is selected, so agent doors/)).toBeInTheDocument()
  })

  it('an absent requirement registry is degraded, not an empty grid', async () => {
    await mountOn('doors', {
      'GET /api/doors/readiness': { registry_loaded: false, scenarios: {} },
    })
    expect(await screen.findByTestId('dash-doors-registry')).toHaveTextContent(/registry is not loaded/)
    expect(screen.queryByTestId('dash-doors-grid')).toBeNull()
  })
})

// ─── Tabs ───────────────────────────────────────────────────────────────────

describe('tabs', () => {
  it('the selected tab is the green wash with the matte line, and rides ?tab=', async () => {
    routes()
    const setParams = vi.fn()
    render(<CoverageView params={{}} setParams={setParams} />)
    await screen.findByTestId('dash-stats')
    expect(screen.getByTestId('dash-tab-sources')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('dash-tab-sources')).toHaveClass('dash__tab--on')
    fireEvent.click(screen.getByTestId('dash-tab-mitre'))
    expect(setParams).toHaveBeenCalledWith({ tab: 'mitre' })
    expect(screen.getByTestId('dash-tab-mitre')).toHaveClass('dash__tab--on')
    expect(screen.getByTestId('dash-tab-sources')).not.toHaveClass('dash__tab--on')

    const css = fs.readFileSync(path.resolve(__dirname, '../../../styles/destinations/coverage.css'), 'utf8')
    expect(css).toMatch(/\.dash__tab--on\s*\{[^}]*background:\s*var\(--ac-soft\)[^}]*border-color:\s*var\(--gr-matte,\s*var\(--ac-line\)\)/)
    expect(css).toMatch(/\.dash__select--on\s*\{[^}]*background:\s*var\(--ac-soft\)[^}]*border-color:\s*var\(--gr-matte,\s*var\(--ac-line\)\)/)
  })
})
