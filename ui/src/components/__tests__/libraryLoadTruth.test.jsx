/**
 * Library list failures. The Library destination wires OperationsView's
 * `onError` to a no-op, and the list fetch folded a failure into [] — so a
 * 500 or a timeout on /api/scenarios rendered "No scenarios match the current
 * filter." while /api/health was fine and no global banner showed. A failed
 * fetch must read as a failure, never as "no data".
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import OperationsView from '../console/OperationsView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

describe('Library — a failed list fetch is not an empty library', () => {
  it('names the failure and offers a retry instead of "No scenarios match"', async () => {
    let fail = true
    installRoutes({
      'GET /api/scenarios': () => (fail
        ? new Response(JSON.stringify({ detail: { error: 'scenario store unavailable', code: 'DB_ERROR' } }),
          { status: 500, headers: { 'content-type': 'application/json' } })
        : { scenarios: [{ scenario_id: 'SIM-EDR-001', name: 'AWS Cred Hunt', plane: 'EDR' }] }),
      'GET /api/runs': { runs: [] },
    })
    render(<OperationsView />)
    const alert = await screen.findByTestId('library-load-error')
    expect(alert.textContent).toContain('scenario store unavailable')
    expect(screen.queryByText(/No scenarios match/)).toBeNull()

    fail = false
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(screen.getByText('AWS Cred Hunt')).toBeInTheDocument())
    expect(screen.queryByTestId('library-load-error')).toBeNull()
  })

  it('a slow answer for the previous plane never replaces the current plane\'s list', async () => {
    let releaseEdr
    installRoutes({
      'GET /api/scenarios': (url) => {
        const plane = new URL(url).searchParams.get('plane')
        if (plane === 'EDR') {
          return new Promise((resolve) => {
            releaseEdr = () => resolve({ scenarios: [{ scenario_id: 'SIM-EDR-001', name: 'Endpoint one', plane: 'EDR' }] })
          })
        }
        return { scenarios: [{ scenario_id: 'SIM-CDR-001', name: 'Cloud one', plane: 'CDR' }] }
      },
      'GET /api/runs': { runs: [] },
    })
    const { rerender } = render(<OperationsView selectedPlane="EDR" />)
    rerender(<OperationsView selectedPlane="CDR" />)
    await waitFor(() => expect(screen.getByText('Cloud one')).toBeInTheDocument())
    releaseEdr()
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText('Endpoint one')).toBeNull()
    expect(screen.getByText('Cloud one')).toBeInTheDocument()
  })
})
