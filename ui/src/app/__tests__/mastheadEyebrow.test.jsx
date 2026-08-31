/**
 * M-4 — masthead eyebrow copy was inconsistent across seven destinations:
 * nav-group-alone (correct, e.g. "Traffic"/"Manage"), group+phase
 * ("Operate · Phase 2"), group+destination ("Analyze · UC / TC Index"), and
 * destination-alone ("Runs & Proof"). The product has no app-level phase
 * stepper, so "· Phase N" is a reference to something that does not exist.
 * This locks the one chosen pattern (nav group alone, no phase suffix) on
 * the four destinations that deviated from it.
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'

import OperationsView from '../../components/console/OperationsView.jsx'
import TenantManager from '../../components/console/TenantManager.jsx'
import UcTcIndexView from '../../components/console/UcTcIndexView.jsx'
import RunDetailView from '../../components/console/RunDetailView.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'

void React

describe('masthead eyebrow copy — one pattern, no "· Phase N" (M-4)', () => {
  it('OperationsView (Library): "Operate", not "Operate · Phase 2"', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [] }, 'GET /api/agents': [] })
    render(<OperationsView />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /library/i })).toBeInTheDocument())
    expect(screen.getByText('Operate')).toBeInTheDocument()
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()
  })

  it('TenantManager: "Manage", not "Manage · Phase 1"', () => {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, tenants: [], tenant: null }}>
        <TenantManager />
      </EnvironmentContext.Provider>,
    )
    expect(screen.getByText('Manage')).toBeInTheDocument()
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()
  })

  it('UcTcIndexView: "Analyze", not "Analyze · UC / TC Index"', async () => {
    installRoutes({})
    render(<UcTcIndexView />)
    await waitFor(() => expect(screen.getByTestId('uctc-index')).toBeInTheDocument())
    expect(screen.getByText('Analyze')).toBeInTheDocument()
    expect(screen.queryByText(/Analyze · UC \/ TC Index/)).not.toBeInTheDocument()
  })

  it('RunDetailView: "Operate" (its real nav group), not "Runs & Proof"', () => {
    render(<RunDetailView runId="run-1" run={{ scenario_id: 'SIM-EDR-001', status: 'completed' }} />)
    expect(screen.getByText('Operate')).toBeInTheDocument()
    expect(screen.queryByText(/Runs & Proof/)).not.toBeInTheDocument()
  })
})
