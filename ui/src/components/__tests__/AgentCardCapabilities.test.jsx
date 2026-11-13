/**
 * Agent cards — what each pull agent can do, and whether it runs steps as the
 * service accounts a scenario declares.
 *
 * What was observably wrong on base (f809f140): Manage › Agents showed
 * liveness, host, OS and last-seen, but not the agent's capabilities, although
 * GET /api/agents returns them on every row. A Windows beacon registers
 * ["shell","powershell","artifact-fetch"] and does not claim identity-harness,
 * so every non-direct step identity collapses to `direct` on that host. A DC
 * choosing a target could not see that until the launch preflight.
 *
 * jsdom only — colour and layout are checked in a real browser, not here.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import TargetsView from '../console/TargetsView.jsx'

void React

const naiveUtc = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace('Z', '')

function row(over) {
  return {
    hostname: 'host',
    os: 'linux',
    status: 'online',
    registered_at: naiveUtc(600_000),
    last_seen: naiveUtc(3_000),
    last_seen_age_seconds: 3,
    ...over,
  }
}

function mount(agents) {
  installRoutes({
    'GET /api/infra/bundles': { bundles: [] },
    'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 },
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), agents }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}

function card(id) { return screen.getByText(id).closest('.target-card') }

const HONOURED = 'Runs steps as their declared service accounts'
const NOT_HONOURED = 'Identity not honoured — steps run as the agent\'s own user'

describe('Agent cards · capabilities and execution identity', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('an agent with identity-harness lists its capabilities and says steps run as their service accounts', () => {
    mount([row({ agent_id: 'lin-jb', capabilities: ['shell', 'identity-harness', 'artifact-fetch'] })])
    const c = card('lin-jb')
    const list = within(c).getByRole('list', { name: 'Capabilities' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent))
      .toEqual(['shell', 'identity-harness', 'artifact-fetch'])
    expect(c.textContent).toContain(HONOURED)
    expect(c.textContent).not.toContain(NOT_HONOURED)
  })

  it('a Windows beacon without identity-harness lists what it has and says identity is not honoured', () => {
    mount([row({ agent_id: 'win-jb', os: 'windows', capabilities: ['shell', 'powershell', 'artifact-fetch'] })])
    const c = card('win-jb')
    const list = within(c).getByRole('list', { name: 'Capabilities' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent))
      .toEqual(['shell', 'powershell', 'artifact-fetch'])
    expect(c.textContent).toContain(NOT_HONOURED)
    expect(c.textContent).not.toContain(HONOURED)
  })

  it('a Windows row that claims identity-harness still says identity is not honoured', () => {
    // POST /api/agents/enroll defaults an empty roster to
    // ["shell","identity-harness"]; the Windows executor collapses every
    // non-direct identity to `direct` whatever the row says.
    mount([row({ agent_id: 'win-claims', os: 'windows', capabilities: ['shell', 'identity-harness', 'powershell'] })])
    const c = card('win-claims')
    expect(c.textContent).toContain(NOT_HONOURED)
    expect(c.textContent).not.toContain(HONOURED)
  })

  it.each([
    ['missing', undefined],
    ['empty', []],
  ])('an agent whose capabilities are %s says "Capabilities not reported" and claims nothing', (_label, caps) => {
    mount([row({ agent_id: 'legacy-jb', capabilities: caps })])
    const c = card('legacy-jb')
    expect(c.textContent).toContain('Capabilities not reported')
    expect(within(c).queryByRole('list', { name: 'Capabilities' })).toBeNull()
    expect(c.textContent).not.toContain(HONOURED)
  })
})
