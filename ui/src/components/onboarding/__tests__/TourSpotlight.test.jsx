// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TourSpotlight from '../TourSpotlight.jsx'

void React

const STOP = { id: 'a', anchor: 'anchor-a', destination: 'library', title: 'Start here', body: 'The Library holds 170 scenarios.' }

beforeEach(() => {
  document.body.innerHTML = ''
  const el = document.createElement('div')
  el.setAttribute('data-tour-id', 'anchor-a')
  document.body.appendChild(el)
})

describe('TourSpotlight', () => {
  it('renders the stop title and body', () => {
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByText('Start here')).toBeTruthy()
    expect(screen.getByText(/170 scenarios/)).toBeTruthy()
  })

  it('is a modal dialog named by the stop title', () => {
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    const dlg = screen.getByRole('dialog')
    expect(dlg.getAttribute('aria-modal')).toBe('true')
    expect(dlg.getAttribute('aria-label')).toBe('Start here')
  })

  it('shows progress as step n of total', () => {
    render(<TourSpotlight stop={STOP} index={2} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByText(/3 of 5/)).toBeTruthy()
  })

  it('Escape exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onExit).toHaveBeenCalled()
  })

  // ── I5 — Enter was globally hijacked as "next", so a keyboard user
  // tabbed to Skip who pressed Enter advanced the tour instead of exiting
  // it (and Enter on Back went forward). `fireEvent.keyDown` does not
  // synthesize a button's default Enter-activates-click behavior in jsdom
  // (that's exactly why no test caught this) — `userEvent.keyboard` does. ──
  it('Enter on a focused Skip button activates Skip, not Next (I5)', async () => {
    const onExit = vi.fn()
    const onNext = vi.fn()
    render(<TourSpotlight stop={STOP} index={1} total={5} onNext={onNext} onPrev={vi.fn()} onExit={onExit} />)
    screen.getByRole('button', { name: /skip/i }).focus()
    await userEvent.keyboard('{Enter}')
    expect(onExit).toHaveBeenCalled()
    expect(onNext).not.toHaveBeenCalled()
  })

  it('Enter on a focused Back button activates Back, not Next (I5)', async () => {
    const onNext = vi.fn()
    const onPrev = vi.fn()
    render(<TourSpotlight stop={STOP} index={1} total={5} onNext={onNext} onPrev={onPrev} onExit={vi.fn()} />)
    screen.getByRole('button', { name: /back/i }).focus()
    await userEvent.keyboard('{Enter}')
    expect(onPrev).toHaveBeenCalled()
    expect(onNext).not.toHaveBeenCalled()
  })

  it('Enter still advances when focus is on the dialog itself, not a button', async () => {
    const onNext = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={onNext} onPrev={vi.fn()} onExit={vi.fn()} />)
    screen.getByRole('dialog').focus()
    await userEvent.keyboard('{Enter}')
    expect(onNext).toHaveBeenCalled()
  })

  it('the skip control exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.click(screen.getByRole('button', { name: /skip/i }))
    expect(onExit).toHaveBeenCalled()
  })

  // ── C1 — the deploy-modal-underneath-the-tour trap. Stop 4 spotlights
  // `agent-enroll`, whose click opens a modal that would render underneath
  // the tour's z-900 shutters. Exiting the moment the SPOTLIT control is
  // activated is the fix: the user has done the thing the stop was
  // teaching, so nothing is left dimmed above whatever it opened. ──
  it('activating the SPOTLIT anchor control exits the tour (C1)', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    // `anchor-a` is the real app control (e.g. TargetsView's "+ Deploy
    // agent" button) — the beforeEach fixture stands in for it.
    fireEvent.click(document.querySelector('[data-tour-id="anchor-a"]'))
    expect(onExit).toHaveBeenCalled()
  })

  it('does NOT exit when a click lands elsewhere on the page (only the spotlit anchor triggers it)', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.click(document.body)
    expect(onExit).not.toHaveBeenCalled()
  })

  it('the last stop offers Done rather than Next', () => {
    render(<TourSpotlight stop={STOP} index={4} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(screen.getByRole('button', { name: /done/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^next$/i })).toBeNull()
  })

  it('renders nothing when stop is null', () => {
    const { container } = render(<TourSpotlight stop={null} index={-1} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('restores focus to the previously focused element on exit', () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'Open tour'
    document.body.appendChild(trigger)
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    const { unmount } = render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(document.activeElement).not.toBe(trigger)

    unmount()
    expect(document.activeElement).toBe(trigger)
  })

  it('Tab from the last focusable wraps to the first (the dialog itself)', () => {
    render(<TourSpotlight stop={STOP} index={1} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    const dlg = screen.getByRole('dialog')
    const buttons = screen.getAllByRole('button')
    const last = buttons[buttons.length - 1]
    last.focus()
    expect(document.activeElement).toBe(last)

    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(dlg)
  })

  it('Shift+Tab from the first focusable (the dialog) wraps to the last', () => {
    render(<TourSpotlight stop={STOP} index={1} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    const dlg = screen.getByRole('dialog')
    const buttons = screen.getAllByRole('button')
    const last = buttons[buttons.length - 1]
    dlg.focus()
    expect(document.activeElement).toBe(dlg)

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('renders four dim shutters framing the cutout when the anchor resolves, and no full-viewport dim', () => {
    const { container } = render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.querySelectorAll('.tour__shutter').length).toBe(4)
    expect(container.querySelector('.tour__dim')).toBeNull()
    expect(container.querySelector('.tour__cutout')).not.toBeNull()
  })

  it('falls back to a single full-viewport dim layer, with no shutters, when the anchor element is missing', () => {
    const missing = { ...STOP, anchor: 'does-not-exist' }
    const { container } = render(<TourSpotlight stop={missing} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.querySelector('.tour__dim')).not.toBeNull()
    expect(container.querySelectorAll('.tour__shutter').length).toBe(0)
    expect(container.querySelector('.tour__cutout')).toBeNull()
  })

  it('does not bounce focus to the external trigger while navigating between stops', () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'Open tour'
    document.body.appendChild(trigger)
    trigger.focus()
    const triggerFocusSpy = vi.spyOn(trigger, 'focus')

    const STOP2 = { id: 'b', anchor: 'anchor-a', destination: 'library', title: 'Second stop', body: 'Second stop body text.' }
    const STOP3 = { id: 'c', anchor: 'anchor-a', destination: 'library', title: 'Third stop', body: 'Third stop body text.' }

    const { rerender } = render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    rerender(<TourSpotlight stop={STOP2} index={1} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    rerender(<TourSpotlight stop={STOP3} index={2} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)

    expect(triggerFocusSpy).not.toHaveBeenCalled()
    expect(document.activeElement).not.toBe(trigger)
  })
})
