/**
 * The header's agent chip says whether the active beacon is polling (UI-22).
 *
 * UI-22 was filed against GlobalContextBar/AgentSwitcher. That component is not
 * mounted anywhere in the console: the header a DC actually sees is
 * ConsoleHeader, whose agent chip drew the SAME green dot for any selected
 * agent — polling, stale, offline, or enrolled and never polled. Its only
 * other state was grey for "no agent". So the one always-visible read-out of
 * the active beacon could not show that it had gone offline mid-POV, in colour
 * or otherwise, and the liveness fix round 1 made to AgentSwitcher changed
 * nothing on screen.
 *
 * It now reads the one liveness model (setupProgress.agentLiveness) and says
 * the state three ways: a word beside the name when the beacon is not online,
 * a dot whose SHAPE differs per state, and the accessible name.
 *
 * It can only be as fresh as the provider's agent list, which was fetched once
 * and refreshed only while the Agents surface was open. The provider now
 * re-reads agents on the tick it already uses for runs and health.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import ConsoleHeader from '../console/ConsoleHeader.jsx'
import { EnvironmentProvider, useEnvironment } from '../../context/EnvironmentContext.jsx'

void React

const T = '2026-11-10T10:00:00.250000'
const online = { agent_id: 'jb-on', hostname: 'jb-on-host', os: 'linux', status: 'online', registered_at: '2026-11-10T09:00:00', last_seen: '2026-11-10T10:00:00', last_seen_age_seconds: 2 }
// A Windows beacon that polled and then stopped: it registered its own
// capabilities, so it is offline, not "enrolled".
const offline = { ...online, agent_id: 'jb-off', hostname: 'WIN22', os: 'windows', status: 'offline', capabilities: ['shell', 'powershell', 'artifact-fetch'], last_seen_age_seconds: 900 }
const stale = { ...online, agent_id: 'jb-st', hostname: 'jb-st-host', status: 'stale', last_seen_age_seconds: 95 }
const enrolled = { agent_id: 'jb-new', hostname: 'jb-new-host', os: 'linux', status: 'online', registered_at: T, last_seen: T, last_seen_age_seconds: 3 }

const chip = (agent) => {
  render(<ConsoleHeader health={{ version: '1.1.0' }} agent={agent} />)
  return screen.getByTestId('header-agent')
}

afterEach(() => { vi.useRealTimers() })

describe('ConsoleHeader — the agent chip carries liveness, not just "an agent is selected"', () => {
  it('an offline beacon is named offline, in a word, a shape and the accessible name', () => {
    const c = chip(offline)
    expect(c.getAttribute('data-liveness')).toBe('offline')
    expect(c.querySelector('.pov-chip__state').textContent).toBe('offline')
    expect(c.getAttribute('aria-label')).toMatch(/WIN22/)
    expect(c.getAttribute('aria-label')).toMatch(/offline/)
    const dot = c.querySelector('.pov-chip__dot')
    expect(dot.className).toContain('pov-chip__dot--down')
    expect(dot.getAttribute('aria-hidden')).toBe('true')
  })

  it('stale and enrolled-only are warnings with their own word; enrolled says what it waits for', () => {
    const s = chip(stale)
    expect(s.querySelector('.pov-chip__state').textContent).toBe('stale')
    expect(s.querySelector('.pov-chip__dot').className).toContain('pov-chip__dot--ring')
    s.remove()
    const e = chip(enrolled)
    expect(e.getAttribute('data-liveness')).toBe('enrolled')
    expect(e.querySelector('.pov-chip__state').textContent).toBe('enrolled')
    expect(e.getAttribute('title')).toMatch(/waiting for first beacon poll/)
    expect(e.querySelector('.pov-chip__dot').className).not.toMatch(/pov-chip__dot--(down|off)\b/)
  })

  it('an online beacon stays quiet on screen and still says online to assistive tech', () => {
    const c = chip(online)
    expect(c.querySelector('.pov-chip__state')).toBeNull()
    expect(c.querySelector('.pov-chip__dot').className.trim()).toBe('pov-chip__dot')
    expect(c.getAttribute('aria-label')).toMatch(/online/)
  })

  it('an unrecognised status is not drawn as healthy', () => {
    const c = chip({ ...online, status: 'zombie' })
    expect(c.getAttribute('data-liveness')).toBe('unknown')
    expect(c.querySelector('.pov-chip__dot').className).toContain('pov-chip__dot--off')
    expect(c.querySelector('.pov-chip__state').textContent).toBe('unknown')
  })

  it('no agent keeps the grey dot and says so', () => {
    const c = chip(null)
    expect(c.getAttribute('data-liveness')).toBe('none')
    expect(c.textContent).toMatch(/no agent selected/)
    expect(c.querySelector('.pov-chip__dot').className).toContain('pov-chip__dot--off')
  })
})

describe('EnvironmentProvider — the agent list is kept current for the header', () => {
  function Probe() {
    const { agent } = useEnvironment()
    return <ConsoleHeader health={{}} agent={agent} />
  }

  it('re-reads agents on its poll tick, so a beacon that goes offline shows within one tick', async () => {
    vi.useFakeTimers()
    window.localStorage.clear()
    let rows = [online]
    installRoutes({
      'GET /api/agents': () => ({ agents: rows }),
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/health': { status: 'ok' },
      'GET /api/xsiam/tenants': [],
    })
    render(<EnvironmentProvider runPollMs={10_000}><Probe /></EnvironmentProvider>)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByTestId('header-agent').getAttribute('data-liveness')).toBe('online')

    rows = [{ ...online, status: 'offline', last_seen_age_seconds: 400 }]
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(screen.getByTestId('header-agent').getAttribute('data-liveness')).toBe('offline')
  })
})
