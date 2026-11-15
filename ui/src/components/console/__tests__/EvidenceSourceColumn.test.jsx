/**
 * The scorecard shows where each alert came from, beside its id. The server
 * joins the first recorded observation onto every /api/results row
 * (alert_id / alert_source); a row with none reads as an em dash, not blank.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import EvidenceView from '../EvidenceView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const row = (id, extra) => ({
  id, step_id: `step-0${id}`, mitre_technique: `T100${id}`, plane: 'EDR',
  detection_type: 'BIOC', expected_detection: `det-${id}`,
  expected_outcome: 'detect', outcome: 'pending', outcome_reason: null,
  observed: false, observed_at: null, mttd_seconds: null,
  alert_id: null, alert_source: null, observation_count: 0, ...extra,
})

const lastRun = { runId: 'r1', scenarioId: 'SIM-EDR-001', status: 'complete' }

describe('Scorecard Source column', () => {
  it('shows the Source header, the alert id and its source', async () => {
    installRoutes({
      'GET /api/results/r1': {
        run_id: 'r1', total: 2,
        results: [
          row(1, { alert_id: 'A-1', alert_source: 'XDR Analytics BIOC', observation_count: 1 }),
          row(2),
        ],
      },
    })
    render(<EvidenceView lastRun={lastRun} />)
    await waitFor(() => expect(screen.getByText('det-1')).toBeInTheDocument())
    expect(screen.getByText('Source')).toBeInTheDocument()
    expect(screen.getByText('A-1')).toBeInTheDocument()
    expect(screen.getByText('XDR Analytics BIOC')).toBeInTheDocument()
  })

  it('a row with no source shows an em dash', async () => {
    installRoutes({
      'GET /api/results/r1': { run_id: 'r1', total: 1, results: [row(2)] },
    })
    const { container } = render(<EvidenceView lastRun={lastRun} />)
    await waitFor(() => expect(screen.getByText('det-2')).toBeInTheDocument())
    const cell = container.querySelector('.scorecard__source')
    expect(cell).not.toBeNull()
    expect(cell.textContent).toBe('—')
  })
})
