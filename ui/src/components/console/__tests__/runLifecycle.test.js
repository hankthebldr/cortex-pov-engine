/**
 * runLifecycle — one model for where a run is (design v26 §4.6):
 *
 *   Launched → Running → Finished / Failed / Aborted → Read back → Results
 *
 * and which of five phases it is in: running · nothing · unread · reading ·
 * ready. "Read back" is a fact SimCore recorded about the run (the tenant was
 * read for it), never something the console assumes because the run ended.
 */
import { describe, it, expect } from 'vitest'
import { runLifecycle, readBackOf, STAGE_LABELS } from '../runLifecycle.js'

const base = {
  run_id: 'r-1',
  scenario_id: 'SIM-EDR-001',
  mode: 'pull',
  status: 'complete',
  delivered_at: '2026-11-13T17:00:05',
  output_bytes: 2048,
  tc_verdict: 'pending',
  tc_verdict_detail: { source: 'complete' },
}
const run = (over = {}) => ({ ...base, ...over })
const states = (lc) => lc.stages.map((s) => s.state)

describe('runLifecycle', () => {
  it('names the five stages in order, the third one after how the run ended', () => {
    expect(STAGE_LABELS).toEqual(['Launched', 'Running', 'Finished', 'Read back', 'Results'])
    expect(runLifecycle(run({ status: 'failed' })).stages.map((s) => s.label))
      .toEqual(['Launched', 'Running', 'Failed', 'Read back', 'Results'])
    expect(runLifecycle(run({ status: 'aborted' })).stages[2].label).toBe('Aborted')
  })

  it('a running run is in phase running, on the Running stage', () => {
    const lc = runLifecycle(run({ status: 'running', delivered_at: null, tc_verdict_detail: null }))
    expect(lc.phase).toBe('running')
    expect(states(lc)).toEqual(['done', 'current', 'todo', 'todo', 'todo'])
  })

  it('a queued run is running too, still on Launched', () => {
    const lc = runLifecycle(run({ status: 'pending', delivered_at: null, tc_verdict_detail: null }))
    expect(lc.phase).toBe('running')
    expect(states(lc)).toEqual(['current', 'todo', 'todo', 'todo', 'todo'])
  })

  it('a finished run nobody has read back is unread — ending is not reading', () => {
    const lc = runLifecycle(run())
    expect(lc.phase).toBe('unread')
    expect(lc.readBack).toBeNull()
    expect(states(lc)).toEqual(['done', 'done', 'current', 'todo', 'todo'])
  })

  it('a failed or aborted run shows its end as failed', () => {
    expect(states(runLifecycle(run({ status: 'failed' })))).toEqual(['done', 'done', 'failed', 'todo', 'todo'])
    expect(states(runLifecycle(run({ status: 'aborted' })))).toEqual(['done', 'done', 'failed', 'todo', 'todo'])
  })

  it('a pull run that ended before the agent took its task ran nothing', () => {
    const lc = runLifecycle(run({ status: 'aborted', delivered_at: null, output_bytes: 0 }))
    expect(lc.phase).toBe('nothing')
  })

  it('launch warnings in the output are not a step having run', () => {
    const tail = '!! TARGET_EGRESS_REQUIRED: TOOL-X — pull mode does not install tier-4 tools\n\n--- RUN ABORTED BY OPERATOR ---\n'
    expect(runLifecycle(run({ status: 'aborted', delivered_at: null, output_bytes: 185, output_tail: tail })).phase)
      .toBe('nothing')
  })

  it('a step frame with no delivery stamp still ran (an older SimCore)', () => {
    const tail = '=== STEP 1/2 · step-01 · T1003 · identity=root ===\nok\n--- exit_code=0 duration=1s ---\n'
    expect(runLifecycle(run({ delivered_at: null, output_bytes: 900, output_tail: tail })).phase).toBe('unread')
  })

  it('reads back as ready once SimCore scored it from a tenant read', () => {
    const lc = runLifecycle(run({ tc_verdict_detail: { source: 'reconcile:xsiam:deathstar', scored_at: '2026-11-13T17:10:00' } }))
    expect(lc.phase).toBe('ready')
    expect(lc.readBack).toEqual({ from: 'xsiam:deathstar', at: '2026-11-13T17:10:00', how: 'reconcile' })
    expect(states(lc)).toEqual(['done', 'done', 'done', 'done', 'done'])
  })

  it('counts a verify that queried the tenant as a read back', () => {
    const lc = runLifecycle(run({
      tc_verdict_detail: { source: 'verify', verification: { tenant: 'deathstar', queries_issued: 3, last_attempt_at: '2026-11-13T17:12:00' } },
    }))
    expect(lc.phase).toBe('ready')
    expect(lc.readBack.from).toBe('deathstar')
  })

  it('a verify with no tenant credential read nothing, so the run stays unread', () => {
    const lc = runLifecycle(run({ tc_verdict_detail: { source: 'verify', verification: undefined } }))
    expect(lc.phase).toBe('unread')
  })

  it('a manual import of observations is a read back, and says where it came from', () => {
    const lc = runLifecycle(run({ tc_verdict_detail: { source: 'reconcile:manual-import' } }))
    expect(lc.phase).toBe('ready')
    expect(lc.readBack.from).toBe('a manual import')
  })

  it('is reading while a read back is in flight, and only for an ended run', () => {
    expect(runLifecycle(run(), { reading: true }).phase).toBe('reading')
    expect(states(runLifecycle(run(), { reading: true }))).toEqual(['done', 'done', 'done', 'current', 'todo'])
    expect(runLifecycle(run({ status: 'running' }), { reading: true }).phase).toBe('running')
  })

  it('a staged push bundle ran off this SimCore and can be read back', () => {
    const lc = runLifecycle(run({ mode: 'push', status: 'staged', delivered_at: null, output_bytes: 0 }))
    expect(lc.phase).toBe('unread')
    expect(lc.stages[2].label).toBe('Staged')
  })

  it('readBackOf is null for a run SimCore never scored from a tenant read', () => {
    expect(readBackOf(null)).toBeNull()
    expect(readBackOf(run({ tc_verdict_detail: null }))).toBeNull()
    expect(readBackOf(run({ tc_verdict_detail: { source: 'complete' } }))).toBeNull()
  })
})
