// @vitest-environment jsdom
import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import {
  tourSeen, markTourSeen, hintUsed, markHintUsed, resetOnboarding,
} from '../onboardingState.js'

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key:        (i)    => Array.from(m.keys())[i] ?? null,
    getItem:    (k)    => (m.has(k) ? m.get(k) : null),
    setItem:    (k, v) => { m.set(k, String(v)) },
    removeItem: (k)    => { m.delete(k) },
    clear:      ()     => { m.clear() },
  }
}

beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => { window.localStorage.clear() })

describe('onboardingState', () => {
  it('reports the tour unseen on a fresh profile', () => {
    expect(tourSeen()).toBe(false)
  })

  it('reports the tour seen after markTourSeen', () => {
    markTourSeen()
    expect(tourSeen()).toBe(true)
  })

  it('writes the exact documented key', () => {
    markTourSeen()
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('tracks hints per control id independently', () => {
    markHintUsed('launch')
    expect(hintUsed('launch')).toBe(true)
    expect(hintUsed('abort')).toBe(false)
  })

  it('resetOnboarding clears tour and hints', () => {
    markTourSeen()
    markHintUsed('launch')
    resetOnboarding()
    expect(tourSeen()).toBe(false)
    expect(hintUsed('launch')).toBe(false)
  })

  it('never throws when storage is unavailable', () => {
    const original = window.localStorage
    Object.defineProperty(window, 'localStorage', {
      get() { throw new Error('SecurityError: storage blocked') },
      configurable: true,
    })
    expect(() => tourSeen()).not.toThrow()
    expect(tourSeen()).toBe(true)   // fail CLOSED: never nag a user we cannot remember
    expect(() => markTourSeen()).not.toThrow()
    Object.defineProperty(window, 'localStorage', {
      value: original, writable: true, configurable: true,
    })
  })
})
