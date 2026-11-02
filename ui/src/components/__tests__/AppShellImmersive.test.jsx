/**
 * AppShell immersive mode + the Escape contract it depends on.
 *
 * Immersive mode (destinations.jsx `immersive: true`, today the Composer) is
 * the shell giving one surface everything below the header. What is pinned
 * here is what is easy to undo by accident:
 *   - the rail is NOT rendered (collapsing it is not the same thing — a
 *     62px strip of glyphs is still a rail the surface has to share)
 *   - the header and the shell `banner` slot stay: consent and SimCore health
 *     are never hidden by a mode
 *   - the mode is route-derived and never persisted, so it cannot rewrite the
 *     DC's stored rail-collapse preference
 *   - Escape is only "consumed" (preventDefault) when it actually closed an
 *     overlay, so a surface's own window-level Escape can tell the two apart
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, createEvent } from '@testing-library/react'

import AppShell from '../console/AppShell.jsx'

void React

const GROUPS = [
  { label: '', items: [
    { id: 'library', label: 'Simulate' },
    { id: 'runs', label: 'Runs' },
  ] },
]

function renderShell(props = {}) {
  return render(
    <AppShell destination="library" navGroups={GROUPS} paletteItems={[]} {...props}>
      <div data-testid="surface">surface</div>
    </AppShell>,
  )
}

const shellRoot = (container) => container.querySelector('.pov-shell')
const workspace = (container) => container.querySelector('.pov-workspace')

beforeEach(() => {
  window.localStorage.clear()
  window.sessionStorage.clear()
})

describe('AppShell — immersive mode', () => {
  it('drops the destination rail and marks the shell + workspace immersive', () => {
    const { container } = renderShell({ destination: 'composer', immersive: true })

    expect(screen.queryByRole('navigation', { name: /console destinations/i })).toBeNull()
    expect(screen.queryByTestId('dest-button-library')).toBeNull()

    const root = shellRoot(container)
    expect(root).toHaveClass('pov-shell--immersive')
    expect(root).toHaveAttribute('data-mode', 'immersive')

    const ws = workspace(container)
    expect(ws).toHaveClass('pov-workspace--immersive')
    expect(ws).not.toHaveClass('pov-workspace--rail-collapsed')

    // The surface still mounts inside the one main landmark.
    expect(screen.getByTestId('surface')).toBeInTheDocument()
    expect(container.querySelector('#cortexsim-main .view')).toContainElement(screen.getByTestId('surface'))
  })

  it('keeps the header, the safety gate and the banner slot — a mode never hides them', () => {
    renderShell({
      destination: 'composer',
      immersive: true,
      tenantId: 'acme-pov-na',
      banner: <div data-testid="shell-banner">SimCore degraded</div>,
    })
    expect(screen.getByRole('button', { name: /command palette/i })).toBeInTheDocument()
    expect(screen.getByTestId('safety-banner')).toBeInTheDocument()
    expect(screen.getByTestId('shell-banner')).toBeInTheDocument()
  })

  it('without immersive, everything is as before: rail present, no immersive markers', () => {
    const { container } = renderShell()

    expect(screen.getByRole('navigation', { name: /console destinations/i })).toBeInTheDocument()
    expect(screen.getByTestId('dest-button-library')).toBeInTheDocument()

    const root = shellRoot(container)
    expect(root).not.toHaveClass('pov-shell--immersive')
    expect(root).not.toHaveAttribute('data-mode')
    expect(workspace(container)).not.toHaveClass('pov-workspace--immersive')
  })

  it('never touches the persisted rail preference, and the rail comes back as the DC left it', () => {
    window.localStorage.setItem('cortexsim.railCollapsed', 'true')
    window.localStorage.setItem('cortexsim.theaterMode', 'false')

    const { container, rerender } = renderShell({ destination: 'composer', immersive: true })
    expect(workspace(container)).not.toHaveClass('pov-workspace--rail-collapsed')
    expect(window.localStorage.getItem('cortexsim.railCollapsed')).toBe('true')
    expect(window.localStorage.getItem('cortexsim.theaterMode')).toBe('false')

    rerender(
      <AppShell destination="library" navGroups={GROUPS} paletteItems={[]}>
        <div data-testid="surface">surface</div>
      </AppShell>,
    )
    const ws = workspace(container)
    expect(ws).not.toHaveClass('pov-workspace--immersive')
    expect(ws).toHaveClass('pov-workspace--rail-collapsed')
    expect(screen.getByRole('navigation', { name: /console destinations/i })).toBeInTheDocument()
    expect(window.localStorage.getItem('cortexsim.railCollapsed')).toBe('true')
  })
})

describe('AppShell — Escape is consumed only when it closed an overlay', () => {
  // Fires Escape on <body> so it bubbles through document (AppShell's
  // listener) and on to window, where an immersive surface listens.
  function pressEscape() {
    const seenAtWindow = []
    const onWindow = (e) => { seenAtWindow.push(e.defaultPrevented) }
    window.addEventListener('keydown', onWindow)
    const ev = createEvent.keyDown(document.body, { key: 'Escape' })
    fireEvent(document.body, ev)
    window.removeEventListener('keydown', onWindow)
    return { ev, seenAtWindow }
  }
  const palette = () => screen.getByRole('dialog', { name: 'Command palette', hidden: true })

  it('with the palette open (⌘K), Escape closes it and is defaultPrevented — a window listener sees that', () => {
    renderShell({ destination: 'composer', immersive: true })
    fireEvent.keyDown(document.body, { key: 'k', metaKey: true })
    expect(palette()).toHaveClass('is-open')

    const { ev, seenAtWindow } = pressEscape()

    expect(palette()).not.toHaveClass('is-open')
    expect(ev.defaultPrevented).toBe(true)
    expect(seenAtWindow).toEqual([true])
  })

  it('with the help overlay open (⌘/), Escape closes it and is defaultPrevented', () => {
    renderShell()
    fireEvent.keyDown(document.body, { key: '/', metaKey: true })
    expect(screen.getByRole('dialog', { name: /help & keyboard shortcuts/i })).toBeInTheDocument()

    const { ev } = pressEscape()

    expect(screen.queryByRole('dialog', { name: /help & keyboard shortcuts/i })).toBeNull()
    expect(ev.defaultPrevented).toBe(true)
  })

  it('with nothing open, Escape is NOT defaultPrevented — it belongs to the surface', () => {
    renderShell({ destination: 'composer', immersive: true })
    expect(palette()).not.toHaveClass('is-open')

    const { ev, seenAtWindow } = pressEscape()

    expect(ev.defaultPrevented).toBe(false)
    expect(seenAtWindow).toEqual([false])
  })

  it('a second Escape after closing the palette is the surface\'s again', () => {
    renderShell({ destination: 'composer', immersive: true })
    fireEvent.keyDown(document.body, { key: 'k', metaKey: true })
    expect(pressEscape().ev.defaultPrevented).toBe(true)
    expect(pressEscape().ev.defaultPrevented).toBe(false)
  })
})
