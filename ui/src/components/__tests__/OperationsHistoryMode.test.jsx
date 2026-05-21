/**
 * Tests for the Operations history-mode chip strip.
 *
 * Verifies that the strip renders the three modes with their counts
 * and that clicking a chip flips the filter so the grid changes.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import OperationsView from '../console/OperationsView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const scenarios = [
  { scenario_id: 'SIM-EDR-001', name: 'AWS Cred Hunt',  plane: 'EDR' },
  { scenario_id: 'SIM-EDR-002', name: 'Linux Persistence', plane: 'EDR' },
  { scenario_id: 'SIM-EDR-003', name: 'Fresh untouched',    plane: 'EDR' },
]

// Two of the three have run history.
const runs = [
  { id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'completed', started_at: '2026-05-10T10:00:00Z' },
  { id: 'r-2', scenario_id: 'SIM-EDR-002', status: 'completed', started_at: '2026-05-11T10:00:00Z' },
]

beforeEach(() => {
  installRoutes({
    'GET /api/scenarios': { scenarios },
    'GET /api/runs': runs,
  })
})

describe('OperationsView history mode strip', () => {
  it('renders all three modes with correct counts', async () => {
    render(<OperationsView />)
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
    })
    const strip = document.querySelector('.ops-history-strip')
    expect(strip).toBeTruthy()
    // 3 buttons: All, Never run, Already run
    const buttons = strip.querySelectorAll('button')
    expect(buttons.length).toBe(3)
    expect(buttons[0].textContent).toMatch(/All/)
    expect(buttons[0].textContent).toMatch(/3/)         // 3 total
    expect(buttons[1].textContent).toMatch(/Never run/)
    expect(buttons[1].textContent).toMatch(/1/)         // SIM-EDR-003
    expect(buttons[2].textContent).toMatch(/Already run/)
    expect(buttons[2].textContent).toMatch(/2/)         // SIM-EDR-001 + 002
  })

  it('clicking "Never run" filters out scenarios with history', async () => {
    render(<OperationsView />)
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
    })
    const neverBtn = Array.from(document.querySelectorAll('.ops-history-strip button'))
      .find((b) => /Never run/.test(b.textContent))
    fireEvent.click(neverBtn)
    await waitFor(() => {
      // Only the untouched scenario remains
      expect(screen.queryByText('AWS Cred Hunt')).toBeNull()
      expect(screen.queryByText('Linux Persistence')).toBeNull()
      expect(screen.getByText('Fresh untouched')).toBeInTheDocument()
    })
  })

  it('clicking "Already run" filters out untouched scenarios', async () => {
    render(<OperationsView />)
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
    })
    const runBtn = Array.from(document.querySelectorAll('.ops-history-strip button'))
      .find((b) => /Already run/.test(b.textContent))
    fireEvent.click(runBtn)
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
      expect(screen.getByText('Linux Persistence')).toBeInTheDocument()
      expect(screen.queryByText('Fresh untouched')).toBeNull()
    })
  })

  it('"All" mode restores the full set', async () => {
    render(<OperationsView />)
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
    })
    const buttons = document.querySelectorAll('.ops-history-strip button')
    fireEvent.click(buttons[1]) // Never run
    await waitFor(() => {
      expect(screen.queryByText('AWS Cred Hunt')).toBeNull()
    })
    fireEvent.click(buttons[0]) // All
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
      expect(screen.getByText('Linux Persistence')).toBeInTheDocument()
      expect(screen.getByText('Fresh untouched')).toBeInTheDocument()
    })
  })
})
