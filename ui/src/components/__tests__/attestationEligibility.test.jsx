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
import { isAttestable, isAttestationRefused, isBulkAttestable } from '../../outcomes.js'

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

  it('round 3 R5c: a prevent-expectation row is never hand-attestable', () => {
    expect(isAttestable({ outcome: null, observed: false, expected_outcome: 'prevent' })).toBe(false)
    expect(isAttestable({ outcome: 'pending', observed: false, expectedOutcome: 'prevent' })).toBe(false)
    expect(isAttestable({ outcome: 'pending', observed: false, expected_outcome: 'detect' })).toBe(true)
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

// ─── Round 3: per-row controls are hidden where the server refuses ─────────
import DetectionDrawer from '../console/DetectionDrawer.jsx'

describe('per-row attestation controls', () => {
  it('isAttestationRefused names the rows the server 409s', () => {
    expect(isAttestationRefused({ outcome: 'prevented' })).toBe(true)
    expect(isAttestationRefused({ outcome: 'not_reached' })).toBe(true)
    expect(isAttestationRefused({ outcome: 'not_executed' })).toBe(true)
    expect(isAttestationRefused({ outcome: 'pending', expected_outcome: 'prevent' })).toBe(true)
    expect(isAttestationRefused({ outcome: null, expectedOutcome: 'prevent' })).toBe(true)
    expect(isAttestationRefused({ outcome: 'missed', expected_outcome: 'detect' })).toBe(false)
    expect(isAttestationRefused({ outcome: null })).toBe(false)
  })

  it('Evidence hides ✓/✗ on a not_reached row and a prevent row, keeps them on a detect row', async () => {
    const results = [
      ROW(1, 'pending', 'EVIDENCE_INCOMPLETE'),
      ROW(2, 'not_reached', 'UPSTREAM_SEVERED'),
      { ...ROW(3, 'pending', 'PREVENTION_UNCONFIRMED_NO_ALERT'), expected_outcome: 'prevent' },
    ]
    installRoutes({ 'GET /api/results/run-a': { ...PAYLOAD, results, total: 3 } })
    const { container } = render(
      <EvidenceView lastRun={{ runId: 'run-a', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-1')).toBeInTheDocument())
    const actions = (text) => Array.from(container.querySelectorAll('.scorecard__row'))
      .find((r) => r.textContent.includes(text)).querySelector('.scorecard__row-actions')
    expect(actions('det-1')).not.toBeNull()
    expect(actions('det-2')).toBeNull()
    expect(actions('det-3')).toBeNull()
  })

  it('the drawer hides Mark detected / Mark missed on a refused row', () => {
    const row = { id: 5, tid: 'T1005', plane: 'EDR', detectionType: 'BIOC', alert: 'x',
      observed: true, outcome: 'prevented', outcome_reason: null, expectedOutcome: 'prevent', notes: '' }
    render(<DetectionDrawer row={row} open onClose={() => {}} />)
    expect(screen.queryByText(/Mark detected/)).toBeNull()
    expect(screen.queryByText(/Mark missed/)).toBeNull()
    expect(screen.getByTestId('drawer-attestation-refused')).toBeInTheDocument()
  })
})

// ─── Round 4 I-2: bulk attestation never overwrites an engine miss ─────────
describe('bulk attestation excludes evidence-backed misses', () => {
  const MISS_PAYLOAD = {
    ...PAYLOAD,
    results: [
      ROW(1, 'pending', 'EVIDENCE_INCOMPLETE'),
      ROW(2, 'not_reached', 'UPSTREAM_SEVERED'),
      ROW(3, 'missed', null),
    ],
    total: 3,
  }

  it('isBulkAttestable: a missed row is per-row only', () => {
    expect(isBulkAttestable({ outcome: 'missed', observed: false })).toBe(false)
    expect(isAttestable({ outcome: 'missed', observed: false })).toBe(true)
    expect(isBulkAttestable({ outcome: 'pending', observed: false })).toBe(true)
  })

  it('Validate-all marks the pending row only and names the miss among the skipped', async () => {
    const validated = []
    installRoutes({
      'GET /api/results/run-a': MISS_PAYLOAD,
      'PUT /api/results/1/validate': () => { validated.push(1); return { id: 1 } },
      'PUT /api/results/3/validate': () => { validated.push(3); return { id: 3 } },
    })
    const { container } = render(
      <EvidenceView lastRun={{ runId: 'run-a', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByText('det-3')).toBeInTheDocument())
    expect(screen.getByTestId('validate-all-skipped').textContent).toMatch(/2 skipped/)
    expect(screen.getByTestId('validate-all-skipped').textContent).toMatch(/evidence-backed miss/)
    fireEvent.click(screen.getByRole('button', { name: /Validate all/ }))
    await waitFor(() => expect(validated).toEqual([1]))
    // The per-row control on the missed row stays: a DC may overrule one row.
    const missRow = Array.from(container.querySelectorAll('.scorecard__row'))
      .find((r) => r.textContent.includes('det-3'))
    expect(missRow.querySelector('.scorecard__row-actions')).not.toBeNull()
  })

  it('the wizard bulk path skips the missed row too', async () => {
    const validated = []
    installRoutes({
      'GET /api/results/run-a': MISS_PAYLOAD,
      'PUT /api/results/1/validate': () => { validated.push(1); return { id: 1 } },
      'PUT /api/results/3/validate': () => { validated.push(3); return { id: 3 } },
    })
    render(<ResultsValidationWizard runId="run-a" onClose={vi.fn()} onMessage={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/det-3/)).toBeInTheDocument())
    expect(screen.getByTestId('wizard-bulk-skipped').textContent).toMatch(/2 skipped/)
    fireEvent.click(screen.getByRole('button', { name: /Mark all observed/ }))
    await waitFor(() => expect(validated).toEqual([1]))
  })
})
