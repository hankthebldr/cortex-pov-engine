/**
 * Library selection race. handleSelect set the summary, armed the id, then
 * awaited the detail fetch with no guard. Click A (slow detail) then B (fast):
 * A's detail landed last, so the drawer — and the launch hook and ⌘L, which
 * both read `selected` — targeted A while the operator had picked B. The
 * Launch button would have dispatched the wrong scenario.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import OperationsView from '../console/OperationsView.jsx'
import { installRoutes } from '../../test/mockFetch.js'

void React

const list = [
  { scenario_id: 'SIM-EDR-001', name: 'Alpha card', plane: 'EDR' },
  { scenario_id: 'SIM-EDR-002', name: 'Bravo card', plane: 'EDR' },
]
const detail = (id, name) => ({
  scenario_id: id, name, plane: 'EDR', pull_supported: true, push_supported: true,
  execution_identity: { default: 'www-data', options: ['www-data'] }, steps: [],
})

describe('Library — the newest selection is the one the drawer launches', () => {
  it('a slow detail for an earlier card does not replace the later selection', async () => {
    let releaseAlpha
    const fetchMock = installRoutes({
      'GET /api/scenarios': { scenarios: list },
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [{ agent_id: 'jb-1', status: 'online' }] },
      'GET /api/scenarios/SIM-EDR-001': () => new Promise((resolve) => {
        releaseAlpha = () => resolve(detail('SIM-EDR-001', 'Alpha detail'))
      }),
      'GET /api/scenarios/SIM-EDR-002': detail('SIM-EDR-002', 'Bravo detail'),
      'POST /api/run': { run_id: 'r-1', status: 'queued' },
    })
    render(<OperationsView />)
    await waitFor(() => expect(screen.getByText('Alpha card')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Alpha card'))
    fireEvent.click(screen.getByText('Bravo card'))
    await waitFor(() => expect(document.querySelector('.insp-launch__title').textContent).toBe('Bravo detail'))

    await act(async () => { releaseAlpha() })
    expect(document.querySelector('.insp-launch__title').textContent).toBe('Bravo detail')

    // And the launch the drawer would fire is for B.
    const launchBtn = await waitFor(() => {
      const b = screen.getAllByRole('button', { name: /^Launch/ })[0]
      expect(b).not.toBeDisabled()
      return b
    })
    fireEvent.click(launchBtn)
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([u, init]) => String(u).endsWith('/api/run') && init?.method === 'POST')
      expect(post).toBeTruthy()
      expect(JSON.parse(post[1].body).scenario_id).toBe('SIM-EDR-002')
    })
  })
})
