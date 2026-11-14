import React, { useState } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, cleanup } from '@testing-library/react'
import { installRoutes } from '../../../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../../../context/EnvironmentContext.jsx'
import { __resetComponentSources } from '../../useComponentStatus.js'
import { COMPONENT_ABOUT, componentAbout } from '../../componentAbout.js'
import ComponentPage from '../ComponentPage.jsx'
import PovengineComponent from '../PovengineComponent.jsx'
import AgentsComponent from '../AgentsComponent.jsx'
import TenantComponent from '../TenantComponent.jsx'
import HttpCollectorComponent from '../HttpCollectorComponent.jsx'
import FirewallComponent from '../FirewallComponent.jsx'
import { BrokerVmComponent, XdrCollectorComponent, CortexEngineComponent } from '../ConfirmedComponents.jsx'

void React

/**
 * Every component page explains itself (design v33 CHANGE C7): four plain
 * lines under the header — What it is · Why the POV needs it · What it
 * unlocks · Where it lives — on all eight pages and on every tab.
 */
function Routed({ Comp, initial }) {
  const [params, setP] = useState(initial)
  const setParams = (p) => setP((prev) => {
    const next = { ...prev, ...p }
    Object.keys(next).forEach((k) => { if (next[k] == null) delete next[k] })
    return next
  })
  return <Comp params={params} setParams={setParams} onNavigate={vi.fn()} />
}

function mount(Comp, params = {}) {
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), refreshTenants: vi.fn() }}>
      <Routed Comp={Comp} initial={params} />
    </EnvironmentContext.Provider>,
  )
}

const LABELS = ['What it is', 'Why the POV needs it', 'What it unlocks', 'Where it lives']

const PAGES = [
  ['pov', 'POVengine', PovengineComponent],
  ['agent', 'Agents', AgentsComponent],
  ['tenant', 'Tenant', TenantComponent],
  ['http', 'HTTP collector', HttpCollectorComponent],
  ['bvm', 'Broker VM', BrokerVmComponent],
  ['ngfw', 'Firewall', FirewallComponent],
  ['xdrc', 'XDR Collector', XdrCollectorComponent],
  ['engine', 'Cortex Engine', CortexEngineComponent],
]

function aboutLines() {
  const about = screen.getByRole('region', { name: 'About this component' })
  const terms = within(about).getAllByRole('term').map((n) => n.textContent)
  const defs = within(about).getAllByRole('definition').map((n) => n.textContent)
  return { terms, defs }
}

beforeEach(() => {
  window.localStorage.clear()
  __resetComponentSources()
  installRoutes({
    'GET /api/collector-targets': { targets: [] },
    'GET /api/ngfw': { firewalls: [], write_enabled: false },
    'GET /api/agents/install/attempts': { attempts: [] },
    'GET /api/infra/bundles': { bundles: [] },
    'GET /api/credentials/integrations': { integrations: [] },
  })
})
afterEach(cleanup)

describe('component pages explain themselves', () => {
  it.each(PAGES)('%s: the %s page opens with its four lines', (key, name, Comp) => {
    mount(Comp)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(name)
    const { terms, defs } = aboutLines()
    expect(terms).toEqual(LABELS)
    const a = COMPONENT_ABOUT[key]
    expect(defs).toEqual([a.what, a.why, a.unlocks, a.where])
    defs.forEach((d) => expect(d.trim().length).toBeGreaterThan(0))
  })

  it.each(PAGES)('%s: the four lines stay on the Setup tab', (key, _name, Comp) => {
    mount(Comp, { tab: 'setup' })
    expect(aboutLines().defs[0]).toBe(COMPONENT_ABOUT[key].what)
  })
})

describe('an id with no About entry', () => {
  const base = {
    name: 'Demo',
    icon: 'people',
    status: { status: 'up', detail: 'All fine' },
    setup: () => null,
    params: {},
    setParams: () => {},
  }

  it.each([['an unknown id', 'demo'], ['a prototype key', 'constructor'], ['no id at all', undefined]])(
    '%s renders the page without an About block',
    (_what, componentKey) => {
      render(<ComponentPage {...base} componentKey={componentKey} />)
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Demo')
      expect(screen.getByRole('group', { name: 'Component pages' })).toBeInTheDocument()
      expect(screen.queryByRole('region', { name: 'About this component' })).toBeNull()
      expect(componentAbout(componentKey)).toBeNull()
    },
  )
})
