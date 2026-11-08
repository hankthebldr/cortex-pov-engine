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
import { render, screen, fireEvent } from '@testing-library/react'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import AgentSwitcher from '../console/GlobalContextBar/AgentSwitcher.jsx'

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
