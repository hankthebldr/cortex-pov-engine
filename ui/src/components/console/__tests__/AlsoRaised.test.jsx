/**
 * Results shows the alerts Cortex raised for a run that match none of its
 * expected detections (#346). They were real and shown nowhere: 15 agent
 * module alerts on a sweep credited nothing and left no trace on their runs.
 *
 * The server lists them on /api/results/{run_id} as `additional_detections`;
 * the section is one line per alert (name · source · step · time), with the
 * count in its title and one line saying they are not coverage. Alerts that
 * are the same on the page share a line with a count.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import AlsoRaised, { ALSO_RAISED_NOTE } from '../AlsoRaised.jsx'
import EvidenceView from '../EvidenceView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const ALERTS = [
  {
    alert_external_id: '1502439', alert_name: 'Dumping Registry hives with passwords',
    alert_source: 'XDR BIOC', techniques: ['T1003'], step_id: 'step-04',
    observed_at: '2026-11-15T05:37:05',
  },
  {
    alert_external_id: '1502431', alert_name: 'Potentially Dangerous Tool - 433033227',
    alert_source: 'XDR Agent', techniques: [], step_id: 'step-04',
    observed_at: '2026-11-15T05:37:15',
  },
]

describe('AlsoRaised', () => {
  it('lists each alert on one line: name, source, step, time', () => {
    render(<AlsoRaised alerts={ALERTS} />)
    const section = screen.getByTestId('also-raised')
    expect(within(section).getByRole('heading', { name: 'Also raised by Cortex (2)' }))
      .toBeInTheDocument()
    const items = within(section).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([
      'Dumping Registry hives with passwords · XDR BIOC · step-04 · 2026-11-15 05:37:05 UTC',
      'Potentially Dangerous Tool - 433033227 · XDR Agent · step-04 · 2026-11-15 05:37:15 UTC',
    ])
  })

  it('says once that they are not coverage', () => {
    render(<AlsoRaised alerts={ALERTS} />)
    expect(screen.getAllByText(ALSO_RAISED_NOTE)).toHaveLength(1)
  })

  it('reads the server time as UTC, whatever zone the browser is in', () => {
    render(<AlsoRaised alerts={ALERTS.slice(0, 1)} />)
    const time = screen.getByText('2026-11-15 05:37:05 UTC')
    expect(time.getAttribute('datetime')).toBe('2026-11-15T05:37:05.000Z')
  })

  it('names what an alert record does not carry instead of leaving a gap', () => {
    render(<AlsoRaised alerts={[{ alert_external_id: 'x' }]} />)
    expect(screen.getByRole('listitem').textContent)
      .toBe('Unnamed alert · source unknown · step unknown · time unknown')
  })

  it('renders nothing when there is no such alert', () => {
    const { container } = render(<AlsoRaised alerts={[]} />)
    expect(container).toBeEmptyDOMElement()
    expect(render(<AlsoRaised />).container).toBeEmptyDOMElement()
  })
})

describe('AlsoRaised: identical alerts share a line', () => {
  // SIM-KOI-005 on the sweep: the agent raised one alert four times in a second.
  const staged = (id, extra = {}) => ({
    alert_external_id: id, alert_name: 'Staged Malware Activity - 2149710479',
    alert_source: 'XDR Agent', severity: 'high', techniques: ['T1567.004'],
    step_id: 'step-03', observed_at: '2026-11-15T05:58:15', ...extra,
  })

  it('collapses them to one line with a count, and the title still counts every alert', () => {
    render(<AlsoRaised alerts={['1502496', '1502495', '1502494', '1502492'].map((id) => staged(id))} />)
    expect(screen.getByRole('heading', { name: 'Also raised by Cortex (4)' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Staged Malware Activity - 2149710479 ×4 · XDR Agent · step-03 · 2026-11-15 05:58:15 UTC',
    ])
  })

  it('shows the time of the first when the repeats are seconds apart', () => {
    render(<AlsoRaised alerts={[
      staged('a', { observed_at: '2026-11-15T05:58:15' }),
      staged('b', { observed_at: '2026-11-15T05:58:19' }),
    ]} />)
    expect(screen.getByRole('listitem').textContent)
      .toBe('Staged Malware Activity - 2149710479 ×2 · XDR Agent · step-03 · 2026-11-15 05:58:15 UTC')
  })

  it('keeps apart alerts that differ in name, severity, source or step', () => {
    render(<AlsoRaised alerts={[
      staged('a'),
      staged('b', { alert_name: 'Staged Malware Activity - 4238392387' }),
      staged('c', { severity: 'medium' }),
      staged('d', { alert_source: 'XDR BIOC' }),
      staged('e', { step_id: 'step-04' }),
      staged('f'),
    ]} />)
    expect(screen.getByRole('heading', { name: 'Also raised by Cortex (6)' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Staged Malware Activity - 2149710479 ×2 · XDR Agent · step-03 · 2026-11-15 05:58:15 UTC',
      'Staged Malware Activity - 4238392387 · XDR Agent · step-03 · 2026-11-15 05:58:15 UTC',
      'Staged Malware Activity - 2149710479 · XDR Agent · step-03 · 2026-11-15 05:58:15 UTC',
      'Staged Malware Activity - 2149710479 · XDR BIOC · step-03 · 2026-11-15 05:58:15 UTC',
      'Staged Malware Activity - 2149710479 · XDR Agent · step-04 · 2026-11-15 05:58:15 UTC',
    ])
  })

  it('a single alert carries no count', () => {
    render(<AlsoRaised alerts={[staged('a')]} />)
    expect(screen.getByRole('listitem').textContent).not.toContain('×')
  })
})

describe('Results evidence view', () => {
  const row = {
    id: 1, step_id: 'step-02', mitre_technique: 'T1059.001', plane: 'EDR',
    detection_type: 'ABIOC', expected_detection: 'Rapid privilege escalation',
    expected_outcome: 'detect', outcome: 'pending', outcome_reason: null,
    observed: false, observed_at: null, mttd_seconds: null,
    alert_id: null, alert_source: null, observation_count: 0,
  }
  const lastRun = { runId: 'r1', scenarioId: 'SIM-EDR-018', status: 'complete' }

  it('shows the section under the scorecard when the run has such alerts', async () => {
    installRoutes({
      'GET /api/results/r1': {
        run_id: 'r1', total: 1, results: [row], additional_detections: ALERTS,
      },
    })
    render(<EvidenceView lastRun={lastRun} />)
    await waitFor(() => expect(screen.getByText('Rapid privilege escalation')).toBeInTheDocument())
    const section = screen.getByTestId('also-raised')
    expect(within(section).getByText('Also raised by Cortex (2)')).toBeInTheDocument()
    expect(within(section).getByText('Potentially Dangerous Tool - 433033227')).toBeInTheDocument()
  })

  it('shows no section for a run with none, or a server that predates the field', async () => {
    installRoutes({
      'GET /api/results/r1': { run_id: 'r1', total: 1, results: [row] },
    })
    render(<EvidenceView lastRun={lastRun} />)
    await waitFor(() => expect(screen.getByText('Rapid privilege escalation')).toBeInTheDocument())
    expect(screen.queryByTestId('also-raised')).toBeNull()
  })
})
