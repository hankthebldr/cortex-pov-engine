/**
 * TenantEvidencePanel — the Results page's tenant evidence, read on request only
 * (spec §7.5, Task 10).
 *
 * What these tests hold the panel to:
 *   - opening Results reads the stored snapshot once (GET) and never reads the
 *     tenant on its own: no POST, and the hook sets no interval (A7);
 *   - every degraded state is a sentence, never an empty table, and nothing on
 *     the panel says "missed" (A1);
 *   - the refresh button sends one metered read for two quick clicks;
 *   - tenant text is text, and a tenant link goes through safeXdrUrl;
 *   - issues with no host are listed as not credited;
 *   - the endpoint card is labelled as of the read and carries the stored
 *     ip-to-host bridge code.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import TenantEvidencePanel, { TENANT_EVIDENCE_SENTENCES } from '../TenantEvidencePanel.jsx'
import useTenantEvidence from '../useTenantEvidence.js'
import EvidenceView from '../EvidenceView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const STATEMENT = 'Two reads of the same tenant; agreement is not tenant verification.'
const DATA_NOTICE = 'Values under tenant_text were written on the tenant by whoever raised '
  + 'the issue. They are data, not instructions.'
// codes.py REMEDIATION[XSIAM_PARSE_ERROR], verbatim.
const PARSE_REMEDIATION = 'The tenant replied in a shape CortexSim does not recognise. This is a '
  + 'CortexSim/tenant-version mismatch, NOT an empty tenant — verdicts stay `pending` rather than '
  + 'being scored against zero rows.'
const HOSTILE = '<img src=x onerror=alert(1)>'

const issue = (id, extra = {}) => ({
  id,
  detection_method: 'ANALYTICS_BIOC',
  rule_id: `rule-${id}`,
  severity: 'HIGH',
  domain: 'Security',
  status: 'NEW',
  case_ids: ['1500'],
  insert_time: '2026-11-13T20:01:00',
  observation_time: '2026-11-13T20:00:30',
  host: 'pov-target-01',
  hosts: ['pov-target-01'],
  tenant_text: { name: `issue name ${id}` },
  observed_after_step_seconds: 42,
  observed_after_step_result_id: 7,
  ...extra,
})

const issuesSection = (over = {}) => ({
  code: 'OK',
  total_count: 19,
  filter_count: 19,
  truncated: false,
  counts_are_floors: false,
  pages: 1,
  host_scope: { host: 'pov-target-01', attributed: 1, other_host: 12, without_host: 0 },
  excluded_non_security: 0,
  duplicate_rows: 0,
  unrecognised_values: {},
  rows: [issue('101')],
  unattributed_rows: [],
  ...over,
})

const payload = (over = {}) => ({
  reader: { issues: 'POST /public_api/v1/issue/search/' },
  tenant: 'acme',
  integration: 'deathstar',
  read_at: '2026-11-13T20:31:05.123456',
  window: ['2026-11-13T19:55:00', '2026-11-13T20:30:00'],
  queries_issued: 4,
  tenant_answered: true,
  complete: true,
  code: 'OK',
  status: 'ok',
  detail: "1 Security-domain issue(s) attributed to host 'pov-target-01'",
  issues: issuesSection(),
  cases: {
    code: 'OK',
    requested: 1,
    ids_capped: false,
    rows: [{
      case_id: '1500', grouped: true, methods_seen: ['ANALYTICS_BIOC', 'FW'],
      issues_seen: 3, issue_count: 4,
      xdr_url: 'https://acme.xdr.us.paloaltonetworks.com/case?caseId=1500',
      xdr_url_withheld: false, tenant_text: { name: 'case one' },
    }],
  },
  endpoint: {
    code: 'OK',
    as_of: '2026-11-13T20:31:05.123456',
    truncated: false,
    record: {
      endpoint_id: 'e1', endpoint_name: 'pov-target-01', endpoint_status: 'CONNECTED',
      operational_status: 'PROTECTED', endpoint_version: '9.4.0.470', content_version: '1820-123',
      last_seen: '2026-11-13T20:30:00', is_isolated: 'AGENT_UNISOLATED', ip: ['192.168.1.50'],
      tenant_text: { assigned_prevention_policy: 'Linux default' },
    },
  },
  agreement: {
    both: { id: 2, name_host_window: 1 }, matcher_only: 1, reader_only: 2,
    unjoinable: 0, ambiguous: 0,
    id_space: { probed: 4, hit: 2, no_hit: 2, code: 'OK' }, pairs: [],
  },
  tenant_version: null,
  tenant_version_code: 'TENANT_VERSION_NOT_REPORTED',
  tenant_verified: 0,
  statement: STATEMENT,
  data_notice: DATA_NOTICE,
  ...over,
})

const snapshotOf = (p) => ({
  id: 11, run_id: 'r1', integration: p.integration, tenant: p.tenant, read_at: p.read_at,
  since: p.window[0], until: p.window[1], code: p.code, queries_issued: p.queries_issued,
  reader_version: '1', payload: p,
})

const NOT_READ = {
  run_id: 'r1', status: 'pending', code: 'TENANT_EVIDENCE_NOT_READ',
  snapshot: null, history: [], case_ledger: null,
}

const stored = (p, ledger = null) => ({
  run_id: 'r1', status: p.status, code: p.code, snapshot: snapshotOf(p),
  history: [{ id: 11, read_at: p.read_at, code: p.code }], case_ledger: ledger,
})

const postNothingRead = (code, status) => ({
  run_id: 'r1', read: false, status, code, detail: 'SimCore detail',
  retry_after_seconds: null, snapshot: null, issues: null,
})

const calls = (handler) => handler.mock.calls.map(([input, init = {}]) => {
  const url = typeof input === 'string' ? input : input.url
  return `${(init.method || 'GET').toUpperCase()} ${new URL(url, 'http://localhost').pathname}`
})

const panel = () => screen.getByTestId('tenant-evidence')
const refreshButton = () => screen.getByRole('button', { name: 'Refresh tenant evidence' })

// ─── A7: nothing reads the tenant on its own ─────────────────────────────────

describe('mounting Results issues no POST and the hook sets no interval', () => {
  it('the hook reads the stored snapshot once and starts no interval', async () => {
    const handler = installRoutes({ 'GET /api/runs/r1/tenant-evidence': NOT_READ })
    const spy = vi.spyOn(globalThis, 'setInterval')
    const { result } = renderHook(() => useTenantEvidence('r1'))
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    expect(result.current.data).toEqual(NOT_READ)
    expect(spy).not.toHaveBeenCalled()
    expect(calls(handler)).toEqual(['GET /api/runs/r1/tenant-evidence'])
  })

  it('Results sends one GET of tenant evidence and no POST in 60 s', async () => {
    vi.useFakeTimers()
    const handler = installRoutes({
      'GET /api/results/r1': { run_id: 'r1', total: 0, results: [] },
      'GET /api/runs/r1/tenant-evidence': NOT_READ,
    })
    render(<EvidenceView pinnedRun={{ runId: 'r1', scenarioId: 'SIM-EDR-001', status: 'complete' }} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    const seen = calls(handler)
    expect(seen.filter((c) => c === 'GET /api/runs/r1/tenant-evidence')).toHaveLength(1)
    expect(seen.filter((c) => c.startsWith('POST '))).toEqual([])
    // The scorecard's own 8 s poll is untouched.
    expect(seen.filter((c) => c === 'GET /api/results/r1').length).toBeGreaterThan(1)
  })
})

// ─── degraded states are sentences ───────────────────────────────────────────

const S = TENANT_EVIDENCE_SENTENCES
const noRows = (over) => issuesSection({
  host_scope: { host: 'pov-target-01', attributed: 0, other_host: 3, without_host: 0 },
  rows: [], unattributed_rows: [], ...over,
})

const DEGRADED = [
  {
    name: 'not read',
    get: NOT_READ,
    sentence: S.TENANT_EVIDENCE_NOT_READ,
  },
  {
    name: 'no credential',
    get: NOT_READ,
    post: postNothingRead('NO_TENANT_CONFIGURED', 'pending'),
    sentence: S.NO_TENANT_CONFIGURED,
  },
  {
    name: 'no run window',
    get: NOT_READ,
    post: postNothingRead('NO_RUN_WINDOW', 'degraded'),
    // True for a run with no timed step AND for a push run, which SimCore
    // refuses before it looks at steps (tenant_evidence.py, NO_RUN_WINDOW).
    sentence: 'This run has no execution window this instance can read: no step has an execution '
      + 'time, or it is a push run whose bundle ran offline. Nothing was read from the tenant.',
  },
  {
    name: 'unscoped',
    get: stored(payload({
      code: 'UNSCOPED', status: 'degraded', complete: true,
      issues: noRows({ host_scope: { host: null, attributed: null, other_host: null, without_host: 0 } }),
      cases: { code: 'UNSCOPED', rows: null, requested: 0, detail: 'no run host' },
      endpoint: { code: 'UNSCOPED', as_of: '2026-11-13T20:31:05', record: null, detail: 'no run host' },
      agreement: {
        both: { id: null, name_host_window: null }, matcher_only: null, reader_only: null,
        unjoinable: 0, ambiguous: null, id_space: { probed: 0, hit: 0, no_hit: 0, code: 'OK' }, pairs: [],
      },
    })),
    sentence: S.UNSCOPED,
  },
  {
    name: 'zero attributed',
    get: stored(payload({ code: 'NO_ATTRIBUTED_ISSUES', status: 'degraded', issues: noRows(), cases: { code: 'OK', rows: [], requested: 0, ids_capped: false } })),
    sentence: S.NO_ATTRIBUTED_ISSUES,
  },
  {
    name: 'truncated',
    get: stored(payload({
      code: 'NO_ATTRIBUTED_ISSUES', status: 'degraded', complete: false,
      issues: noRows({ truncated: true, counts_are_floors: true, filter_count: 900 }),
      cases: { code: 'OK', rows: [], requested: 0, ids_capped: false },
    })),
    sentence: S.TRUNCATED,
  },
  {
    name: 'truncation unknown',
    get: stored(payload({
      code: 'NO_ATTRIBUTED_ISSUES', status: 'degraded', complete: false,
      issues: noRows({ truncated: null, counts_are_floors: null, filter_count: null }),
      cases: { code: 'OK', rows: [], requested: 0, ids_capped: false },
    })),
    sentence: S.TRUNCATION_UNKNOWN,
  },
  {
    name: 'quota refused',
    get: stored(payload({
      code: 'QUOTA_REFUSED', status: 'degraded', complete: false,
      issues: {
        code: 'QUOTA_REFUSED', refused_locally: true, attempted: true, retry_after_seconds: 30,
        detail: "CortexSim's own per-tenant request budget refused this read", remediation: 'retry later',
        total_count: null, filter_count: null, truncated: null, counts_are_floors: null, pages: 0,
        host_scope: { host: 'pov-target-01', attributed: null, other_host: null, without_host: null },
        excluded_non_security: null, duplicate_rows: null, unrecognised_values: null,
        rows: null, unattributed_rows: null,
      },
      cases: { code: 'QUOTA_REFUSED', attempted: false, retry_after_seconds: 30, rows: null, requested: 0 },
      endpoint: { code: 'QUOTA_REFUSED', attempted: false, retry_after_seconds: 30, as_of: '2026-11-13T20:31:05', record: null },
      agreement: null,
    })),
    sentence: S.QUOTA_REFUSED(30),
  },
  {
    name: 'a failed section',
    get: stored(payload({
      code: 'XSIAM_PARSE_ERROR', status: 'degraded', complete: false,
      issues: {
        code: 'XSIAM_PARSE_ERROR', detail: 'the issue read returned a row this reader does not recognise',
        remediation: PARSE_REMEDIATION,
        total_count: null, filter_count: null, truncated: null, counts_are_floors: null,
        host_scope: null, excluded_non_security: null, unrecognised_values: null,
        rows: null, unattributed_rows: null,
      },
      cases: null,
      agreement: null,
    })),
    sentence: S.SECTION_FAILED('Issue', 'XSIAM_PARSE_ERROR', PARSE_REMEDIATION),
  },
]

describe('each degraded code renders its own sentence, never an empty table', () => {
  it.each(DEGRADED)('$name', async ({ get, post, sentence }) => {
    installRoutes({
      'GET /api/runs/r1/tenant-evidence': get,
      ...(post ? { 'POST /api/runs/r1/tenant-evidence': post } : {}),
    })
    render(<TenantEvidencePanel runId="r1" />)
    if (post) {
      await waitFor(() => expect(refreshButton()).not.toBeDisabled())
      fireEvent.click(refreshButton())
    }
    await waitFor(() => expect(within(panel()).getByText(sentence)).toBeInTheDocument())
    // No credited issue row, and no table without a row in it.
    expect(within(panel()).queryAllByTestId('te-issue-row')).toHaveLength(0)
    for (const table of panel().querySelectorAll('table')) {
      expect(table.querySelectorAll('tbody tr').length).toBeGreaterThan(0)
    }
    expect(panel().textContent).not.toMatch(/miss/i)
  })
})

// ─── Review Focus 2: one click, one metered read ─────────────────────────────

describe('Read from tenant posts once and is disabled while in flight', () => {
  it('two quick clicks send one POST, and the button is disabled until it answers', async () => {
    const answers = []
    const answer = (body) => answers.splice(0).forEach((resolve) => resolve(body))
    const handler = installRoutes({
      'GET /api/runs/r1/tenant-evidence': NOT_READ,
      'POST /api/runs/r1/tenant-evidence': () => new Promise((resolve) => { answers.push(resolve) }),
    })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(refreshButton()).not.toBeDisabled())
    fireEvent.click(refreshButton())
    fireEvent.click(refreshButton())
    expect(refreshButton()).toBeDisabled()
    await act(async () => { answer(postNothingRead('NO_TENANT_CONFIGURED', 'pending')) })
    await waitFor(() => expect(refreshButton()).not.toBeDisabled())
    expect(calls(handler).filter((c) => c.startsWith('POST '))).toEqual(['POST /api/runs/r1/tenant-evidence'])
  })

  it('two read() calls in the same tick send one POST', async () => {
    const answers = []
    const answer = (body) => answers.splice(0).forEach((resolve) => resolve(body))
    const handler = installRoutes({
      'GET /api/runs/r1/tenant-evidence': NOT_READ,
      'POST /api/runs/r1/tenant-evidence': () => new Promise((resolve) => { answers.push(resolve) }),
    })
    const { result } = renderHook(() => useTenantEvidence('r1'))
    await waitFor(() => expect(result.current.data).toEqual(NOT_READ))
    let first
    let second
    act(() => { first = result.current.read(); second = result.current.read() })
    await act(async () => { answer(postNothingRead('NO_TENANT_CONFIGURED', 'pending')); await first; await second })
    expect(calls(handler).filter((c) => c.startsWith('POST '))).toHaveLength(1)
  })
})

// ─── tenant text is data ─────────────────────────────────────────────────────

describe('tenant text renders as text', () => {
  it('a planted tag in an issue, case or policy name stays literal and draws no img', async () => {
    const p = payload({
      issues: issuesSection({ rows: [issue('101', { tenant_text: { name: HOSTILE } })] }),
    })
    p.cases.rows[0].tenant_text = { name: HOSTILE }
    p.endpoint.record.tenant_text = { assigned_prevention_policy: HOSTILE }
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(p) })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getAllByText(HOSTILE).length).toBe(3))
    expect(panel().querySelector('img')).toBeNull()
    expect(within(panel()).getByText(DATA_NOTICE)).toBeInTheDocument()
  })
})

describe('xdr_url is linked only through safeXdrUrl', () => {
  it('links an https paloaltonetworks.com case and nothing else', async () => {
    const p = payload()
    p.cases.rows = [
      { ...p.cases.rows[0], case_id: '1500' },
      { ...p.cases.rows[0], case_id: '1501', xdr_url: 'http://evil.example.com/case' },
      { ...p.cases.rows[0], case_id: '1502', xdr_url: 'javascript:alert(1)' },
      { ...p.cases.rows[0], case_id: '1503', xdr_url: null, xdr_url_withheld: true },
    ]
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(p) })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByText('1503')).toBeInTheDocument())
    const links = Array.from(panel().querySelectorAll('a'))
    expect(links.map((a) => a.getAttribute('href'))).toEqual([
      'https://acme.xdr.us.paloaltonetworks.com/case?caseId=1500',
    ])
    expect(links[0].getAttribute('rel')).toMatch(/noopener/)
    // The two unsafe urls and the one SimCore withheld: no link, and the raw url is not printed.
    expect(within(panel()).getAllByText('link withheld')).toHaveLength(3)
    expect(panel().textContent).not.toMatch(/evil\.example|javascript:/)
  })
})

// ─── the header and the agreement figures ────────────────────────────────────

describe('the header says when the tenant was read and that it is display only', () => {
  it('names the read time in UTC, never the integration, and keeps the statement by the agreement', async () => {
    // A reconciled run: its case ledger names the integration too.
    const ledger = {
      read_at: '2026-11-13T20:10:00', basis: 'alert_incident_ids', integration: 'deathstar',
      merge_class: 'ONE_CASE', merge_reason: 'one case', code: 'CASE_LEDGER_OK',
      shared_with_other_runs: false, cases: [], alerts: [],
    }
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(payload(), ledger) })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByText(
      'Read from the tenant at 2026-11-13 20:31 UTC · display only, does not change the verdict',
    )).toBeInTheDocument())
    expect(within(panel()).getByRole('heading', { name: 'Tenant evidence' })).toBeInTheDocument()
    expect(within(panel()).getByTestId('run-case')).toBeInTheDocument()
    expect(panel().textContent).not.toMatch(/deathstar|xsiam_tenant|tenant-verified/i)
    const agreement = within(panel()).getByTestId('te-agreement')
    expect(within(agreement).getByText(STATEMENT)).toBeInTheDocument()
    expect(panel().textContent.match(/4 tenant requests/g)).toHaveLength(1)
  })

  it('an agreement count the read could not measure reads unknown, never 0', async () => {
    const p = payload({
      agreement: {
        both: { id: 2, name_host_window: null }, matcher_only: null, reader_only: null,
        unjoinable: 0, ambiguous: null, id_space: { probed: 4, hit: 2, no_hit: 2, code: 'OK' }, pairs: [],
      },
    })
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(p) })
    render(<TenantEvidencePanel runId="r1" />)
    const agreement = await waitFor(() => within(panel()).getByTestId('te-agreement'))
    expect(agreement.textContent).toMatch(/matcher only unknown/)
    expect(agreement.textContent).toMatch(/reader only unknown/)
    expect(agreement.textContent).not.toMatch(/matcher only 0/)
  })
})

// ─── what is credited and what is not ────────────────────────────────────────

describe('unattributed issues render under "not credited"', () => {
  it('a hostless issue is listed under Not credited, apart from the credited rows', async () => {
    const p = payload({
      issues: issuesSection({
        host_scope: { host: 'pov-target-01', attributed: 1, other_host: 12, without_host: 1 },
        unattributed_rows: [issue('202', { host: null, hosts: [], tenant_text: { name: 'hostless issue' } })],
      }),
    })
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(p) })
    render(<TenantEvidencePanel runId="r1" />)
    const notCredited = await waitFor(() => within(panel()).getByTestId('te-not-credited'))
    expect(within(notCredited).getByText(/not credited/i)).toBeInTheDocument()
    expect(within(notCredited).getByText('hostless issue')).toBeInTheDocument()
    const credited = within(panel()).getAllByTestId('te-issue-row')
    expect(credited).toHaveLength(1)
    expect(within(credited[0]).getByText('issue name 101')).toBeInTheDocument()
    expect(within(credited[0]).queryByText('hostless issue')).toBeNull()
  })
})

describe('the endpoint card is "as of read" and shows a stored bridge code', () => {
  it('labels the record as of the read and carries the ledger\'s bridge code', async () => {
    const ledger = {
      read_at: '2026-11-13T20:10:00', basis: 'alert_incident_ids', integration: 'deathstar',
      merge_class: 'SPLIT_NATIVE', merge_reason: 'two cases; ip-to-host bridge: UNBRIDGED_IP_HOST',
      code: 'CASE_LEDGER_OK', shared_with_other_runs: false, cases: [], alerts: [],
      bridge: { host: 'pov-target-01', agent_ip: '192.168.1.50', endpoint_ips: ['10.0.0.9'],
        code: 'UNBRIDGED_IP_HOST', read_at: '2026-11-13T20:10:00' },
    }
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(payload(), ledger) })
    render(<TenantEvidencePanel runId="r1" />)
    const card = await waitFor(() => within(panel()).getByTestId('te-endpoint'))
    expect(within(card).getByText(/as of read/i)).toBeInTheDocument()
    expect(within(card).getByText('CONNECTED')).toBeInTheDocument()
    expect(within(card).getByText('UNBRIDGED_IP_HOST')).toBeInTheDocument()
  })

  it('nests the case heading under the panel heading', async () => {
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(payload()) })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByTestId('run-case')).toBeInTheDocument())
    expect(within(panel()).getByRole('heading', { name: 'Tenant evidence', level: 2 })).toBeInTheDocument()
    expect(within(panel()).getByRole('heading', { name: 'Case', level: 3 })).toBeInTheDocument()
    expect(within(panel()).getAllByRole('heading', { level: 2 })).toHaveLength(1)
  })

  it('embeds the case panel unless the page already shows it', async () => {
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': stored(payload()) })
    const { unmount } = render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByTestId('run-case')).toBeInTheDocument())
    unmount()
    render(<TenantEvidencePanel runId="r1" showCaseLedger={false} />)
    await waitFor(() => expect(within(panel()).getByTestId('te-endpoint')).toBeInTheDocument())
    expect(within(panel()).queryByTestId('run-case')).toBeNull()
  })
})

// ─── no internal names, and an unknown state is never "never read" ──────────

const failWith = (status, body) => () => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
})

describe('the panel says "this instance" and "the tenant", never internal names', () => {
  it('an answer this console does not recognise names neither SimCore nor the integration', async () => {
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': { run_id: 'r1', snapshot: 'odd' } })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByText(/does not recognise/)).toBeInTheDocument())
    expect(panel().textContent).not.toMatch(/SimCore/)
  })

  it('a read that made no new read for an unknown reason does not say SimCore', async () => {
    installRoutes({
      'GET /api/runs/r1/tenant-evidence': NOT_READ,
      'POST /api/runs/r1/tenant-evidence': postNothingRead('SOME_FUTURE_CODE', 'pending'),
    })
    render(<TenantEvidencePanel runId="r1" />)
    fireEvent.click(refreshButton())
    await waitFor(() => expect(within(panel()).getByText(/SOME_FUTURE_CODE/)).toBeInTheDocument())
    expect(panel().textContent).not.toMatch(/SimCore/)
  })

  it('a failed read whose error text names the integration renders a fixed sentence without it', async () => {
    installRoutes({
      'GET /api/runs/r1/tenant-evidence': NOT_READ,
      'POST /api/runs/r1/tenant-evidence': failWith(500, { detail: {
        error: "integration 'deathstar' secret could not be decrypted with the master key",
        code: 'CREDENTIAL_ERROR', detail: null,
      } }),
    })
    render(<TenantEvidencePanel runId="r1" />)
    fireEvent.click(refreshButton())
    await waitFor(() => expect(within(panel()).getByText(TENANT_EVIDENCE_SENTENCES.CREDENTIAL_ERROR)).toBeInTheDocument())
    expect(panel().textContent).not.toMatch(/deathstar|decrypted/)
  })

  it('an error with an unknown code names the code or status, never its message', async () => {
    installRoutes({
      'GET /api/runs/r1/tenant-evidence': failWith(503, { detail: {
        error: "integration 'deathstar' is locked", code: 'DB_LOCKED', detail: null,
      } }),
    })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByText(/DB_LOCKED/)).toBeInTheDocument())
    expect(panel().textContent).not.toMatch(/deathstar|locked'/)
  })
})

describe('an unread panel says "not read" once', () => {
  it('the quiet line carries it; the sentence below only says what Refresh does', async () => {
    installRoutes({ 'GET /api/runs/r1/tenant-evidence': NOT_READ })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByText(S.TENANT_EVIDENCE_NOT_READ)).toBeInTheDocument())
    expect(S.TENANT_EVIDENCE_NOT_READ).toBe('Refresh reads it once; nothing is read automatically.')
    expect(panel().textContent.match(/Not read from the tenant yet/g)).toHaveLength(1)
  })
})

describe('a stored read that could not be loaded is unknown, not "never read"', () => {
  it('a failed GET says the stored read could not be loaded', async () => {
    installRoutes({
      'GET /api/runs/r1/tenant-evidence': failWith(500, { error: 'boom', code: 'INTERNAL', detail: null }),
    })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(within(panel()).getByText(
      'The stored read could not be loaded, so whether this run was read from the tenant is unknown.',
    )).toBeInTheDocument())
    expect(panel().textContent).not.toMatch(/Not read from the tenant yet/)
    expect(panel().textContent).not.toMatch(/Refresh reads it once/)
  })
})

describe('a read that failed or timed out reloads the stored read', () => {
  it('a rejected POST is followed by one GET and no second POST', async () => {
    const handler = installRoutes({
      'GET /api/runs/r1/tenant-evidence': NOT_READ,
      'POST /api/runs/r1/tenant-evidence': failWith(502, { detail: {
        error: 'pull failed', code: 'CONNECTOR_PULL_FAILED', detail: null,
      } }),
    })
    render(<TenantEvidencePanel runId="r1" />)
    await waitFor(() => expect(calls(handler)).toEqual(['GET /api/runs/r1/tenant-evidence']))
    fireEvent.click(refreshButton())
    await waitFor(() => expect(refreshButton()).not.toBeDisabled())
    await waitFor(() => expect(calls(handler)).toEqual([
      'GET /api/runs/r1/tenant-evidence',
      'POST /api/runs/r1/tenant-evidence',
      'GET /api/runs/r1/tenant-evidence',
    ]))
    expect(within(panel()).getByText(/CONNECTOR_PULL_FAILED/)).toBeInTheDocument()
  })
})
