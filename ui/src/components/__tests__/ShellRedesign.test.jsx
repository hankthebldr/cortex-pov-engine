/**
 * Shell redesign — the three things the PANW redesign added above the
 * workspace, and the one thing it changed inside the header.
 *
 * Each test here pins a decision that is easy to undo by accident:
 *   - the safety banner is a per-TENANT consent gate, not a dismissable notice
 *   - the phase bar answers "where am I in the work", including the honest
 *     "nowhere" for destinations outside the six-phase model
 *   - the header run view is UNCONDITIONAL (it used to vanish between runs,
 *     which is most of a session)
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import SafetyBanner, { ackKey } from '../console/SafetyBanner.jsx'
import PhaseBar, { PHASES, phaseIndexFor } from '../console/PhaseBar.jsx'
import ConsoleHeader from '../console/ConsoleHeader.jsx'

beforeEach(() => {
  window.localStorage.clear()
})

describe('SafetyBanner — blast-radius consent', () => {
  it('shows the warning, the tenant it applies to, and how to acknowledge it', () => {
    render(<SafetyBanner tenantId="acme-pov-na" />)
    expect(screen.getByTestId('safety-banner')).toBeInTheDocument()
    expect(screen.getByText(/Warning · Detection Testing/i)).toBeInTheDocument()
    expect(screen.getByText('acme-pov-na')).toBeInTheDocument()
    expect(screen.getByTestId('safety-banner-ack')).toBeInTheDocument()
  })

  it('is an alert, not a status — it must interrupt', () => {
    render(<SafetyBanner tenantId="acme-pov-na" />)
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('disappears once acknowledged for that tenant', async () => {
    const user = userEvent.setup()
    render(<SafetyBanner tenantId="acme-pov-na" />)
    await user.click(screen.getByTestId('safety-banner-ack'))
    expect(screen.queryByTestId('safety-banner')).not.toBeInTheDocument()
    expect(window.localStorage.getItem(ackKey('acme-pov-na'))).toBe('true')
  })

  it('RE-ARMS on a different tenant — acknowledging a lab does not acknowledge a customer', () => {
    // This is the whole reason the acknowledgement is keyed rather than global.
    window.localStorage.setItem(ackKey('my-lab'), 'true')
    const { rerender } = render(<SafetyBanner tenantId="my-lab" />)
    expect(screen.queryByTestId('safety-banner')).not.toBeInTheDocument()

    rerender(<SafetyBanner tenantId="customer-prod" />)
    expect(screen.getByTestId('safety-banner')).toBeInTheDocument()
  })

  it('treats "no tenant selected" as its own acknowledgeable scope', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<SafetyBanner tenantId={null} />)
    expect(screen.getByText(/the active tenant/i)).toBeInTheDocument()
    await user.click(screen.getByTestId('safety-banner-ack'))
    expect(screen.queryByTestId('safety-banner')).not.toBeInTheDocument()

    // …and acknowledging "none" must not silence a real tenant.
    rerender(<SafetyBanner tenantId="acme-pov-na" />)
    expect(screen.getByTestId('safety-banner')).toBeInTheDocument()
  })

  it('SHOWS when storage throws — a safety notice fails loud, not closed', () => {
    const spy = vi.spyOn(window.localStorage.__proto__, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    render(<SafetyBanner tenantId="acme-pov-na" />)
    expect(screen.getByTestId('safety-banner')).toBeInTheDocument()
    spy.mockRestore()
  })
})

describe('PhaseBar — where am I in a POV run', () => {
  it('renders all six phases in order', () => {
    render(<PhaseBar destination="library" />)
    expect(PHASES).toHaveLength(6)
    for (const p of PHASES) {
      expect(screen.getByTestId(`phase-button-${p.label.toLowerCase()}`)).toBeInTheDocument()
    }
  })

  it('marks the phase that owns the current destination', () => {
    render(<PhaseBar destination="runs" />)
    expect(screen.getByTestId('phase-button-observe')).toHaveAttribute('aria-current', 'step')
    expect(screen.getByTestId('phase-button-scope')).not.toHaveAttribute('aria-current')
  })

  it('routes each phase to the destination that owns it', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    render(<PhaseBar destination="library" onNavigate={onNavigate} />)
    await user.click(screen.getByTestId('phase-button-compose'))
    expect(onNavigate).toHaveBeenCalledWith('composer')
    await user.click(screen.getByTestId('phase-button-prove'))
    expect(onNavigate).toHaveBeenCalledWith('coverage')
  })

  it('puts `composer` in Compose, never in Launch', () => {
    // Launch is a state the Composer ENTERS after preflight, not a place you
    // navigate to — highlighting it on arrival would claim progress not made.
    expect(phaseIndexFor('composer')).toBe(1)
    expect(PHASES[1].label).toBe('Compose')
  })

  it('marks NO phase for a destination outside the model, rather than guessing', () => {
    expect(phaseIndexFor('guided')).toBe(-1)
    render(<PhaseBar destination="guided" />)
    expect(document.querySelectorAll('[aria-current="step"]')).toHaveLength(0)
    // …and must not mark every earlier step "done" either.
    expect(document.querySelectorAll('.phase-step--done')).toHaveLength(0)
  })
})

describe('ConsoleHeader — the run view is always present', () => {
  const health = { hostname: 'lab-test', version: 'v1.0', sensors: {} }

  it('shows LIVE with a step counter while a run is in flight', () => {
    render(
      <ConsoleHeader
        health={health}
        activeRun={{ runId: 'r-1', scenarioId: 'SIM-EDR-001', step: 2, totalSteps: 5, elapsed: 137 }}
      />,
    )
    const view = screen.getByTestId('header-run-view')
    expect(view).toHaveAttribute('data-run-state', 'live')
    expect(view).toHaveTextContent('SIM-EDR-001')
    expect(view).toHaveTextContent('2/5')
  })

  it('falls back to the LAST RUN when nothing is in flight', () => {
    render(
      <ConsoleHeader
        health={health}
        activeRun={null}
        lastRun={{ runId: 'r-0', scenarioId: 'SIM-CDR-003', status: 'completed' }}
      />,
    )
    const view = screen.getByTestId('header-run-view')
    expect(view).toHaveAttribute('data-run-state', 'last')
    expect(view).toHaveTextContent('SIM-CDR-003')
  })

  it('says NO RUNS explicitly when there has never been one', () => {
    // An empty slot would read as "we failed to load it".
    render(<ConsoleHeader health={health} activeRun={null} lastRun={null} />)
    const view = screen.getByTestId('header-run-view')
    expect(view).toHaveAttribute('data-run-state', 'none')
    expect(view).toHaveTextContent(/nothing launched yet/i)
  })

  it('routes live → the live tab and last → the evidence tab', async () => {
    const user = userEvent.setup()
    const onNavigate = vi.fn()
    const { rerender } = render(
      <ConsoleHeader health={health} onNavigate={onNavigate}
        activeRun={{ runId: 'r-1', scenarioId: 'S', step: 1, totalSteps: 2, elapsed: 1 }} />,
    )
    await user.click(screen.getByTestId('header-run-view'))
    expect(onNavigate).toHaveBeenCalledWith('runs', { run: 'r-1', tab: 'live' })

    rerender(
      <ConsoleHeader health={health} onNavigate={onNavigate}
        activeRun={null} lastRun={{ runId: 'r-0', scenarioId: 'S', status: 'completed' }} />,
    )
    await user.click(screen.getByTestId('header-run-view'))
    expect(onNavigate).toHaveBeenCalledWith('runs', { run: 'r-0', tab: 'evidence' })
  })

  it('keeps the tenant and agent switchers — they are richer than static pills', () => {
    // Guard against a redesign pass "simplifying" the header by deleting the
    // provider-backed switchers.
    const { container } = render(<ConsoleHeader health={health} />)
    expect(container.querySelector('.brand-marks__panw')).toBeTruthy()
    expect(screen.getByRole('button', { name: /command palette/i })).toBeInTheDocument()
    expect(screen.getByText(/LAB-TEST/)).toBeInTheDocument()
  })

  it('swaps the PANW lockup by theme — the dark asset has a WHITE wordmark', () => {
    // panw-mark.png is invisible on the light header; panw-primary.png is the
    // black-wordmark pair. Getting this backwards makes the brand disappear.
    const { container, rerender } = render(<ConsoleHeader health={health} colorTheme="light" />)
    expect(container.querySelector('.brand-marks__panw').getAttribute('src'))
      .toContain('panw-primary')
    rerender(<ConsoleHeader health={health} colorTheme="dark" />)
    expect(container.querySelector('.brand-marks__panw').getAttribute('src'))
      .toContain('panw-mark')
  })

  it('renders the tour trigger with a beacon only until the tour has been seen', () => {
    const { container, rerender } = render(
      <ConsoleHeader health={health} onStartTour={() => {}} tourSeen={false} />,
    )
    expect(screen.getByTestId('header-tour-trigger')).toBeInTheDocument()
    expect(container.querySelector('.tour-trigger__beacon')).toBeTruthy()

    rerender(<ConsoleHeader health={health} onStartTour={() => {}} tourSeen />)
    expect(container.querySelector('.tour-trigger__beacon')).toBeFalsy()
  })

  it('omits the tour trigger entirely when no tour handler is wired', () => {
    render(<ConsoleHeader health={health} />)
    expect(screen.queryByTestId('header-tour-trigger')).not.toBeInTheDocument()
  })
})
