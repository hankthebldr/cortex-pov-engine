/**
 * runRows — the pure helpers behind a Runs row.
 *
 * What these pin:
 *   - a naive server timestamp is read as UTC (the non-UTC bug class: a DC in
 *     NAM used to see every run "in 5 hours");
 *   - the detections cell never turns a run that died into a detection miss;
 *   - grouping collapses ONLY consecutive repeats, and only at the threshold;
 *   - paging reports a real total and never drops the last partial page.
 */
import { describe, it, expect } from 'vitest'
import {
  relativeTime, absoluteTime, runDuration, detectionCell,
  filterRuns, groupRuns, pageOf, runAgent, PAGE_SIZE,
} from '../runRows.js'

const NOW = Date.parse('2026-11-02T22:10:00Z')

describe('relativeTime / absoluteTime', () => {
  it('reads a zone-less SimCore timestamp as UTC', () => {
    // 22:07:46 UTC, 2m14s before NOW. Parsed as local time in UTC-6 this
    // would be "in 5h 57m", which is the bug.
    expect(relativeTime('2026-11-02T22:07:46.252776', NOW)).toBe('2m ago')
    expect(absoluteTime('2026-11-02T22:07:46.252776')).toBe('2026-11-02 22:07:46 UTC')
  })
  it('scales from seconds to days and never fabricates a time', () => {
    expect(relativeTime('2026-11-02T22:09:50', NOW)).toBe('10s ago')
    expect(relativeTime('2026-11-02T18:10:00', NOW)).toBe('4h ago')
    expect(relativeTime('2026-10-30T22:10:00', NOW)).toBe('3d ago')
    expect(relativeTime(null, NOW)).toBe('—')
    expect(relativeTime('not a time', NOW)).toBe('—')
    expect(absoluteTime(null)).toBe('')
  })
})

describe('runDuration', () => {
  it('is completed_at − started_at for a terminal run', () => {
    expect(runDuration({ status: 'complete', started_at: '2026-11-02T22:07:46.252', completed_at: '2026-11-02T22:07:50.022' }, NOW)).toBe('3.8s')
    expect(runDuration({ status: 'failed', started_at: '2026-11-02T22:00:00', completed_at: '2026-11-02T22:02:10' }, NOW)).toBe('2m 10s')
  })
  it('is live elapsed while running, and a dash when nothing can be measured', () => {
    expect(runDuration({ status: 'running', started_at: '2026-11-02T22:09:15' }, NOW)).toBe('45s')
    expect(runDuration({ status: 'staged', started_at: '2026-11-02T22:09:15' }, NOW)).toBe('—')
    expect(runDuration({ status: 'complete' }, NOW)).toBe('—')
    // A failed run that never got a completed_at stamp is not "still running".
    expect(runDuration({ status: 'failed', started_at: '2026-11-02T22:09:15' }, NOW)).toBe('—')
  })
})

describe('detectionCell — the truthfulness rule', () => {
  it('shows d / n for a complete or running run', () => {
    expect(detectionCell({ status: 'complete', detected_count: 3, expected_detections: 10 }))
      .toEqual({ text: '3 / 10', tone: 'partial', title: '3 of 10 expected detections observed on this SimCore' })
    expect(detectionCell({ status: 'running', detected_count: 0, expected_detections: 4 }).text).toBe('0 / 4')
    expect(detectionCell({ status: 'complete', detected_count: 4, expected_detections: 4 }).tone).toBe('hit')
  })
  it('NEVER renders a failed or aborted run with zero hits as a miss', () => {
    const cell = detectionCell({ status: 'failed', detected_count: 0, expected_detections: 10 })
    expect(cell.text).toBe('unproven')
    expect(cell.tone).toBe('unproven')
    expect(cell.title).toMatch(/did not complete/)
    expect(detectionCell({ status: 'aborted', detected_count: 0, expected_detections: 2 }).text).toBe('unproven')
  })
  it('keeps the hits a failed run DID produce before it died', () => {
    expect(detectionCell({ status: 'failed', detected_count: 2, expected_detections: 10 }).text).toBe('2 / 10')
  })
  it('renders a dash — not 0 / 0 — when the API reports no rows or no field', () => {
    expect(detectionCell({ status: 'staged', detected_count: 0, expected_detections: 0 }).text).toBe('—')
    expect(detectionCell({ status: 'complete' }).text).toBe('—')
    expect(detectionCell({ status: 'complete' }).title).toMatch(/not reported/)
  })
})

describe('runAgent', () => {
  it('names the beacon for a pull run and the bundle for a push run', () => {
    expect(runAgent({ mode: 'pull', target: 'pov-target-01-41a13a' })).toBe('pov-target-01-41a13a')
    expect(runAgent({ mode: 'push' })).toBe('push bundle')
    expect(runAgent({ mode: 'pull' })).toBe('—')
  })
})

const RUNS = [
  { run_id: 'a', scenario_id: 'SIM-EDR-001', status: 'failed' },
  { run_id: 'b', scenario_id: 'SIM-EDR-001', status: 'failed' },
  { run_id: 'c', scenario_id: 'SIM-EDR-001', status: 'complete' },
  { run_id: 'd', scenario_id: 'SIM-EDR-008', status: 'complete' },
  { run_id: 'e', scenario_id: 'SIM-EDR-001', status: 'failed' },
  { run_id: 'f', scenario_id: 'SIM-EDR-001', status: 'failed' },
  { run_id: 'g', scenario_id: 'SIM-CDR-003', status: 'staged' },
]

describe('filterRuns', () => {
  it('filters by scenario and status independently and together', () => {
    expect(filterRuns(RUNS, { scenario: 'SIM-EDR-008' }).map((r) => r.run_id)).toEqual(['d'])
    expect(filterRuns(RUNS, { status: 'failed' })).toHaveLength(4)
    expect(filterRuns(RUNS, { scenario: 'SIM-EDR-001', status: 'complete' }).map((r) => r.run_id)).toEqual(['c'])
    expect(filterRuns(RUNS, {})).toHaveLength(7)
  })
  it('treats the legacy "completed" token as complete', () => {
    expect(filterRuns([{ run_id: 'x', scenario_id: 's', status: 'completed' }], { status: 'complete' })).toHaveLength(1)
  })
})

describe('groupRuns', () => {
  it('collapses only CONSECUTIVE repeats at or above the threshold', () => {
    const groups = groupRuns(RUNS, 3)
    // a,b,c are three consecutive SIM-EDR-001 → one group; d alone; e,f are
    // only two → two plain rows; g alone.
    expect(groups.map((g) => [g.kind, g.rows.length])).toEqual([
      ['group', 3], ['row', 1], ['row', 1], ['row', 1], ['row', 1],
    ])
    expect(groups[0].scenarioId).toBe('SIM-EDR-001')
    expect(groups[0].newest.run_id).toBe('a')
    expect(groups[0].byStatus).toEqual({ failed: 2, complete: 1 })
  })
  it('never groups across a scenario boundary', () => {
    const groups = groupRuns(RUNS, 2)
    expect(groups.map((g) => g.rows.map((r) => r.run_id))).toEqual([
      ['a', 'b', 'c'], ['d'], ['e', 'f'], ['g'],
    ])
  })
})

describe('pageOf', () => {
  it('pages over whatever list it is given and reports a real total', () => {
    const items = Array.from({ length: 53 }, (_, i) => i)
    expect(PAGE_SIZE).toBe(25)
    expect(pageOf(items, 1)).toEqual({ items: items.slice(0, 25), page: 1, pages: 3, total: 53 })
    expect(pageOf(items, 3).items).toEqual(items.slice(50))
    // Out-of-range pages clamp rather than return nothing.
    expect(pageOf(items, 9).page).toBe(3)
    expect(pageOf(items, 0).page).toBe(1)
    expect(pageOf([], 1)).toEqual({ items: [], page: 1, pages: 1, total: 0 })
  })
})
