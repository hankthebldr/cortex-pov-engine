// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { chipFor, filterRunnable, isRunnable } from '../doorModel.js'
import useScenarioFilterModule from '../../useScenarioFilter.js'

const r = (over = {}) => ({
  scenario_id: 'SIM-X-001', state: 'needs_doors', scorable: 0, total: 4, underived: 0,
  route_blocked: 0, unevaluated: 0, closed: [], agent_scoped_evaluated: false, ...over,
})

describe('chipFor', () => {
  it('chip_ready', () => {
    expect(chipFor(r({ state: 'ready', scorable: 4 })).label).toBe('READY')
  })

  it('chip_partial_counts', () => {
    expect(chipFor(r({ state: 'partial', scorable: 3, total: 5 })).label).toBe('PARTIAL 3/5')
  })

  it('chip_needs_doors_names_closed', () => {
    const c = chipFor(r({ closed: [{ door_id: 'xdr_agent', detections: 3 }, { door_id: 'ngfw', detections: 1 }] }))
    expect(c.label).toBe('NEEDS DOORS')
    expect(c.title).toContain('xdr_agent')
    expect(c.title).toContain('ngfw')
  })

  it('chip_not_checked_when_all_unknown', () => {
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
