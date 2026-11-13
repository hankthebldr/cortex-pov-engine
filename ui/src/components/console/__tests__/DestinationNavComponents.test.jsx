import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import DestinationNav from '../DestinationNav.jsx'
import { navGroups } from '../../../app/destinations.jsx'

void React

/**
 * The rail's Components item (design v26 §1, §5): a numbered journey heading,
 * an expandable submenu of the eight components, a status on each child that
 * is a shape and a word (never colour alone), and "N issues" on the group.
 */
const STATUS = {
  pov: { status: 'up' },
  agent: { status: 'down', detail: 'none online' },
  tenant: { status: 'off' },
  http: { status: 'degraded' },
  bvm: { status: 'unknown', word: 'You confirmed' },
  ngfw: { status: 'unknown', word: 'Not blocked' },
  xdrc: { status: 'unknown', word: "Can't check" },
  engine: { status: 'unknown', word: "Can't check" },
}

function renderNav(props = {}) {
  const onNavigate = vi.fn()
  const groups = navGroups({ componentIssues: '2 issues' }, { components: STATUS })
  render(<DestinationNav groups={groups} active="start" onNavigate={onNavigate} {...props} />)
  return { onNavigate }
}

describe('rail — journeys and the Components submenu', () => {
  it('numbers the three journeys and labels Reference without one', () => {
    renderNav()
    const nav = screen.getByRole('navigation', { name: /console destinations/i })
    for (const [num, label] of [['1', 'Onboard & connect'], ['2', 'Compose & simulate'], ['3', 'Run & observe']]) {
      const head = within(nav).getByText(label).closest('.pov-rail__head')
      expect(head).toHaveTextContent(num + label)
    }
    expect(within(nav).getByText('Reference')).toBeInTheDocument()
  })

  it('shows the issue count on Components', () => {
    renderNav()
    expect(screen.getByTestId('dest-button-components')).toHaveTextContent('2 issues')
  })

  it('starts collapsed away from the components, and the chevron opens it', () => {
    renderNav()
    const toggle = screen.getByRole('button', { name: /show components/i })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: 'Components' })).toBeNull()
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: /hide components/i })).toHaveAttribute('aria-expanded', 'true')
    const group = screen.getByRole('group', { name: 'Components' })
    expect(within(group).getAllByRole('button')).toHaveLength(8)
  })

  it('opens by itself on a component page and marks that component current', () => {
    renderNav({ active: 'components', activeChild: 'agent' })
    const group = screen.getByRole('group', { name: 'Components' })
    const agents = within(group).getByRole('button', { name: /agents/i })
    expect(agents).toHaveAttribute('aria-current', 'page')
  })

  it('gives every child its status as an icon with a word, never colour alone', () => {
    renderNav({ active: 'components' })
    const group = screen.getByRole('group', { name: 'Components' })
    expect(within(group).getByRole('img', { name: 'Up' })).toBeInTheDocument()
    expect(within(group).getByRole('img', { name: 'Down' })).toBeInTheDocument()
    expect(within(group).getByRole('img', { name: 'Off' })).toBeInTheDocument()
    expect(within(group).getByRole('img', { name: 'Degraded' })).toBeInTheDocument()
    expect(within(group).getByRole('img', { name: 'You confirmed' })).toBeInTheDocument()
    expect(within(group).getAllByRole('img', { name: "Can't check" })).toHaveLength(2)
  })

  it('a child navigates to its own page', () => {
    const { onNavigate } = renderNav({ active: 'components' })
    const group = screen.getByRole('group', { name: 'Components' })
    fireEvent.click(within(group).getByRole('button', { name: /firewall/i }))
    expect(onNavigate).toHaveBeenCalledWith('component-firewall', undefined)
    fireEvent.click(within(group).getByRole('button', { name: /broker vm/i }))
    expect(onNavigate).toHaveBeenLastCalledWith('components', { open: 'bvm' })
  })

  it('the Components label itself opens the Components page', () => {
    const { onNavigate } = renderNav()
    fireEvent.click(screen.getByTestId('dest-button-components'))
    expect(onNavigate).toHaveBeenCalledWith('components')
  })
})
