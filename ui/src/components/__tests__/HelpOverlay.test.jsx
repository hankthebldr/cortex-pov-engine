// @vitest-environment jsdom
/**
 * Smoke + interaction tests for HelpOverlay and its first-run helpers.
 */
import React from 'react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import HelpOverlay, {
  shouldShowOnFirstRun,
  markFirstRunSeen,
} from '../console/HelpOverlay.jsx'

void React

// localStorage polyfill — jsdom in this Node env doesn't ship one (same
// pattern as usePinnedScenarios.test).
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
      writable: true, configurable: true,
    })
  }
})

beforeEach(() => {
  window.localStorage.clear()
})

describe('shouldShowOnFirstRun + markFirstRunSeen', () => {
  it('returns true on a fresh browser', () => {
    expect(shouldShowOnFirstRun()).toBe(true)
  })

  it('returns false after markFirstRunSeen', () => {
    markFirstRunSeen()
    expect(shouldShowOnFirstRun()).toBe(false)
  })
})

describe('<HelpOverlay />', () => {
  it('renders nothing when closed', () => {
    const { container } = render(<HelpOverlay open={false} />)
    expect(container.querySelector('.help-overlay')).toBeFalsy()
  })

  it('renders the shortcuts tab by default when open', () => {
    render(<HelpOverlay open onClose={() => {}} />)
    expect(screen.getByText(/Quick reference/i)).toBeInTheDocument()
    expect(screen.getByText(/Command palette/i)).toBeInTheDocument()
    expect(screen.getByText(/Filter palette/i)).toBeInTheDocument()
  })

  it('switches to the tabs cheatsheet pane on click', () => {
    render(<HelpOverlay open onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /tab cheatsheet/i }))
    expect(screen.getByText(/attack-narrative timeline/i)).toBeInTheDocument()
  })

  it('switches to the about pane and lists PANW stack', () => {
    render(<HelpOverlay open onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'About' }))
    // "CortexSim" appears twice (eyebrow + about header) — assert
    // something only the About pane renders.
    expect(screen.getByText(/Strata Network Security/i)).toBeInTheDocument()
    expect(screen.getByText(/quality-assurance/i)).toBeInTheDocument()
  })

  it('Close button calls onClose', () => {
    const onClose = vi.fn()
    render(<HelpOverlay open onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: /close help overlay/i }))
    expect(onClose).toHaveBeenCalled()
  })

  it('clicking the backdrop (not the dialog) closes', () => {
    const onClose = vi.fn()
    const { container } = render(<HelpOverlay open onClose={onClose} />)
    const backdrop = container.querySelector('.help-overlay__backdrop')
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalled()
  })

  it('does not close when clicking inside the dialog', () => {
    const onClose = vi.fn()
    const { container } = render(<HelpOverlay open onClose={onClose} />)
    const dialog = container.querySelector('.help-overlay')
    fireEvent.click(dialog)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('renders an optional "Start guided tour" CTA when onTour is provided', () => {
    const onTour = vi.fn()
    render(<HelpOverlay open onClose={() => {}} onTour={onTour} />)
    const tour = screen.getByRole('button', { name: /start guided tour/i })
    fireEvent.click(tour)
    expect(onTour).toHaveBeenCalled()
  })
})
