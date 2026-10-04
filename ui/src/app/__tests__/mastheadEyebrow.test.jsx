/**
 * No phase vocabulary on any page.
 *
 * The phase rail ("1 · Scope … 6 · Prove") and the flow bar that restated it
 * are gone; the rail is a task list now. Every page that carried a
 * "Phase 2 · Compose" eyebrow was therefore naming a model the console no
 * longer shows, which is the same drift this guard has always existed to stop
 * — a page describing itself in a vocabulary the navigation does not use.
 *
 * One exception survives unchanged: the run detail's eyebrow carries per-RUN
 * facts so two attempts at the same workflow are distinguishable at a glance.
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

describe('page mastheads carry no phase vocabulary', () => {
  it('OperationsView (Simulate › Scenarios)', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [] }, 'GET /api/agents': [] })
    render(<OperationsView />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /library/i })).toBeInTheDocument())
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()
  })

  it('TenantManager (Tenant, and Get started step 2)', () => {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, tenants: [], tenant: null }}>
        <TenantManager />
      </EnvironmentContext.Provider>,
    )
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()
  })

  it('TenantManager embedded drops its masthead entirely', () => {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, tenants: [], tenant: null }}>
        <TenantManager embedded />
      </EnvironmentContext.Provider>,
    )
    expect(screen.queryByText('XSIAM Tenants')).not.toBeInTheDocument()
  })

  it('UcTcIndexView (Catalog › UC / TC index)', async () => {
    installRoutes({})
    render(<UcTcIndexView />)
    await waitFor(() => expect(screen.getByTestId('uctc-index')).toBeInTheDocument())
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()
  })

  it('RunDetailView: per-RUN facts, not the phase — two attempts must differ', () => {
    // The exception to the rule above, and the reason for it: the run detail
    // is the one surface a DC opens twice on the same workflow. Its eyebrow
    // has to distinguish the two attempts, which a phase label cannot do.
    const { rerender } = render(
      <RunDetailView runId="run-1" run={{ scenario_id: 'SIM-EDR-001', started_at: '14 Sep 16:04', status: 'completed' }} />,
    )
    expect(screen.getByText(/SIM-EDR-001 · 14 Sep 16:04/)).toBeInTheDocument()
    expect(screen.queryByText(/Phase \d/)).not.toBeInTheDocument()

    rerender(
      <RunDetailView runId="run-2" run={{ scenario_id: 'SIM-EDR-001', started_at: '15 Sep 09:12', status: 'completed' }} />,
    )
    expect(screen.getByText(/SIM-EDR-001 · 15 Sep 09:12/)).toBeInTheDocument()
  })
})
