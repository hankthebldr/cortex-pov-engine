/**
 * Immersive-mode exit routing.
 *
 * Two layers:
 *   1. The pure return-target helpers (immersiveReturn.js) against the real
 *      registry — what gets recorded, and where Exit lands.
 *   2. AppConsole end to end: Library (with an open scenario) → Composer →
 *      the surface's onExit → back on the SAME Library page with the SAME
 *      params; and a cold #/composer deep link exiting to its navParent.
 *
 * The Composer and the Library's OperationsView are stubbed: this file pins
 * the SHELL's contract (which props an immersive surface receives, what the
 * shell hides and keeps), not either surface's internals. Stubbing the
 * Composer also keeps React Flow out of this test entirely.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import { exitTarget, nextReturnTarget } from '../immersiveReturn.js'
import { DESTINATIONS, DEFAULT_DESTINATION, getDestination, isImmersive } from '../destinations.jsx'

void React

vi.mock('../../components/console/ComposerView.jsx', () => ({
  default: function ComposerStub({ onExit, params = {} }) {
    return (
      <div className="composer-stub" data-testid="composer-stub" data-has-exit={String(typeof onExit === 'function')}>
        <span data-testid="composer-params">{JSON.stringify(params)}</span>
        <button type="button" data-testid="composer-exit" onClick={() => onExit && onExit()}>Exit</button>
      </div>
    )
  },
}))

vi.mock('../../components/console/OperationsView.jsx', () => ({
  default: function OperationsStub({ requestOpenScenarioId }) {
    return <div data-testid="ops-stub">{requestOpenScenarioId || '(none)'}</div>
  },
}))

vi.mock('../../components/console/ReadinessView.jsx', () => ({
  default: function ReadinessStub({ onExit, onNavigate }) {
    return (
      <div
        data-testid="readiness-stub"
        data-has-exit={String(typeof onExit === 'function')}
        data-has-navigate={String(typeof onNavigate === 'function')}
      />
    )
  },
}))

// ── 1. Pure helpers + registry ──────────────────────────────────────────────
describe('immersive registry flag', () => {
  it('the Composer is immersive; ordinary pages are not', () => {
    expect(isImmersive('composer')).toBe(true)
    expect(isImmersive('library')).toBe(false)
    expect(isImmersive('runs')).toBe(false)
    expect(isImmersive('not-a-destination')).toBe(false)
    expect(isImmersive(undefined)).toBe(false)
  })

  it('every immersive destination is off-rail and has a navParent to exit to', () => {
    const immersive = DESTINATIONS.filter((d) => d.immersive)
    expect(immersive.map((d) => d.id)).toContain('composer')
    for (const d of immersive) {
      expect(d.hidden, `${d.id} must not be a rail item`).toBe(true)
      expect(d.redirect).toBeFalsy()
      expect(getDestination(d.navParent), `${d.id}.navParent must resolve`).toBeTruthy()
      expect(getDestination(d.navParent).immersive, `${d.id}.navParent must not be immersive`).toBeFalsy()
    }
  })
})

describe('nextReturnTarget', () => {
  it('records an ordinary page with a COPY of its params', () => {
    const params = { open: 'SIM-EDR-001' }
    const rec = nextReturnTarget(null, getDestination('library'), params)
    expect(rec).toEqual({ destination: 'library', params: { open: 'SIM-EDR-001' } })
    params.open = 'mutated'
    expect(rec.params.open).toBe('SIM-EDR-001')
  })

  it('never records an immersive destination or a redirect stub', () => {
    const prev = { destination: 'runs', params: { run: 'r-1' } }
    expect(nextReturnTarget(prev, getDestination('composer'), { draft: 'x' })).toBe(prev)
    expect(nextReturnTarget(prev, getDestination('setup'), {})).toBe(prev)
    expect(nextReturnTarget(prev, null, {})).toBe(prev)
  })
})

describe('exitTarget', () => {
  const composer = getDestination('composer')

  it('returns to the recorded page with its params', () => {
    expect(exitTarget({ destination: 'runs', params: { run: 'r-1', tab: 'live' } }, composer, DEFAULT_DESTINATION))
      .toEqual({ destination: 'runs', params: { run: 'r-1', tab: 'live' } })
  })

  it('a cold deep link (nothing recorded) exits to the navParent', () => {
    expect(exitTarget(null, composer, DEFAULT_DESTINATION)).toEqual({ destination: 'library', params: null })
  })

  it('a record pointing at the immersive page itself is ignored', () => {
    expect(exitTarget({ destination: 'composer', params: {} }, composer, DEFAULT_DESTINATION))
      .toEqual({ destination: 'library', params: null })
  })

  it('falls back to the default destination when there is no navParent either', () => {
    expect(exitTarget(null, { id: 'x', immersive: true }, DEFAULT_DESTINATION))
      .toEqual({ destination: DEFAULT_DESTINATION, params: null })
  })
})

// ── 2. AppConsole end to end ────────────────────────────────────────────────
describe('AppConsole — immersive Composer mount and exit routing', () => {
  let AppConsole

  beforeEach(async () => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    AppConsole = (await import('../../AppConsole.jsx')).default
  })

  afterEach(() => {
    window.location.hash = ''
  })

  it('library → composer → onExit returns to #/library with the same params', async () => {
    window.location.hash = '#/library?open=SIM-EDR-001'
    const { container } = render(<AppConsole />)

    expect(await screen.findByTestId('ops-stub')).toHaveTextContent('SIM-EDR-001')
    expect(screen.getByRole('navigation', { name: /console destinations/i })).toBeInTheDocument()
    expect(screen.getByTestId('section-tabs')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('section-tab-composer'))
    const composer = await screen.findByTestId('composer-stub')

    // Immersive: the rail and the tab strip are gone, the shell says so, and
    // the surface received a working onExit.
    expect(composer).toHaveAttribute('data-has-exit', 'true')
    expect(screen.queryByRole('navigation', { name: /console destinations/i })).toBeNull()
    expect(screen.queryByTestId('section-tabs')).toBeNull()
    expect(container.querySelector('.pov-shell')).toHaveAttribute('data-mode', 'immersive')
    // The header stays.
    expect(screen.getByRole('button', { name: /command palette/i })).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('composer-exit'))

    expect(await screen.findByTestId('ops-stub')).toHaveTextContent('SIM-EDR-001')
    await waitFor(() => expect(window.location.hash).toBe('#/library?open=SIM-EDR-001'))
    expect(container.querySelector('.pov-shell')).not.toHaveAttribute('data-mode')
    expect(screen.getByRole('navigation', { name: /console destinations/i })).toBeInTheDocument()
    expect(screen.getByTestId('section-tabs')).toBeInTheDocument()
  })

  it('a cold #/composer deep link exits to its navParent, library', async () => {
    window.location.hash = '#/composer'
    render(<AppConsole />)

    await screen.findByTestId('composer-stub')
    fireEvent.click(screen.getByTestId('composer-exit'))

    await waitFor(() => expect(window.location.hash).toBe('#/library'))
    expect(await screen.findByTestId('ops-stub')).toBeInTheDocument()
  })

  it('an unreachable SimCore is still announced in immersive mode', async () => {
    // The default fetch stub (src/test/setup.js) answers every request 404,
    // so the /api/health reachability probe fails — exactly a dead SimCore.
    window.location.hash = '#/composer'
    render(<AppConsole />)

    await screen.findByTestId('composer-stub')
    expect(await screen.findByTestId('api-down-banner')).toBeInTheDocument()
  })

  it('a non-immersive surface never receives onExit', async () => {
    // ReadinessView is mounted directly (withSuspense is a passthrough), so it
    // sees exactly the props AppConsole hands a surface.
    window.location.hash = '#/preflight'
    const { container } = render(<AppConsole />)

    const stub = await screen.findByTestId('readiness-stub')
    expect(stub).toHaveAttribute('data-has-exit', 'false')
    expect(stub).toHaveAttribute('data-has-navigate', 'true')
    expect(container.querySelector('.pov-shell')).not.toHaveAttribute('data-mode')
    expect(screen.getByRole('navigation', { name: /console destinations/i })).toBeInTheDocument()
  })
})
