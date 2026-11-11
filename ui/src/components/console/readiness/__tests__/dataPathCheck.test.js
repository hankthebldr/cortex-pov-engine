import { describe, it, expect } from 'vitest'
import { preflightScenario, dataPathCheck, PF } from '../scenarioPreflight.js'

const r = (over = {}) => ({
  scenario_id: 'SIM-X-001', state: 'partial', scorable: 2, total: 5, underived: 0,
  route_blocked: 0, unevaluated: 0, closed: [{ door_id: 'xdr_agent', detections: 3 }],
  agent_scoped_evaluated: false, ...over,
})
const SC = { scenario_id: 'SIM-X-001', steps: [] }

describe('dataPathCheck', () => {
  it('data_path_pass_when_ready', () => {
    const c = dataPathCheck(SC, r({ state: 'ready', scorable: 5, closed: [] }))
    expect(c.status).toBe(PF.PASS)
    expect(c.id).toBe('data-path')
    expect(c.label).toBe('Data path')
    expect(c.detail).toBe('every door this scenario needs is open')
  })

  it('data_path_warn_counts', () => {
    const c = dataPathCheck(SC, r())
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toBe('3 of 5 detections will not be scored: xdr_agent')
  })

  it('data_path_scores_nothing', () => {
    const c = dataPathCheck(SC, r({ state: 'needs_doors', scorable: 0, total: 4 }))
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toBe('this run scores nothing')
  })

  it('data_path_unknown_without_input', () => {
    for (const input of [undefined, null]) {
      const c = dataPathCheck(SC, input)
      expect(c.status).toBe(PF.UNKNOWN)
      expect(c.status).not.toBe(PF.PASS)
      expect(c.detail).toBe('door readiness not reported')
    }
    expect(dataPathCheck(SC, r({ state: 'not_evaluated' })).status).toBe(PF.UNKNOWN)
  })

  it('an unrecognised state is unknown, never a pass', () => {
    const c = dataPathCheck(SC, r({ state: 'wat' }))
    expect(c.status).toBe(PF.UNKNOWN)
    expect(c.detail).toMatch(/unrecognised/)
  })

  it('never_blocks', () => {
    const shapes = [undefined, null, r(), r({ state: 'ready' }), r({ state: 'needs_doors', scorable: 0 }),
      r({ state: 'not_evaluated' }), r({ state: 'wat' }), {}]
    for (const s of shapes) expect(dataPathCheck(SC, s).status).not.toBe(PF.BLOCK)
  })
})

describe('preflightScenario carries the sixth check', () => {
  it('appends data-path last and degrades the verdict without blocking it', () => {
    const pf = preflightScenario({ scenario: SC, doorReadiness: r() })
    expect(pf.checks).toHaveLength(6)
    expect(pf.checks[5].id).toBe('data-path')
    expect(pf.blocking.map((c) => c.id)).not.toContain('data-path')
  })

  it('absent doorReadiness is unknown', () => {
    const pf = preflightScenario({ scenario: SC })
    expect(pf.checks[5].status).toBe(PF.UNKNOWN)
  })
})
