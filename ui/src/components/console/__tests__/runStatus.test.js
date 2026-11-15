/**
 * runStatus — the predicates every surface reads a run's state through.
 *
 * Pins the one distinction this file exists for: `staged` is SETTLED (a push
 * bundle never progresses on this SimCore, so nothing should keep polling it)
 * but it is not TERMINAL (nothing ran here, so it can never be "the last
 * completed run") and it is not UNPROVEN (nothing failed).
 */
import { describe, it, expect } from 'vitest'
import { isRunComplete, isRunTerminal, isRunSettled, isRunUnproven } from '../runStatus.js'

describe('isRunSettled', () => {
  it('is terminal OR staged — a push bundle never progresses on this SimCore', () => {
    for (const s of ['complete', 'completed', 'failed', 'aborted', 'staged', 'STAGED']) expect(isRunSettled(s)).toBe(true)
    for (const s of ['pending', 'running', '', null, undefined]) expect(isRunSettled(s)).toBe(false)
  })
  it('keeps staged OUT of terminal/complete — "last completed run" must never be a bundle nobody ran', () => {
    expect(isRunTerminal('staged')).toBe(false)
    expect(isRunComplete('staged')).toBe(false)
  })
  it('does not call a staged run unproven — nothing failed, nothing ran here', () => {
    expect(isRunUnproven('staged')).toBe(false)
  })
})
