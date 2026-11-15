/**
 * CommandPalette — the ⌘K search (design v26 §4.5, §8).
 *
 * Pointer and keyboard pick the same row: hovering a row makes it the active
 * row, and Enter then runs the command on that row, not some other one.
 *
 * It is a dialog named Search with a labelled search box; it keeps Tab inside
 * itself, closes on Escape and gives focus back to whatever opened it. Each
 * result names what it is: the page's icon, a label and a kind.
 */
import React, { useState } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import CommandPalette from '../console/CommandPalette.jsx'

void React

function makeItems() {
  return [
    { id: 'go-library', title: 'Scenarios', kind: 'Page', icon: 'line-chart', onSelect: vi.fn() },
    { id: 'go-runs', title: 'Runs', kind: 'Page', icon: 'donut-chart', onSelect: vi.fn() },
    { id: 'scn-SIM-EDR-001', title: 'SIM-EDR-001 · Credential dumping', kind: 'Scenario', icon: 'line-chart', onSelect: vi.fn() },
    { id: 'ttp-TTP-2026-0001', title: 'TTP-2026-0001 · LSASS memory read', kind: 'TTP card', icon: 'password', onSelect: vi.fn() },
  ]
}

describe('<CommandPalette /> — focus on open', () => {
  it('owns focus the moment it opens, so the first keys typed after ⌘K are a query', () => {
    // It used to move focus on a 20 ms timer, and anything typed in that gap
    // went to whatever button had focus before (Enter would press it).
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const { rerender } = render(<CommandPalette open={false} items={makeItems()} />)
    rerender(<CommandPalette open items={makeItems()} />)
    // No timers advanced, no waitFor: focus must already be in the search box.
    expect(screen.getByRole('searchbox', { name: /search pages/i })).toHaveFocus()
    opener.remove()
  })
})

describe('<CommandPalette />', () => {
  it('Enter runs the hovered command, not the last one', () => {
    const items = makeItems()
    const onClose = vi.fn()
    render(<CommandPalette open items={items} onClose={onClose} />)

    fireEvent.mouseEnter(screen.getByRole('button', { name: /^Runs/ }))
    fireEvent.keyDown(document, { key: 'Enter' })

    expect(items[1].onSelect).toHaveBeenCalledTimes(1)
    expect(items[3].onSelect).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('highlights the hovered row', () => {
    render(<CommandPalette open items={makeItems()} />)

    const hovered = screen.getByRole('button', { name: /SIM-EDR-001/ })
    fireEvent.mouseEnter(hovered)

    expect(hovered).toHaveClass('cmd-result--active')
    expect(screen.getByRole('button', { name: /TTP-2026-0001/ })).not.toHaveClass('cmd-result--active')
  })

  it('is a dialog named Search with a labelled search box', () => {
    render(<CommandPalette open items={makeItems()} />)

    const dialog = screen.getByRole('dialog', { name: /^search$/i })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByRole('searchbox', { name: /search/i })).toBeInTheDocument()
  })

  it('renders nothing while closed', () => {
    render(<CommandPalette open={false} items={makeItems()} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows each result with its page icon, its label and its kind', () => {
    render(<CommandPalette open items={makeItems()} />)

    const row = screen.getByRole('button', { name: /SIM-EDR-001/ })
    expect(row.querySelector('img[src="/icons/line-chart.png"]')).toBeTruthy()
    expect(within(row).getByText('SIM-EDR-001 · Credential dumping')).toBeInTheDocument()
    expect(within(row).getByText('Scenario')).toBeInTheDocument()

    const card = screen.getByRole('button', { name: /TTP-2026-0001/ })
    expect(card.querySelector('img[src="/icons/password.png"]')).toBeTruthy()
    expect(within(card).getByText('TTP card')).toBeInTheDocument()
  })

  it('finds a result by its id', () => {
    render(<CommandPalette open items={makeItems()} />)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'ttp-2026' } })
    expect(screen.getByRole('button', { name: /TTP-2026-0001/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /SIM-EDR-001/ })).not.toBeInTheDocument()
  })

  it('closes on Escape', () => {
    const onClose = vi.fn()
    render(<CommandPalette open items={makeItems()} onClose={onClose} />)
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('keeps Tab inside the dialog', () => {
    render(<CommandPalette open items={makeItems()} />)
    const dialog = screen.getByRole('dialog', { name: /^search$/i })
    const box = within(dialog).getByRole('searchbox')
    const buttons = within(dialog).getAllByRole('button')
    const last = buttons[buttons.length - 1]

    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(box)

    fireEvent.keyDown(box, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
  })

  it('gives focus back to the control that opened it', () => {
    function Harness() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>Open search</button>
          <CommandPalette open={open} items={makeItems()} onClose={() => setOpen(false)} />
        </>
      )
    }
    render(<Harness />)
    const opener = screen.getByRole('button', { name: 'Open search' })
    opener.focus()
    fireEvent.click(opener)
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(document.activeElement).toBe(opener)
  })
})
