/**
 * AgentSwitcher — the header pill must not call an agent healthy before a
 * beacon has polled.
 *
 * On base the dot read `agent.status` alone, so a row the installer had only
 * enrolled (SimCore stamps it 'online' at enrolment, with last_seen ==
 * registered_at) showed a healthy dot in every console surface's header.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import AgentSwitcher from '../console/GlobalContextBar/AgentSwitcher.jsx'
import TenantSwitcher from '../console/GlobalContextBar/TenantSwitcher.jsx'

void React

const T = '2026-11-05T10:00:00.250000'
const enrolled = { agent_id: 'jb-new', hostname: 'jb-new-host', os: 'linux', status: 'online', registered_at: T, last_seen: T, last_seen_age_seconds: 3 }
const winEnrolled = { agent_id: 'win-new', hostname: 'WIN-NEW', os: 'windows', status: 'online', capabilities: ['shell'], registered_at: '2026-11-05T10:00:00', last_seen: '2026-11-05T10:00:09', last_seen_age_seconds: 2 }
const offline = { agent_id: 'jb-off', hostname: 'jb-off-host', os: 'linux', status: 'offline', last_seen_age_seconds: 900 }
const online = { agent_id: 'jb-on', hostname: 'jb-on-host', os: 'linux', status: 'online', capabilities: ['shell', 'identity-harness'], registered_at: '2026-11-05T09:00:00', last_seen: '2026-11-05T10:00:00', last_seen_age_seconds: 2 }

function mount(agents, active) {
  render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, agents, agent: active }}>
      <AgentSwitcher />
    </EnvironmentContext.Provider>,
  )
  return screen.getByRole('button', { name: /^Agent:/ })
}

function option(label) {
  return screen.getAllByRole('option').find((o) => o.textContent.includes(label))
}

describe('AgentSwitcher liveness', () => {
  it('an enrolled-only active agent is a warning dot, not a healthy one', () => {
    const pill = mount([enrolled], enrolled)
    expect(pill.querySelector('.env-pill__dot--warn')).not.toBeNull()
  })

  it('the menu names the waiting state, and colours offline as bad', () => {
    const pill = mount([enrolled, winEnrolled, offline, online], online)
    fireEvent.click(pill)
    expect(option('jb-new-host').textContent).toContain('waiting for first beacon poll')
    expect(option('jb-new-host').querySelector('.env-pill__dot--warn')).not.toBeNull()
    expect(option('WIN-NEW').querySelector('.env-pill__dot--warn')).not.toBeNull()
    expect(option('jb-off-host').querySelector('.env-pill__dot--bad')).not.toBeNull()
    const on = option('jb-on-host').querySelector('.switcher__option-dot')
    expect(on.className).not.toMatch(/--warn|--bad/)
  })
})

// UI-22, as filed: this pill said liveness with dot colour alone — an empty
// span with a --warn / --bad class, under an aria-label of `Agent: <name>`.
//
// NOTE: nothing in the console mounts AgentSwitcher (only this file imports
// it). These cases keep the component honest for whoever mounts it; the header
// a DC actually sees is ConsoleHeader, pinned in HeaderAgentLiveness.test.jsx.
describe('AgentSwitcher — liveness is said in words, not only colour (UI-22)', () => {
  it('the accessible name carries the state, for every state', () => {
    expect(mount([offline], offline).getAttribute('aria-label')).toBe('Agent: jb-off-host — offline')
    cleanup()
    expect(mount([online], online).getAttribute('aria-label')).toBe('Agent: jb-on-host — online')
    cleanup()
    expect(mount([enrolled], enrolled).getAttribute('aria-label'))
      .toBe('Agent: jb-new-host — enrolled — waiting for first beacon poll')
  })

  it('a beacon that is not online shows a short state word beside the dot', () => {
    const pill = mount([offline], offline)
    const word = pill.querySelector('.env-pill__state')
    expect(word).not.toBeNull()
    expect(word.textContent).toBe('offline')
    expect(word.className).toContain('env-pill__state--bad')
  })

  it('an online beacon stays quiet on screen: no extra word, the name still says online', () => {
    const pill = mount([online], online)
    expect(pill.querySelector('.env-pill__state')).toBeNull()
    expect(pill.getAttribute('aria-label')).toMatch(/online$/)
  })

  it('the dot is decoration to assistive tech, and carries a shape hook per state', () => {
    const pill = mount([offline], offline)
    const dot = pill.querySelector('.env-pill__dot')
    expect(dot.getAttribute('aria-hidden')).toBe('true')
    expect(dot.getAttribute('data-status')).toBe('bad')
  })

  it('every option reads its state, including an online one whose dot is the only visual cue', () => {
    const pill = mount([offline, online, enrolled], online)
    fireEvent.click(pill)
    expect(option('jb-on-host').textContent).toMatch(/online/)
    expect(option('jb-off-host').textContent).toMatch(/offline/)
    // Not said twice where the meta line already shows it.
    expect(option('jb-off-host').textContent.match(/offline/g)).toHaveLength(1)
    for (const o of screen.getAllByRole('option')) {
      expect(o.querySelector('.env-pill__dot').getAttribute('aria-hidden')).toBe('true')
    }
  })
})

describe('TenantSwitcher — a tenant with no reported status is not a green dot (UI-22)', () => {
  const mountTenant = (env) => {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, ...env }}>
        <TenantSwitcher />
      </EnvironmentContext.Provider>,
    )
    return screen.getByRole('button', { name: /^Tenant:/ })
  }

  it('no status is a neutral dot and says "status not reported"', () => {
    const t = { name: 'lab', base_url: 'https://api-lab.xdr.example' }
    const pill = mountTenant({ tenants: [t], tenant: t, health: null })
    const dot = pill.querySelector('.env-pill__dot')
    expect(dot.className).toContain('env-pill__dot--none')
    expect(pill.getAttribute('aria-label')).toBe('Tenant: lab — status not reported')
  })

  it('a reported status is named in the accessible name', () => {
    const t = { name: 'lab', status: 'degraded' }
    const pill = mountTenant({ tenants: [t], tenant: t, health: null })
    expect(pill.getAttribute('aria-label')).toBe('Tenant: lab — degraded')
    expect(pill.querySelector('.env-pill__state').textContent).toBe('degraded')
  })
})
