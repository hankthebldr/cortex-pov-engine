// @vitest-environment jsdom
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
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

  it('the skip control exits', () => {
    const onExit = vi.fn()
    render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={onExit} />)
    fireEvent.click(screen.getByRole('button', { name: /skip/i }))
    expect(onExit).toHaveBeenCalled()
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

  it('omits the dim layer and relies on the cutout box-shadow when the anchor resolves', () => {
    const { container } = render(<TourSpotlight stop={STOP} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.querySelector('.tour__dim')).toBeNull()
    expect(container.querySelector('.tour__cutout')).not.toBeNull()
  })

  it('falls back to the full dim layer when the anchor element is missing', () => {
    const missing = { ...STOP, anchor: 'does-not-exist' }
    const { container } = render(<TourSpotlight stop={missing} index={0} total={5} onNext={vi.fn()} onPrev={vi.fn()} onExit={vi.fn()} />)
    expect(container.querySelector('.tour__dim')).not.toBeNull()
    expect(container.querySelector('.tour__cutout')).toBeNull()
  })
})
