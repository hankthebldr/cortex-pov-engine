// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
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
afterEach(() => {
  vi.restoreAllMocks()
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

// Small enough to keep the suite fast while still proving the wait actually
// happened (a stop that mounts within this window must be caught).
const FAST_TIMEOUT_MS = 40

describe('useTour', () => {
  it('does not auto-start when the tour is already seen', () => {
    markTourSeen()
    anchor('anchor-a')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(result.current.active).toBe(false)
  })

  it('SKIPS a stop whose anchor is absent rather than hanging', async () => {
    anchor('anchor-a')
    // anchor-b deliberately absent — and never appears
    anchor('anchor-c')
    const { result } = renderHook(() => useTour({
      stops: STOPS, onNavigate: vi.fn(), autoStart: true, anchorTimeoutMs: FAST_TIMEOUT_MS,
    }))
    await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
    expect(result.current.stop.id).toBe('a')
    await act(async () => { await result.current.next() })
    expect(result.current.stop.id).toBe('c')   // skipped b, did NOT stall on it
  })

  it('exits immediately and marks seen when NO anchor exists', async () => {
    const { result } = renderHook(() => useTour({
      stops: STOPS, onNavigate: vi.fn(), autoStart: true, anchorTimeoutMs: FAST_TIMEOUT_MS,
    }))
    // No anchor ever mounts, so this must wait out all three stops' bounded
    // timeouts before giving up — not resolve on the same short beat the
    // other fixtures use for an anchor that IS already present.
    await act(async () => { await new Promise((r) => setTimeout(r, FAST_TIMEOUT_MS * 3 + 20)) })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('marks seen when exited part-way', async () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
    act(() => { result.current.exit() })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('does not re-show after an exit', async () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const first = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
    act(() => { first.result.current.exit() })
    const second = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    expect(second.result.current.active).toBe(false)
  })

  it('navigates to each stop destination as it advances', async () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const onNavigate = vi.fn()
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate, autoStart: true }))
    await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
    expect(onNavigate).toHaveBeenCalledWith('library')
    await act(async () => { await result.current.next() })
    expect(onNavigate).toHaveBeenCalledWith('agents')
  })

  it('finishing the last stop ends the tour and marks seen', async () => {
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: true }))
    await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
    await act(async () => { await result.current.next() })
    await act(async () => { await result.current.next() })
    await act(async () => { await result.current.next() })
    expect(result.current.active).toBe(false)
    expect(window.localStorage.getItem('cortexsim.onboarding.tourSeenV1')).toBe('true')
  })

  it('start() runs the tour even when already seen (Help CTA path)', async () => {
    markTourSeen()
    anchor('anchor-a'); anchor('anchor-b'); anchor('anchor-c')
    const { result } = renderHook(() => useTour({ stops: STOPS, onNavigate: vi.fn(), autoStart: false }))
    await act(async () => { await result.current.start() })
    expect(result.current.active).toBe(true)
    expect(result.current.stop.id).toBe('a')
  })

  // ── C2 — cold-chunk-cache race: a lazy anchor must be AWAITED, not
  // synchronously probed-and-skipped. jsdom has no real lazy loading, so a
  // slow chunk is simulated by appending the anchor after a short delay. ──
  describe('C2 — anchor wait window (cold chunk cache)', () => {
    it('lands on a stop whose anchor mounts mid-wait, rather than skipping it', async () => {
      anchor('anchor-a')
      // anchor-b is NOT present yet — simulate its lazy chunk landing 15ms
      // into the wait window, well inside the 40ms timeout.
      anchor('anchor-c')
      const { result } = renderHook(() => useTour({
        stops: STOPS, onNavigate: vi.fn(), autoStart: true, anchorTimeoutMs: FAST_TIMEOUT_MS,
      }))
      await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
      expect(result.current.stop.id).toBe('a')

      const nextPromise = act(async () => {
        setTimeout(() => anchor('anchor-b'), 15)
        await result.current.next()
      })
      await nextPromise
      expect(result.current.stop.id).toBe('b') // NOT skipped to 'c'
    })

    it('still skips after the bounded wait when the anchor never mounts, and logs at debug', async () => {
      anchor('anchor-a'); anchor('anchor-c')
      // anchor-b never appears at all.
      const debugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
      const { result } = renderHook(() => useTour({
        stops: STOPS, onNavigate: vi.fn(), autoStart: true, anchorTimeoutMs: FAST_TIMEOUT_MS,
      }))
      await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
      await act(async () => { await result.current.next() })
      expect(result.current.stop.id).toBe('c') // skipped, not hung
      expect(debugSpy).toHaveBeenCalled()
      expect(debugSpy.mock.calls.some((args) => String(args[0]).includes('anchor-b'))).toBe(true)
    })
  })

  // ── I3 — Back must be able to re-mount a destination, the same way
  // forward progress does. Simulated by having the mock onNavigate append
  // the target anchor when called with a matching destination (standing in
  // for a lazy surface mounting once navigated to). ──
  describe('I3 — prev() re-mounts a destination it navigates back into', () => {
    it('walks 5→4→3, not 5→3→1: Back reaches a stop anchored in a currently-unmounted destination', async () => {
      // Only the ALWAYS-mounted nav anchors exist up front (mirrors
      // DestinationNav being static while Library/Agents surfaces are lazy).
      anchor('anchor-a')
      anchor('anchor-c')
      const onNavigate = vi.fn((destId) => {
        // Stand-in for a lazy surface mounting once its destination is
        // navigated to — exactly what real navigation does for
        // scenario-card-first / agent-enroll.
        if (destId === 'agents' && !document.querySelector('[data-tour-id="anchor-b"]')) {
          anchor('anchor-b')
        }
      })
      const { result } = renderHook(() => useTour({
        stops: STOPS, onNavigate, autoStart: true, anchorTimeoutMs: FAST_TIMEOUT_MS,
      }))
      await act(async () => { await new Promise((r) => setTimeout(r, 5)) })
      await act(async () => { await result.current.next() }) // -> b (mounts via onNavigate)
      await act(async () => { await result.current.next() }) // -> c
      expect(result.current.stop.id).toBe('c')

      // Simulate stop b's anchor having gone back out of the DOM the way a
      // lazy surface unmounts when the user is no longer on its destination.
      document.querySelector('[data-tour-id="anchor-b"]')?.remove()

      await act(async () => { await result.current.prev() })
      expect(result.current.stop.id).toBe('b') // NOT skipped straight to 'a'
    })
  })
})
