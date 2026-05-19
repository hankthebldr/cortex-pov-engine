import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import LaunchPanel from '../LaunchPanel.jsx'
import { installRoutes } from '../../test/mockFetch.js'

const baseScenario = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential Dumping',
  plane: 'EDR',
  pull_supported: true,
  push_supported: true,
  execution_identity: {
    default: 'www-data',
    options: ['www-data', 'root', 'nobody'],
  },
}

describe('<LaunchPanel />', () => {
  it('shows an empty-state when no scenario is selected', () => {
    installRoutes({ 'GET /api/agents': { agents: [], total: 0 } })
    render(<LaunchPanel scenario={null} onRunComplete={vi.fn()} onError={vi.fn()} />)
    expect(screen.getByText(/Select a scenario/)).toBeInTheDocument()
  })

  it('warns when in pull mode but no agents are connected and disables Launch', async () => {
    installRoutes({ 'GET /api/agents': { agents: [], total: 0 } })
    render(<LaunchPanel scenario={baseScenario} onRunComplete={vi.fn()} onError={vi.fn()} />)
    expect(await screen.findByText(/No agents connected/)).toBeInTheDocument()

    const launchBtn = screen.getByRole('button', { name: /Launch Run/ })
    expect(launchBtn).toBeDisabled()
  })

  it('launches a pull run with the selected identity and agent', async () => {
    installRoutes({
      'GET /api/agents': {
        agents: [
          { id: 'agent-1', hostname: 'lab-host-1', os: 'linux' },
          { id: 'agent-2', hostname: 'lab-host-2', os: 'linux' },
        ],
        total: 2,
      },
      'POST /api/run': async (_url, init) => {
        const body = JSON.parse(init.body)
        return new Response(
          JSON.stringify({ run_id: 'r-123', id: 'r-123', mode: body.mode, echoed: body }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        )
      },
    })

    const onRunComplete = vi.fn()
    render(<LaunchPanel scenario={baseScenario} onRunComplete={onRunComplete} onError={vi.fn()} />)

    // Wait for agents to load, then change identity to "root".
    // Option text is `${hostname} (${os})`, so match by substring.
    await screen.findByText(/lab-host-1/)
    await userEvent.selectOptions(screen.getByLabelText(/Execution Identity/), 'root')

    await userEvent.click(screen.getByRole('button', { name: /Launch Run/ }))
    await waitFor(() => expect(onRunComplete).toHaveBeenCalled())
    const launchPayload = onRunComplete.mock.calls[0][0].echoed
    expect(launchPayload).toMatchObject({
      scenario_id: 'SIM-EDR-001',
      mode: 'pull',
      identity: 'root',
      target_agent_id: 'agent-1',
    })
  })

  it('switching to push mode hides agent picker and exposes format toggle', async () => {
    installRoutes({ 'GET /api/agents': { agents: [], total: 0 } })
    render(<LaunchPanel scenario={baseScenario} onRunComplete={vi.fn()} onError={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Push/ }))
    expect(screen.getByRole('button', { name: /bash/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /k8s/ })).toBeInTheDocument()
    expect(screen.queryByText(/No agents connected/)).not.toBeInTheDocument()
  })

  it('surfaces launch failures via onError', async () => {
    installRoutes({
      'GET /api/agents': {
        agents: [{ id: 'agent-x', hostname: 'lab-host-error', os: 'linux' }],
        total: 1,
      },
      'POST /api/run': () =>
        new Response(JSON.stringify({ detail: 'orchestrator down' }), {
          status: 422,
          headers: { 'content-type': 'application/json' },
        }),
    })
    const onError = vi.fn()
    render(<LaunchPanel scenario={baseScenario} onRunComplete={vi.fn()} onError={onError} />)
    // Option text is `hostname (os)` — match the substring rather than exact.
    await screen.findByText(/lab-host-error/)
    await userEvent.click(screen.getByRole('button', { name: /Launch Run/ }))
    await waitFor(() => expect(onError).toHaveBeenCalledWith('orchestrator down'))
  })

  it('disables modes the scenario does not support', () => {
    installRoutes({ 'GET /api/agents': { agents: [], total: 0 } })
    render(
      <LaunchPanel
        scenario={{ ...baseScenario, push_supported: false }}
        onRunComplete={vi.fn()}
        onError={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /Push/ })).toBeDisabled()
  })
})
