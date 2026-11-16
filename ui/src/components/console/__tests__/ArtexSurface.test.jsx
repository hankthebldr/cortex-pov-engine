/**
 * ArtexSurface — the Offensive > ARTEX destination.
 *
 * jsdom cannot see the real framed app, so these assert the surface's own
 * branching: which gate shows for which /api/artex/status answer, and that the
 * same-origin frame appears only when ARTEX is reachable and initialized.
 */
import React from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import ArtexSurface from '../ArtexSurface.jsx'
import { DESTINATIONS, JOURNEYS, getDestination } from '../../../app/destinations.jsx'

void React

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mountWith(status) {
  installRoutes({ 'GET /api/artex/status': status })
  return render(<ArtexSurface onExit={() => {}} />)
}

describe('ArtexSurface', () => {
  it('shows a loading state, then the not-enabled gate with no frame', async () => {
    mountWith({ reachability: 'not_enabled', initialized: null, code: 'ARTEX_NOT_ENABLED', detail: 'Set CORTEXSIM_ARTEX_ORIGIN to enable ARTEX.' })
    expect(screen.getByRole('status')).toBeTruthy()
    const gate = await screen.findByTestId('artex-gate-not-enabled')
    expect(gate.textContent).toMatch(/ARTEX is not enabled/i)
    expect(gate.textContent).toMatch(/CORTEXSIM_ARTEX_ORIGIN/)
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('shows the unreachable gate with a retry control and no frame', async () => {
    mountWith({ reachability: 'unreachable', initialized: null, code: 'ARTEX_UNREACHABLE', detail: 'connection refused' })
    const gate = await screen.findByTestId('artex-gate-unreachable')
    expect(gate.textContent).toMatch(/ARTEX service unreachable/i)
    expect(gate.textContent).toMatch(/connection refused/)
    expect(screen.getByTestId('artex-retry')).toBeTruthy()
    expect(document.querySelector('iframe')).toBeNull()
  })

  it('retry re-fetches the status', async () => {
    const handler = installRoutes({ 'GET /api/artex/status': { reachability: 'unreachable', initialized: null, code: 'X', detail: 'down' } })
    render(<ArtexSurface onExit={() => {}} />)
    await screen.findByTestId('artex-gate-unreachable')
    const before = handler.mock.calls.length
    fireEvent.click(screen.getByTestId('artex-retry'))
    await waitFor(() => expect(handler.mock.calls.length).toBeGreaterThan(before))
  })

  it('shows the finish-setup gate when reachable but not initialized', async () => {
    mountWith({ reachability: 'reachable', initialized: false, code: 'ARTEX_NEEDS_SETUP', detail: 'first run' })
    const gate = await screen.findByTestId('artex-gate-needs-setup')
    expect(gate.textContent).toMatch(/Finish ARTEX first-run setup/i)
    expect(document.querySelector('iframe')).toBeNull()
    // The affordance opens ARTEX's own setup inside the frame.
    fireEvent.click(screen.getByTestId('artex-open-setup'))
    const frame = await screen.findByTestId('artex-frame')
    expect(frame.getAttribute('src')).toMatch(/\/artex\/$/)
  })

  it('frames same-origin /artex/ when reachable and initialized', async () => {
    mountWith({ reachability: 'reachable', initialized: true, code: 'OK', detail: '' })
    const frame = await screen.findByTestId('artex-frame')
    expect(frame.tagName).toBe('IFRAME')
    expect(frame.getAttribute('src')).toBe('/artex/')
    expect(frame.getAttribute('title')).toMatch(/ARTEX/)
    expect(document.querySelector('[data-testid^="artex-gate"]')).toBeNull()
  })
})

describe('artex destination registration', () => {
  it('is an immersive destination in an Offensive journey before Reference', () => {
    const d = getDestination('artex')
    expect(d.group).toBe('offensive')
    expect(d.immersive).toBe(true)
    expect(d.exitTo).toBe('runs')
    const ids = JOURNEYS.map((j) => j.id)
    expect(ids).toContain('offensive')
    expect(ids.indexOf('offensive')).toBeLessThan(ids.indexOf('reference'))
    expect(DESTINATIONS.some((x) => x.id === 'artex')).toBe(true)
  })
})
