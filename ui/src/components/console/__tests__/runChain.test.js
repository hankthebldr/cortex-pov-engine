/**
 * runChain.liveStepStates — what each card on Run detail's live chain says
 * (design v26 §4.6).
 *
 * Before the run is read back a step is only "Ran" or "Did not run": the host
 * recorded that it ran, and nothing more is known. After the read back a card
 * can say Detected, Broken or Not seen, from the run's Result rows.
 */
import { describe, it, expect } from 'vitest'
import { liveStepStates, laneDestination } from '../runChain.js'

const rows = [
  { id: 'step-01', status: 'ok' },
  { id: 'step-02', status: 'error' },
  { id: 'step-03', status: 'ok' },
  { id: 'step-04', status: 'not_reached' },
  { id: 'step-05', status: 'ok' },
]
const results = [
  { step_id: 'step-01', outcome: 'detected' },
  { step_id: 'step-01', outcome: 'pending' },
  { step_id: 'step-02', outcome: 'pending' },
  { step_id: 'step-03', outcome: 'missed' },
  { step_id: 'step-04', outcome: 'not_executed' },
]

describe('liveStepStates', () => {
  it('before a read back, says only Ran or Did not run', () => {
    expect(liveStepStates(rows, 'unread', results)).toEqual({
      'step-01': 'ran', 'step-02': 'ran', 'step-03': 'ran', 'step-04': 'did_not_run', 'step-05': 'ran',
    })
  })

  it('while running, a step with no record yet is left Waiting', () => {
    const live = [{ id: 'step-01', status: 'ok' }, { id: 'step-02', status: 'running' }, { id: 'step-03', status: 'queued' }]
    expect(liveStepStates(live, 'running', [])).toEqual({ 'step-01': 'ran' })
  })

  it('after a read back, a hit is Detected, a failed step Broken, an unmatched expectation Not seen', () => {
    const s = liveStepStates(rows, 'ready', results)
    expect(s['step-01']).toBe('detected')
    expect(s['step-02']).toBe('broken')
    expect(s['step-03']).toBe('not_seen')
    expect(s['step-04']).toBe('did_not_run')
  })

  it('a step that expected nothing stays Ran after the read back — never Not seen', () => {
    expect(liveStepStates(rows, 'ready', results)['step-05']).toBe('ran')
  })

  it('reads the legacy observed flag as a hit', () => {
    expect(liveStepStates([{ id: 'a', status: 'ok' }], 'ready', [{ step_id: 'a', observed: true }])).toEqual({ a: 'detected' })
  })
})

describe('laneDestination', () => {
  it('opens the component behind each signal path', () => {
    expect(laneDestination('ENDPOINT')).toBe('agents')
    expect(laneDestination('WIRE')).toBe('firewall')
    expect(laneDestination('CLOUD')).toBe('tenants')
    expect(laneDestination('ENG')).toBe('tenants')
    expect(laneDestination('COLLECTOR')).toBe('start')
  })
})
