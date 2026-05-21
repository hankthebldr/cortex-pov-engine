/**
 * Tests for useScenarioRunHistory hook + formatAgo helper.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import useScenarioRunHistory, { formatAgo } from '../console/useScenarioRunHistory.js'
import { installRoutes } from '../../test/mockFetch.js'

void React

function Probe() {
  const { historyByScenario, loading } = useScenarioRunHistory()
  if (loading) return <div>loading</div>
  return (
    <div>
      {Array.from(historyByScenario.entries()).map(([sid, h]) => (
        <div key={sid} data-testid={`row-${sid}`}>
          {sid}|{h.count}|{h.lastStatus}|{h.lastRunId}
        </div>
      ))}
    </div>
  )
}

describe('useScenarioRunHistory', () => {
  it('rolls runs up by scenario_id', async () => {
    installRoutes({
      'GET /api/runs': [
        { id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'completed', started_at: '2026-05-01T10:00:00Z' },
        { id: 'r-2', scenario_id: 'SIM-EDR-001', status: 'failed',    started_at: '2026-05-03T10:00:00Z' },
        { id: 'r-3', scenario_id: 'SIM-CDR-002', status: 'running',   started_at: '2026-05-05T10:00:00Z' },
      ],
    })
    render(<Probe />)
    await waitFor(() => {
      expect(screen.getByTestId('row-SIM-EDR-001')).toBeInTheDocument()
    })
    // SIM-EDR-001 should have 2 runs and last status = failed (most recent)
    expect(screen.getByTestId('row-SIM-EDR-001').textContent).toBe('SIM-EDR-001|2|failed|r-2')
    expect(screen.getByTestId('row-SIM-CDR-002').textContent).toBe('SIM-CDR-002|1|running|r-3')
  })

  it('handles empty run list', async () => {
    installRoutes({ 'GET /api/runs': [] })
    render(<Probe />)
    await waitFor(() => {
      expect(screen.queryByText(/loading/)).toBeNull()
    })
    // No rows rendered
    expect(document.querySelectorAll('[data-testid^="row-"]').length).toBe(0)
  })

  it('handles error response gracefully', async () => {
    installRoutes({ 'GET /api/runs': new Response('boom', { status: 500 }) })
    render(<Probe />)
    await waitFor(() => {
      expect(screen.queryByText(/loading/)).toBeNull()
    })
    expect(document.querySelectorAll('[data-testid^="row-"]').length).toBe(0)
  })

  it('handles {runs: [...]} wrapper shape', async () => {
    installRoutes({
      'GET /api/runs': [
        { id: 'r-9', scenario_id: 'SIM-NDR-003', status: 'completed', started_at: '2026-05-02T00:00:00Z' },
      ],
    })
    render(<Probe />)
    await waitFor(() => {
      expect(screen.getByTestId('row-SIM-NDR-003')).toBeInTheDocument()
    })
  })

  it('skips runs missing scenario_id', async () => {
    installRoutes({
      'GET /api/runs': [
        { id: 'r-1', status: 'completed', started_at: '2026-05-01T10:00:00Z' }, // no scenario_id
        { id: 'r-2', scenario_id: 'SIM-EDR-001', status: 'completed', started_at: '2026-05-01T10:00:00Z' },
      ],
    })
    render(<Probe />)
    await waitFor(() => {
      expect(screen.getByTestId('row-SIM-EDR-001')).toBeInTheDocument()
    })
    expect(document.querySelectorAll('[data-testid^="row-"]').length).toBe(1)
  })
})

describe('formatAgo', () => {
  it('returns empty string for falsy input', () => {
    expect(formatAgo(null)).toBe('')
    expect(formatAgo(undefined)).toBe('')
    expect(formatAgo(0)).toBe('')
  })

  it('reports "just now" for <60s', () => {
    expect(formatAgo(Date.now() - 5_000)).toBe('just now')
  })

  it('reports minutes', () => {
    expect(formatAgo(Date.now() - 5 * 60 * 1000)).toBe('5m ago')
  })

  it('reports hours', () => {
    expect(formatAgo(Date.now() - 3 * 60 * 60 * 1000)).toBe('3h ago')
  })

  it('reports days', () => {
    expect(formatAgo(Date.now() - 4 * 24 * 60 * 60 * 1000)).toBe('4d ago')
  })

  it('reports months', () => {
    expect(formatAgo(Date.now() - 90 * 24 * 60 * 60 * 1000)).toBe('3mo ago')
  })

  it('clamps future timestamps to "just now"', () => {
    expect(formatAgo(Date.now() + 60_000)).toBe('just now')
  })
})
