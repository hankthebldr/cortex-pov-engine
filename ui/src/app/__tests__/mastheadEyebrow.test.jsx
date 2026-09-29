/**
 * Masthead eyebrow copy — one pattern across every destination.
 *
 * This guard originally locked the OPPOSITE rule: nav group alone, never
 * "· Phase N", on the reasoning that the product had no app-level phase
 * stepper and a phase reference therefore pointed at nothing.
 *
 * That premise no longer holds. The rail groups ARE the phase model, they
 * carry the phase numeral, and the flow bar names the current phase on every
 * surface. So the pattern inverted with it: `Phase N · Group`, exactly as the
 * rail names it. A page whose eyebrow disagreed with the rail entry that
 * opened it is precisely the drift the single wayfinding model exists to
 * remove — which is the same argument the old rule made, pointed the other
 * way now that there is a model to point at.
 *
 * One deliberate exception, asserted below: the run detail. Its eyebrow
 * carries the facts that differ between two runs of the SAME workflow, so two
 * attempts are distinguishable at a glance. A generic "PHASE 5 · OBSERVE"
 * there restated what the rail already said.
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
  it('OperationsView (Library): the phase the rail puts it in', async () => {
    installRoutes({ 'GET /api/scenarios': { scenarios: [] }, 'GET /api/agents': [] })
    render(<OperationsView />)
    await waitFor(() => expect(screen.getByRole('heading', { name: /library/i })).toBeInTheDocument())
    expect(screen.getByText('Phase 2 · Compose')).toBeInTheDocument()
  })

  it('TenantManager: the phase the rail puts it in', () => {
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, tenants: [], tenant: null }}>
        <TenantManager />
      </EnvironmentContext.Provider>,
    )
    expect(screen.getByText('Phase 1 · Scope')).toBeInTheDocument()
  })

  it('UcTcIndexView: the phase the rail puts it in', async () => {
    installRoutes({})
    render(<UcTcIndexView />)
    await waitFor(() => expect(screen.getByTestId('uctc-index')).toBeInTheDocument())
    expect(screen.getByText('Phase 2 · Compose')).toBeInTheDocument()
    expect(screen.queryByText(/Analyze · UC \/ TC Index/)).not.toBeInTheDocument()
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
