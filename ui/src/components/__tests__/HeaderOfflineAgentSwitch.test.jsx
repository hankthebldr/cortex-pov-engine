/**
 * #341 — the header's active agent is offline while another agent is online.
 *
 * The active-agent pointer lives in the browser, and its stale-pointer guard
 * falls back only when the agent is DELETED. An agent that stopped polling
 * stayed active, so launches targeted a beacon that could not take them. The
 * header now says "<id> offline" and offers Switch to <the online agent> —
 * the most recently seen when several are online. It never switches on its
 * own: the pointer moves only on the click.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act, fireEvent, cleanup } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import ConsoleHeader from '../console/ConsoleHeader.jsx'
import { onlineAlternative } from '../console/setupProgress.js'
import { EnvironmentProvider, useEnvironment } from '../../context/EnvironmentContext.jsx'

void React

const row = (id, hostname, status, age) => ({
  agent_id: id, hostname, os: 'linux', status,
  registered_at: '2026-11-14T09:00:00', last_seen: '2026-11-14T10:00:00',
  last_seen_age_seconds: age, capabilities: ['shell'],
})
const dockerOff = row('docker-desktop', 'docker-desktop', 'offline', 5400)
const target = row('pov-target-01-4dcdce', 'pov-target-01', 'online', 4)
const older = row('lab-02', 'lab-02', 'online', 25)
const alsoOff = row('lab-03', 'lab-03', 'offline', 9000)

afterEach(() => { cleanup(); vi.useRealTimers() })

const switchButton = () => screen.queryByRole('button', { name: /^Switch to / })

describe('onlineAlternative', () => {
  it('names the online agent when the active one is offline', () => {
    expect(onlineAlternative(dockerOff, [dockerOff, target])).toBe(target)
  })

  it('picks the most recently seen of several online agents', () => {
    expect(onlineAlternative(dockerOff, [older, dockerOff, target])).toBe(target)
    expect(onlineAlternative(dockerOff, [target, older].map((a) => a === target ? { ...a, last_seen_age_seconds: 40 } : a)).agent_id)
      .toBe('lab-02')
  })

  it('offers nothing when the active agent is online, absent, or no other is online', () => {
    expect(onlineAlternative(target, [dockerOff, target, older])).toBeNull()
    expect(onlineAlternative(null, [target])).toBeNull()
    expect(onlineAlternative(dockerOff, [dockerOff, alsoOff])).toBeNull()
  })
})

describe('ConsoleHeader — an offline active agent', () => {
  it('says "<id> offline" and offers Switch to the online agent', () => {
    const onSwitchAgent = vi.fn()
    render(<ConsoleHeader health={{}} agent={dockerOff} agents={[dockerOff, target]} onSwitchAgent={onSwitchAgent} />)
    expect(screen.getByTestId('header-agent')).toHaveTextContent('docker-desktop')
    expect(screen.getByTestId('header-agent')).toHaveTextContent('offline')
    const btn = switchButton()
    expect(btn).toHaveTextContent('Switch to pov-target-01')
    expect(onSwitchAgent).not.toHaveBeenCalled()
    fireEvent.click(btn)
    expect(onSwitchAgent).toHaveBeenCalledWith('pov-target-01-4dcdce')
  })

  it('offers the most recently seen when several are online', () => {
    render(<ConsoleHeader health={{}} agent={dockerOff} agents={[older, dockerOff, target]} onSwitchAgent={() => {}} />)
    expect(switchButton()).toHaveTextContent('Switch to pov-target-01')
  })

  it('offers no switch when no other agent is online, or the active one is online', () => {
    const { unmount } = render(<ConsoleHeader health={{}} agent={dockerOff} agents={[dockerOff, alsoOff]} onSwitchAgent={() => {}} />)
    expect(switchButton()).toBeNull()
    unmount()
    render(<ConsoleHeader health={{}} agent={target} agents={[dockerOff, target]} onSwitchAgent={() => {}} />)
    expect(switchButton()).toBeNull()
  })
})

describe('EnvironmentProvider — the pointer moves only on the click', () => {
  function Probe() {
    const { agent, agents, setAgent } = useEnvironment()
    return <ConsoleHeader health={{}} agent={agent} agents={agents} onSwitchAgent={setAgent} />
  }

  it('keeps the offline agent active until Switch is clicked, then points at the online one', async () => {
    vi.useFakeTimers()
    window.localStorage.clear()
    window.localStorage.setItem('cortexsim.activeAgent', 'docker-desktop')
    installRoutes({
      'GET /api/agents': { agents: [dockerOff, target] },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/runs': { runs: [] },
      'GET /api/health': { status: 'ok' },
      'GET /api/xsiam/tenants': [],
    })
    render(<EnvironmentProvider runPollMs={10_000}><Probe /></EnvironmentProvider>)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByTestId('header-agent')).toHaveTextContent('docker-desktop')
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    // Still the offline one: nothing switched on its own.
    expect(screen.getByTestId('header-agent')).toHaveTextContent('docker-desktop')

    await act(async () => { fireEvent.click(switchButton()) })
    expect(screen.getByTestId('header-agent')).toHaveTextContent('pov-target-01')
    expect(screen.getByTestId('header-agent').getAttribute('data-liveness')).toBe('online')
    expect(window.localStorage.getItem('cortexsim.activeAgent')).toBe('pov-target-01-4dcdce')
    expect(switchButton()).toBeNull()
  })
})
