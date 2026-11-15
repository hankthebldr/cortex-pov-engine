/**
 * runState — how a run went, and whether it has been reviewed, as the word a
 * Runs row and the run's page show. Execution words only: a run never reads
 * "detected" or "missed" here.
 */
import { describe, it, expect } from 'vitest'
import { runState, reviewState, STEP_STATES } from '../runState.js'
import { runLifecycle } from '../runLifecycle.js'
import { STATUS } from '../kit/index.js'

const base = { mode: 'pull', delivered_at: '2026-11-13T17:00:03', output_bytes: 900 }
const word = (run) => runState(run, runLifecycle(run)).word
const review = (run, opts) => reviewState(runLifecycle(run, opts))?.word ?? null

describe('runState', () => {
  it('names each run status in execution words', () => {
    expect(word({ ...base, status: 'pending', delivered_at: null })).toBe('Queued')
    expect(word({ ...base, status: 'running' })).toBe('Running')
    expect(word({ ...base, status: 'complete' })).toBe('Ran')
    expect(word({ ...base, status: 'completed' })).toBe('Ran')
    expect(word({ ...base, status: 'failed' })).toBe('Failed')
    expect(word({ ...base, status: 'aborted' })).toBe('Aborted')
    expect(word({ mode: 'push', status: 'staged' })).toBe('Staged')
    expect(word({ ...base, status: 'weird' })).toBe('Unknown')
  })

  it('a run that ended before any step ran Did not run, whatever its status says', () => {
    expect(word({ mode: 'pull', status: 'failed', delivered_at: null, output: '' })).toBe('Did not run')
    expect(word({ ...base, status: 'complete', tc_verdict_detail: { source: 'complete', reason: 'not_executed' } }))
      .toBe('Did not run')
  })

  it('carries a StatusIcon tone with every word', () => {
    for (const s of Object.values(STEP_STATES)) {
      expect(Object.keys(STATUS)).toContain(s.status)
      expect(s.word).not.toMatch(/detect|miss|seen/i)
    }
  })
})

describe('reviewState', () => {
  const done = { ...base, status: 'complete' }
  it('says Reviewed only for a run SimCore scored from a tenant read', () => {
    expect(review({ ...done, tc_verdict_detail: { source: 'reconcile:xsiam:lab' } })).toBe('Reviewed')
    expect(review({ ...done, tc_verdict_detail: { source: 'complete' } })).toBe('Not reviewed')
    expect(review({ ...done, tc_verdict_detail: { source: 'complete' } }, { reading: true })).toBe('Reviewing…')
  })

  it('has nothing to say while a run is live or when nothing ran', () => {
    expect(review({ ...base, status: 'running' })).toBeNull()
    expect(review({ mode: 'pull', status: 'failed', delivered_at: null, output: '' })).toBeNull()
  })
})
