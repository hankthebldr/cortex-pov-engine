import { describe, it, expect } from 'vitest'
import { preflightScenario, dataPathCheck, PF } from '../scenarioPreflight.js'

const r = (over = {}) => ({
  scenario_id: 'SIM-X-001', state: 'partial', scorable: 2, total: 5, underived: 0,
  route_blocked: 0, unevaluated: 0, not_open: [{ door_id: 'xdr_agent', detections: 3 }],
  agent_scoped_evaluated: false, ...over,
})
const SC = { scenario_id: 'SIM-X-001', steps: [] }

const ROWS = [
  { key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', state: 'closed', rung: 'verified' },
  { key: 'ngfw_traffic', door_id: 'ngfw_traffic', label: 'NGFW traffic logs', state: 'unknown', rung: 'configured' },
]
const BOTH_CLOSED = [
  { key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', state: 'closed', rung: 'verified' },
  { key: 'ngfw_traffic', door_id: 'ngfw_traffic', label: 'NGFW traffic logs', state: 'closed', rung: 'verified' },
]
const UNCHECKED = [{ door_id: 'ngfw_traffic', detections: 3 }]
const ROUTE = 'detections are on a collector route this console has not resolved (BIOC and IOC never fire there)'
const NOTHING = 'no detection here is confirmed able to fire yet'

// Every shape the check can be handed, for the sweeps below.
const SHAPES = [undefined, null, r(), r({ state: 'ready', scorable: 5, not_open: [] }),
  r({ state: 'needs_doors', scorable: 0, total: 3 }), r({ state: 'not_evaluated' }), r({ state: 'wat' }), {},
  r({ route_blocked: 5, underived: 2 }), r({ not_open: UNCHECKED }),
  r({ state: 'needs_doors', scorable: 0, total: 2, not_open: [], route_blocked: 2 })]
const allText = (c) => [c.summary, c.detail, c.remediation].join(' | ')

describe('dataPathCheck', () => {
  it('data_path_pass_when_ready', () => {
    const c = dataPathCheck(SC, r({ state: 'ready', scorable: 5, not_open: [] }), ROWS)
    expect(c.status).toBe(PF.PASS)
    expect(c.id).toBe('data-path')
    expect(c.label).toBe('Data path')
    expect(c.detail).toBe('every door this scenario needs is open')
  })

  it('data_path_warn_counts_only_closed_doors', () => {
    const c = dataPathCheck(SC, r(), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.summary).toBe('2 of 5 detections can fire')
    expect(c.detail).toBe('3 of 5 detections will not be scored: xdr_agent')
  })

  it('data_path_never_sums_a_detection_held_by_two_closed_doors', () => {
    // 3 + 2 door x detection pairs over 4 detections: a summed "5 of 4" would overcount.
    const c = dataPathCheck(SC, r({ total: 4, scorable: 0, state: 'needs_doors',
      not_open: [{ door_id: 'xdr_agent', detections: 3 }, { door_id: 'ngfw_traffic', detections: 2 }] }), BOTH_CLOSED)
    expect(c.detail).toContain('detections behind closed doors will not be scored: xdr_agent, ngfw_traffic')
    expect(c.detail).not.toMatch(/\d+ of 4 detections will not be scored/)
  })

  it('data_path_unknown_when_every_not_open_door_is_unchecked', () => {
    const c = dataPathCheck(SC, r({ not_open: UNCHECKED }), ROWS)
    expect(c.status).toBe(PF.UNKNOWN)
    expect(c.summary).toBe('doors not checked yet')
    expect(c.detail).toContain('ngfw_traffic')
    expect(c.detail).not.toMatch(/will not be scored/)
    expect(c.remediation).toBe('Door checks land with the setup steps; until then this is advice, not a finding.')
  })

  it('data_path_unchecked_doors_without_door_rows_is_unknown_not_warn', () => {
    expect(dataPathCheck(SC, r(), null).status).toBe(PF.UNKNOWN)
  })

  it('data_path_closed_and_unchecked_warns_on_closed_only', () => {
    const c = dataPathCheck(SC, r({ not_open: [...r().not_open, ...UNCHECKED] }), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toContain('3 of 5 detections will not be scored: xdr_agent')
    expect(c.detail).toContain('doors not checked yet: ngfw_traffic (configured)')
  })

  it('data_path_route_blocked_copy', () => {
    const c = dataPathCheck(SC, r({ not_open: [], route_blocked: 2 }), ROWS)
    expect(c.status).toBe(PF.WARN)
    expect(c.detail).toBe(`2 of 5 ${ROUTE}`)
    expect(c.detail).not.toMatch(/cannot raise/)
    expect(c.remediation).toMatch(/BIOC and IOC never fire/)
  })

  it('data_path_route_blocked_combines_with_closed', () => {
    const c = dataPathCheck(SC, r({ route_blocked: 1 }), ROWS)
    expect(c.detail).toBe(`3 of 5 detections will not be scored: xdr_agent; 1 of 5 ${ROUTE}`)
  })

  it('data_path_underived_and_unevaluated_are_not_judged_never_not_scored', () => {
    const c = dataPathCheck(SC, r({ underived: 1, unevaluated: 1 }), ROWS)
    expect(c.detail).toBe('3 of 5 detections will not be scored: xdr_agent; 2 not judged')
    const only = dataPathCheck(SC, r({ not_open: [], underived: 2 }), ROWS)
    expect(only.status).toBe(PF.UNKNOWN)
    expect(only.detail).toBe('2 not judged')
  })

  it('data_path_nothing_confirmed_able_to_fire', () => {
    const rbOnly = dataPathCheck(SC, r({ state: 'needs_doors', scorable: 0, total: 2, not_open: [], route_blocked: 2 }), ROWS)
    expect(rbOnly.status).toBe(PF.WARN)
    expect(rbOnly.detail).toBe(`${NOTHING}; each one is still scored as today; 2 of 2 ${ROUTE}`)

    // Behind a closed door (phase 2 only): no "still scored as today" beside "will not be scored".
    const closed = dataPathCheck(SC, r({ state: 'needs_doors', scorable: 0, total: 3 }), ROWS)
    expect(closed.status).toBe(PF.WARN)
    expect(closed.detail).toBe(`${NOTHING}; 3 of 3 detections will not be scored: xdr_agent`)
  })

  it('data_path_nothing_confirmed_guard', () => {
    const base0 = { state: 'needs_doors', scorable: 0, total: 4 }
    // underived detections: not judged, so not "nothing can fire"
    expect(dataPathCheck(SC, r({ ...base0, underived: 1 }), ROWS).detail).not.toContain(NOTHING)
    // unevaluated
    expect(dataPathCheck(SC, r({ ...base0, unevaluated: 1 }), ROWS).detail).not.toContain(NOTHING)
    // an unchecked door may still open
    const mixed = dataPathCheck(SC, r({ ...base0, not_open: [...r().not_open, ...UNCHECKED] }), ROWS)
    expect(mixed.detail).not.toContain(NOTHING)
    // all unchecked: unknown, not a warning
    expect(dataPathCheck(SC, r({ ...base0, not_open: UNCHECKED }), ROWS).status).toBe(PF.UNKNOWN)
  })

  it('data_path_unknown_without_input', () => {
    for (const input of [undefined, null]) {
      const c = dataPathCheck(SC, input)
      expect(c.status).toBe(PF.UNKNOWN)
      expect(c.detail).toBe('door readiness not reported')
    }
  })

  it('data_path_surfaces_a_fetch_error_instead_of_not_reported', () => {
    const c = dataPathCheck(SC, null, null, null, 'SimCore answered 500')
    expect(c.status).toBe(PF.UNKNOWN)
    expect(c.summary).toBe('unavailable')
    expect(c.detail).toBe('door readiness unavailable: SimCore answered 500')
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
    for (const s of SHAPES) {
      expect(dataPathCheck(SC, s, ROWS).status).not.toBe(PF.BLOCK)
      expect(dataPathCheck(SC, s, null).status).not.toBe(PF.BLOCK)
    }
  })

  it('phase_1_copy_never_claims_scoring_that_has_not_landed', () => {
    // Nothing is excluded in phase 1, so no string may say a detection "can be scored", that a
    // run "scores nothing", point at a surface that shows no door state, or name a roadmap phase.
    // ("will not be scored" stays, only for a door whose state is conclusively closed.)
    for (const s of SHAPES) {
      for (const rows of [ROWS, null]) {
        const text = allText(dataPathCheck(SC, s, rows))
        expect(text).not.toMatch(/can be scored|scores nothing|cannot raise|Data streams|phase/i)
      }
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

  it('carries a door fetch error through to the check', () => {
    const pf = preflightScenario({ scenario: SC, doorError: 'boom' })
    expect(pf.checks[5].detail).toBe('door readiness unavailable: boom')
  })
})
