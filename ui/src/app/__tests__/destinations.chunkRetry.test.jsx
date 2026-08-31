/**
 * Follow-up to the "never blank-screen" guarantee (SurfaceError.jsx /
 * apiErrors.test.jsx): what happens when a lazy DESTINATION CHUNK itself
 * fails to load — the post-redeploy case where a tab's index.html still
 * points at hashed chunk filenames the new build no longer serves (404 on
 * every destination), or a one-off network blip on a single chunk fetch.
 *
 * `SurfaceBoundary` DID already catch this (React surfaces a Suspense-thrown
 * rejected import as an ordinary render error to the nearest boundary), but
 * its "Retry" button only cleared local error state and re-rendered the
 * SAME `lazy()` object. React caches a lazy component's outcome — success
 * OR rejection — forever once resolved; re-rendering it after a rejection
 * re-throws the identical cached error without ever calling the import
 * factory again. That made the existing Retry button a dead end for this
 * specific failure mode: clicking it looked actionable but could not
 * possibly recover, and gave no indication that a full reload (not a
 * same-tab retry) is what actually fixes a stale-build 404.
 *
 * `makeLazySurface` (destinations.jsx) fixes this by keying a fresh
 * `lazy()` — and therefore a fresh `import()` call — off a retry counter,
 * and tags a rejected `factory()` so SurfaceError.jsx can render a
 * distinguishable state (chunk-load vs. an ordinary in-surface throw) with
 * two real actions: re-attempt the import, or reload the whole app.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { makeLazySurface } from '../destinations.jsx'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('makeLazySurface — a rejected lazy import is recoverable, not a dead end', () => {
  it('renders a distinguishable "out of date / reload" state, not the generic surface-threw copy', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const loader = () => Promise.reject(new Error('Failed to fetch dynamically imported module'))
    const Surface = makeLazySurface(loader, 'Coverage')

    render(<Surface />)

    const el = await screen.findByTestId('surface-error')
    expect(el.textContent).toContain('Coverage')
    expect(el.textContent.toLowerCase()).toContain('reload')
    expect(screen.getByRole('button', { name: /reload app/i })).toBeTruthy()
    // The generic ("SimCore may be restarting…") copy is for an ordinary
    // in-surface throw — showing it here would tell the operator to wait on
    // a backend that was never involved in a chunk 404.
    expect(el.textContent).not.toContain('SimCore may be restarting')
  })

  it('"Try again" re-invokes the import — a real retry, not a replay of the cached rejection', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let attempts = 0
    const loader = vi.fn(() => {
      attempts += 1
      if (attempts === 1) return Promise.reject(new Error('chunk load error'))
      return Promise.resolve({ default: () => <div data-testid="recovered">back</div> })
    })
    const Surface = makeLazySurface(loader, 'Coverage')

    render(<Surface />)
    await screen.findByTestId('surface-error')
    expect(loader).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /try again/i }))

    await waitFor(() => expect(screen.getByTestId('recovered')).toBeTruthy())
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it('"Reload app" drives a full page reload — the fix for a stale index.html that "Try again" cannot repair', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const reload = vi.fn()
    const originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, reload },
    })

    const loader = () => Promise.reject(new Error('Failed to fetch dynamically imported module'))
    const Surface = makeLazySurface(loader, 'Coverage')
    render(<Surface />)
    await screen.findByTestId('surface-error')

    fireEvent.click(screen.getByRole('button', { name: /reload app/i }))
    expect(reload).toHaveBeenCalledTimes(1)

    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
  })

  it('an ordinary render throw AFTER a successful import still gets the plain Retry copy, unchanged', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const Boom = () => { throw new Error('surface exploded') }
    const loader = () => Promise.resolve({ default: Boom })
    const Surface = makeLazySurface(loader, 'Coverage')

    render(<Surface />)

    const el = await screen.findByTestId('surface-error')
    expect(el.textContent).toContain('surface exploded')
    expect(el.textContent).toContain('SimCore may be restarting')
    expect(screen.getByRole('button', { name: /^↻ Retry$/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /reload app/i })).toBeNull()
  })
})
