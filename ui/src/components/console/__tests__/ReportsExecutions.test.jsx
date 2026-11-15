/**
 * Reports › Executions — the rollup of every run on this engine.
 *
 * What these pin:
 *   - the page is named Reports and opens on Executions, read from
 *     GET /api/reports/executions (the library is not fetched for it);
 *   - a detection nobody reviewed reads Not reviewed, never Missed, and a
 *     scenario no reviewed run reached reads Not reviewed, not a row of zeros;
 *   - an empty engine is an empty state, a failed read is an error, and
 *     neither is a table;
 *   - a value outside the vocabulary is shown, not dropped;
 *   - every state is an icon and a word, every action a kit verb, and there
 *     is no Compare on the page.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import CoverageView, { DASH_TABS } from '../CoverageView.jsx'
import ReportsExecutions from '../ReportsExecutions.jsx'
import {
  COLUMNS, RANGES, checkReport, nextSort, outcomeChips, runStatusChips, runStatusMeta, sinceFor,
  sortRows, stepChips,
} from '../reportsModel.js'
import { VERBS } from '../kit/index.js'

void React

const outcomes = (o = {}) => ({
  expected: 0, prevented: 0, detected: 0, detected_not_prevented: 0, missed: 0, pending: 0,
  not_reached: 0, not_executed: 0, unrecognised: {}, ...o,
})

const REPORT = {
  generated_at: '2026-11-15T07:00:00+00:00',
  window: { since: null, until: null, first_run_at: '2026-11-01T10:00:00+00:00', last_run_at: '2026-11-14T12:00:00+00:00' },
  filters: { plane: null, status: null, agent: null, include_drafts: false },
  filter_options: { planes: ['EDR', 'ITDR', 'NDR'], statuses: ['pending', 'running', 'complete', 'failed', 'aborted', 'staged'], agents: ['agent-a', 'agent-b'] },
  empty: false,
  engine_runs: 7,
  totals: {
    runs: { total: 6, by_status: { pending: 0, running: 1, complete: 3, failed: 1, aborted: 0, staged: 1 }, unrecognised: {}, reviewed: 3, not_reviewed: 3 },
    scenarios: { exercised: 3, corpus: 4, corpus_exercised: 3, corpus_not_run: 1 },
    techniques_exercised: 5,
    test_cases_touched: 4,
    steps: { recorded: 5, ran: 2, failed: 1, did_not_run: 1, interrupted: 1, interrupted_by: { timeout: 1 }, unrecognised: {}, runs_with_host_record: 2, runs_without_host_record: 4 },
    detections: { ...outcomes({ expected: 17, prevented: 1, detected: 4, detected_not_prevented: 1, missed: 2, pending: 1, not_reached: 1, not_executed: 1 }), not_reviewed: 6 },
  },
  by_scenario: [
    {
      scenario_id: 'SIM-NDR-001', name: 'C2 beacon', plane: 'NDR', techniques: ['T1071.001'], uc_ref: 'UCS-NDR-01', tc_refs: ['TC-NDR-01'],
      detection_sources: [{ id: 'ngfw_traffic', label: 'NGFW traffic logs' }],
      runs: 2, runs_by_status: { complete: 1, running: 1 }, first_run_at: '2026-11-11T10:00:00+00:00', last_run_at: '2026-11-14T12:00:00+00:00',
      last_status: 'running', last_run_id: 'r-running', reviewed_runs: 1, last_reviewed_run_id: 'r-ndr-1', last_reviewed_at: '2026-11-11T10:00:00+00:00',
      outcomes: outcomes({ expected: 2, detected: 1, detected_not_prevented: 1 }),
    },
    {
      scenario_id: 'SIM-EDR-002', name: 'Reverse shell', plane: 'EDR', techniques: ['T1059.004'], uc_ref: 'UCS-EDR-02', tc_refs: ['TC-EDR-09'],
      detection_sources: null,
      runs: 1, runs_by_status: { staged: 1 }, first_run_at: '2026-11-13T10:00:00+00:00', last_run_at: '2026-11-13T10:00:00+00:00',
      last_status: 'staged', last_run_id: 'r-staged', reviewed_runs: 0, last_reviewed_run_id: null, last_reviewed_at: null,
      outcomes: null,
    },
    {
      scenario_id: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR', techniques: ['T1003.008', 'T1087.001', 'T1552.001'], uc_ref: 'UCS-EDR-02', tc_refs: ['TC-EDR-03', 'TC-EDR-05'],
      detection_sources: [{ id: 'xdr_agent', label: 'XDR agent' }, { id: 'ngfw_traffic', label: 'NGFW traffic logs' }],
      runs: 3, runs_by_status: { complete: 2, failed: 1 }, first_run_at: '2026-11-01T10:00:00+00:00', last_run_at: '2026-11-12T10:00:00+00:00',
      last_status: 'failed', last_run_id: 'r-edr-unrev', reviewed_runs: 2, last_reviewed_run_id: 'r-edr-new', last_reviewed_at: '2026-11-10T10:00:00+00:00',
      outcomes: outcomes({ expected: 4, prevented: 1, detected: 1, missed: 1, pending: 1 }),
    },
  ],
  by_technique: [
    { technique: 'T1003.008', name: 'OS Credential Dumping', scenarios: ['SIM-EDR-001'], runs: 3, last_run_at: '2026-11-12T10:00:00+00:00', reviewed_scenarios: 1, outcomes: outcomes({ expected: 2, missed: 1, pending: 1 }) },
    { technique: 'T1059.004', name: 'Unix Shell', scenarios: ['SIM-EDR-002'], runs: 1, last_run_at: '2026-11-13T10:00:00+00:00', reviewed_scenarios: 0, outcomes: null },
    { technique: 'T1071.001', name: 'Web Protocols', scenarios: ['SIM-NDR-001'], runs: 2, last_run_at: '2026-11-14T12:00:00+00:00', reviewed_scenarios: 1, outcomes: outcomes({ expected: 2, detected: 1, detected_not_prevented: 1 }) },
  ],
  by_test_case: [
    { tc_ref: 'TC-EDR-03', name: 'Multi-method malware detection', uc_ref: 'UCS-EDR-02', scenarios: ['SIM-EDR-001'], runs: 3, last_run_at: '2026-11-12T10:00:00+00:00', reviewed_scenarios: 1, outcomes: outcomes({ expected: 4, prevented: 1, detected: 1, missed: 1, pending: 1 }) },
    { tc_ref: 'TC-EDR-09', name: null, uc_ref: null, scenarios: ['SIM-EDR-002'], runs: 1, last_run_at: '2026-11-13T10:00:00+00:00', reviewed_scenarios: 0, outcomes: null },
  ],
  by_plane: [{ plane: 'EDR', runs: 4, scenarios: 2 }, { plane: 'NDR', runs: 2, scenarios: 1 }],
  by_agent: [{ agent: 'agent-b', runs: 3, scenarios: 2, last_run_at: '2026-11-14T12:00:00+00:00' }],
  detection_sources: { available: true, reason: null, failed: ['SIM-EDR-002'] },
}

const EMPTY = {
  ...REPORT, empty: true, engine_runs: 0, by_scenario: [], by_technique: [], by_test_case: [], by_plane: [], by_agent: [],
  totals: {
    ...REPORT.totals,
    runs: { total: 0, by_status: { pending: 0, running: 0, complete: 0, failed: 0, aborted: 0, staged: 0 }, unrecognised: {}, reviewed: 0, not_reviewed: 0 },
    scenarios: { exercised: 0, corpus: 4, corpus_exercised: 0, corpus_not_run: 4 },
    detections: { ...outcomes(), not_reviewed: 0 },
  },
}

function mount(report = REPORT, props = {}) {
  const fetchMock = installRoutes({ 'GET /api/reports/executions': report })
  render(<ReportsExecutions {...props} />)
  return fetchMock
}

const reportCalls = (fetchMock) => fetchMock.mock.calls
  .map(([u]) => String(u)).filter((u) => u.includes('/api/reports/executions'))
const rowIds = () => within(screen.getByTestId('rep-table')).getAllByRole('row').slice(1)
  .map((r) => r.getAttribute('data-testid').replace('rep-row-', ''))

// ─── The page ────────────────────────────────────────────────────────────────

describe('Reports opens on Executions', () => {
  it('is named Reports, and Executions is the first, selected tab', async () => {
    const fetchMock = installRoutes({ 'GET /api/reports/executions': REPORT })
    render(<CoverageView />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Reports')
    const tabs = within(screen.getByRole('tablist', { name: 'Reports' })).getAllByRole('tab')
    expect(tabs[0]).toHaveTextContent('Executions')
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(DASH_TABS[0]).toEqual(['executions', 'Executions'])
    await screen.findByTestId('rep-totals')
    // The rollup is its own read: the library and the run list are not fetched for it.
    const urls = fetchMock.mock.calls.map(([u]) => String(u))
    expect(urls.some((u) => u.includes('/api/reports/executions'))).toBe(true)
    expect(urls.some((u) => /\/api\/(scenarios|runs)\b/.test(u))).toBe(false)
    expect(screen.queryByTestId('dash-stats')).toBeNull()
  })

  it('keeps the old deep links: ?tab=mitre still opens the ATT&CK tab', async () => {
    installRoutes({
      'GET /api/scenarios': { scenarios: [{ scenario_id: 'SIM-EDR-001', plane: 'EDR', steps: [] }] },
      'GET /api/runs': { runs: [], total: 0 },
      'GET /api/mitre/coverage': { summary: {}, by_tactic: [] },
    })
    render(<CoverageView params={{ tab: 'mitre' }} />)
    expect(screen.getByRole('tab', { name: 'MITRE ATT&CK' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByTestId('dash-attack')).toBeInTheDocument()
    expect(screen.queryByTestId('reports-executions')).toBeNull()
  })

  it('has no Compare, and no intro paragraph under the heading', async () => {
    installRoutes({ 'GET /api/reports/executions': REPORT })
    const { container } = render(<CoverageView />)
    await screen.findByTestId('rep-table')
    expect(screen.queryByText(/compare/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /compare/i })).toBeNull()
    expect(container.querySelector('.pov-page__lede')).toBeNull()
  })
})

// ─── Totals ──────────────────────────────────────────────────────────────────

describe('the totals strip', () => {
  it('counts runs, scenarios, techniques, test cases, steps and detections', async () => {
    mount()
    const totals = await screen.findByTestId('rep-totals')
    expect(within(screen.getByTestId('rep-total-runs')).getByText('6')).toBeInTheDocument()
    expect(screen.getByTestId('rep-total-runs')).toHaveTextContent('3 reviewed')
    expect(within(screen.getByTestId('rep-total-scenarios')).getByText('3')).toBeInTheDocument()
    // Never run is a count beside the total, not a row at 0 %.
    expect(screen.getByTestId('rep-not-run')).toHaveTextContent('1 of 4 not run')
    expect(within(screen.getByTestId('rep-total-techniques')).getByText('5')).toBeInTheDocument()
    expect(within(screen.getByTestId('rep-total-test-cases')).getByText('4')).toBeInTheDocument()
    expect(within(screen.getByTestId('rep-total-detections')).getByText('17')).toBeInTheDocument()
    expect(within(totals).getAllByText(/^(Runs|Scenarios|Techniques|Test cases|Steps|Detections)$/)).toHaveLength(6)
  })

  it('every status is an icon and a word', async () => {
    mount()
    await screen.findByTestId('rep-totals')
    const runs = screen.getByTestId('rep-total-runs')
    for (const word of ['Finished', 'Failed', 'Staged', 'Running']) {
      expect(within(runs).getByRole('img', { name: word })).toBeInTheDocument()
      expect(within(runs).getByText(word)).toBeInTheDocument()
    }
    const steps = screen.getByTestId('rep-total-steps')
    for (const word of ['Ran', 'Failed', 'Did not run', 'Interrupted', 'No host record']) {
      expect(within(steps).getByRole('img', { name: word })).toBeInTheDocument()
    }
    expect(within(steps).getByTestId('rep-chip-no_host_record')).toHaveTextContent('4')
  })

  it('a detection nobody reviewed is Not reviewed, never Missed', async () => {
    mount()
    await screen.findByTestId('rep-totals')
    const det = screen.getByTestId('rep-total-detections')
    expect(within(det).getByTestId('rep-chip-not_reviewed')).toHaveTextContent('6')
    expect(within(within(det).getByTestId('rep-chip-not_reviewed')).getByRole('img', { name: 'Not reviewed' })).toBeInTheDocument()
    // Missed is the engine's two, not two plus the six nobody looked at.
    expect(within(det).getByTestId('rep-chip-missed')).toHaveTextContent('2')
    expect(within(det).getByTestId('rep-chip-detected_not_prevented')).toHaveTextContent('1')
    expect(within(within(det).getByTestId('rep-chip-detected_not_prevented')).getByRole('img', { name: 'Detected, not prevented' }))
      .toBeInTheDocument()
  })

  it('a status or outcome outside the vocabulary is shown, not dropped', async () => {
    mount({
      ...REPORT,
      totals: {
        ...REPORT.totals,
        runs: { ...REPORT.totals.runs, unrecognised: { completed: 2 } },
        detections: { ...REPORT.totals.detections, unrecognised: { blocked: 3 } },
        steps: { ...REPORT.totals.steps, unrecognised: { vaporised: 1 } },
      },
    })
    await screen.findByTestId('rep-totals')
    const odd = within(screen.getByTestId('rep-total-runs')).getByTestId('rep-chip-unrecognised')
    expect(odd).toHaveTextContent('2')
    expect(odd).toHaveAttribute('title', expect.stringContaining('completed (2)'))
    expect(within(odd).getByRole('img', { name: 'Unrecognised' })).toBeInTheDocument()
    expect(within(screen.getByTestId('rep-total-detections')).getByTestId('rep-chip-unrecognised'))
      .toHaveAttribute('title', expect.stringContaining('blocked (3)'))
    expect(within(screen.getByTestId('rep-total-steps')).getByTestId('rep-chip-unrecognised'))
      .toHaveAttribute('title', expect.stringContaining('vaporised (1)'))
  })
})

// ─── The table ───────────────────────────────────────────────────────────────

describe('the table', () => {
  it('is one row per scenario, newest run first, with scoped headers', async () => {
    mount()
    const table = await screen.findByTestId('rep-table')
    expect(rowIds()).toEqual(['SIM-NDR-001', 'SIM-EDR-002', 'SIM-EDR-001'])
    const heads = within(table).getAllByRole('columnheader')
    expect(heads.map((h) => h.textContent)).toEqual(COLUMNS.scenario.map((c) => c.label))
    for (const h of heads) expect(h).toHaveAttribute('scope', 'col')
    // Column headers are one or two words.
    for (const c of Object.values(COLUMNS).flat()) expect(c.label.split(' ').length).toBeLessThanOrEqual(3)
    expect(within(table).getByRole('rowheader', { name: /SIM-EDR-001/ })).toHaveAttribute('scope', 'row')
    expect(within(table).getByRole('link', { name: 'SIM-EDR-001' })).toHaveAttribute('href', '#/library?open=SIM-EDR-001')
  })

  it('sorts from its headers, which are buttons that say which way', async () => {
    mount()
    const table = await screen.findByTestId('rep-table')
    const last = within(table).getByRole('columnheader', { name: 'Last run' })
    expect(last).toHaveAttribute('aria-sort', 'descending')
    const runs = within(table).getByRole('columnheader', { name: 'Runs' })
    expect(runs).toHaveAttribute('aria-sort', 'none')
    fireEvent.click(within(runs).getByRole('button', { name: 'Runs' }))
    expect(runs).toHaveAttribute('aria-sort', 'ascending')
    expect(last).toHaveAttribute('aria-sort', 'none')
    expect(rowIds()).toEqual(['SIM-EDR-002', 'SIM-NDR-001', 'SIM-EDR-001'])
    fireEvent.click(within(runs).getByRole('button', { name: 'Runs' }))
    expect(runs).toHaveAttribute('aria-sort', 'descending')
    expect(rowIds()).toEqual(['SIM-EDR-001', 'SIM-NDR-001', 'SIM-EDR-002'])
    // The action column does not sort, and is the last one.
    const open = within(table).getByRole('columnheader', { name: 'Open' })
    expect(open).not.toHaveAttribute('aria-sort')
    expect(within(open).queryByRole('button')).toBeNull()
    expect(COLUMNS.scenario.at(-1).id).toBe('action')
  })

  it('a row shows its mapping, its runs and the outcomes of its last reviewed run', async () => {
    mount()
    await screen.findByTestId('rep-table')
    const row = screen.getByTestId('rep-row-SIM-EDR-001')
    expect(row).toHaveTextContent('Credential dumping')
    expect(row).toHaveTextContent('T1003.008')
    expect(row).toHaveTextContent('+1')
    expect(row).toHaveTextContent('UCS-EDR-02')
    expect(row).toHaveTextContent('TC-EDR-03')
    expect(row).toHaveTextContent('XDR agent')
    expect(row).toHaveTextContent('2 of 3')
    expect(within(row).getByRole('img', { name: 'Failed' })).toBeInTheDocument()
    for (const word of ['Prevented', 'Detected', 'Missed', 'Pending']) {
      expect(within(row).getByRole('img', { name: word })).toBeInTheDocument()
    }
    expect(within(row).queryByRole('img', { name: 'Not reviewed' })).toBeNull()
  })

  it('a scenario no reviewed run reached reads Not reviewed: no zeros, no Missed', async () => {
    mount()
    await screen.findByTestId('rep-table')
    const row = screen.getByTestId('rep-row-SIM-EDR-002')
    expect(within(row).getByRole('img', { name: 'Not reviewed' })).toBeInTheDocument()
    expect(within(row).queryByRole('img', { name: 'Missed' })).toBeNull()
    expect(within(row).queryByRole('img', { name: 'Detected' })).toBeNull()
    expect(within(row).getByRole('img', { name: 'Staged' })).toBeInTheDocument()
    expect(row).toHaveTextContent('0 of 1')
    // Its doors could not be derived: Can't check, never "none".
    expect(within(row).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
  })

  it('fits its card: a class per column, an ellipsis with the whole text as its tooltip', async () => {
    mount()
    const table = await screen.findByTestId('rep-table')
    // Every header and every cell names its column, so a column is sized, and gives way, as one.
    const ids = COLUMNS.scenario.map((c) => `rep-cell--${c.id}`)
    expect(within(table).getAllByRole('columnheader').map((h) => h.className)).toEqual(ids)
    for (const row of within(table).getAllByRole('row').slice(1)) {
      expect([...row.children].map((c) => c.className.split(' ')[0])).toEqual(ids)
    }
    // Text longer than its column is one line with the whole text behind it.
    const row = screen.getByTestId('rep-row-SIM-EDR-001')
    const sources = within(row).getByText('XDR agent').closest('.rep-fit')
    expect(sources).toHaveTextContent('XDR agent +1')
    expect(sources).toHaveAttribute('title', 'XDR agent, NGFW traffic logs')
    expect(within(row).getByText('Credential dumping')).toHaveClass('rep-fit')
    expect(within(row).getByText('Credential dumping')).toHaveAttribute('title', 'Credential dumping')
    // First run gives way first on a narrow window; the fact stays in the Last run tooltip.
    expect(row.querySelector('.rep-cell--last [title]').getAttribute('title'))
      .toBe('2026-11-12 10:00:00 UTC\nFirst run 2026-11-01 10:00:00 UTC')
  })

  it('opens the last reviewed run in Results, or the run when none was reviewed', async () => {
    const onNavigate = vi.fn()
    mount(REPORT, { onNavigate })
    await screen.findByTestId('rep-table')
    fireEvent.click(screen.getByRole('button', { name: 'Open results of SIM-EDR-001' }))
    expect(onNavigate).toHaveBeenLastCalledWith('proof', { run: 'r-edr-new' })
    fireEvent.click(screen.getByRole('button', { name: 'Open run of SIM-EDR-002' }))
    expect(onNavigate).toHaveBeenLastCalledWith('runs', { run: 'r-staged' })
    expect(screen.queryByRole('button', { name: 'Open results of SIM-EDR-002' })).toBeNull()
  })

  it('switches between By scenario, By technique and By test case', async () => {
    mount()
    await screen.findByTestId('rep-table')
    const group = screen.getByRole('group', { name: 'Group by' })
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(['By scenario', 'By technique', 'By test case'])
    expect(within(group).getByRole('button', { name: 'By scenario' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(group).getByRole('button', { name: 'By technique' }))
    expect(within(group).getByRole('button', { name: 'By technique' })).toHaveAttribute('aria-pressed', 'true')
    expect(rowIds()).toEqual(['T1071.001', 'T1059.004', 'T1003.008'])
    const tech = screen.getByTestId('rep-row-T1003.008')
    expect(tech).toHaveTextContent('OS Credential Dumping')
    expect(within(tech).getByRole('link', { name: 'SIM-EDR-001' })).toHaveAttribute('href', '#/library?open=SIM-EDR-001')
    expect(within(tech).getByRole('img', { name: 'Missed' })).toBeInTheDocument()
    expect(within(screen.getByTestId('rep-row-T1059.004')).getByRole('img', { name: 'Not reviewed' })).toBeInTheDocument()

    fireEvent.click(within(group).getByRole('button', { name: 'By test case' }))
    expect(rowIds()).toEqual(['TC-EDR-09', 'TC-EDR-03'])
    expect(screen.getByTestId('rep-row-TC-EDR-03')).toHaveTextContent('Multi-method malware detection')
    expect(within(screen.getByTestId('rep-table')).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(COLUMNS.test_case.map((c) => c.label))
  })
})

// ─── Filters and downloads ───────────────────────────────────────────────────

describe('filters', () => {
  it('are labelled, and each one re-reads the report with its parameter', async () => {
    const fetchMock = mount()
    await screen.findByTestId('rep-table')
    expect(reportCalls(fetchMock)).toHaveLength(1)
    expect(reportCalls(fetchMock)[0]).toMatch(/\/api\/reports\/executions$/)

    fireEvent.change(screen.getByLabelText('Plane'), { target: { value: 'NDR' } })
    await waitFor(() => expect(reportCalls(fetchMock).at(-1)).toContain('plane=NDR'))
    expect(screen.getByLabelText('Plane')).toHaveClass('dash__select--on')

    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'failed' } })
    await waitFor(() => expect(reportCalls(fetchMock).at(-1)).toContain('status=failed'))
    expect(within(screen.getByLabelText('Status')).getByRole('option', { name: 'Finished' })).toHaveValue('complete')

    fireEvent.change(screen.getByLabelText('Agent'), { target: { value: 'agent-b' } })
    await waitFor(() => expect(reportCalls(fetchMock).at(-1)).toContain('agent=agent-b'))

    const drafts = screen.getByRole('button', { name: 'Include drafts' })
    expect(drafts).toHaveAttribute('aria-pressed', 'false')
    expect(reportCalls(fetchMock).at(-1)).not.toContain('include_drafts')
    fireEvent.click(drafts)
    expect(drafts).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(reportCalls(fetchMock).at(-1)).toContain('include_drafts=true'))
  })

  it('the time range is 24 h, 7 d, 30 d or All, sent as a UTC since', async () => {
    const fetchMock = mount()
    await screen.findByTestId('rep-table')
    const range = screen.getByRole('group', { name: 'Time range' })
    expect(within(range).getAllByRole('button').map((b) => b.textContent)).toEqual(['24 h', '7 d', '30 d', 'All'])
    expect(within(range).getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
    expect(reportCalls(fetchMock)[0]).not.toContain('since=')
    fireEvent.click(within(range).getByRole('button', { name: '7 d' }))
    await waitFor(() => expect(reportCalls(fetchMock).at(-1)).toMatch(/since=\d{4}-\d{2}-\d{2}T[\d%A.]+Z/))
    const sent = new URL(reportCalls(fetchMock).at(-1)).searchParams.get('since')
    expect(Date.now() - Date.parse(sent)).toBeGreaterThanOrEqual(7 * 24 * 3600e3)
    expect(Date.now() - Date.parse(sent)).toBeLessThan(7 * 24 * 3600e3 + 60e3)
  })

  it('downloads CSV and JSON for the filters in force, with the Download verb', async () => {
    const fetchMock = installRoutes({
      'GET /api/reports/executions': (url) => (String(url).includes('format=csv')
        ? new Response('"scenario_id"\n', { status: 200, headers: { 'content-type': 'text/csv', 'content-disposition': 'attachment; filename="cortexsim-executions-20261115.csv"' } })
        : REPORT),
    })
    const createUrl = URL.createObjectURL
    const revokeUrl = URL.revokeObjectURL
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const saved = []
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() { saved.push(this.download) })
    try {
      render(<ReportsExecutions />)
      await screen.findByTestId('rep-table')
      fireEvent.change(screen.getByLabelText('Plane'), { target: { value: 'EDR' } })
      await waitFor(() => expect(reportCalls(fetchMock).at(-1)).toContain('plane=EDR'))

      const csv = screen.getByRole('button', { name: 'Download CSV' })
      expect(csv).toHaveAttribute('data-verb', 'Download')
      expect(csv.querySelector('svg')).toHaveAttribute('data-icon', 'download')
      fireEvent.click(csv)
      await waitFor(() => expect(saved).toEqual(['cortexsim-executions-20261115.csv']))
      expect(reportCalls(fetchMock).at(-1)).toMatch(/plane=EDR.*format=csv/)

      fireEvent.click(screen.getByRole('button', { name: 'Download JSON' }))
      await waitFor(() => expect(saved).toHaveLength(2))
      expect(saved[1]).toMatch(/^cortexsim-executions-\d{8}\.json$/)
      expect(reportCalls(fetchMock).at(-1)).toMatch(/plane=EDR.*format=json/)
    } finally {
      click.mockRestore()
      URL.createObjectURL = createUrl
      URL.revokeObjectURL = revokeUrl
    }
  })

  it('a download that fails says so', async () => {
    installRoutes({
      'GET /api/reports/executions': (url) => (String(url).includes('format=')
        ? new Response(JSON.stringify({ error: 'disk full', code: 'X' }), { status: 500, headers: { 'content-type': 'application/json' } })
        : REPORT),
    })
    render(<ReportsExecutions />)
    await screen.findByTestId('rep-table')
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
    expect(await screen.findByTestId('rep-download-error')).toHaveTextContent('disk full')
    expect(screen.getByTestId('rep-table')).toBeInTheDocument()
  })
})

// ─── Empty, loading, error ───────────────────────────────────────────────────

describe('empty, loading and error are said', () => {
  it('an empty engine is "No runs yet" and a Run, never a table of zeros', async () => {
    const onNavigate = vi.fn()
    mount(EMPTY, { onNavigate })
    const empty = await screen.findByTestId('rep-empty')
    expect(empty).toHaveTextContent('No runs yet.')
    expect(screen.queryByTestId('rep-table')).toBeNull()
    expect(screen.queryByTestId('rep-totals')).toBeNull()
    const run = within(empty).getByRole('button', { name: 'Run' })
    expect(run).toHaveClass('kit-btn--secondary')
    fireEvent.click(run)
    expect(onNavigate).toHaveBeenCalledWith('library')
    // Nothing to save, so the downloads are off.
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Download JSON' })).toBeDisabled()
  })

  it('filters that match nothing on a busy engine say that instead', async () => {
    mount({ ...EMPTY, engine_runs: 7 })
    expect(await screen.findByTestId('rep-no-match')).toHaveTextContent('No runs match these filters.')
    expect(screen.queryByTestId('rep-empty')).toBeNull()
    expect(screen.queryByTestId('rep-table')).toBeNull()
  })

  it('says it is loading before the report arrives', async () => {
    let release
    installRoutes({ 'GET /api/reports/executions': () => new Promise((resolve) => { release = () => resolve(REPORT) }) })
    render(<ReportsExecutions />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading…')
    expect(screen.getByTestId('reports-executions')).toHaveAttribute('aria-busy', 'true')
    release()
    await screen.findByTestId('rep-table')
    expect(screen.getByTestId('reports-executions')).toHaveAttribute('aria-busy', 'false')
  })

  it('a failed read shows the error, not an empty table or "No runs yet"', async () => {
    installRoutes({
      'GET /api/reports/executions': () => new Response(
        JSON.stringify({ error: 'database is locked', code: 'INTERNAL_ERROR' }),
        { status: 500, headers: { 'content-type': 'application/json' } }),
    })
    render(<ReportsExecutions />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('database is locked')
    expect(within(alert).getByRole('img', { name: 'Not loaded' })).toBeInTheDocument()
    expect(screen.queryByTestId('rep-table')).toBeNull()
    expect(screen.queryByTestId('rep-empty')).toBeNull()
    expect(screen.queryByText(/no runs/i)).toBeNull()
  })

  it('a body that is not the report is an error, not an empty engine', async () => {
    mount({ runs: [] })
    expect(await screen.findByRole('alert')).toHaveTextContent(/unrecognised shape/)
    expect(screen.queryByTestId('rep-empty')).toBeNull()
    expect(() => checkReport({ totals: {}, by_scenario: [] })).toThrow(/unrecognised shape/)
    expect(checkReport(REPORT)).toBe(REPORT)
  })

  it('a filter change that fails drops the old table with it', async () => {
    let fail = false
    installRoutes({
      'GET /api/reports/executions': () => (fail
        ? new Response(JSON.stringify({ error: 'boom' }), { status: 500, headers: { 'content-type': 'application/json' } })
        : REPORT),
    })
    render(<ReportsExecutions />)
    await screen.findByTestId('rep-table')
    fail = true
    fireEvent.change(screen.getByLabelText('Plane'), { target: { value: 'NDR' } })
    expect(await screen.findByTestId('rep-error')).toHaveTextContent('boom')
    expect(screen.queryByTestId('rep-table')).toBeNull()
  })
})

// ─── Kit and copy ────────────────────────────────────────────────────────────

describe('kit controls and terse copy', () => {
  it('every button on the tab is a kit verb with its icon, a filter control, or a sort header', async () => {
    mount()
    await screen.findByTestId('rep-table')
    const root = screen.getByTestId('reports-executions')
    for (const b of within(root).getAllByRole('button')) {
      const kit = b.classList.contains('kit-btn')
      const control = /rep-seg__opt|rep-toggle|rep-table__sort/.test(b.className)
      expect(kit || control, `"${b.textContent}" is neither a kit button nor a filter control`).toBe(true)
      if (kit) {
        expect(VERBS).toContain(b.getAttribute('data-verb'))
        expect(b.querySelector('svg.kit-icon')).not.toBeNull()
        // No filled primary on this page: every action is secondary.
        expect(b).toHaveClass('kit-btn--secondary')
      }
    }
    expect(root.querySelector('.btn')).toBeNull()
  })

  it('carries no paragraph of explanation', async () => {
    mount()
    await screen.findByTestId('rep-table')
    expect(screen.getByTestId('reports-executions').querySelectorAll('p')).toHaveLength(0)
  })
})

// ─── The model ───────────────────────────────────────────────────────────────

describe('reportsModel', () => {
  it('outcomeChips keeps Not reviewed apart from Missed and null apart from zero', () => {
    expect(outcomeChips(null)).toBeNull()
    expect(outcomeChips(outcomes())).toEqual([])
    const chips = outcomeChips({ ...outcomes({ missed: 2, detected: 1 }), not_reviewed: 5 })
    expect(chips.map((c) => [c.key, c.word, c.n, c.status])).toEqual([
      ['detected', 'Detected', 1, 'up'], ['missed', 'Missed', 2, 'down'], ['not_reviewed', 'Not reviewed', 5, 'unknown'],
    ])
    // A not-reviewed row never borrows a tone that reads as a verdict.
    expect(chips.find((c) => c.key === 'not_reviewed').status).not.toBe('down')
    const odd = outcomeChips(outcomes({ unrecognised: { blocked: 2, vanished: 1 } }))
    expect(odd).toEqual([expect.objectContaining({ key: 'unrecognised', word: 'Unrecognised', n: 3, status: 'degraded' })])
  })

  it('names run statuses and never colours one it does not know', () => {
    expect(runStatusMeta('complete')).toEqual({ status: 'up', word: 'Finished' })
    expect(runStatusMeta('pending').word).toBe('Queued')
    expect(runStatusMeta('exploded')).toEqual({ status: 'unknown', word: 'exploded' })
    expect(runStatusChips(REPORT.totals.runs).map((c) => [c.word, c.n])).toEqual([
      ['Finished', 3], ['Failed', 1], ['Staged', 1], ['Running', 1],
    ])
    expect(stepChips(REPORT.totals.steps).map((c) => c.word)).toEqual(['Ran', 'Failed', 'Did not run', 'Interrupted', 'No host record'])
    expect(stepChips(REPORT.totals.steps).find((c) => c.key === 'interrupted').title).toBe('timeout (1)')
  })

  it('sinceFor reads a preset as a UTC instant, and All as no bound', () => {
    const now = Date.parse('2026-11-15T12:00:00Z')
    expect(RANGES.map(([id]) => id)).toEqual(['24h', '7d', '30d', 'all'])
    expect(sinceFor('24h', now)).toBe('2026-11-14T12:00:00.000Z')
    expect(sinceFor('30d', now)).toBe('2026-10-16T12:00:00.000Z')
    expect(sinceFor('all', now)).toBeUndefined()
    expect(sinceFor('nonsense', now)).toBeUndefined()
  })

  it('sorts a copy, with rows that have no value last either way', () => {
    const col = COLUMNS.scenario.find((c) => c.id === 'outcomes')
    const rows = REPORT.by_scenario
    expect(sortRows(rows, col, 'desc').map((r) => r.scenario_id)).toEqual(['SIM-EDR-001', 'SIM-NDR-001', 'SIM-EDR-002'])
    expect(sortRows(rows, col, 'asc').map((r) => r.scenario_id)).toEqual(['SIM-NDR-001', 'SIM-EDR-001', 'SIM-EDR-002'])
    expect(rows.map((r) => r.scenario_id)).toEqual(['SIM-NDR-001', 'SIM-EDR-002', 'SIM-EDR-001'])
    expect(nextSort({ id: 'runs', dir: 'asc' }, 'runs')).toEqual({ id: 'runs', dir: 'desc' })
    expect(nextSort({ id: 'runs', dir: 'desc' }, 'last')).toEqual({ id: 'last', dir: 'asc' })
  })
})
