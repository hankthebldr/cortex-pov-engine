/**
 * The header breadcrumb: journey › page › object (design C9).
 *
 *   1. breadcrumbFor against the real destination registry: which crumbs a
 *      route gets and where each one leads.
 *   2. <Breadcrumb>: every crumb but the last is a link that navigates; the
 *      last is the current page and is not a link.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'

import { breadcrumbFor } from '../breadcrumb.js'
import Breadcrumb from '../../components/console/Breadcrumb.jsx'

void React

const labels = (crumbs) => crumbs.map((c) => c.label)
const targets = (crumbs) => crumbs.map((c) => c.destination || null)

describe('breadcrumbFor — journey › page › object, from the registry', () => {
  it('an open run: Run & observe › Runs › Run <id>', () => {
    const crumbs = breadcrumbFor('runs', { run: 'r-42', tab: 'live' })
    expect(labels(crumbs)).toEqual(['Run & observe', 'Runs', 'Run r-42'])
    // The journey leads to its first rail page; the last crumb leads nowhere.
    expect(targets(crumbs)).toEqual(['runs', 'runs', null])
  })

  it('a run id is shortened to eight characters; the whole id stays in the title', () => {
    const crumbs = breadcrumbFor('proof', { run: 'ac5c9515-a8b3-496d-927f-5af92fe4e6bc' })
    expect(labels(crumbs)).toEqual(['Run & observe', 'Results', 'Run ac5c9515'])
    expect(crumbs[2].title).toBe('Run ac5c9515-a8b3-496d-927f-5af92fe4e6bc')
  })

  it('a page with nothing open ends on the page', () => {
    expect(labels(breadcrumbFor('runs'))).toEqual(['Run & observe', 'Runs'])
    expect(labels(breadcrumbFor('runs', { status: 'failed', page: '2' }))).toEqual(['Run & observe', 'Runs'])
    expect(labels(breadcrumbFor('runs', { compare: '1' }))).toEqual(['Run & observe', 'Runs', 'Compare runs'])
  })

  it('an open scenario, from Scenarios, the Composer and the Launch gate', () => {
    expect(labels(breadcrumbFor('library', { open: 'SIM-EDR-001' })))
      .toEqual(['Compose & simulate', 'Scenarios', 'Scenario SIM-EDR-001'])
    expect(labels(breadcrumbFor('composer', { from: 'SIM-EDR-001' })))
      .toEqual(['Compose & simulate', 'Build a chain', 'Scenario SIM-EDR-001'])
    // `guided` is an alias of the Launch gate since the launch gate MVP, so its
    // crumb is the Launch gate's and is not named twice.
    const guided = breadcrumbFor('guided', { arm: 'SIM-EDR-001' })
    expect(labels(guided)).toEqual(['Compose & simulate', 'Launch gate', 'Scenario SIM-EDR-001'])
    expect(targets(guided)).toEqual(['library', 'guided', null])
    // The launch is the Launch gate (#299); the old guided route only redirects there.
    const gate = breadcrumbFor('preflight', { arm: 'SIM-EDR-001' })
    expect(labels(gate)).toEqual(['Compose & simulate', 'Launch gate', 'Scenario SIM-EDR-001'])
    expect(targets(gate)).toEqual(['library', 'preflight', null])
  })

  it('a component: its page sits under Components, in Onboard & connect', () => {
    const page = breadcrumbFor('component-agents', { tab: 'setup' })
    expect(labels(page)).toEqual(['Onboard & connect', 'Components', 'Agents'])
    expect(targets(page)).toEqual(['start', 'components', null])
    expect(labels(breadcrumbFor('components', { open: 'ngfw' })))
      .toEqual(['Onboard & connect', 'Components', 'Firewall'])
  })

  it('a tab names its rail item first: Catalog › UC / TC index', () => {
    const tc = breadcrumbFor('uctc', { tc: 'TC-EDR-03' })
    expect(labels(tc)).toEqual(['Reference', 'Catalog', 'UC / TC index', 'Test case TC-EDR-03'])
    expect(targets(tc)).toEqual(['ttps', 'ttps', 'uctc', null])
    // Dashboards is a rail item of its own now (design C8), not a Results tab.
    expect(labels(breadcrumbFor('coverage'))).toEqual(['Run & observe', 'Dashboards'])
    expect(labels(breadcrumbFor('ttps', { open: 'TTP-2026-0001' })))
      .toEqual(['Reference', 'Catalog', 'TTP card TTP-2026-0001'])
  })

  it('an alias that shares its rail item\'s name is not named twice', () => {
    expect(labels(breadcrumbFor('readiness'))).toEqual(['Compose & simulate', 'Launch gate'])
  })

  it('an unknown destination has no crumbs', () => {
    expect(breadcrumbFor('no-such-page')).toEqual([])
  })
})

describe('<Breadcrumb>', () => {
  const crumbs = breadcrumbFor('runs', { run: 'r-42', tab: 'live' })

  it('every crumb but the last is a link with a real href; the last is the current page', () => {
    render(<Breadcrumb crumbs={crumbs} onNavigate={() => {}} />)
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((a) => a.textContent)).toEqual(['Run & observe', 'Runs'])
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#/runs', '#/runs'])

    const current = within(nav).getByText('Run r-42')
    expect(current).toHaveAttribute('aria-current', 'page')
    expect(current.closest('a')).toBeNull()
  })

  it('a click navigates through the router; a modified click is left to the browser', () => {
    const onNavigate = vi.fn()
    render(<Breadcrumb crumbs={breadcrumbFor('uctc', { tc: 'TC-EDR-03' })} onNavigate={onNavigate} />)
    const catalog = screen.getByRole('link', { name: 'Catalog' })

    fireEvent.click(catalog)
    expect(onNavigate).toHaveBeenCalledWith('ttps', null)

    onNavigate.mockClear()
    fireEvent.click(screen.getByRole('link', { name: 'UC / TC index' }), { metaKey: true })
    expect(onNavigate).not.toHaveBeenCalled()
  })

  it('renders nothing without crumbs', () => {
    const { container } = render(<Breadcrumb crumbs={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
