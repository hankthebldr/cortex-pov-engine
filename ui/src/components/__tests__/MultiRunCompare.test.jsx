/**
 * Runs · Compare (design change C2).
 *
 * Two or three finished runs as aria-pressed chips; a detection × run grid;
 * paired rows Detected / Steps that ran / Fastest detection / Workflow; each
 * column header carries the run's next action. Only a run that has been read
 * back may say Detected or Not seen.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup, act } from '@testing-library/react'
import MultiRunCompare from '../console/MultiRunCompare.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

afterEach(cleanup)

const READ_DETAIL = { source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T17:10:00' }
const UNREAD_DETAIL = { source: 'complete' }

const run = (id, over = {}) => ({
  run_id: id, scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull',
  started_at: '2026-11-13T17:00:00', completed_at: '2026-11-13T17:01:00',
  delivered_at: '2026-11-13T17:00:03', tc_verdict_detail: UNREAD_DETAIL, ...over,
})

const READ = run('r-read', { tc_verdict_detail: READ_DETAIL })
const UNREAD = run('r-unread')
const RUNNING = run('r-live', { status: 'running', completed_at: null })
const STAGED = run('r-staged', { status: 'staged', mode: 'push' })

const row = (id, outcome, over = {}) => ({
  id, step_id: `step-0${id}`, signal_type: 'BIOC', detection_id: `bioc-${id}`,
  expected_detection: `det ${id}`, outcome, outcome_reason: null, observed: false, mttd_seconds: null, ...over,
})

const READ_RESULTS = {
  results: [row(1, 'detected', { observed: true, mttd_seconds: 38 }), row(2, 'missed'), row(3, 'not_reached')],
  coverage: { denominator: 2, observed_in_denominator: 1, pct: 50 },
}
// The unread run's rows carry a hand mark and a stale outcome: neither is a
// tenant read, so neither may show.
const UNREAD_RESULTS = {
  results: [row(1, 'detected', { observed: true, mttd_seconds: 12 }), row(2, 'missed'), row(3, 'not_executed')],
  coverage: { denominator: 2, observed_in_denominator: 1, pct: 50 },
}

function mount(routes, props = {}) {
  const calls = installRoutes(routes)
  const utils = render(<MultiRunCompare {...props} />)
  return { calls, ...utils }
}

const chips = () => screen.queryAllByTestId('run-compare-chip')
const chip = (id) => chips().find((c) => c.dataset.runId === id)
const columnCells = (runId) => {
  const heads = screen.getAllByTestId('run-compare-head')
  const i = heads.findIndex((h) => h.dataset.runId === runId)
  return screen.getAllByTestId('run-compare-row').map((r) => within(r).getAllByTestId('run-compare-cell')[i])
}
const sumRow = (key) => Array.from(screen.getByTestId(`run-compare-sum-${key}`).querySelectorAll('td')).map((td) => td.textContent)

async function pick(...ids) {
  await waitFor(() => expect(chips().length).toBeGreaterThan(0))
  for (const id of ids) fireEvent.click(chip(id))
}

describe('Runs · Compare — picking runs', () => {
  it('offers only finished runs, as aria-pressed chips', async () => {
    mount({ 'GET /api/runs': [READ, UNREAD, RUNNING, STAGED] })
    await waitFor(() => expect(chips()).toHaveLength(2))
    expect(chips().map((c) => c.dataset.runId)).toEqual(['r-read', 'r-unread'])
    for (const c of chips()) expect(c).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(chip('r-read'))
    expect(chip('r-read')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('run-compare-count')).toHaveTextContent('1')
    expect(screen.getByTestId('run-compare-pick-more')).toHaveTextContent('Pick one more run to compare.')
    expect(screen.queryByTestId('run-compare-grid')).toBeNull()
  })

  it('takes at most three runs and says why a fourth is refused', async () => {
    const many = Array.from({ length: 5 }, (_, i) => run(`r-${i}`))
    const routes = { 'GET /api/runs': many }
    for (const r of many) routes[`GET /api/results/${r.run_id}`] = { results: [] }
    mount(routes)
    await pick('r-0', 'r-1', 'r-2', 'r-3')
    const pressed = chips().filter((c) => c.getAttribute('aria-pressed') === 'true').map((c) => c.dataset.runId)
    expect(pressed).toEqual(['r-0', 'r-1', 'r-2'])
    expect(screen.getByTestId('run-compare-full')).toHaveTextContent('Compare up to three runs — remove one first.')
    expect(screen.getAllByTestId('run-compare-head')).toHaveLength(3)
    // Remove one, and a fourth can come in.
    fireEvent.click(chip('r-1'))
    fireEvent.click(chip('r-3'))
    expect(chips().filter((c) => c.getAttribute('aria-pressed') === 'true').map((c) => c.dataset.runId))
      .toEqual(['r-0', 'r-2', 'r-3'])
    expect(screen.getByTestId('run-compare-full')).toHaveTextContent('')
  })

  it('two picks before a re-render both count, and a fourth is still refused', async () => {
    const many = Array.from({ length: 4 }, (_, i) => run(`r-${i}`))
    const routes = { 'GET /api/runs': many }
    for (const r of many) routes[`GET /api/results/${r.run_id}`] = { results: [] }
    mount(routes)
    await waitFor(() => expect(chips()).toHaveLength(4))
    act(() => {
      for (const id of ['r-0', 'r-1', 'r-2', 'r-3']) chip(id).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    expect(chips().filter((c) => c.getAttribute('aria-pressed') === 'true').map((c) => c.dataset.runId))
      .toEqual(['r-0', 'r-1', 'r-2'])
    expect(screen.getByTestId('run-compare-full')).toHaveTextContent('Compare up to three runs')
  })

  it('a runs list that failed to load is an error, never "no runs"', async () => {
    mount({
      'GET /api/runs': new Response(JSON.stringify({ error: 'boom', code: 'X' }), { status: 500, headers: { 'content-type': 'application/json' } }),
    })
    expect(await screen.findByTestId('run-compare-error')).toHaveTextContent(/could not be loaded/i)
    expect(screen.queryByTestId('run-compare-none')).toBeNull()
  })
})

describe('Runs · Compare — the honesty rule', () => {
  const routes = () => ({
    'GET /api/runs': [READ, UNREAD],
    'GET /api/results/r-read': READ_RESULTS,
    'GET /api/results/r-unread': UNREAD_RESULTS,
  })

  it('a run that was not read back never shows Detected or Not seen', async () => {
    mount(routes())
    await pick('r-read', 'r-unread')
    await waitFor(() => expect(screen.getAllByTestId('run-compare-row')).toHaveLength(3))

    const unread = columnCells('r-unread')
    for (const cell of unread) {
      expect(within(cell).queryByRole('img', { name: 'Detected' })).toBeNull()
      expect(within(cell).queryByRole('img', { name: 'Not seen' })).toBeNull()
      expect(cell.textContent).not.toMatch(/Detected|Not seen/)
    }
    expect(unread.map((c) => within(c).getByRole('img').getAttribute('aria-label')))
      .toEqual(['Ran — not read back', 'Ran — not read back', 'Did not run'])

    const read = columnCells('r-read')
    expect(read.map((c) => within(c).getByRole('img').getAttribute('aria-label')))
      .toEqual(['Detected', 'Not seen', 'Did not run'])
  })

  it('the paired rows give the unread run a dash, not a count or a time', async () => {
    mount(routes())
    await pick('r-read', 'r-unread')
    await waitFor(() => expect(sumRow('detected')).toEqual(['1 of 2', '—']))
    expect(sumRow('fastest')).toEqual(['38s', '—'])
    expect(sumRow('steps')).toEqual(['2 of 3', '2 of 3'])
    expect(sumRow('workflow')).toEqual(['SIM-EDR-001', 'SIM-EDR-001'])
  })
})

describe('Runs · Compare — each column carries its next action', () => {
  it('Read back on an unread run reads it back, then the column can claim', async () => {
    let readBack = false
    const { calls } = mount({
      'GET /api/runs': () => (readBack ? [READ, { ...UNREAD, tc_verdict_detail: READ_DETAIL }] : [READ, UNREAD]),
      'GET /api/results/r-read': READ_RESULTS,
      'GET /api/results/r-unread': () => UNREAD_RESULTS,
      'POST /api/runs/:id/reconcile': () => { readBack = true; return { source: 'xsiam:lab', observed: 1, denominator: 2 } },
    })
    await pick('r-read', 'r-unread')
    const head = await waitFor(() => screen.getAllByTestId('run-compare-head').find((h) => h.dataset.runId === 'r-unread'))
    expect(within(head).getByRole('img', { name: 'Not read back' })).toBeInTheDocument()
    fireEvent.click(within(head).getByRole('button', { name: 'Read back' }))

    await waitFor(() => {
      const h = screen.getAllByTestId('run-compare-head').find((x) => x.dataset.runId === 'r-unread')
      expect(within(h).getByRole('button', { name: 'Open results' })).toBeInTheDocument()
    })
    const posts = calls.mock.calls
      .filter(([, init]) => (init?.method || 'GET').toUpperCase() === 'POST')
      .map(([url]) => new URL(url, 'http://localhost').pathname)
    expect(posts).toEqual(['/api/runs/r-unread/reconcile'])
    await waitFor(() => expect(columnCells('r-unread').map((c) => within(c).getByRole('img').getAttribute('aria-label')))
      .toEqual(['Detected', 'Not seen', 'Did not run']))
  })

  it('Open results on a read-back run opens its results', async () => {
    const onOpenResults = vi.fn()
    mount({
      'GET /api/runs': [READ, UNREAD],
      'GET /api/results/r-read': READ_RESULTS,
      'GET /api/results/r-unread': UNREAD_RESULTS,
    }, { onOpenResults })
    await pick('r-read', 'r-unread')
    const head = await waitFor(() => screen.getAllByTestId('run-compare-head').find((h) => h.dataset.runId === 'r-read'))
    expect(within(head).getByRole('img', { name: 'Read back' })).toBeInTheDocument()
    expect(within(head).queryByRole('button', { name: 'Read back' })).toBeNull()
    fireEvent.click(within(head).getByRole('button', { name: 'Open results' }))
    expect(onOpenResults).toHaveBeenCalledWith('r-read')
  })

  it('runs of different scenarios say so, and the rest read "Not in this run"', async () => {
    const other = run('r-cdr', { scenario_id: 'SIM-CDR-014', tc_verdict_detail: READ_DETAIL })
    mount({
      'GET /api/runs': [READ, other],
      'GET /api/results/r-read': READ_RESULTS,
      'GET /api/results/r-cdr': { results: [row(9, 'detected')], coverage: null },
    })
    await pick('r-read', 'r-cdr')
    expect(await screen.findByTestId('run-compare-mixed')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('run-compare-row')).toHaveLength(4))
    const nine = screen.getAllByTestId('run-compare-row').find((r) => r.dataset.detection === 'bioc-9')
    expect(within(nine).getAllByTestId('run-compare-cell')[0]).toHaveTextContent('Not in this run')
  })
})

describe('Runs · Compare — prevention delta', () => {
  const pairRuns = [
    run('r-report', { agent_profile_mode: 'report', paired_with_run_id: null, tc_verdict_detail: READ_DETAIL }),
    run('r-block', { agent_profile_mode: 'block', paired_with_run_id: 'r-report', tc_verdict_detail: READ_DETAIL }),
    run('r-solo', { tc_verdict_detail: READ_DETAIL }),
  ]
  const PAIR = {
    runs: [pairRuns[0], pairRuns[1]],
    delta: {
      report_run_id: 'r-report', block_run_id: 'r-block', severed_at_step: 'step-02',
      steps: [
        { step_id: 'step-01', step_name: 'dump', detections: [
          { detection_id: 'bioc-1', signal_type: 'BIOC', prevention_module: 'BTP',
            report: { outcome: 'detected', mttd_seconds: 38 },
            block: { outcome: 'prevented', outcome_reason: 'blocked', mttd_seconds: 4 } },
        ] },
        { step_id: 'step-02', step_name: 'exfil', detections: [
          { detection_id: 'bioc-2', signal_type: 'XQL', prevention_module: null,
            report: { outcome: 'detected', mttd_seconds: 50 }, block: null },
        ] },
      ],
    },
  }
  const base = {
    'GET /api/results/r-report': { results: [] },
    'GET /api/results/r-block': { results: [] },
    'GET /api/results/r-solo': { results: [] },
  }

  it('renders the delta table for two read-back runs that are a pair', async () => {
    mount({ ...base, 'GET /api/runs': pairRuns, 'GET /api/runs/:id/pair': PAIR })
    await pick('r-report', 'r-block')
    const table = await screen.findByTestId('pair-delta-table')
    const heads = Array.from(table.querySelectorAll('th')).map((h) => h.textContent)
    expect(heads).toEqual(['Step', 'Detection', 'Type', 'Module', 'Report-mode', 'Block-mode'])
    const rows = table.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('prevented')
    expect(rows[1].textContent).toContain('—')
    expect(rows[1].textContent).not.toMatch(/null/)
  })

  it('holds the delta back until both runs of the pair are read back', async () => {
    const unreadPair = pairRuns.map((r) => ({ ...r, tc_verdict_detail: UNREAD_DETAIL }))
    const { calls } = mount({ ...base, 'GET /api/runs': unreadPair, 'GET /api/runs/:id/pair': PAIR })
    await pick('r-report', 'r-block')
    expect(await screen.findByTestId('pair-delta-unread')).toBeInTheDocument()
    expect(screen.queryByTestId('pair-delta-table')).toBeNull()
    const asked = calls.mock.calls.map(([url]) => new URL(url, 'http://localhost').pathname)
    expect(asked.some((p) => p.endsWith('/pair'))).toBe(false)
  })

  it('leaves the plain comparison alone when the two runs are not a pair', async () => {
    mount({ ...base, 'GET /api/runs': pairRuns })
    await pick('r-report', 'r-solo')
    await waitFor(() => expect(screen.getByTestId('run-compare-grid')).toBeInTheDocument())
    expect(screen.queryByTestId('pair-delta-table')).toBeNull()
    expect(screen.queryByTestId('pair-delta-unread')).toBeNull()
  })

  it('says so when the delta has no rows', async () => {
    mount({ ...base, 'GET /api/runs': pairRuns, 'GET /api/runs/:id/pair': { runs: [], delta: { steps: [] } } })
    await pick('r-report', 'r-block')
    expect(await screen.findByText('No delta rows — the pair has no comparable detections.')).toBeInTheDocument()
  })

  it('shows the structured detail of a pair 409', async () => {
    mount({
      ...base,
      'GET /api/runs': pairRuns,
      'GET /api/runs/:id/pair': new Response(JSON.stringify({
        detail: { error: 'Run not paired', code: 'RUN_NOT_PAIRED', detail: 'partner run was deleted' },
      }), { status: 409, headers: { 'content-type': 'application/json' } }),
    })
    await pick('r-report', 'r-block')
    const err = await screen.findByTestId('pair-delta-error')
    expect(err.textContent).toMatch(/partner run was deleted/)
    expect(err.textContent).toMatch(/RUN_NOT_PAIRED/)
  })
})
