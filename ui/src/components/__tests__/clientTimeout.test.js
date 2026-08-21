import { describe, it, expect, afterEach, vi } from 'vitest'
import { request, REQUEST_TIMEOUT_MS } from '../../api/client.js'

/**
 * `fetch` has NO default timeout.
 *
 * A SimCore that accepts the connection and never answers hangs the calling
 * surface forever — a spinner that spins for the length of a customer meeting,
 * indistinguishable from a blank screen. That is not hypothetical here: a
 * blocking tenant call inside an async handler was measured stalling
 * /api/health for 28 seconds, with every console request queued behind it.
 */

/** A fetch that never answers, but honours the abort signal like a real one. */
function hangingFetch() {
  return vi.fn((_url, init = {}) => new Promise((_resolve, reject) => {
    const signal = init.signal
    if (!signal) return // no signal attached — the bug this test exists to catch
    if (signal.aborted) { reject(abortError()); return }
    signal.addEventListener('abort', () => reject(abortError()))
  }))
}

function abortError() {
  const e = new Error('The operation was aborted.')
  e.name = 'AbortError'
  return e
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('request() timeout', () => {
  it('fails with a NAMED code instead of hanging forever', async () => {
    vi.useFakeTimers()
    globalThis.fetch = hangingFetch()

    const p = request('/api/health')
    // Attach the rejection handler before advancing so the rejection is never
    // unhandled between tick and assertion.
    const assertion = expect(p).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT' })
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 10)
    await assertion
  })

  it('the message tells the operator where to look', async () => {
    vi.useFakeTimers()
    globalThis.fetch = hangingFetch()
    const p = request('/api/scenarios').catch((e) => e)
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 10)
    const err = await p
    expect(err.message).toMatch(/did not answer \/api\/scenarios in 20s/)
    expect(err.message).toMatch(/api\/health/)
    expect(err.detail).toMatchObject({ path: '/api/scenarios', timeout_ms: REQUEST_TIMEOUT_MS })
  })

  it('does not fire on a request that answers in time', async () => {
    vi.useFakeTimers()
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    await expect(request('/api/health')).resolves.toEqual({ status: 'ok' })
  })

  it('yields to a caller-supplied signal — useShelf owns a 180s staging deadline', async () => {
    const controller = new AbortController()
    globalThis.fetch = vi.fn((_u, init) => {
      // The caller's signal must be the one that reaches fetch, unchanged.
      expect(init.signal).toBe(controller.signal)
      return Promise.resolve(new Response('{}', {
        status: 200, headers: { 'content-type': 'application/json' },
      }))
    })
    await request('/api/shelf/stage', { method: 'POST', signal: controller.signal })
    expect(globalThis.fetch).toHaveBeenCalled()
  })

  it('a genuine network failure keeps its own error, not a fake timeout', async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new TypeError('Failed to fetch')))
    await expect(request('/api/health')).rejects.toThrow('Failed to fetch')
  })
})
