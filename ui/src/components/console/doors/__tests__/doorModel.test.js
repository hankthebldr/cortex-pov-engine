// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { chipFor, filterRunnable, isRunnable } from '../doorModel.js'
import useScenarioFilterModule from '../../useScenarioFilter.js'

const r = (over = {}) => ({
  scenario_id: 'SIM-X-001', state: 'needs_doors', scorable: 0, total: 4, underived: 0,
  route_blocked: 0, unevaluated: 0, not_open: [], agent_scoped_evaluated: true, ...over,
})

describe('chipFor', () => {
  it('chip_ready', () => {
    expect(chipFor(r({ state: 'ready', scorable: 4 })).label).toBe('READY')
  })

  it('chip_partial_counts', () => {
    expect(chipFor(r({ state: 'partial', scorable: 3, total: 5 })).label).toBe('PARTIAL 3/5')
  })

  const rows = [
    { key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', state: 'closed', rung: 'verified' },
    { key: 'ngfw_traffic', door_id: 'ngfw_traffic', label: 'NGFW traffic logs', state: 'unknown', rung: 'configured' },
    { key: 'cloud_aws', door_id: 'cloud_aws', label: 'AWS audit logs', state: 'unknown', rung: 'authored' },
  ]
  const notOpenSet = [{ door_id: 'xdr_agent', detections: 3 }, { door_id: 'ngfw_traffic', detections: 1 }]

  it('chip_needs_doors_names_closed', () => {
    const c = chipFor(r({ not_open: notOpenSet }), rows)
    expect(c.label).toBe('NEEDS DOORS')
    expect(c.title).toContain('XDR agent: closed')
    expect(c.title).toContain('NGFW traffic logs: configured')
  })

  it('chip_tooltip_uses_door_labels_not_raw_ids', () => {
    const c = chipFor(r({ not_open: notOpenSet }), rows)
    expect(c.title).not.toMatch(/xdr_agent|ngfw_traffic/)
    // No row for the door at all: the id is the only name there is.
    const bare = chipFor(r({ not_open: [{ door_id: 'saas_okta', detections: 1 }] }), rows)
    expect(bare.title).toContain('saas_okta: authored')
  })

  it('chip_not_checked_when_all_unknown', () => {
    const c = chipFor(r({ not_open: [{ door_id: 'ngfw_traffic', detections: 2 }, { door_id: 'cloud_aws', detections: 1 }] }), rows)
    expect(c.label).toBe('NOT CHECKED')
    expect(c.title).toContain('NGFW traffic logs: configured')
    expect(c.title).toContain('AWS audit logs: authored')
    expect(c.title).not.toMatch(/closed/)
  })

  it('chip_not_checked_when_no_door_rows_were_fetched', () => {
    expect(chipFor(r({ not_open: notOpenSet }), null).label).toBe('NOT CHECKED')
  })

  it('chip_agent_scoped_key_is_door_id_colon_agent_id', () => {
    const scoped = [{ key: 'xdr_agent:a1', door_id: 'xdr_agent', label: 'XDR agent', state: 'closed', rung: 'verified' }]
    const c = r({ not_open: [{ door_id: 'xdr_agent', detections: 2 }] })
    expect(chipFor(c, scoped, 'a1').label).toBe('NEEDS DOORS')
    expect(chipFor(c, scoped, 'other').label).toBe('NOT CHECKED')
  })

  it('chip_partial_says_can_fire_never_scored', () => {
    const c = chipFor(r({ state: 'partial', scorable: 3, total: 5, not_open: [{ door_id: 'ngfw_traffic', detections: 2 }] }), rows)
    expect(c.title).toMatch(/^3 of 5 detections can fire/)
    expect(c.title).not.toMatch(/scored/)
  })

  it('chip_says_agent_doors_were_not_evaluated_without_a_target', () => {
    const note = 'agent-scoped doors not evaluated: no target selected'
    for (const state of ['ready', 'partial', 'needs_doors', 'not_evaluated']) {
      const off = chipFor(r({ state, scorable: 1, agent_scoped_evaluated: false }), rows)
      expect(off.title, state).toContain(note)
      const on = chipFor(r({ state, scorable: 1, agent_scoped_evaluated: true }), rows)
      expect(on.title, state).not.toContain(note)
    }
  })

  it('chip_collector_only_names_the_route_not_unchecked_doors', () => {
    const c = chipFor(r({ state: 'needs_doors', scorable: 0, total: 2, route_blocked: 2, not_open: [] }), rows)
    expect(c.label).toBe('NOT CHECKED')
    expect(c.title).toBe('2 of 2 detections are on a collector route this console has not resolved '
      + '(BIOC and IOC never fire there)')
    expect(c.title).not.toMatch(/not checked yet/)
  })

  it('chip_titles_never_claim_scoring_in_phase_1', () => {
    const shapes = [r({ state: 'ready', scorable: 4 }), r({ state: 'partial', scorable: 1, not_open: notOpenSet }),
      r({ not_open: notOpenSet }), r({ route_blocked: 2 }), r({ state: 'not_evaluated' })]
    for (const s of shapes) {
      for (const rs of [rows, null]) expect(chipFor(s, rs).title).not.toMatch(/scor|phase/i)
    }
  })

  it('chip_not_checked_when_not_evaluated', () => {
    expect(chipFor(r({ state: 'not_evaluated', scorable: 0 })).label).toBe('NOT CHECKED')
  })

  it('chip_not_checked_when_readiness_absent', () => {
    expect(chipFor(undefined).label).toBe('NOT CHECKED')
    expect(chipFor(null).label).toBe('NOT CHECKED')
  })

  it('an unrecognised state is surfaced, not read as a known one', () => {
    const c = chipFor(r({ state: 'wat' }))
    expect(c.label).toBe('NOT CHECKED')
    expect(c.tone).toBe('error')
    expect(c.title).toMatch(/unrecognised/)
  })
})

describe('Runnable here filter', () => {
  const scenarios = [{ scenario_id: 'A' }, { scenario_id: 'B' }, { scenario_id: 'C' }, { scenario_id: 'D' }]
  const map = {
    A: r({ state: 'ready' }), B: r({ state: 'partial', scorable: 1 }),
    C: r({ state: 'needs_doors' }), D: r({ state: 'not_evaluated' }),
  }

  it('filter_runnable_keeps_ready_and_partial', () => {
    expect(filterRunnable(scenarios, map, true).map((s) => s.scenario_id)).toEqual(['A', 'B'])
    expect(isRunnable(map.C)).toBe(false)
  })

  it('nothing_hidden_by_default', () => {
    expect(filterRunnable(scenarios, map, false)).toHaveLength(4)
    expect(filterRunnable(scenarios, null, false)).toHaveLength(4)
  })

  it('a scenario with no readiness entry is not runnable when the filter is on', () => {
    expect(filterRunnable(scenarios, {}, true)).toHaveLength(0)
  })
})

describe('useScenarioFilter wiring', () => {
  it('exposes a runnable flag that defaults off', async () => {
    const { renderHook, act } = await import('@testing-library/react')
    const { result } = renderHook(() => useScenarioFilterModule())
    expect(result.current.filter.runnable).toBe(false)
    expect(result.current.activeCount).toBe(0)
    const scenarios = [{ scenario_id: 'A' }, { scenario_id: 'C' }]
    const map = { A: r({ state: 'ready' }), C: r({ state: 'needs_doors' }) }
    expect(result.current.applyTo(scenarios, map)).toHaveLength(2)
    act(() => result.current.setRunnable(true))
    expect(result.current.activeCount).toBe(1)
    expect(result.current.applyTo(scenarios, map).map((s) => s.scenario_id)).toEqual(['A'])
  })
})
