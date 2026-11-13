/**
 * verdictReason — why a run verdict is `pending` with nothing scored.
 *
 * Pins: the two engine reasons get a plain label and an action sentence; an
 * unknown reason token is shown as-is, never dropped; no reason renders
 * nothing.
 */
import { describe, it, expect } from 'vitest'
import { verdictReason } from '../verdictReason.js'

describe('verdictReason', () => {
  it('names a run where nothing executed and says to re-run it', () => {
    const r = verdictReason({ reason: 'not_executed' })
    expect(r).toMatchObject({ key: 'not_executed', label: 'nothing executed', tone: 'warn' })
    expect(r.sentence).toMatch(/re-run/i)
  })

  it('names a run whose detectors are all soaking and says it resolves on its own', () => {
    const r = verdictReason({ reason: 'soak_pending' })
    expect(r).toMatchObject({ key: 'soak_pending', label: 'detectors soaking', tone: 'info' })
    expect(r.sentence).toMatch(/window/i)
  })

  it('keeps an unknown reason visible as its raw token', () => {
    expect(verdictReason({ reason: 'SOMETHING_NEW' })).toMatchObject({
      key: 'SOMETHING_NEW', label: 'SOMETHING_NEW', tone: 'neutral', sentence: null,
    })
  })

  it('returns null when the verdict carries no reason', () => {
    expect(verdictReason({ reason: null })).toBeNull()
    expect(verdictReason(null)).toBeNull()
    expect(verdictReason(undefined)).toBeNull()
  })
})
