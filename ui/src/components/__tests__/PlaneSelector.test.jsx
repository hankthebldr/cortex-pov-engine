import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PlaneSelector from '../PlaneSelector.jsx'
import { installRoutes, scenarioFixtures } from '../../test/mockFetch.js'

describe('<PlaneSelector />', () => {
  it('renders the six known planes with counts derived from the scenarios API', async () => {
    installRoutes({
      'GET /api/scenarios': { scenarios: scenarioFixtures, total: scenarioFixtures.length },
    })
    const onSelect = vi.fn()
    render(<PlaneSelector selectedPlane={null} onSelectPlane={onSelect} />)

    // All six labels are rendered immediately
    for (const label of ['EDR', 'CDR', 'NDR', 'ITDR', 'Cloud App', 'Analytics']) {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument()
    }

    // Count badges appear after the fetch resolves.  EDR has 2 fixtures, others 1.
    await waitFor(() => {
      const edrBtn = screen.getByRole('button', { name: /EDR/ })
      expect(edrBtn).toHaveTextContent('2')
    })
  })

  it('fires onSelectPlane with the plane id when a plane button is clicked', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [], total: 0 } })
    const onSelect = vi.fn()
    render(<PlaneSelector selectedPlane={null} onSelectPlane={onSelect} />)

    await userEvent.click(screen.getByRole('button', { name: /CDR/ }))
    expect(onSelect).toHaveBeenCalledWith('CDR')
  })

  it('shows a "Clear filter" button when a plane is already selected', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [], total: 0 } })
    render(<PlaneSelector selectedPlane="NDR" onSelectPlane={vi.fn()} />)
    expect(await screen.findByRole('button', { name: /Clear filter/ })).toBeInTheDocument()
  })

  it('degrades to zero counts when the API errors', async () => {
    installRoutes({
      'GET /api/scenarios': () =>
        new Response('boom', { status: 500 }),
    })
    render(<PlaneSelector selectedPlane={null} onSelectPlane={vi.fn()} />)
    // After the error resolves, no count > 0 should be present
    await waitFor(() => {
      const edrBtn = screen.getByRole('button', { name: /EDR/ })
      // The "·" loading dot is replaced by 0 once loading completes
      expect(edrBtn.textContent).toMatch(/EDR.*0/s)
    })
  })
})
