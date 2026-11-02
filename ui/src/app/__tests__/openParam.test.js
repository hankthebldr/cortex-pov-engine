/**
 * Every surface accepts `?open=<id>` as the deep link that opens a thing.
 * They used to disagree — Library `?open=`, Composer `?from=`, TTPs `?ttp=`,
 * Runs `?run=` — so `#/composer?open=SIM-EDR-001` rendered the empty state
 * with no error. The legacy names stay as aliases: bookmarks, the e2e specs
 * and the Composer's own `setParams({ from })` keep working.
 */
import { describe, it, expect } from 'vitest'
import { openParam, uctcOpen } from '../openParam.js'

describe('openParam', () => {
  it('reads `open` first', () => {
    expect(openParam({ open: 'SIM-EDR-001', from: 'SIM-X' }, 'from')).toBe('SIM-EDR-001')
  })

  it("falls back to the surface's legacy name", () => {
    expect(openParam({ from: 'SIM-EDR-001' }, 'from')).toBe('SIM-EDR-001')
    expect(openParam({ ttp: 'TTP-2026-0004' }, 'ttp')).toBe('TTP-2026-0004')
  })

  it('is null when neither is present or both are blank', () => {
    expect(openParam({}, 'from')).toBeNull()
    expect(openParam({ open: '', from: '' }, 'from')).toBeNull()
    expect(openParam(undefined, 'from')).toBeNull()
  })
})

describe('uctcOpen — the UC / TC index addresses two id spaces', () => {
  it('routes `open` to the test case or the use case by prefix', () => {
    expect(uctcOpen({ open: 'TC-EDR-03' })).toEqual({ uc: null, tc: 'TC-EDR-03' })
    expect(uctcOpen({ open: 'UC-EDR' })).toEqual({ uc: 'UC-EDR', tc: null })
    expect(uctcOpen({ open: 'UCS-EDR-02' })).toEqual({ uc: 'UCS-EDR-02', tc: null })
  })

  it('keeps the explicit `uc` / `tc` pair as the aliases', () => {
    expect(uctcOpen({ uc: 'UC-EDR', tc: 'TC-EDR-03' })).toEqual({ uc: 'UC-EDR', tc: 'TC-EDR-03' })
    expect(uctcOpen({})).toEqual({ uc: null, tc: null })
  })
})
