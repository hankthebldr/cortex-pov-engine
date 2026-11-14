/**
 * RunCasePanel — the run's XSIAM case and disposition.
 *
 * Disposition is context, never detection evidence: pins the verbatim
 * disclaimer, that merge_class carries no pass/fail styling, and that a tenant
 * supplied xdr_url is linked only when https on *.paloaltonetworks.com.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import RunCasePanel, { CASE_DISCLAIMER, NO_CASE_YET_HINT, safeXdrUrl } from '../RunCasePanel.jsx'

afterEach(cleanup)

const LEDGER = {
  read_at: '2026-11-11T10:00:00+00:00',
  basis: 'alert_incident_ids',
  integration: 'acme',
  merge_class: 'SPLIT_SEALED',
  merge_reason: 'case 901 was sealed before the chain finished',
  code: 'CASE_LEDGER_OK',
  shared_with_other_runs: true,
  cases: [
    { case_id: '900', status: 'resolved_other', closed_by: 'socfw_auto_triage', sealed: true,
      starred: false, assigned: false,
      xdr_url: 'https://acme.xdr.us.paloaltonetworks.com/incidents/900',
      issues_from_run: 2, issues_other: 3, issues_closed_first: 1 },
    { case_id: '901', status: 'new', closed_by: 'open', sealed: false, starred: false,
      assigned: false, xdr_url: 'http://evil.example.com/x',
      issues_from_run: 1, issues_other: 0, issues_closed_first: 0 },
  ],
  alerts: [],
}

describe('RunCasePanel', () => {
  it('renders merge class and reason', () => {
    render(<RunCasePanel ledger={LEDGER} />)
    expect(screen.getByText('SPLIT_SEALED')).toBeTruthy()
    expect(screen.getByText(/case 901 was sealed before the chain finished/)).toBeTruthy()
  })

  it('renders one badge per case with closed_by', () => {
    render(<RunCasePanel ledger={LEDGER} />)
    const rows = screen.getAllByTestId('run-case-row')
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText('socfw_auto_triage')).toBeTruthy()
    expect(within(rows[1]).getByText('open')).toBeTruthy()
  })

  it('renders the disclaimer verbatim', () => {
    render(<RunCasePanel ledger={LEDGER} />)
    expect(CASE_DISCLAIMER).toBe(
      'Tenant disposition is shown for context. It is not detection evidence.')
    expect(screen.getByText(CASE_DISCLAIMER)).toBeTruthy()
  })

  it('renders read_at', () => {
    render(<RunCasePanel ledger={LEDGER} />)
    expect(screen.getByText(/2026-11-11T10:00:00\+00:00/)).toBeTruthy()
  })

  it('renders shared-with-other-runs count', () => {
    render(<RunCasePanel ledger={LEDGER} />)
    expect(screen.getByTestId('run-case-shared').textContent).toMatch(/3/)
  })

  it('null ledger renders "not read yet"', () => {
    render(<RunCasePanel ledger={null} />)
    expect(screen.getByText(/Not read yet/)).toBeTruthy()
    expect(screen.getByText(CASE_DISCLAIMER)).toBeTruthy()
  })

  it('links xdr_url only for https paloaltonetworks.com hosts', () => {
    render(<RunCasePanel ledger={LEDGER} />)
    const rows = screen.getAllByTestId('run-case-row')
    const a = within(rows[0]).getByRole('link')
    expect(a.getAttribute('href')).toBe('https://acme.xdr.us.paloaltonetworks.com/incidents/900')
    expect(a.getAttribute('rel')).toMatch(/noopener/)
    expect(within(rows[1]).queryByRole('link')).toBeNull()
    expect(within(rows[1]).getByText('http://evil.example.com/x')).toBeTruthy()
  })

  it('shows a non-OK code and a failed issue read', () => {
    render(<RunCasePanel ledger={{ ...LEDGER, code: 'CASE_READ_FAILED',
      issue_read: { ok: false, code: 'ISSUE_READ_500', pages: 0, remediation: 'retry the reconcile' } }} />)
    expect(screen.getByText(/CASE_READ_FAILED/)).toBeTruthy()
    expect(screen.getByText(/ISSUE_READ_500/)).toBeTruthy()
    expect(screen.getByText(/retry the reconcile/)).toBeTruthy()
  })

  it('never uses detection-verdict chip classes', () => {
    const { container } = render(<RunCasePanel ledger={LEDGER} />)
    expect(container.querySelector('.chip--detected, .chip--missed')).toBeNull()
  })

  it.each([
    'https://a.xdr.paloaltonetworks.com/x) [click](http://evil.example/',
    'https://a.xdr.paloaltonetworks.com/x\n\n# Injected',
    'https://evil.example\\@a.xdr.paloaltonetworks.com/x',
    'https://u:p@a.xdr.paloaltonetworks.com/x',
    'https://a.xdr.paloaltonetworks.com:8443/x',
    'https://evilpaloaltonetworks.com/x',
  ])('withholds hostile xdr_url %j', (url) => {
    expect(safeXdrUrl(url)).toBeNull()
    render(<RunCasePanel ledger={{ ...LEDGER, cases: [{ ...LEDGER.cases[0], xdr_url: url }] }} />)
    expect(screen.queryByRole('link')).toBeNull()
  })

  it.each(['merge_class', 'cases', 'code'])('ledger missing %s renders unreadable', (key) => {
    const led = { ...LEDGER }
    delete led[key]
    render(<RunCasePanel ledger={led} />)
    expect(screen.getByTestId('run-case-unreadable')).toBeTruthy()
    expect(screen.getByText(CASE_DISCLAIMER)).toBeTruthy()
    expect(screen.queryAllByTestId('run-case-row')).toHaveLength(0)
  })

  it('NO_CASE_YET says the next reconcile advances it, not a refresh', () => {
    render(<RunCasePanel ledger={{ ...LEDGER, merge_class: 'NO_CASE_YET', cases: [],
      shared_with_other_runs: false }} />)
    const hint = screen.getByTestId('run-case-no-case-yet')
    expect(hint.textContent).toMatch(/the next reconcile/)
    expect(hint.textContent).toMatch(/A refresh re-reads cases only/)
    // Identical to NO_CASE_YET_HINT in core/connectors/case_ledger.py.
    expect(NO_CASE_YET_HINT).toBe(
      'No case yet: the next reconcile re-reads these alerts and rebuilds the ledger once the tenant has grouped them into a case. A refresh re-reads cases only, and this ledger names none.')
  })

  it('renders read_at as UTC', () => {
    render(<RunCasePanel ledger={{ ...LEDGER, read_at: '2026-11-11T10:00:00' }} />)
    expect(screen.getByText(/read 2026-11-11T10:00:00 UTC/)).toBeTruthy()
  })

  it('a non-object case entry renders unreadable', () => {
    render(<RunCasePanel ledger={{ ...LEDGER, cases: [LEDGER.cases[0], 'oops'] }} />)
    expect(screen.getByTestId('run-case-unreadable').textContent)
      .toMatch(/cases contains a non-object entry/)
    expect(screen.queryAllByTestId('run-case-row')).toHaveLength(0)
  })

  it('ledger with wrong-typed cases renders unreadable', () => {
    render(<RunCasePanel ledger={{ ...LEDGER, cases: 'oops' }} />)
    expect(screen.getByTestId('run-case-unreadable')).toBeTruthy()
  })

  // The console says "the tenant", never the integration's internal name.
  it('the read line never names the integration', () => {
    const { container } = render(<RunCasePanel ledger={{ ...LEDGER, integration: 'lab-deathstar' }} />)
    expect(container.textContent).not.toMatch(/lab-deathstar/)
    expect(screen.getByText(/read 2026-11-11T10:00:00\+00:00 UTC · alert_incident_ids/)).toBeTruthy()
  })

  it('its heading is an h2 unless the page nests it deeper', () => {
    const { unmount } = render(<RunCasePanel ledger={LEDGER} />)
    expect(screen.getByRole('heading', { name: 'Case', level: 2 })).toBeTruthy()
    unmount()
    render(<RunCasePanel ledger={null} headingLevel={3} />)
    expect(screen.getByRole('heading', { name: 'Case', level: 3 })).toBeTruthy()
    expect(screen.queryByRole('heading', { level: 2 })).toBeNull()
  })
})
