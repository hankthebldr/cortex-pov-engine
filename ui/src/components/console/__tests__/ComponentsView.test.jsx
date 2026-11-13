import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, waitFor, cleanup, fireEvent } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../../context/EnvironmentContext.jsx'
import ComponentsView from '../ComponentsView.jsx'
import { __resetComponentSources } from '../useComponentStatus.js'

void React

/**
 * The Components page (design v26 §9.2): the hub for the eight components,
 * rebuilt on real state only. The minimal pass retired the old page because
 * every row on it was seed data; this one reads SimCore or says it cannot.
 */
function mount(env = {}, props = {}) {
  const onNavigate = vi.fn()
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, ...env }}>
      <ComponentsView onNavigate={onNavigate} params={{}} {...props} />
    </EnvironmentContext.Provider>,
  )
  return { onNavigate }
}

const row = (name) => screen.getByRole('listitem', { name })

describe('Components page', () => {
  beforeEach(() => {
    __resetComponentSources()
    installRoutes({
      'GET /api/collector-targets': {
        targets: [{ name: 'lab', kind: 'xsiam_http_collector', is_default: true, last_check: { verdict: 'refused', code: 'HTTP_401' } }],
      },
      'GET /api/ngfw': { firewalls: [], write_enabled: false },
    })
  })
  afterEach(cleanup)

  it('lists the eight components with a status each', async () => {
    mount({ healthModel: { reachable: true, overall: 'ok', degraded: [], gaps: [] } })
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Components')
    const items = screen.getAllByRole('listitem')
    expect(items.map((li) => li.getAttribute('aria-label'))).toEqual([
      'POVengine', 'Agents', 'Tenant', 'HTTP collector', 'Broker VM', 'Firewall', 'XDR Collector', 'Cortex Engine',
    ])
    expect(within(row('POVengine')).getByRole('img', { name: 'Up' })).toBeInTheDocument()
    await waitFor(() => expect(within(row('HTTP collector')).getByRole('img', { name: 'Down' })).toBeInTheDocument())
  })

  it('never paints a component SimCore cannot see as healthy', async () => {
    mount()
    for (const name of ['Broker VM', 'XDR Collector', 'Cortex Engine']) {
      expect(within(row(name)).queryByRole('img', { name: 'Up' }), name).toBeNull()
    }
    expect(within(row('XDR Collector')).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
  })

  it('a problem is an error badge that opens the page that fixes it', async () => {
    const { onNavigate } = mount({ agents: [{ agent_id: 'a1', hostname: 'a1', status: 'offline' }] })
    const badge = within(row('Agents')).getByRole('button', { name: /none online/i })
    fireEvent.click(badge)
    expect(onNavigate).toHaveBeenCalledWith('component-agents', { tab: 'setup' })
  })

  it('Configure opens where each component is configured today', async () => {
    const { onNavigate } = mount()
    fireEvent.click(within(row('Firewall')).getByRole('button', { name: 'Configure' }))
    expect(onNavigate).toHaveBeenLastCalledWith('component-firewall', { tab: 'setup' })
    fireEvent.click(within(row('Broker VM')).getByRole('button', { name: 'Configure' }))
    expect(onNavigate).toHaveBeenLastCalledWith('start', { step: 'bvm' })
  })

  it('offers no button where there is nothing to open yet', async () => {
    mount()
    for (const name of ['XDR Collector', 'Cortex Engine']) {
      expect(within(row(name)).queryAllByRole('button'), name).toHaveLength(0)
    }
  })

  it('?open= marks the component it names', async () => {
    mount({}, { params: { open: 'bvm' } })
    expect(row('Broker VM')).toHaveAttribute('aria-current', 'true')
    expect(row('Agents')).not.toHaveAttribute('aria-current')
  })
})
