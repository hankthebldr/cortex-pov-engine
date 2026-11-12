import { describe, it, expect, vi, beforeEach } from 'vitest'
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

  // PUSH-09 / UI-03 / UI-04 — the legacy panel had the same bash|k8s-only
  // selector, the same hard-coded `.sh` name, and the same invisible refusal.
  describe('push bundle format', () => {
    let savedAs
    let urls
    const formatOf = (u) => new URL(u, 'http://localhost').searchParams.get('format')

    beforeEach(() => {
      savedAs = []
      urls = []
      URL.createObjectURL = vi.fn(() => 'blob:mock')
      URL.revokeObjectURL = vi.fn()
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
        savedAs.push(this.download)
      })
    })

    it('offers PowerShell and auto beside bash and k8s', async () => {
      installRoutes({ 'GET /api/agents': { agents: [], total: 0 } })
      render(<LaunchPanel scenario={baseScenario} onRunComplete={vi.fn()} onError={vi.fn()} />)
      await userEvent.click(screen.getByRole('button', { name: /Push/ }))
      expect(screen.getByRole('button', { name: /^PowerShell$/ })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^auto$/ })).toBeInTheDocument()
    })

    it('a PowerShell download asks for format=powershell and saves the .ps1 the server names', async () => {
      installRoutes({
        'GET /api/agents': { agents: [], total: 0 },
        'GET /api/scenarios/SIM-EDR-001/download': (url) => {
          urls.push(url)
          return new Response('# ps1\n', {
            status: 200,
            headers: {
              'Content-Disposition': 'attachment; filename="cortexsim-SIM-EDR-001.ps1"',
              'X-CortexSim-Bundle-Target': 'windows',
            },
          })
        },
      })
      render(<LaunchPanel scenario={baseScenario} onRunComplete={vi.fn()} onError={vi.fn()} />)
      await userEvent.click(screen.getByRole('button', { name: /Push/ }))
      await userEvent.click(screen.getByRole('button', { name: /^PowerShell$/ }))
      await userEvent.click(screen.getByRole('button', { name: /Download/ }))
      await waitFor(() => expect(savedAs).toEqual(['cortexsim-SIM-EDR-001.ps1']))
      expect(formatOf(urls[0])).toBe('powershell')
      expect(screen.getByText(/PowerShell 5\.1/)).toBeInTheDocument()
    })

    it('a refused download is rendered beside the button, with code and step ids', async () => {
      installRoutes({
        'GET /api/agents': { agents: [], total: 0 },
        'GET /api/scenarios/SIM-EDR-001/download': () => new Response(JSON.stringify({
          detail: {
            error: 'Scenario cannot emit a bundle for this target',
            code: 'BUNDLE_TARGET_UNSATISFIABLE',
            detail: 'target=windows scenario=SIM-EDR-001 unresolved=[step-02 (WINDOWS_COMMAND_UNAVAILABLE)] '
              + '— declare platform_variants.windows on those steps',
          },
        }), { status: 409, headers: { 'content-type': 'application/json' } }),
      })
      render(<LaunchPanel scenario={baseScenario} onRunComplete={vi.fn()} onError={vi.fn()} />)
      await userEvent.click(screen.getByRole('button', { name: /Push/ }))
      await userEvent.click(screen.getByRole('button', { name: /^PowerShell$/ }))
      await userEvent.click(screen.getByRole('button', { name: /Download/ }))
      const alert = await screen.findByRole('alert')
      expect(alert).toHaveTextContent('BUNDLE_TARGET_UNSATISFIABLE')
      expect(alert).toHaveTextContent('step-02')
      expect(savedAs).toEqual([])
    })
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
