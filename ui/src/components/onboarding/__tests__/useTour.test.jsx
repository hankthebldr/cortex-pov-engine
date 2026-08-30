// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useTour } from '../useTour.js'
import { markTourSeen } from '../onboardingState.js'

void React

function makeStorageStub() {
  const m = new Map()
  return {
    get length() { return m.size },
    key: (i) => Array.from(m.keys())[i] ?? null,
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
    clear: () => { m.clear() },
  }
}
beforeAll(() => {
  if (!window.localStorage) {
    Object.defineProperty(window, 'localStorage', {
      value: makeStorageStub(), writable: true, configurable: true,
    })
  }
})
beforeEach(() => {
  window.localStorage.clear()
  document.body.innerHTML = ''
})

function anchor(id) {
  const el = document.createElement('div')
  el.setAttribute('data-tour-id', id)
  document.body.appendChild(el)
  return el
}

const STOPS = [
  { id: 'a', anchor: 'anchor-a', destination: 'library', title: 'A', body: 'body a' },
  { id: 'b', anchor: 'anchor-b', destination: 'agents',  title: 'B', body: 'body b' },
  { id: 'c', anchor: 'anchor-c', destination: 'runs',    title: 'C', body: 'body c' },
]

describe('useTour', () => {
  it('does not auto-start when the tour is already seen', () => {
    markTourSeen()
    anchor('anchor-a')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.active).toBe(false)
  })

  it('SKIPS a stop whose anchor is absent rather than hanging', () => {
    anchor('anchor-a')
    // anchor-b deliberately absent
    anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.stop.id).toBe('a')
    act(() => { result.current.next() })
    expect(result.current.stop.id).toBe('c')   // skipped b, did NOT stall on it
  })

  it('exits immediately and marks seen when NO anchor exists', () => {
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('marks seen when exited part-way', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    act(() => { result.current.exit() })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('does not re-show after an exit', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const first = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    act(() => { first.result.current.exit() })
    const second = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(second.result.current.active).toBe(false)
  })

  it('navigates to each stop destination as it advances', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const onNavigate = vi.fn()
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate, autoStart: true }))
    expect(onNavigate).toHaveBeenCalledWith('library')
    act(() => { result.current.next() })
    expect(onNavigate).toHaveBeenCalledWith('agents')
  })

  it('finishing the last stop ends the tour and marks seen', () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    act(() => { result.current.next() })
    act(() => { result.current.next() })
    act(() => { result.current.next() })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('start() runs the tour even when already seen (Help CTA path)', () => {
    markTourSeen()
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: false }))
    act(() => { result.current.start() })
    expect(result.current.active).toBe(true)
    expect(result.current.stop.id).toBe('a')
  })
})
