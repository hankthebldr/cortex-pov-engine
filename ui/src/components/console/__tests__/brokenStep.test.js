/**
 * The causes a Broken step's sheet lists, read from the host's record of the
 * step, in the order a DC should check them, each with the place that fixes it.
 */
import { describe, it, expect } from 'vitest'
import { brokenStepCauses } from '../brokenStep.js'

const ctx = { runId: 'run-1', scenarioId: 'SIM-EDR-001' }
const ids = (row) => brokenStepCauses(row, ctx).map((c) => c.id)

describe('brokenStepCauses', () => {
  it('a plain non-zero exit gets only its output, which says why', () => {
    const causes = brokenStepCauses({ status: 'error', rc: 2, termination: 'exited' }, ctx)
    expect(causes.map((c) => c.id)).toEqual(['output'])
    expect(causes[0].detail).toMatch(/exited 2/)
    expect(causes[0].place.to).toEqual({ destination: 'runs', params: { run: 'run-1', tab: 'live' } })
  })

  it('an agent that went quiet comes first, then what the command reported', () => {
    expect(ids({ status: 'severed', rc: 127, termination: 'beacon_lost' }))
      .toEqual(['severed', 'not_found', 'output'])
  })

  it('a step killed by a signal POVengine did not send points at Results, not at a fault', () => {
    const [killed] = brokenStepCauses({ status: 'error', rc: 137, signal: 9, termination: 'signaled' }, ctx)
    expect(killed.id).toBe('killed')
    expect(killed.title).toMatch(/signal 9/)
    expect(killed.detail).toMatch(/host half of a prevention/)
    expect(killed.place.to).toEqual({ destination: 'proof', params: { run: 'run-1' } })
  })

  it('an abort is the abort, not a kill', () => {
    expect(ids({ status: 'error', rc: 130, signal: 15, termination: 'aborted' })).toEqual(['aborted', 'output'])
  })

  it('a timeout opens the chain to change the step', () => {
    const [t] = brokenStepCauses({ status: 'error', rc: 124, termination: 'timeout' }, ctx)
    expect(t.id).toBe('timeout')
    expect(t.place.to).toEqual({ destination: 'composer', params: { from: 'SIM-EDR-001' } })
  })

  it('the frame notes say what the host lacked, before the command-level codes', () => {
    const causes = brokenStepCauses({
      status: 'error', rc: 126, termination: 'exited',
      runtimeNote: 'python3 not on PATH', identityNote: 'www-data has no login shell',
    }, ctx)
    expect(causes.map((c) => c.id)).toEqual(['runtime', 'identity', 'not_executable', 'output'])
    expect(causes[0].detail).toBe('python3 not on PATH')
    expect(causes[1].place.to).toEqual({ destination: 'component-agents', params: { tab: 'fleet' } })
  })

  it('a missing command opens the tools the shelf has not staged', () => {
    const c = brokenStepCauses({ status: 'error', rc: 127, termination: 'exited' }, ctx).find((x) => x.id === 'not_found')
    expect(c.place.to).toEqual({ destination: 'adapters', params: { supply: 'unstaged' } })
  })

  it('every cause names a place; nothing is a dead end', () => {
    const causes = brokenStepCauses({
      status: 'severed', rc: 127, signal: 9, termination: 'signaled',
      runtimeNote: 'x', identityNote: 'y',
    }, ctx)
    for (const c of causes) {
      expect(c.place.label).toBeTruthy()
      expect(c.place.to.destination).toBeTruthy()
    }
    expect(causes[causes.length - 1].id).toBe('output')
  })
})
