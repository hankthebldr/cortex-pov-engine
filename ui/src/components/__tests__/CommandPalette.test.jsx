/**
 * CommandPalette — pointer and keyboard pick the same row.
 *
 * Hovering a row makes it the active row, and Enter then runs the command
 * on that row, not some other one. The search box is announced by name, not
 * only by its placeholder.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CommandPalette from '../console/CommandPalette.jsx'

void React

function makeItems() {
  return [
    { section: 'Go to', id: 'go-library', title: 'Go to Library', onSelect: vi.fn() },
    { section: 'Go to', id: 'go-runs', title: 'Go to Runs', onSelect: vi.fn() },
    { section: 'Actions', id: 'new-pov', title: 'New POV run', onSelect: vi.fn() },
    { section: 'Actions', id: 'export', title: 'Export POV report', onSelect: vi.fn() },
  ]
}

describe('<CommandPalette />', () => {
  it('Enter runs the hovered command, not the last one', () => {
    const items = makeItems()
    const onClose = vi.fn()
    render(<CommandPalette open items={items} onClose={onClose} />)

    fireEvent.mouseEnter(screen.getByRole('button', { name: /Go to Runs/ }))
    fireEvent.keyDown(document, { key: 'Enter' })

    expect(items[1].onSelect).toHaveBeenCalledTimes(1)
    expect(items[3].onSelect).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('highlights the hovered row', () => {
    render(<CommandPalette open items={makeItems()} />)

    const hovered = screen.getByRole('button', { name: /New POV run/ })
    fireEvent.mouseEnter(hovered)

    expect(hovered).toHaveClass('cmd-result--active')
    expect(screen.getByRole('button', { name: /Export POV report/ })).not.toHaveClass('cmd-result--active')
  })

  it('names the search box for assistive technology', () => {
    render(<CommandPalette open items={makeItems()} />)

    expect(screen.getByRole('textbox', { name: /search commands/i })).toBeInTheDocument()
  })
})
