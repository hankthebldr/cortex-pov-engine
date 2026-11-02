/**
 * RunList — the Runs table rebuilt on live run records.
 *
 * Pins: every row field comes from `/api/runs` fields; the started cell
 * carries the absolute UTC time as its title; filters write URL params
 * (scenario / status) and reset the page; consecutive repeats collapse into
 * a group with a status breakdown and expand in place; the pager label
 * counts the filtered set; a failed run never reads as a detection miss.
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
    ...over,
  }
}

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
    const det = within(row).getByTestId('run-detections')
    expect(det).toHaveTextContent('3 / 10')
    expect(det).toHaveAttribute('title', '3 of 10 expected detections observed on this SimCore')
    expect(within(row).getByTestId('run-status')).toHaveTextContent('complete')
  })

  it('names the push bundle as the agent for a push run', () => {
    render(<RunList runs={[run({ mode: 'push', status: 'staged', target: null, expected_detections: 0, detected_count: 0 })]} />)
    const row = screen.getByTestId('run-row')
    expect(row).toHaveTextContent('push bundle')
    expect(within(row).getByTestId('run-detections')).toHaveTextContent('—')
    expect(within(row).getByTestId('run-duration')).toHaveTextContent('—')
  })

  it('never renders a failed run with no hits as a detection miss', () => {
    render(<RunList runs={[run({ status: 'failed', detected_count: 0 })]} />)
    const det = screen.getByTestId('run-detections')
    expect(det).toHaveTextContent('unproven')
    expect(det.className).toMatch(/run-list__det--unproven/)
    expect(screen.getByTestId('run-status').className).toMatch(/--unproven/)
  })

  it('opens the run on click', () => {
    const onOpen = vi.fn()
    render(<RunList runs={[run({ run_id: 'open-me' })]} onOpen={onOpen} />)
    fireEvent.click(screen.getByTestId('run-row'))
    expect(onOpen).toHaveBeenCalledWith('open-me')
  })

  it('states where the counts come from', () => {
    render(<RunList runs={[run()]} />)
    expect(screen.getByTestId('run-list')).toHaveTextContent('tenant-verified: 0')
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
