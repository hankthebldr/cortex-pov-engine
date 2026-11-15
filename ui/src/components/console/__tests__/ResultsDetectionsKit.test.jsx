/**
 * Results › Detections, on the console kit and in few words.
 *
 *   - Each row's state is a StatusIcon and one word from one vocabulary:
 *     Detected · Prevented · Missed · Pending · Not reached · Not executed ·
 *     Not reviewed. A row nothing has judged reads Pending on a reviewed run
 *     and Not reviewed on one that has not been reviewed.
 *   - A tile is a sentence-case label, one number, and at most one short line.
 *   - With no MTTD the MTTD panel is one line.
 *   - Row actions are kit icon buttons that name themselves in a tooltip.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, waitFor, within, cleanup, fireEvent } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import EvidenceView from '../EvidenceView.jsx'

void React

afterEach(cleanup)

const row = (id, over = {}) => ({
  id, plane: 'EDR', detection_type: 'BIOC', mitre_technique: 'T1003',
  expected_detection: `det-${id}`, observed: false, outcome: null, ...over,
})
const RESULTS = {
  results: [
    row(1, { outcome: 'detected', observed: true, mttd_seconds: 42 }),
    row(2, { outcome: 'prevented', observed: true }),
    row(3, { outcome: 'missed' }),
    row(4, { outcome: 'pending', outcome_reason: 'DEDUP_WINDOW' }),
    row(5, { outcome: 'not_reached' }),
    row(6, { outcome: 'not_executed' }),
    row(7),                                   // nothing has judged it
    row(8, { outcome: 'detected', outcome_reason: 'DETECTED_NOT_PREVENTED', observed: true, expected_outcome: 'prevent' }),
  ],
  coverage: { denominator: 6, observed_in_denominator: 3, pct: 50.0, excluded: { not_reached: 1, not_executed: 1, soak_pending: 0 } },
}
const RUN = { runId: 'run-1', scenarioId: 'SIM-EDR-001', status: 'complete' }

const rowOf = (text) => screen.getByText(text).closest('.scorecard__row')
const stateOf = (text) => rowOf(text).querySelector('.scorecard__state .kit-status__icon').getAttribute('aria-label')
const tile = (label) => [...document.querySelectorAll('.kpi')].find((k) => k.querySelector('.kpi__label').textContent === label)

async function mount(results = RESULTS, props = {}) {
  installRoutes({ 'GET /api/results/run-1': results })
  const view = render(<EvidenceView pinnedRun={RUN} {...props} />)
  await waitFor(() => expect(document.querySelector('.scorecard__row')).not.toBeNull())
  return view
}

describe('Detections — one state vocabulary, as a StatusIcon and a word', () => {
  it('names each row from the seven words', async () => {
    await mount()
    expect([1, 2, 3, 4, 5, 6, 7].map((n) => stateOf(`det-${n}`))).toEqual([
      'Detected', 'Prevented', 'Missed', 'Pending', 'Not reached', 'Not executed', 'Pending',
    ])
  })

  it('a row nothing has judged reads Not reviewed on a run that has not been reviewed', async () => {
    await mount(RESULTS, { reviewed: false })
    expect(stateOf('det-7')).toBe('Not reviewed')
    // A fact the host recorded is still a fact before any review.
    expect(stateOf('det-6')).toBe('Not executed')
  })

  it('a prevention that was only detected reads Not prevented, never a pass', async () => {
    await mount()
    expect(stateOf('det-8')).toBe('Not prevented')
    expect(rowOf('det-8').querySelector('.kit-status--down')).not.toBeNull()
  })

  it('draws no uppercase pill and no "awaiting reconcile"', async () => {
    await mount()
    expect(document.querySelector('.scorecard__status')).toBeNull()
    expect(document.body.textContent).not.toMatch(/awaiting reconcile/i)
  })
})

describe('Detections — tiles say one number and at most one short line', () => {
  it('uses sentence-case labels', async () => {
    await mount()
    expect([...document.querySelectorAll('.kpi__label')].map((k) => k.textContent))
      .toEqual(['Coverage', 'Median MTTD', 'Analytics plane', 'Pending'])
  })

  it('coverage says how many were detected and how many were not counted', async () => {
    await mount()
    expect(tile('Coverage').querySelector('.kpi__value').textContent).toBe('50%')
    expect(tile('Coverage').querySelector('.kpi__meta').textContent).toBe('3 of 6 detected · 2 not counted')
  })

  it('with nothing left to count it says only what was not counted', async () => {
    await mount({
      results: [row(5, { outcome: 'not_reached' }), row(6, { outcome: 'not_executed' })],
      coverage: { denominator: 0, observed_in_denominator: 0, pct: 0, excluded: { not_reached: 1, not_executed: 1, soak_pending: 0 } },
    })
    expect(tile('Coverage').querySelector('.kpi__value').textContent).toBe('—')
    expect(tile('Coverage').querySelector('.kpi__meta').textContent).toBe('2 not counted')
  })

  it('every line under a number is one short line', async () => {
    await mount()
    for (const meta of document.querySelectorAll('.kpi__meta')) {
      expect(meta.textContent.split(/\s+/).length).toBeLessThanOrEqual(8)
      expect(meta.textContent).not.toMatch(/—|\(/)
    }
  })
})

describe('Detections — the MTTD panel', () => {
  it('is one line when there is no MTTD', async () => {
    await mount({ results: [row(3, { outcome: 'missed' })], coverage: { denominator: 1, observed_in_denominator: 0, pct: 0, excluded: {} } })
    const panel = screen.getByTestId('mttd-panel')
    expect(panel).toHaveTextContent(/^No MTTD yet$/)
  })

  it('draws the distribution once a detection has one', async () => {
    await mount()
    expect(screen.getByTestId('mttd-panel')).toHaveTextContent('MTTD distribution')
    expect(screen.getByRole('img', { name: 'Detection MTTD histogram' })).toBeInTheDocument()
  })
})

describe('Detections — row actions are kit icon buttons with their label in a tooltip', () => {
  it('names each action by its tooltip and draws only its icon', async () => {
    await mount()
    const actions = within(rowOf('det-3')).getAllByRole('button')
    expect(actions.map((b) => b.getAttribute('title'))).toEqual(['Mark detected', 'Reset'])
    for (const b of actions) {
      expect(b).toHaveClass('kit-btn')
      expect(b.querySelector('svg')).not.toBeNull()
      expect(b.textContent).toBe('')
      expect(b).toHaveAccessibleName(b.getAttribute('title'))
    }
  })

  it('marking a row calls validate for that row', async () => {
    const fetchMock = installRoutes({
      'GET /api/results/run-1': RESULTS,
      'PUT /api/results/:id/validate': { ok: true },
    })
    render(<EvidenceView pinnedRun={RUN} />)
    await waitFor(() => expect(document.querySelector('.scorecard__row')).not.toBeNull())
    fireEvent.click(within(rowOf('det-7')).getByRole('button', { name: 'Mark detected' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url).includes('/api/results/7/validate') && init?.method === 'PUT')).toBe(true))
  })

  it('says how many rows Validate all skips in two words', async () => {
    await mount()
    const skipped = screen.getByTestId('validate-all-skipped')
    expect(skipped.textContent).toBe('3 skipped')
    expect(skipped).toHaveAttribute('title')
  })
})
