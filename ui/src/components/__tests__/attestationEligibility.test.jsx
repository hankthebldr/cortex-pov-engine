/**
 * Residual R5 — the bulk attestation paths act on exactly the rows the server
 * accepts (`PUT /validate`: outcome none / pending / missed, not yet
 * observed), and the rows they skip are counted next to the button.
 *
 * Validate-all used to require `observed == null`, which a live payload never
 * carries (`Result.observed` is a non-null bool), so the button was
 * permanently disabled; the wizard's bulk path marked every `!observed` row,
 * not_reached / not_executed included.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import EvidenceView from '../console/EvidenceView.jsx'
import ResultsValidationWizard from '../ResultsValidationWizard.jsx'
import { installRoutes } from '../../test/mockFetch.js'
import { isAttestable } from '../../outcomes.js'

void React

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const ROW = (id, outcome, reason, observed = false) => ({
  id, run_id: 'run-a', step_id: `step-0${id}`, step_name: `s${id}`, mitre_technique: `T100${id}`,
  plane: 'EDR', signal_type: 'BIOC', detection_type: 'BIOC',
  expected_detection: `det-${id}`, expected_outcome: 'detect',
  outcome, outcome_reason: reason, observed, observed_at: null, mttd_seconds: null,
})

const PAYLOAD = {
  run_id: 'run-a',
  results: [
    ROW(1, 'pending', 'PREVENTION_UNCONFIRMED_NO_ALERT'),
    ROW(2, 'not_reached', 'UPSTREAM_SEVERED'),
  ],
  total: 2,
  coverage: { observed: 0, total: 2, denominator: 1, observed_in_denominator: 0, prevented: 0,
    excluded: { not_reached: 1, not_executed: 0, soak_pending: 0 }, pct: 0.0, by_type: {} },
}

describe('isAttestable', () => {
  it('is the server rule: outcome none / pending / missed, not yet observed', () => {
    expect(isAttestable({ outcome: null, observed: false })).toBe(true)
    expect(isAttestable({ outcome: 'pending', observed: false })).toBe(true)
    expect(isAttestable({ outcome: 'missed', observed: false })).toBe(true)
    expect(isAttestable({ outcome: 'pending', observed: true })).toBe(false)
    expect(isAttestable({ outcome: 'not_reached', observed: false })).toBe(false)
    expect(isAttestable({ outcome: 'not_executed', observed: false })).toBe(false)
    expect(isAttestable({ outcome: 'prevented', observed: true })).toBe(false)
    expect(isAttestable({ outcome: 'detected', observed: true })).toBe(false)
  })
})

describe('EvidenceView Validate-all', () => {
  it('is enabled on a run with one pending row, and names the skipped row', async () => {
    const validated = []
    installRoutes({
      'GET /api/results/run-a': PAYLOAD,
      'PUT /api/results/1/validate': (_u, init) => { validated.push(1); return { id: 1, ...JSON.parse(init.body) } },
      'PUT /api/results/2/validate': () => { validated.push(2); return { id: 2 } },
    })
    render(<EvidenceView lastRun={{ runId: 'run-a', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-1')).toBeInTheDocument())
    const btn = screen.getByRole('button', { name: /Validate all/ })
    expect(btn).not.toBeDisabled()
    expect(btn.textContent).toMatch(/1/)
    expect(screen.getByTestId('validate-all-skipped').textContent).toMatch(/1 skipped/)
    fireEvent.click(btn)
    await waitFor(() => expect(validated).toEqual([1]))
  })
})

describe('ResultsValidationWizard bulk path', () => {
  it('marks only attestable rows and shows the skipped count', async () => {
    const validated = []
    installRoutes({
      'GET /api/results/run-a': PAYLOAD,
      'PUT /api/results/1/validate': () => { validated.push(1); return { id: 1 } },
      'PUT /api/results/2/validate': () => { validated.push(2); return { id: 2 } },
    })
    render(<ResultsValidationWizard runId="run-a" onClose={vi.fn()} onMessage={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/det-1/)).toBeInTheDocument())
    expect(screen.getByTestId('wizard-bulk-skipped').textContent).toMatch(/1 skipped/)
    fireEvent.click(screen.getByRole('button', { name: /Mark all observed/ }))
    await waitFor(() => expect(validated).toEqual([1]))
  })
})
