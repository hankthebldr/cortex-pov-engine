/**
 * SimCore serialises `datetime.utcnow().isoformat()` — UTC with NO zone
 * designator — and JavaScript reads a zone-less date-time as LOCAL time. CI
 * runs in UTC, where that error is zero, so these tests pin a non-UTC zone:
 * every DC outside UTC (i.e. all of them) was seeing shifted times, a dead
 * beacon reading "live" in NAM and a live one reading "stale" in APAC.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, renderHook } from '@testing-library/react'
import { parseServerTime } from '../../api/time.js'
import TargetsView from '../console/TargetsView.jsx'
import EventStream from '../console/EventStream.jsx'
import { EnvironmentContext, DEFAULT_ENV, EnvironmentProvider, useEnvironment } from '../../context/EnvironmentContext.jsx'
import useScenarioRunHistory, { formatAgo } from '../console/useScenarioRunHistory.js'
import { installRoutes } from '../../test/mockFetch.js'

void React

/** A zone-less ISO string for `msAgo` before now, exactly as SimCore emits it. */
function naiveUtc(msAgo) {
  return new Date(Date.now() - msAgo).toISOString().replace('Z', '')
}

let savedTz
beforeEach(() => {
  savedTz = process.env.TZ
  if (typeof window !== 'undefined' && 'EventSource' in window) delete window.EventSource
})
afterEach(() => {
  if (savedTz === undefined) delete process.env.TZ
  else process.env.TZ = savedTz
})

describe('parseServerTime', () => {
  it('reads a zone-less SimCore timestamp as UTC, whatever the browser zone', () => {
    process.env.TZ = 'America/Los_Angeles'
    expect(parseServerTime('2026-10-04T12:00:00')).toBe(Date.UTC(2026, 9, 4, 12, 0, 0))
    expect(parseServerTime('2026-10-04T12:00:00.250000')).toBe(Date.UTC(2026, 9, 4, 12, 0, 0, 250))
    process.env.TZ = 'Asia/Tokyo'
    expect(parseServerTime('2026-10-04T12:00:00')).toBe(Date.UTC(2026, 9, 4, 12, 0, 0))
  })

  it('leaves a zoned timestamp alone and keeps garbage NaN', () => {
    process.env.TZ = 'Asia/Tokyo'
    expect(parseServerTime('2026-10-04T12:00:00Z')).toBe(Date.UTC(2026, 9, 4, 12))
    expect(parseServerTime('2026-10-04T12:00:00+02:00')).toBe(Date.UTC(2026, 9, 4, 10))
    expect(Number.isNaN(parseServerTime('not a time'))).toBe(true)
    expect(Number.isNaN(parseServerTime(null))).toBe(true)
  })
})

describe('agent liveness on the Agents surface', () => {
  function renderAgents(agents) {
    return render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, agents }}>
        <TargetsView />
      </EnvironmentContext.Provider>,
    )
  }

  it('a beacon silent for 10 minutes reads stale for a DC in NAM (not live, not 0s ago)', () => {
    process.env.TZ = 'America/Los_Angeles'
    renderAgents([{ agent_id: 'jb-dead', hostname: 'jb', last_seen: naiveUtc(10 * 60_000) }])
    const card = screen.getByText('jb-dead').closest('.target-card')
    expect(card.querySelector('.target-card__pill--stale')).not.toBeNull()
    expect(card.querySelector('.target-card__pill--live')).toBeNull()
    expect(card.textContent).toContain('seen 10m ago')
  })

  it('a beacon that checked in 5 s ago reads live for a DC in APAC (not stale, not hours ago)', () => {
    process.env.TZ = 'Asia/Tokyo'
    renderAgents([{ agent_id: 'jb-live', hostname: 'jb', last_seen: naiveUtc(5_000) }])
    const card = screen.getByText('jb-live').closest('.target-card')
    expect(card.querySelector('.target-card__pill--live')).not.toBeNull()
    expect(card.querySelector('.target-card__pill--stale')).toBeNull()
    expect(card.textContent).toMatch(/seen \ds ago/)
  })
})

describe('active-run elapsed time', () => {
  it('is minutes, not hours off, for a run SimCore stamped 3 minutes ago', async () => {
    process.env.TZ = 'America/Los_Angeles'
    installRoutes({
      'GET /api/runs': { runs: [{ run_id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'running', started_at: naiveUtc(180_000) }] },
      'GET /api/scenarios': { scenarios: [] },
      'GET /api/agents': { agents: [] },
      'GET /api/health': { status: 'ok' },
      'GET /api/credentials/xsiam': { tenants: [] },
    })
    const wrapper = ({ children }) => <EnvironmentProvider runPollMs={60_000}>{children}</EnvironmentProvider>
    const { result } = renderHook(() => useEnvironment(), { wrapper })
    await waitFor(() => expect(result.current.activeRun).not.toBeNull())
    expect(result.current.activeRun.elapsed).toBeGreaterThanOrEqual(179)
    expect(result.current.activeRun.elapsed).toBeLessThan(240)
  })
})

describe('run-history "ago" and the event log clock', () => {
  it('the Library history badge says a run 2 h ago happened 2h ago, not just now', async () => {
    process.env.TZ = 'America/Los_Angeles'
    installRoutes({
      'GET /api/runs': { runs: [{ run_id: 'r-old', scenario_id: 'SIM-EDR-001', status: 'complete', started_at: naiveUtc(2 * 3600_000) }] },
    })
    const { result } = renderHook(() => useScenarioRunHistory())
    await waitFor(() => expect(result.current.historyByScenario.get('SIM-EDR-001')).toBeTruthy())
    expect(formatAgo(result.current.historyByScenario.get('SIM-EDR-001').lastRunAt)).toBe('2h ago')
  })

  it('event lines print the UTC clock SimCore stamped, not one shifted by the browser zone', async () => {
    process.env.TZ = 'Asia/Tokyo'
    installRoutes({
      'GET /api/runs/r-tz': {
        id: 'r-tz', scenario_id: 'SIM-EDR-001', status: 'running',
        started_at: '2026-05-20T22:00:00', steps: [],
      },
    })
    render(<EventStream runId="r-tz" />)
    await waitFor(() => expect(screen.getByText(/run started/i)).toBeInTheDocument())
    expect(screen.getByText('22:00:00')).toBeInTheDocument()
  })
})

