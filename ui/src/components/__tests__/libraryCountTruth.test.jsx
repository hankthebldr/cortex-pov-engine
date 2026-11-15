/**
 * The Library masthead printed "0 scenarios · loading scenarios…" on every
 * entry: the count was `visibleScenarios.length` whether or not the list had
 * arrived. A zero rendered as a fact is the Gate A5 failure mode — "none
 * loaded yet" and "none exist" must never read the same. And the provider
 * already holds the full corpus, so the first paint can show it instead of
 * refetching into an empty grid.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import OperationsView from '../console/OperationsView.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const two = [
  { scenario_id: 'SIM-EDR-001', name: 'Alpha card', plane: 'EDR' },
  { scenario_id: 'SIM-EDR-002', name: 'Bravo card', plane: 'EDR' },
]

describe('Library — a count is printed only once the list has arrived', () => {
  it('shows "…" while the fetch is in flight, never 0', async () => {
    let release
    installRoutes({
      'GET /api/scenarios': () => new Promise((resolve) => { release = () => resolve({ scenarios: two }) }),
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
    })
    render(<OperationsView />)
    await waitFor(() => expect(typeof release).toBe('function'))
    expect(screen.getByTestId('library-count').textContent).toBe('…')
    await act(async () => { release() })
    await waitFor(() => expect(screen.getByTestId('library-count').textContent).toBe('2'))
  })

  it("paints the provider's list on first render instead of an empty grid", () => {
    installRoutes({
      'GET /api/scenarios': () => new Promise(() => {}), // never answers
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
    })
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, scenarios: two }}>
        <OperationsView />
      </EnvironmentContext.Provider>,
    )
    expect(screen.getByText('Alpha card')).toBeInTheDocument()
    expect(screen.getByTestId('library-count').textContent).toBe('2')
  })
})
