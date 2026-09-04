/**
 * Vitest global setup.
 *
 * Loads jest-dom matchers, polyfills the bits of `window` the UI touches
 * (matchMedia, scrollTo, ResizeObserver — used by various components), and
 * resets every fetch mock between tests so cross-pollination can't happen.
 */
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

beforeEach(() => {
  // Most components use fetch via api/client.js → window.location.origin.
  // Default to a stub that returns 404 so we *notice* if a test forgets to mock.
  globalThis.fetch = vi.fn(() =>
    Promise.resolve(
      new Response(JSON.stringify({ error: 'unmocked fetch' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  )
})

if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }))
  }

  if (!window.ResizeObserver) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }

  if (!window.scrollTo) {
    window.scrollTo = vi.fn()
  }

  // Web Storage. Absent in this jsdom configuration, and its absence was costing
  // 43 failures across 3 files: every suite whose beforeEach ran
  // `window.localStorage.clear()` threw before its first assertion, so the whole
  // file reported red for a reason unrelated to the component under test.
  //
  // Both are polyfilled, not just localStorage: the blast-radius consent gate
  // (SafetyBanner.jsx) is sessionStorage-backed, and a missing sessionStorage
  // would silently push it down its catch branch — where it always renders
  // UNacknowledged. The test would then pass while proving nothing about the
  // acknowledged path.
  const memoryStorage = () => {
    let store = new Map()
    return {
      get length() { return store.size },
      key: (i) => Array.from(store.keys())[i] ?? null,
      getItem: (k) => (store.has(String(k)) ? store.get(String(k)) : null),
      setItem: (k, v) => { store.set(String(k), String(v)) },
      removeItem: (k) => { store.delete(String(k)) },
      clear: () => { store = new Map() },
    }
  }
  for (const name of ['localStorage', 'sessionStorage']) {
    if (!window[name]) {
      Object.defineProperty(window, name, {
        value: memoryStorage(), configurable: true, writable: true,
      })
    }
  }
}
