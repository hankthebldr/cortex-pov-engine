/**
 * Tests for filtering Scenarios by run history.
 *
 * The All / Never run / Already run strip is now the Status filter of the v26
 * filter row (§4.4). Same behaviour: each option carries its count, and
 * choosing one narrows the grid; "Any status" restores it.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
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
  { id: 'r-1', scenario_id: 'SIM-EDR-001', status: 'complete', started_at: '2026-05-10T10:00:00Z' },
  { id: 'r-2', scenario_id: 'SIM-EDR-002', status: 'complete', started_at: '2026-05-11T10:00:00Z' },
]

beforeEach(() => {
  installRoutes({
    'GET /api/scenarios': { scenarios },
    'GET /api/runs': runs,
  })
})

const status = () => screen.getByRole('combobox', { name: 'Status' })

describe('Scenarios — status filter (run history)', () => {
  it('offers Never run and Already run with their counts', async () => {
    render(<OperationsView />)
    await waitFor(() => {
      expect(within(status()).getByRole('option', { name: 'Already run · 2' })).toBeInTheDocument()
    })
    expect(within(status()).getByRole('option', { name: 'Never run · 1' })).toBeInTheDocument()
    expect(within(status()).getByRole('option', { name: 'Any status' })).toBeInTheDocument()
  })

  it('"Never run" filters out scenarios with history', async () => {
    render(<OperationsView />)
    await waitFor(() => expect(within(status()).getByRole('option', { name: 'Never run · 1' })).toBeInTheDocument())
    fireEvent.change(status(), { target: { value: 'never' } })
    await waitFor(() => {
      expect(screen.queryByText('AWS Cred Hunt')).toBeNull()
      expect(screen.queryByText('Linux Persistence')).toBeNull()
      expect(screen.getByText('Fresh untouched')).toBeInTheDocument()
    })
  })

  it('"Already run" filters out untouched scenarios', async () => {
    render(<OperationsView />)
    await waitFor(() => expect(within(status()).getByRole('option', { name: 'Already run · 2' })).toBeInTheDocument())
    fireEvent.change(status(), { target: { value: 'run' } })
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
      expect(screen.getByText('Linux Persistence')).toBeInTheDocument()
      expect(screen.queryByText('Fresh untouched')).toBeNull()
    })
  })

  it('"Any status" restores the full set', async () => {
    render(<OperationsView />)
    await waitFor(() => expect(within(status()).getByRole('option', { name: 'Never run · 1' })).toBeInTheDocument())
    fireEvent.change(status(), { target: { value: 'never' } })
    await waitFor(() => {
      expect(screen.queryByText('AWS Cred Hunt')).toBeNull()
    })
    fireEvent.change(status(), { target: { value: '' } })
    await waitFor(() => {
      expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument()
      expect(screen.getByText('Linux Persistence')).toBeInTheDocument()
      expect(screen.getByText('Fresh untouched')).toBeInTheDocument()
    })
  })
})
