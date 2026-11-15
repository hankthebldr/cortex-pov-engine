/**
 * liveOutput — what a running step has printed so far: the tail the run
 * record carried when the page opened, then the chunks the run's stream
 * delivers. A chunk already shown is skipped by its `seq`, and a step's stored
 * frame ends its live text (the stored text replaces it; never both).
 */
import { describe, it, expect } from 'vitest'
import { liveOutput } from '../liveOutput.js'

const chunk = (stepId, seq, message) => ({
  id: `c-${stepId}-${seq}`, level: 'info', phase: 'output', live: true, stepId, seq, message,
})
const stored = (stepId, message) => ({
  id: `s-${stepId}`, level: 'info', phase: 'output', live: false, stepId, seq: null, message,
})
const start = (stepId, command) => ({
  id: `st-${stepId}`, level: 'step', phase: 'start', live: false, stepId, command, message: `Running: ${command}`,
})
const FRAME = '=== STEP 1/2 · step-01 · T1003.008 · identity=root ===\nstored line\n--- exit_code=0 duration=1.2s ---\n'

describe('liveOutput', () => {
  it('starts from the tail the record carried', () => {
    const live = liveOutput({ tail: [{ step_id: 'step-01', text: 'one\ntwo\n', seq: 2, truncated: false }] })
    expect(live.text.get('step-01')).toBe('one\ntwo\n')
  })

  it('appends stream chunks, skipping any the tail already holds', () => {
    const live = liveOutput({
      tail: [{ step_id: 'step-01', text: 'one\ntwo\n', seq: 2, truncated: false }],
      events: [chunk('step-01', 1, 'one\n'), chunk('step-01', 2, 'two\n'), chunk('step-01', 3, 'three\n')],
    })
    expect(live.text.get('step-01')).toBe('one\ntwo\nthree\n')
  })

  it('shows a chunk once when the stream delivers it twice', () => {
    const live = liveOutput({ events: [chunk('step-01', 1, 'one\n'), chunk('step-01', 1, 'one\n'), chunk('step-01', 2, 'two\n')] })
    expect(live.text.get('step-01')).toBe('one\ntwo\n')
  })

  it('keeps each step its own text', () => {
    const live = liveOutput({ events: [chunk('step-01', 1, 'a\n'), chunk('step-02', 1, 'b\n')] })
    expect([...live.text.entries()]).toEqual([['step-01', 'a\n'], ['step-02', 'b\n']])
  })

  it("drops a step's live text when its stored frame arrives", () => {
    const live = liveOutput({
      tail: [{ step_id: 'step-01', text: 'one\n', seq: 1, truncated: false }],
      events: [chunk('step-01', 2, 'two\n'), stored('step-01', FRAME), chunk('step-01', 3, 'late\n')],
    })
    expect(live.text.has('step-01')).toBe(false)
  })

  it('hands back a stored frame the record has not caught up with, and only that', () => {
    const events = [stored('step-01', FRAME)]
    expect(liveOutput({ events, output: '' }).stored).toBe(FRAME)
    expect(liveOutput({ events, output: FRAME }).stored).toBe('')
  })

  it('a stored frame that does not end the step leaves its live text alone', () => {
    const live = liveOutput({
      events: [chunk('step-01', 1, 'one\n'), stored('step-01', '!! ANCHOR NOT HONOURED: no parent process\n')],
    })
    expect(live.text.get('step-01')).toBe('one\n')
  })

  it('reads a step start off the stream, with its command', () => {
    const live = liveOutput({ events: [start('step-02', 'gcore -o /tmp/dump 1')] })
    expect(live.starts).toEqual([{ step_id: 'step-02', command: 'gcore -o /tmp/dump 1' }])
  })

  it('with nothing to read is empty', () => {
    const live = liveOutput({})
    expect(live.text.size).toBe(0)
    expect(live.starts).toEqual([])
    expect(live.stored).toBe('')
  })
})
