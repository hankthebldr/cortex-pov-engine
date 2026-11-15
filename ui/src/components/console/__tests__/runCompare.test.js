/**
 * runCompare — the model behind Runs · Compare (design change C2).
 *
 * The honesty rule: only a run that has been read back may say Detected or
 * Not seen for a detection. A run that was not read back says what the run
 * itself knows — "Ran — not read back" or "Did not run" — whatever its Result
 * rows carry, a hand mark (`observed: true`) and an outcome included.
 */
import { describe, it, expect } from 'vitest'
import {
  COMPARE_MAX, COMPARE_FULL, togglePick, isComparable, compareCell, cellMeta,
  buildCompare, formatSeconds,
} from '../runCompare.js'
import { runLifecycle } from '../runLifecycle.js'
import { LIVE_STATES } from '../chainStage.js'

const READ = {
  run_id: 'r-read', scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull',
  delivered_at: '2026-11-13T17:00:05',
  tc_verdict_detail: { source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T17:10:00' },
}
const UNREAD = {
  run_id: 'r-unread', scenario_id: 'SIM-EDR-001', status: 'complete', mode: 'pull',
  delivered_at: '2026-11-13T18:00:05',
  tc_verdict_detail: { source: 'complete' },
}
const row = (id, outcome, over = {}) => ({
  id, step_id: `step-0${id}`, signal_type: 'BIOC', detection_id: `bioc-${id}`,
  expected_detection: `det ${id}`, outcome, outcome_reason: null, observed: false, mttd_seconds: null, ...over,
})

describe('compareCell — the honesty rule', () => {
  it('a read-back run says Detected / Not seen / Did not run', () => {
    expect(compareCell(READ, row(1, 'detected', { observed: true }))).toBe('detected')
    expect(compareCell(READ, row(1, 'prevented', { observed: true }))).toBe('detected')
    expect(compareCell(READ, row(2, 'missed'))).toBe('not_seen')
    expect(compareCell(READ, row(3, 'pending'))).toBe('not_seen')
    expect(compareCell(READ, row(4, 'not_reached'))).toBe('did_not_run')
    expect(compareCell(READ, row(5, 'not_executed'))).toBe('did_not_run')
  })

  it('a run that was not read back never says Detected or Not seen, whatever its rows carry', () => {
    const rows = [
      row(1, 'detected', { observed: true, mttd_seconds: 30 }), // a hand mark
      row(2, 'missed'),
      row(3, 'pending'),
      row(4, null, { observed: true }),                          // legacy validation
      row(5, null),
    ]
    for (const r of rows) {
      const k = compareCell(UNREAD, r)
      expect(k).toBe('ran')
      expect(['detected', 'not_seen']).not.toContain(k)
    }
    expect(compareCell(UNREAD, row(6, 'not_reached'))).toBe('did_not_run')
    expect(compareCell(UNREAD, row(7, 'not_executed'))).toBe('did_not_run')
  })

  it('a run where nothing ran says Did not run for every row', () => {
    expect(compareCell(UNREAD, row(1, null), { nothingRan: true })).toBe('did_not_run')
    expect(compareCell(READ, row(1, 'detected'), { nothingRan: true })).toBe('did_not_run')
  })

  it('a detection the run does not carry is "Not in this run", not a state', () => {
    expect(compareCell(READ, null)).toBe('absent')
    expect(cellMeta('absent')).toEqual({ status: null, word: 'Not in this run' })
  })

  it('speaks the chain stage’s words', () => {
    expect(cellMeta('detected')).toBe(LIVE_STATES.detected)
    expect(cellMeta('ran').word).toBe('Ran — not read back')
    expect(cellMeta('did_not_run').word).toBe('Did not run')
  })
})

describe('picking runs', () => {
  it('takes two or three finished runs and refuses a fourth', () => {
    expect(COMPARE_MAX).toBe(3)
    let s = { picked: [] }
    for (const id of ['a', 'b', 'c']) s = togglePick(s.picked, id)
    expect(s).toEqual({ picked: ['a', 'b', 'c'], refused: false })
    const fourth = togglePick(s.picked, 'd')
    expect(fourth).toEqual({ picked: ['a', 'b', 'c'], refused: true })
    expect(COMPARE_FULL).toMatch(/up to three/)
    expect(togglePick(s.picked, 'b')).toEqual({ picked: ['a', 'c'], refused: false })
  })

  it('only a run that ended here can be compared', () => {
    expect(isComparable({ status: 'complete' })).toBe(true)
    expect(isComparable({ status: 'failed' })).toBe(true)
    expect(isComparable({ status: 'aborted' })).toBe(true)
    expect(isComparable({ status: 'running' })).toBe(false)
    expect(isComparable({ status: 'pending' })).toBe(false)
    expect(isComparable({ status: 'staged' })).toBe(false)
  })
})

describe('buildCompare', () => {
  const readRows = [
    row(1, 'detected', { observed: true, mttd_seconds: 38 }),
    row(2, 'missed'),
    row(3, 'not_reached'),
  ]
  const unreadRows = [
    row(1, 'detected', { observed: true, mttd_seconds: 12 }),
    row(2, null),
    row(3, 'not_executed'),
  ]
  const lifecycles = { 'r-read': runLifecycle(READ), 'r-unread': runLifecycle(UNREAD) }
  const results = {
    'r-read': { rows: readRows, coverage: { denominator: 2, observed_in_denominator: 1 } },
    'r-unread': { rows: unreadRows, coverage: { denominator: 2, observed_in_denominator: 1 } },
  }
  const grid = buildCompare({ runs: [READ, UNREAD], lifecycles, results })
  const sum = (k) => grid.summary.find((s) => s.key === k).values.map((v) => v.text)

  it('lines each detection up across the runs', () => {
    expect(grid.rows.map((r) => r.slug)).toEqual(['bioc-1', 'bioc-2', 'bioc-3'])
    expect(grid.rows.map((r) => r.cells.map((c) => c.key))).toEqual([
      ['detected', 'ran'],
      ['not_seen', 'ran'],
      ['did_not_run', 'did_not_run'],
    ])
    expect(grid.mixed).toBe(false)
  })

  it('the paired rows claim a detection count and a time only for a read-back run', () => {
    expect(sum('detected')).toEqual(['1 of 2', '—'])
    expect(sum('fastest')).toEqual(['38s', '—'])
    expect(sum('steps')).toEqual(['2 of 3', '2 of 3'])
    expect(sum('workflow')).toEqual(['SIM-EDR-001', 'SIM-EDR-001'])
  })

  it('a column whose results did not load makes no claim', () => {
    const g = buildCompare({ runs: [READ, UNREAD], lifecycles, results: { 'r-read': results['r-read'], 'r-unread': { error: 'boom' } } })
    expect(g.rows.map((r) => r.cells[1].key)).toEqual(['error', 'error', 'error'])
    expect(g.summary.find((s) => s.key === 'steps').values[1].text).toBe('—')
  })

  it('runs of different scenarios line up only what they share', () => {
    const other = { ...READ, run_id: 'r-other', scenario_id: 'SIM-CDR-014' }
    const g = buildCompare({
      runs: [READ, other],
      lifecycles: { 'r-read': runLifecycle(READ), 'r-other': runLifecycle(other) },
      results: { 'r-read': results['r-read'], 'r-other': { rows: [row(9, 'detected')], coverage: null } },
    })
    expect(g.mixed).toBe(true)
    const nine = g.rows.find((r) => r.slug === 'bioc-9')
    expect(nine.cells.map((c) => c.key)).toEqual(['absent', 'detected'])
  })
})

describe('formatSeconds', () => {
  it('reads seconds and minutes, and a dash for nothing', () => {
    expect(formatSeconds(38)).toBe('38s')
    expect(formatSeconds(125)).toBe('2m 5s')
    expect(formatSeconds(120)).toBe('2m')
    expect(formatSeconds(null)).toBe('—')
    expect(formatSeconds('x')).toBe('—')
  })
})
