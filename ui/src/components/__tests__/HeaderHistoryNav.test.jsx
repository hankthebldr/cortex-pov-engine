/**
 * The header's Back / Forward (design C9).
 *
 *   1. <HistoryNav>: each button is disabled when there is nowhere to go, a
 *      click moves, and ⌘[ / ⌘] (Mac) or Ctrl+[ / Ctrl+] (elsewhere) do the
 *      same, except while focus is in a text input or a textarea.
 *   2. AppConsole end to end: the header walks the router's own history
 *      (pages, and a run opened with setParams), and the breadcrumb follows.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, within } from '@testing-library/react'

import HistoryNav from '../console/HistoryNav.jsx'
import AppConsole from '../../AppConsole.jsx'

void React

let platform = ''
beforeEach(() => {
  platform = ''
  vi.spyOn(window.navigator, 'platform', 'get').mockImplementation(() => platform)
})
afterEach(() => {
  vi.restoreAllMocks()
})

function renderNav(props = {}) {
  const onBack = vi.fn()
  const onForward = vi.fn()
  const utils = render(<HistoryNav canBack canForward onBack={onBack} onForward={onForward} {...props} />)
  return { onBack, onForward, ...utils }
}

describe('<HistoryNav> — buttons', () => {
  it('names each button and disables it when there is nowhere to go', () => {
    const { rerender } = render(<HistoryNav canBack={false} canForward={false} />)
    const group = screen.getByRole('group', { name: 'History' })
    expect(within(group).getByRole('button', { name: 'Back' })).toBeDisabled()
    expect(within(group).getByRole('button', { name: 'Forward' })).toBeDisabled()

    rerender(<HistoryNav canBack canForward={false} />)
    expect(screen.getByRole('button', { name: 'Back' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Forward' })).toBeDisabled()
  })

  it('a click moves back or forward', () => {
    const { onBack, onForward } = renderNav()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(onBack).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Forward' }))
    expect(onForward).toHaveBeenCalledTimes(1)
  })

  it('draws the kit glyphs', () => {
    renderNav()
    expect(screen.getByRole('button', { name: 'Back' }).querySelector('[data-icon="back"]')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Forward' }).querySelector('[data-icon="forward"]')).not.toBeNull()
  })
})

describe('<HistoryNav> — keyboard', () => {
  it('Ctrl+[ and Ctrl+] move back and forward off a Mac', () => {
    const { onBack, onForward } = renderNav()
    expect(screen.getByRole('button', { name: 'Back' })).toHaveAttribute('aria-keyshortcuts', 'Control+[')
    fireEvent.keyDown(document.body, { key: '[', ctrlKey: true })
    expect(onBack).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document.body, { key: ']', ctrlKey: true })
    expect(onForward).toHaveBeenCalledTimes(1)
    // ⌘ is not the modifier here.
    fireEvent.keyDown(document.body, { key: '[', metaKey: true })
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('⌘[ and ⌘] on a Mac, where Ctrl does nothing', () => {
    platform = 'MacIntel'
    const { onBack, onForward } = renderNav()
    expect(screen.getByRole('button', { name: 'Back' })).toHaveAttribute('aria-keyshortcuts', 'Meta+[')
    fireEvent.keyDown(document.body, { key: '[', metaKey: true })
    expect(onBack).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document.body, { key: ']', metaKey: true })
    expect(onForward).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(document.body, { key: '[', ctrlKey: true })
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('does not fire while focus is in a text input or a textarea', () => {
    const onBack = vi.fn()
    const onForward = vi.fn()
    render(
      <>
        <input aria-label="Search" />
        <textarea aria-label="Notes" />
        <HistoryNav canBack canForward onBack={onBack} onForward={onForward} />
      </>,
    )
    const input = screen.getByRole('textbox', { name: 'Search' })
    input.focus()
    // Not prevented either: the keys stay the field's.
    expect(fireEvent.keyDown(input, { key: '[', ctrlKey: true })).toBe(true)
    const notes = screen.getByRole('textbox', { name: 'Notes' })
    notes.focus()
    fireEvent.keyDown(notes, { key: ']', ctrlKey: true })
    expect(onBack).not.toHaveBeenCalled()
    expect(onForward).not.toHaveBeenCalled()

    // Out of the field, the same keys move.
    notes.blur()
    fireEvent.keyDown(document.body, { key: '[', ctrlKey: true })
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('does nothing in a direction with nowhere to go', () => {
    const { onBack, onForward } = renderNav({ canBack: false, canForward: false })
    fireEvent.keyDown(document.body, { key: '[', ctrlKey: true })
    fireEvent.keyDown(document.body, { key: ']', ctrlKey: true })
    expect(onBack).not.toHaveBeenCalled()
    expect(onForward).not.toHaveBeenCalled()
  })
})

// Long enough for a hash navigation's hashchange to have landed.
const settle = () => act(() => new Promise((r) => setTimeout(r, 50)))

describe('AppConsole — the header walks the router history', () => {
  beforeEach(() => {
    window.localStorage.setItem('cortexsim.helpOverlay.seenV1', 'true')
    window.history.replaceState(null, '', '#/runs')
  })
  afterEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    window.history.replaceState(null, '', '#/')
  })

  it('pages: Back and Forward return through them, by click and by keyboard', async () => {
    render(<AppConsole />)
    await settle()
    const back = screen.getByTestId('header-back')
    const forward = screen.getByTestId('header-forward')
    expect(back).toBeDisabled()
    expect(forward).toBeDisabled()

    fireEvent.click(screen.getByTestId('dest-button-proof'))
    await settle()
    expect(window.location.hash).toBe('#/proof')
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByText('Results')).toHaveAttribute('aria-current', 'page')
    expect(back).toBeEnabled()

    fireEvent.click(back)
    await settle()
    expect(window.location.hash).toBe('#/runs')
    expect(screen.getByTestId('dest-button-runs')).toHaveAttribute('aria-current', 'page')
    expect(back).toBeDisabled()
    expect(forward).toBeEnabled()

    fireEvent.keyDown(document.body, { key: ']', ctrlKey: true })
    await settle()
    expect(window.location.hash).toBe('#/proof')
    expect(forward).toBeDisabled()
  })

  it('a view opened with setParams is a place: Back closes it, and the breadcrumb names it', async () => {
    window.history.replaceState(null, '', '#/start')
    render(<AppConsole />)
    await settle()

    fireEvent.click(screen.getByTestId('dest-button-runs'))
    await settle()
    // Compare is opened with setParams, which adds no browser entry.
    fireEvent.click(screen.getByRole('button', { name: 'Compare runs' }))
    await settle()
    expect(window.location.hash).toBe('#/runs?compare=1')
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByText('Compare runs')).toHaveAttribute('aria-current', 'page')
    expect(within(crumbs).getByRole('link', { name: 'Runs' })).toHaveAttribute('href', '#/runs')

    fireEvent.click(screen.getByTestId('header-back'))
    await settle()
    expect(window.location.hash).toBe('#/runs')
    fireEvent.click(screen.getByTestId('header-back'))
    await settle()
    expect(window.location.hash).toBe('#/start')
    expect(screen.getByTestId('header-back')).toBeDisabled()
  })

  it('a breadcrumb link navigates', async () => {
    window.history.replaceState(null, '', '#/uctc?tc=TC-EDR-03')
    render(<AppConsole />)
    await settle()
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByText('Test case TC-EDR-03')).toHaveAttribute('aria-current', 'page')

    fireEvent.click(within(crumbs).getByRole('link', { name: 'Catalog' }))
    await settle()
    expect(window.location.hash).toBe('#/ttps')
    expect(screen.getByTestId('header-back')).toBeEnabled()
  })
})
