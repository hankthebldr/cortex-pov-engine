// @vitest-environment jsdom
/**
 * Unit tests for the localStorage-backed pinned-scenarios hook.
 *
 * We don't render React directly — we test the hook by spinning up a tiny
 * test component via @testing-library/react's renderHook. The cap-of-12 +
 * reorder behavior is part of the contract surfaced to AppConsole, so each
 * is tested explicitly.
 *
 * Note: this version of jsdom loads `window` but not `localStorage`, so we
 * polyfill a minimal Map-backed Storage implementation before tests run.
 * Production code does `try { window.localStorage… } catch {}` so the real
 * browser path is untouched.
 */
import React from 'react'
import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import usePinnedScenarios from '../console/usePinnedScenarios.js'

// Keep React in scope so vitest treats this file as JSX-bearing.
void React

const STORAGE_KEY = 'cortexsim.pinnedScenarios.v1'

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
      value: makeStorageStub(),
      writable: true,
      configurable: true,
    })
  }
})

beforeEach(() => {
  window.localStorage.clear()
})

describe('usePinnedScenarios', () => {
  it('starts empty when storage is clear', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    expect(result.current.pinnedIds).toEqual([])
    expect(result.current.isPinned('SIM-MP-004')).toBe(false)
  })

  it('pins a scenario and persists to localStorage', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => result.current.pin('SIM-MP-004'))
    expect(result.current.pinnedIds).toEqual(['SIM-MP-004'])
    expect(result.current.isPinned('SIM-MP-004')).toBe(true)
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY))).toEqual(['SIM-MP-004'])
  })

  it('prepends the newest pin (most-recent-first ordering)', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => result.current.pin('A'))
    act(() => result.current.pin('B'))
    act(() => result.current.pin('C'))
    expect(result.current.pinnedIds).toEqual(['C', 'B', 'A'])
  })

  it('does not duplicate when the same id is pinned twice', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => result.current.pin('SIM-MP-004'))
    act(() => result.current.pin('SIM-MP-004'))
    expect(result.current.pinnedIds).toEqual(['SIM-MP-004'])
  })

  it('toggle adds when missing and removes when present', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => result.current.toggle('A'))
    expect(result.current.isPinned('A')).toBe(true)
    act(() => result.current.toggle('A'))
    expect(result.current.isPinned('A')).toBe(false)
  })

  it('unpin removes an entry; missing entries are no-ops', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => result.current.pin('A'))
    act(() => result.current.unpin('A'))
    expect(result.current.pinnedIds).toEqual([])
    // No throw on unpinning something that was never pinned.
    act(() => result.current.unpin('never-existed'))
    expect(result.current.pinnedIds).toEqual([])
  })

  it('caps the pin list at MAX_PINNED (12)', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => {
      for (let i = 0; i < 15; i++) result.current.pin(`SIM-${i}`)
    })
    expect(result.current.pinnedIds.length).toBe(12)
    // The most recent 12 should be retained (SIM-14 first, SIM-3 last).
    expect(result.current.pinnedIds[0]).toBe('SIM-14')
  })

  it('clear empties the list', () => {
    const { result } = renderHook(() => usePinnedScenarios())
    act(() => result.current.pin('A'))
    act(() => result.current.pin('B'))
    act(() => result.current.clear())
    expect(result.current.pinnedIds).toEqual([])
  })

  it('hydrates from localStorage on initial mount', () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(['SIM-MP-001', 'SIM-EDR-003']))
    const { result } = renderHook(() => usePinnedScenarios())
    expect(result.current.pinnedIds).toEqual(['SIM-MP-001', 'SIM-EDR-003'])
  })

  it('survives a malformed localStorage payload', () => {
    window.localStorage.setItem(STORAGE_KEY, 'not-valid-json{{')
    const { result } = renderHook(() => usePinnedScenarios())
    expect(result.current.pinnedIds).toEqual([])
  })
})
