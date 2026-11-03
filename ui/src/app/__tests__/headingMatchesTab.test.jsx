/**
 * The h1 is the tab label. One surface carried three names — rail "Simulate",
 * h1 "Library", tab "Scenarios"; rail "Catalog", h1 "TTP Cards", tab
 * "TTP cards" — so a DC reading the rail could not predict the page title,
 * and copy across the console used all three. The rule: the rail names the
 * TASK, the tab names the PAGE, and the page's h1 repeats the tab verbatim.
 * This guard reads the label from the registry so a rename in one place
 * fails here instead of drifting.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { sectionFor } from '../destinations.jsx'
import OperationsView from '../../components/console/OperationsView.jsx'
import TtpBrowserView from '../../components/console/TtpBrowserView.jsx'

void React

const tabLabel = (id) => sectionFor(id).tabs.find(([tab]) => tab === id)[1]

describe('page h1 is the tab label', () => {
  it('Simulate › Scenarios', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [] }, 'GET /api/runs': { runs: [] }, 'GET /api/agents': { agents: [] } })
    render(<OperationsView />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('library')))
  })

  it('Catalog › TTP cards', async () => {
    installRoutes({ 'GET /api/ttps': { ttps: [] } })
    render(<TtpBrowserView />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(tabLabel('ttps')))
  })
})
