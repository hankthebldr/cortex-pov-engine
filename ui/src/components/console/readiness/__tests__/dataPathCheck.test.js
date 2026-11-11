import { describe, it, expect } from 'vitest'
import { preflightScenario, dataPathCheck, PF } from '../scenarioPreflight.js'

const r = (over = {}) => ({
  scenario_id: 'SIM-X-001', state: 'partial', scorable: 2, total: 5, underived: 0,
  route_blocked: 0, unevaluated: 0, closed: [{ door_id: 'xdr_agent', detections: 3 }],
  agent_scoped_evaluated: false, ...over,
})
const SC = { scenario_id: 'SIM-X-001', steps: [] }

const ROWS = [
  { key: 'xdr_agent', door_id: 'xdr_agent', state: 'closed', rung: 'verified' },
  { key: 'ngfw_traffic', door_id: 'ngfw_traffic', state: 'unknown', rung: 'configured' },
]
const UNCHECKED = [{ door_id: 'ngfw_traffic', detections: 3 }]

describe('dataPathCheck', () => {
  it('data_path_pass_when_ready', () => {
    const c = dataPathCheck(SC, r({ state: 'ready', scorable: 5, closed: [] }), ROWS)
    expect(c.status).toBe(PF.PASS)
    expect(c.id).toBe('data-path')
    expect(c.label).toBe('Data path')
    expect(c.detail).toBe('every door this scenario needs is open')
  })

  it('data_path_warn_counts_only_closed_doors', () => {
    const c = dataPathCheck(SC, r(), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toBe('3 of 5 detections will not be scored: xdr_agent')
  })

  it('data_path_unknown_when_every_not_open_door_is_unchecked', () => {
    const c = dataPathCheck(SC, r({ closed: UNCHECKED }), ROWS)
    expect(c.status).toBe(PF.UNKNOWN)
    expect(c.summary).toBe('doors not checked yet')
    expect(c.detail).toContain('ngfw_traffic')
    expect(c.detail).not.toMatch(/will not be scored/)
  })

  it('data_path_unchecked_doors_without_door_rows_is_unknown_not_warn', () => {
    expect(dataPathCheck(SC, r(), null).status).toBe(PF.UNKNOWN)
  })

  it('data_path_closed_and_unchecked_warns_on_closed_only', () => {
    const c = dataPathCheck(SC, r({ closed: [...r().closed, ...UNCHECKED] }), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toContain('3 of 5 detections will not be scored: xdr_agent')
    expect(c.detail).toContain('doors not checked yet: ngfw_traffic (configured)')
  })

  it('data_path_route_blocked_copy', () => {
    const c = dataPathCheck(SC, r({ closed: [], route_blocked: 2 }), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toBe('2 of 5 detections are on a collector route that cannot raise them')
  })

  it('data_path_route_blocked_combines_with_closed', () => {
    const c = dataPathCheck(SC, r({ route_blocked: 1 }), ROWS)
    expect(c.detail).toBe('3 of 5 detections will not be scored: xdr_agent; '
      + '1 of 5 detections are on a collector route that cannot raise them')
  })

  it('data_path_underived_and_unevaluated_are_not_judged_never_not_scored', () => {
    const c = dataPathCheck(SC, r({ underived: 1, unevaluated: 1 }), ROWS)
    expect(c.detail).toBe('3 of 5 detections will not be scored: xdr_agent; 2 not judged')
    const only = dataPathCheck(SC, r({ closed: [], underived: 2 }), ROWS)
    expect(only.status).toBe(PF.UNKNOWN)
    expect(only.detail).toBe('2 not judged')
  })

  it('data_path_scores_nothing', () => {
    const c = dataPathCheck(SC, r({ state: 'needs_doors', scorable: 0, total: 4 }), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toBe('this run scores nothing')
    const rbOnly = dataPathCheck(SC, r({ state: 'needs_doors', scorable: 0, total: 2, closed: [], route_blocked: 2 }), ROWS)
    expect(rbOnly.detail).toBe('this run scores nothing')
  })

  it('data_path_scores_nothing_guard', () => {
    const base0 = { state: 'needs_doors', scorable: 0, total: 4 }
    // underived detections: not judged, so not "scores nothing"
    expect(dataPathCheck(SC, r({ ...base0, underived: 1 }), ROWS).detail).not.toBe('this run scores nothing')
    // unevaluated
    expect(dataPathCheck(SC, r({ ...base0, unevaluated: 1 }), ROWS).detail).not.toBe('this run scores nothing')
    // an unchecked door may still open: not "scores nothing"
    const mixed = dataPathCheck(SC, r({ ...base0, closed: [...r().closed, ...UNCHECKED] }), ROWS)
    expect(mixed.detail).not.toBe('this run scores nothing')
    // all unchecked: unknown, not a warning
    expect(dataPathCheck(SC, r({ ...base0, closed: UNCHECKED }), ROWS).status).toBe(PF.UNKNOWN)
  })

  it('data_path_unknown_without_input', () => {
    for (const input of [undefined, null]) {
      const c = dataPathCheck(SC, input)
      expect(c.status).toBe(PF.UNKNOWN)
      expect(c.detail).toBe('door readiness not reported')
    }
  })

  it('data_path_not_evaluated_reuses_chip_copy', () => {
    const c = dataPathCheck(SC, r({ state: 'not_evaluated' }))
    expect(c.status).toBe(PF.UNKNOWN)
    expect(c.detail).toBe('no detection could be judged')
  })

  it('an unrecognised state is unknown, never a pass', () => {
    const c = dataPathCheck(SC, r({ state: 'wat' }))
    expect(c.status).toBe(PF.UNKNOWN)
    expect(c.detail).toMatch(/unrecognised/)
  })

  it('never_blocks', () => {
    const shapes = [undefined, null, r(), r({ state: 'ready' }), r({ state: 'needs_doors', scorable: 0 }),
      r({ state: 'not_evaluated' }), r({ state: 'wat' }), {}, r({ route_blocked: 5, underived: 2 })]
    for (const s of shapes) {
      expect(dataPathCheck(SC, s, ROWS).status).not.toBe(PF.BLOCK)
      expect(dataPathCheck(SC, s, null).status).not.toBe(PF.BLOCK)
    }
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
