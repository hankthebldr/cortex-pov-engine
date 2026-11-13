/**
 * Library list failures. The Library destination wires OperationsView's
 * `onError` to a no-op, and the list fetch folded a failure into [] — so a
 * 500 or a timeout on /api/scenarios rendered "No scenarios match the current
 * filter." while /api/health was fine and no global banner showed. A failed
 * fetch must read as a failure, never as "no data".
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import OperationsView from '../console/OperationsView.jsx'
import { installRoutes } from '../../test/mockFetch.js'
import { getDestination } from '../../app/destinations.jsx'
import { EnvironmentProvider } from '../../context/EnvironmentContext.jsx'

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

  // The failure is rendered inline as an alert. It was ALSO handed to
  // `onError`, which the Library destination renders as a second alert at the
  // top of the page — one fault, announced twice, in two places that then had
  // to be dismissed separately.
  it('reports a list failure once: the inline alert, not a second one through onError', async () => {
    installRoutes({
      'GET /api/scenarios': () => new Response(
        JSON.stringify({ detail: { error: 'scenario store unavailable', code: 'DB_ERROR' } }),
        { status: 500, headers: { 'content-type': 'application/json' } },
      ),
      'GET /api/runs': { runs: [] },
    })
    const onError = vi.fn()
    render(<OperationsView onError={onError} />)
    await screen.findByTestId('library-load-error')
    expect(onError).not.toHaveBeenCalled()
  })

  it('on the Library destination a list failure is exactly one alert', async () => {
    installRoutes({
      'GET /api/scenarios': () => new Response(
        JSON.stringify({ detail: { error: 'scenario store unavailable', code: 'DB_ERROR' } }),
        { status: 500, headers: { 'content-type': 'application/json' } },
      ),
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [] },
      'GET /api/health': { status: 'ok' },
    })
    const Library = getDestination('library').Component
    render(<EnvironmentProvider runPollMs={60_000}><Library params={{}} /></EnvironmentProvider>)
    await screen.findByTestId('library-load-error')
    // Give a second, late alert every chance to mount before counting.
    await new Promise((r) => setTimeout(r, 30))
    const alerts = screen.getAllByRole('alert')
    expect(alerts.map((a) => a.getAttribute('data-testid'))).toEqual(['library-load-error'])
  })

  it('a refused quick-launch is one alert beside the button, with a polite echo — not two alerts', async () => {
    installRoutes({
      'GET /api/scenarios': { scenarios: [{ scenario_id: 'SIM-EDR-001', name: 'AWS Cred Hunt', plane: 'EDR' }] },
      'GET /api/scenarios/SIM-EDR-001': {
        scenario_id: 'SIM-EDR-001', name: 'AWS Cred Hunt', plane: 'EDR', pull_supported: true, push_supported: true,
        execution_identity: { default: 'root', options: ['root'] }, steps: [],
      },
      'GET /api/runs': { runs: [] },
      'GET /api/agents': { agents: [{ agent_id: 'a1', hostname: 'h', os: 'linux', status: 'online' }] },
      'POST /api/run': () => new Response(
        JSON.stringify({ detail: { error: 'Target agent is not enrolled', code: 'TARGET_AGENT_NOT_ENROLLED', detail: { missing_agents: ['a1'] } } }),
        { status: 409, headers: { 'content-type': 'application/json' } },
      ),
    })
    const onError = vi.fn()
    const onSurfaceMessage = vi.fn()
    render(<OperationsView onError={onError} onSurfaceMessage={onSurfaceMessage} />)
    fireEvent.click(await screen.findByText('AWS Cred Hunt'))
    const launchBtn = await screen.findByRole('button', { name: 'Run' })
    await waitFor(() => expect(launchBtn).not.toBeDisabled())
    fireEvent.click(launchBtn)
    const inline = await screen.findByTestId('insp-launch-error')
    expect(inline).toHaveAttribute('role', 'alert')
    expect(onError).not.toHaveBeenCalled()
    expect(onSurfaceMessage).toHaveBeenCalledWith(expect.stringMatching(/not enrolled/i), 'warn')
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
