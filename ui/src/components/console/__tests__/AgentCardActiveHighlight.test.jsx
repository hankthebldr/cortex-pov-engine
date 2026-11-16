/**
 * Components › Agents — the active agent's card is marked by its highlight,
 * not by a word.
 *
 * What was observably wrong: the active card carried an "Active" tag beside
 * the agent id. Read next to the liveness icon, it said "online", even for an
 * agent that was offline. The card keeps its highlight (is-active, and
 * aria-pressed on its select button) and drops the tag.
 *
 * jsdom only — the highlight's colour is checked in a real browser.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../../context/EnvironmentContext.jsx'
import TargetsView from '../TargetsView.jsx'

void React

const naiveUtc = (msAgo) => new Date(Date.now() - msAgo).toISOString().replace('Z', '')

const online = {
  agent_id: 'jb-online', hostname: 'host-a', os: 'linux', status: 'online',
  registered_at: naiveUtc(600_000), last_seen: naiveUtc(3_000), last_seen_age_seconds: 3,
}
const offline = {
  agent_id: 'jb-offline', hostname: 'host-b', os: 'linux', status: 'offline',
  registered_at: naiveUtc(86_400_000), last_seen: naiveUtc(7_200_000), last_seen_age_seconds: 7200,
}

function mount(active) {
  installRoutes({ 'GET /api/agents/install/attempts': { attempts: [], total: 0, retained_max: 100 } })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, refreshAgents: vi.fn(), agents: [online, offline], agent: active }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}

const card = (id) => screen.getAllByText(id).find((el) => el.classList.contains('target-card__title')).closest('.target-card')

describe('Agent cards · the active agent', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('is highlighted, with no "Active" label on the card', () => {
    mount(offline)
    const c = card('jb-offline')
    expect(c).toHaveClass('is-active')
    expect(c.querySelector('.target-card__select')).toHaveAttribute('aria-pressed', 'true')
    expect(within(c).queryByText(/^active$/i)).toBeNull()
  })

  it('still reads offline when it is offline', () => {
    mount(offline)
    expect(within(card('jb-offline')).getByTestId('agent-liveness').textContent).toMatch(/offline/i)
  })

  it('other cards are not highlighted', () => {
    mount(offline)
    const other = card('jb-online')
    expect(other).not.toHaveClass('is-active')
    expect(other.querySelector('.target-card__select')).toHaveAttribute('aria-pressed', 'false')
  })
})
