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
