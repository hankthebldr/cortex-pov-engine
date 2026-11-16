/**
 * RunList — the Runs table rebuilt on live run records.
 *
 * Pins: every row field comes from `/api/runs` fields; the started cell
 * carries the absolute UTC time as its title; filters write URL params
 * (scenario / status) and reset the page; consecutive repeats collapse into
 * a group with a status breakdown and expand in place; the pager label
 * counts the filtered set.
 *
 * Design v26 §4.6: each row carries the five-stage lifecycle strip and one
 * action for its phase. A row says how the run went (Ran · Failed · …) and
 * whether it is reviewed, each as a StatusIcon and a word — and never a
 * detection count or verdict: what Cortex saw is on Results.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react'
import RunList from '../RunList.jsx'

afterEach(cleanup)

const T0 = '2026-11-02T22:07:46.252776'

function run(over) {
  return {
    run_id: 'r-' + Math.random().toString(36).slice(2, 10),
    scenario_id: 'SIM-EDR-001',
    status: 'complete',
    mode: 'pull',
    target: 'pov-target-01-41a13a',
    started_at: T0,
    completed_at: '2026-11-02T22:07:50.022',
    expected_detections: 10,
    detected_count: 3,
    delivered_at: '2026-11-02T22:07:47',
    output_bytes: 1200,
    // Read back: SimCore scored this run from a tenant read.
    tc_verdict_detail: { source: 'reconcile:xsiam:lab', scored_at: '2026-11-02T22:12:00' },
    ...over,
  }
}
const UNREAD = { tc_verdict_detail: { source: 'complete' } }
const state = (row) => within(within(row).getByTestId('run-status')).getByRole('img').getAttribute('aria-label')
const review = (row) => within(row).getByTestId('run-review')

describe('RunList rows', () => {
  it('renders every row field from the run record, with the absolute UTC time as the title', () => {
    const r = run({ run_id: 'run-aaaa-bbbb-cccc' })
    render(<RunList runs={[r]} />)
    const row = screen.getByTestId('run-row')
    expect(row).toHaveTextContent('SIM-EDR-001')
    expect(within(row).getByTestId('run-started')).toHaveAttribute('title', '2026-11-02 22:07:46 UTC')
    expect(within(row).getByTestId('run-started').textContent).toMatch(/ago$/)
    expect(row).toHaveTextContent('pov-target-01-41a13a')
    expect(within(row).getByTestId('run-duration')).toHaveTextContent('3.8s')
    expect(state(row)).toBe('Ran')
    expect(within(review(row)).getByRole('img', { name: 'Reviewed' })).toBeInTheDocument()
    // What Cortex saw is on Results: the row carries no count and no verdict.
    expect(row).not.toHaveTextContent(/3 of 10|3 \/ 10|detected|missed/i)
  })

  it('names the push bundle as the agent for a push run', () => {
    render(<RunList runs={[run({ mode: 'push', status: 'staged', target: null, expected_detections: 0, detected_count: 0 })]} />)
    const row = screen.getByTestId('run-row')
    expect(row).toHaveTextContent('push bundle')
    expect(state(row)).toBe('Staged')
    expect(within(row).getByTestId('run-duration')).toHaveTextContent('—')
  })

  it('a failed run reads Failed, and says nothing about detections', () => {
    render(<RunList runs={[run({ status: 'failed', detected_count: 0 })]} />)
    const row = screen.getByTestId('run-row')
    expect(state(row)).toBe('Failed')
    expect(row).not.toHaveTextContent(/unproven|missed|detected/i)
  })

  it('its state is a kit StatusIcon, and its controls are kit buttons', () => {
    render(<RunList runs={[run()]} params={{ status: 'complete' }} />)
    expect(screen.getByTestId('run-status').querySelector('.kit-status')).not.toBeNull()
    expect(screen.getByTestId('run-status').querySelector('.chip')).toBeNull()
    for (const id of ['run-filter-clear', 'run-pager-prev', 'run-pager-next']) {
      expect(screen.getByTestId(id)).toHaveClass('kit-btn')
    }
    expect(document.querySelector('.btn')).toBeNull()
  })

  it('opens the run from its scenario cell', () => {
    const onOpen = vi.fn()
    render(<RunList runs={[run({ run_id: 'open-me' })]} onOpen={onOpen} />)
    fireEvent.click(within(screen.getByTestId('run-row')).getByRole('button', { name: /^Open run SIM-EDR-001/ }))
    expect(onOpen).toHaveBeenCalledWith('open-me')
  })

})

describe('RunList lifecycle (design v26 §4.6)', () => {
  const stageWords = (row) => within(within(row).getByRole('list', { name: /lifecycle/i }))
    .getAllByRole('listitem').map((li) => li.textContent)

  it('draws the five-stage strip on every row, the third stage named by how it ended', () => {
    render(<RunList runs={[run({ run_id: 'f', status: 'failed', ...UNREAD })]} />)
    const words = stageWords(screen.getByTestId('run-row'))
    expect(words).toHaveLength(5)
    expect(words.map((w) => w.split(':')[0])).toEqual(['Launched', 'Running', 'Failed', 'Reviewed', 'Results'])
    expect(words[2]).toMatch(/Failed: failed/)
    expect(words[3]).toMatch(/Reviewed: not yet/)
  })

  it('a running run offers Open, and has no review state yet', () => {
    const onOpen = vi.fn()
    render(<RunList runs={[run({ run_id: 'live', status: 'running', completed_at: null, ...UNREAD })]} onOpen={onOpen} />)
    const row = screen.getByTestId('run-row')
    fireEvent.click(within(row).getByRole('button', { name: 'Open' }))
    expect(onOpen).toHaveBeenCalledWith('live')
    expect(state(row)).toBe('Running')
    expect(review(row)).toHaveTextContent('—')
    expect(row).not.toHaveTextContent(/\d+ of \d+|\d+ \/ \d+/)
  })

  it('a finished run nobody read back offers Review as the primary action and claims nothing', () => {
    const onReadBack = vi.fn()
    render(<RunList runs={[run({ run_id: 'u', ...UNREAD })]} onReadBack={onReadBack} />)
    const row = screen.getByTestId('run-row')
    expect(within(review(row)).getByRole('img', { name: 'Not reviewed' })).toBeInTheDocument()
    expect(row).not.toHaveTextContent('3 of 10')
    const act = within(row).getByRole('button', { name: 'Review' })
    expect(act.className).toMatch(/kit-btn--primary/)
    fireEvent.click(act)
    expect(onReadBack).toHaveBeenCalledWith('u')
  })

  it('while reading, the action reads Reviewing… and is disabled, and the row keeps its place', () => {
    render(<RunList runs={[run({ run_id: 'x', scenario_id: 'SIM-EDR-008', ...UNREAD }), run({ run_id: 'u', ...UNREAD })]} reading={new Set(['u'])} />)
    const rows = screen.getAllByTestId('run-row')
    expect(rows.map((r) => r.getAttribute('data-run-id'))).toEqual(['x', 'u'])
    const btn = within(rows[1]).getByRole('button', { name: 'Reviewing…' })
    expect(btn).toBeDisabled()
    expect(within(review(rows[1])).getByRole('img', { name: 'Reviewing…' })).toBeInTheDocument()
  })

  it('a read-back run offers Open results', () => {
    const onOpenResults = vi.fn()
    render(<RunList runs={[run({ run_id: 'done' })]} onOpenResults={onOpenResults} />)
    fireEvent.click(within(screen.getByTestId('run-row')).getByRole('button', { name: 'Open results' }))
    expect(onOpenResults).toHaveBeenCalledWith('done')
  })

  it('a run that ended before any step ran offers no action', () => {
    render(<RunList runs={[run({ run_id: 'n', status: 'aborted', delivered_at: null, output_bytes: 0, ...UNREAD })]} />)
    const row = screen.getByTestId('run-row')
    expect(state(row)).toBe('Aborted')
    expect(review(row)).toHaveTextContent('—')
    expect(within(row).queryByRole('button', { name: /^review$|open results|^open$/i })).toBeNull()
    expect(within(row).getByTestId('run-action')).toHaveTextContent('—')
  })

  it('a run where no step that carries a detection ran says so and offers no Review', () => {
    const NOT_EXECUTED = { tc_verdict: 'pending', tc_verdict_detail: { source: 'complete', reason: 'not_executed' } }
    render(<RunList runs={[run({ run_id: 'dry', ...NOT_EXECUTED })]} />)
    const row = screen.getByTestId('run-row')
    expect(row).toHaveAttribute('data-phase', 'nothing')
    expect(state(row)).toBe('Did not run')
    expect(within(row).getByTestId('run-status'))
      .toHaveAttribute('title', expect.stringMatching(/no step that carries a detection ran/i))
    expect(within(row).queryByRole('button', { name: /^review$|open results/i })).toBeNull()
  })

  it('states why a read back did not happen, and keeps Review on offer', () => {
    const outcomes = new Map([['u', { ok: false, message: 'No tenant credential is configured, so nothing was read.' }]])
    render(<RunList runs={[run({ run_id: 'u', ...UNREAD })]} outcomes={outcomes} />)
    const row = screen.getByTestId('run-row')
    expect(row).toHaveTextContent('No tenant credential is configured')
    expect(within(row).getByRole('button', { name: 'Review' })).toBeEnabled()
  })
})

describe('RunList filters and paging', () => {
  const RUNS = [
    run({ run_id: 'a', scenario_id: 'SIM-EDR-008', status: 'complete' }),
    run({ run_id: 'b', scenario_id: 'SIM-EDR-001', status: 'failed' }),
    run({ run_id: 'c', scenario_id: 'SIM-CDR-003', status: 'staged', mode: 'push' }),
  ]

  it('writes the scenario filter to the URL params and resets the page', () => {
    const setParams = vi.fn()
    render(<RunList runs={RUNS} params={{ page: '2' }} setParams={setParams} />)
    fireEvent.change(screen.getByTestId('run-filter-scenario'), { target: { value: 'SIM-EDR-001' } })
    expect(setParams).toHaveBeenCalledWith({ scenario: 'SIM-EDR-001', page: null }, { replace: true })
  })

  it('narrows the rows by the scenario and status params', () => {
    render(<RunList runs={RUNS} params={{ scenario: 'SIM-EDR-001' }} />)
    expect(screen.getAllByTestId('run-row')).toHaveLength(1)
    expect(screen.getByTestId('run-row')).toHaveAttribute('data-run-id', 'b')
    expect(screen.getByTestId('run-pager-label')).toHaveTextContent('Page 1 of 1 · 1 run of 3')
    cleanup()
    render(<RunList runs={RUNS} params={{ status: 'complete' }} />)
    expect(screen.getByTestId('run-row')).toHaveAttribute('data-run-id', 'a')
  })

  it('says so when a filter matches nothing, instead of rendering an empty table', () => {
    render(<RunList runs={RUNS} params={{ scenario: 'SIM-NDR-999' }} />)
    expect(screen.getByTestId('run-list-filtered-empty')).toHaveTextContent('no runs match this filter')
    expect(screen.queryAllByTestId('run-row')).toHaveLength(0)
  })

  it('offers a clear control only while a filter is set', () => {
    const setParams = vi.fn()
    render(<RunList runs={RUNS} params={{}} setParams={setParams} />)
    expect(screen.queryByTestId('run-filter-clear')).toBeNull()
    cleanup()
    render(<RunList runs={RUNS} params={{ status: 'failed' }} setParams={setParams} />)
    fireEvent.click(screen.getByTestId('run-filter-clear'))
    expect(setParams).toHaveBeenCalledWith({ scenario: null, status: null, page: null }, { replace: true })
  })

  it('pages at 25 entries and prints the filtered total', () => {
    const many = Array.from({ length: 53 }, (_, i) => run({
      run_id: `m-${i}`,
      // alternate scenarios so nothing groups
      scenario_id: i % 2 ? 'SIM-EDR-001' : 'SIM-EDR-008',
    }))
    const setParams = vi.fn()
    render(<RunList runs={many} params={{ page: '3' }} setParams={setParams} />)
    expect(screen.getByTestId('run-pager-label')).toHaveTextContent('Page 3 of 3 · 53 runs')
    expect(screen.getAllByTestId('run-row')).toHaveLength(3)
    expect(screen.getByTestId('run-pager-next')).toBeDisabled()
    fireEvent.click(screen.getByTestId('run-pager-prev'))
    expect(setParams).toHaveBeenCalledWith({ page: '2' }, { replace: true })
  })
})

describe('RunList grouping', () => {
  const REPEATS = [
    run({ run_id: 'n1', status: 'failed', detected_count: 0 }),
    run({ run_id: 'n2', status: 'failed', detected_count: 0 }),
    run({ run_id: 'n3', status: 'complete' }),
    run({ run_id: 'n4', status: 'failed', detected_count: 0 }),
    run({ run_id: 'o1', scenario_id: 'SIM-EDR-008' }),
  ]

  it('collapses consecutive repeats into one group with a status breakdown', () => {
    render(<RunList runs={REPEATS} />)
    const group = screen.getByTestId('run-group')
    expect(group).toHaveAttribute('data-scenario-id', 'SIM-EDR-001')
    // Only the newest of the four is a visible row until expanded; plus the
    // lone SIM-EDR-008 row outside the group.
    expect(screen.getAllByTestId('run-row')).toHaveLength(2)
    const toggle = screen.getByTestId('run-group-toggle')
    expect(toggle).toHaveTextContent('+3 more')
    expect(toggle).toHaveTextContent('4 consecutive runs of SIM-EDR-001')
    expect(toggle).toHaveTextContent('3 failed · 1 complete')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('expands in place and collapses again', () => {
    render(<RunList runs={REPEATS} />)
    fireEvent.click(screen.getByTestId('run-group-toggle'))
    expect(screen.getAllByTestId('run-row')).toHaveLength(5)
    expect(screen.getByTestId('run-group-toggle')).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByTestId('run-group-toggle'))
    expect(screen.getAllByTestId('run-row')).toHaveLength(2)
  })

  it('does not group two repeats — both attempts stay visible', () => {
    render(<RunList runs={REPEATS.slice(0, 2)} />)
    expect(screen.queryByTestId('run-group')).toBeNull()
    expect(screen.getAllByTestId('run-row')).toHaveLength(2)
  })
})
