/**
 * Smoke + interaction tests for EventStream.
 *
 * The hook is exercised via the polling fallback path — we don't mock
 * the EventSource because jsdom in this env doesn't ship one, so the
 * hook falls through to /api/runs/:id polling, which we mock with
 * installRoutes.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import EventStream from '../console/EventStream.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const runFixture = {
  id: 'r-test',
  scenario_id: 'SIM-MP-004',
  mode: 'pull',
  status: 'running',
  started_at: '2026-05-20T22:00:00Z',
  current_step: 2,
  steps: [
    {
      id: 'step-01',
      mitre_technique: 'T1552.001',
      identity: 'www-data',
      executed_at: '2026-05-20T22:00:10Z',
      status: 'done',
    },
    {
      id: 'step-02',
      mitre_technique: 'T1078.004',
      identity: 'www-data',
      status: 'pending',
    },
  ],
  results: [
    {
      id: 1,
      step_index: 0,
      plane: 'EDR',
      detection_type: 'BIOC',
      expected_description: 'AKIA grep by www-data',
      observed_at: '2026-05-20T22:00:30Z',
      mttd_seconds: 20,
    },
  ],
}

beforeEach(() => {
  // EventSource isn't available in jsdom — leave it undefined so the
  // hook falls through to the polling path immediately.
  if (typeof window !== 'undefined' && 'EventSource' in window) {
    delete window.EventSource
  }
})

describe('<EventStream />', () => {
  it('renders empty state when no runId', () => {
    render(<EventStream runId={null} />)
    expect(screen.getByText(/no active run/i)).toBeInTheDocument()
  })

  it('renders the head with title + filters + controls', () => {
    installRoutes({ 'GET /api/runs/r-test': runFixture })
    render(<EventStream runId="r-test" />)
    expect(screen.getByText(/agent.*stdout/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /info/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /detect/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /pause/i })).toBeInTheDocument()
  })

  it('emits synthetic events from the polling fallback', async () => {
    installRoutes({ 'GET /api/runs/r-test': runFixture })
    render(<EventStream runId="r-test" />)
    await waitFor(() => {
      // The synthetic projector includes [SYNTH] in every message.
      expect(screen.getByText(/run started/i)).toBeInTheDocument()
    }, { timeout: 4000 })
    expect(screen.getAllByText(/SYNTH/).length).toBeGreaterThan(0)
  })

  it('level filter toggle hides events of that level', async () => {
    installRoutes({ 'GET /api/runs/r-test': runFixture })
    render(<EventStream runId="r-test" />)
    await waitFor(() => {
      expect(screen.getByText(/run started/i)).toBeInTheDocument()
    }, { timeout: 4000 })

    // Toggle off "info" — the "run started" line is info-level
    const infoBtn = screen.getByRole('button', { name: /^info$/i })
    expect(infoBtn).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(infoBtn)
    expect(infoBtn).toHaveAttribute('aria-pressed', 'false')
    // The line should no longer be visible
    expect(screen.queryByText(/run started/i)).not.toBeInTheDocument()
  })

  it('pause button toggles to "resume"', async () => {
    installRoutes({ 'GET /api/runs/r-test': runFixture })
    render(<EventStream runId="r-test" />)
    const pause = screen.getByRole('button', { name: /pause/i })
    fireEvent.click(pause)
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
  })

  it('clear button empties the visible buffer', async () => {
    installRoutes({ 'GET /api/runs/r-test': runFixture })
    render(<EventStream runId="r-test" />)
    await waitFor(() => {
      expect(screen.getByText(/run started/i)).toBeInTheDocument()
    }, { timeout: 4000 })
    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    expect(screen.queryByText(/run started/i)).not.toBeInTheDocument()
  })
})
