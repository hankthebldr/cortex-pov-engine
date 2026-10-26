/** resultStatus — the one place a Result row becomes a rendered status (C1). */
import { describe, it, expect } from 'vitest'
import { resultStatus, isHitStatus, OUTCOME_META } from '../../outcomes.js'

describe('resultStatus', () => {
  it('prefers the server outcome over the legacy observed flag', () => {
    expect(resultStatus({ outcome: 'not_reached', observed: false })).toBe('not_reached')
    expect(resultStatus({ outcome: 'not_executed', observed: false })).toBe('not_executed')
    expect(resultStatus({ outcome: 'pending', outcome_reason: 'DEDUP_WINDOW', observed: false })).toBe('pending')
    expect(resultStatus({ outcome: 'prevented', observed: true })).toBe('prevented')
    expect(resultStatus({ outcome: 'missed', observed: false })).toBe('missed')
  })

  it('a detected outcome with DETECTED_NOT_PREVENTED is its own fail status, never ✅', () => {
    const s = resultStatus({ outcome: 'detected', outcome_reason: 'DETECTED_NOT_PREVENTED', observed: true })
    expect(s).toBe('detected_not_prevented')
    expect(OUTCOME_META[s].glyph).not.toBe('✅')
    expect(OUTCOME_META[s].label).toBe('Detected, not prevented')
  })

  it('falls back to the legacy observed mapping only when outcome is absent', () => {
    expect(resultStatus({ outcome: null, observed: true })).toBe('detected')
    expect(resultStatus({ outcome: null, observed: false })).toBe('missed')
    expect(resultStatus({ observed: null })).toBe('pending')
    expect(resultStatus({ outcome: null }, 'pending')).toBe('pending')
  })

  it('raises on an unrecognised outcome rather than reading it as benign', () => {
    expect(() => resultStatus({ outcome: 'blocked' })).toThrow(/unrecognised result outcome/)
    expect(() => resultStatus({ outcome: 'detected_not_prevented' })).toThrow()
  })

  it('counts every row Cortex saw as a hit', () => {
    expect(['prevented', 'detected', 'detected_not_prevented'].every(isHitStatus)).toBe(true)
    expect(['missed', 'pending', 'not_reached', 'not_executed'].some(isHitStatus)).toBe(false)
  })
})
