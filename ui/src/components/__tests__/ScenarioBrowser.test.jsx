import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ScenarioBrowser from '../ScenarioBrowser.jsx'
import { installRoutes, scenarioFixtures } from '../../test/mockFetch.js'

function setup(props = {}, routes = {}) {
  installRoutes({
    'GET /api/scenarios': { scenarios: scenarioFixtures, total: scenarioFixtures.length },
    ...routes,
  })
  const onSelectScenario = vi.fn()
  const utils = render(
    <ScenarioBrowser
      selectedPlane={null}
      selectedScenario={null}
      onSelectScenario={onSelectScenario}
      {...props}
    />,
  )
  return { onSelectScenario, ...utils }
}

describe('<ScenarioBrowser />', () => {
  it('renders one row per scenario from the API', async () => {
    setup()
    await waitFor(() => {
      expect(screen.getByText('Credential Dumping')).toBeInTheDocument()
      expect(screen.getByText('SIM-EDR-001')).toBeInTheDocument()
      expect(screen.getByText('Kerberoast')).toBeInTheDocument()
    })
  })

  it('filters client-side by free-text query across name, id, plane, MITRE tactic', async () => {
    setup()
    await screen.findByText('Credential Dumping')

    const search = screen.getByLabelText(/Filter scenarios/)
    await userEvent.type(search, 'kerber')

    // Kerberoast remains, others gone
    expect(screen.getByText('Kerberoast')).toBeInTheDocument()
    expect(screen.queryByText('Credential Dumping')).not.toBeInTheDocument()
  })

  it('shows an empty-state with a clear-search button when the filter matches nothing', async () => {
    setup()
    await screen.findByText('Credential Dumping')

    await userEvent.type(screen.getByLabelText(/Filter scenarios/), 'zzz-no-match')
    expect(screen.getByText(/No scenarios found/)).toBeInTheDocument()
    const clear = screen.getByRole('button', { name: /Clear search/ })
    await userEvent.click(clear)
    expect(screen.getByText('Credential Dumping')).toBeInTheDocument()
  })

  it('passes the plane filter to the API when selectedPlane is set', async () => {
    const fetchSpy = installRoutes({
      'GET /api/scenarios': { scenarios: scenarioFixtures, total: scenarioFixtures.length },
    })
    render(
      <ScenarioBrowser selectedPlane="EDR" selectedScenario={null} onSelectScenario={vi.fn()} />,
    )
    await waitFor(() => {
      const calledUrls = fetchSpy.mock.calls.map(([u]) => u)
      expect(calledUrls.some((u) => u.includes('plane=EDR'))).toBe(true)
    })
  })

  it('calls onSelectScenario when a row is clicked', async () => {
    const { onSelectScenario } = setup()
    const row = await screen.findByText('Credential Dumping')
    await userEvent.click(row)
    expect(onSelectScenario).toHaveBeenCalledTimes(1)
    expect(onSelectScenario.mock.calls[0][0].scenario_id).toBe('SIM-EDR-001')
  })

  it('surfaces the error message when the API request fails', async () => {
    installRoutes({
      'GET /api/scenarios': () =>
        new Response(
          JSON.stringify({ detail: 'scenarios offline', error: 'X', code: 'X' }),
          { status: 500, headers: { 'content-type': 'application/json' } },
        ),
    })
    render(
      <ScenarioBrowser selectedPlane={null} selectedScenario={null} onSelectScenario={vi.fn()} />,
    )
    expect(await screen.findByText('scenarios offline')).toBeInTheDocument()
  })
})
