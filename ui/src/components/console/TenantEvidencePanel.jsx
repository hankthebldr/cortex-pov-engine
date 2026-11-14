/**
 * TenantEvidencePanel — what the tenant itself holds for this run, read on
 * request only (spec §7.5; design session 2026-11-13).
 *
 * Display only. Reading the tenant here never changes a score, an outcome, MTTD
 * or a verdict: it stores a snapshot beside the run (`tenant_evidence_reads`).
 * Opening the page reads the STORED snapshot once (GET, no outbound call); the
 * icon-only refresh button is the only thing that reads the tenant, one
 * metered read per click (useTenantEvidence).
 *
 * Honesty rules this file keeps:
 *   - every degraded state is a sentence (TENANT_EVIDENCE_SENTENCES), never an
 *     empty table, and nothing here calls a detection missed (A1);
 *   - a count the read did not measure is "unknown", never 0;
 *   - tenant-written text (issue, case and policy names) renders as text, and
 *     a tenant link is linked only through safeXdrUrl;
 *   - no internal name appears: the page says "this instance" and "the tenant",
 *     never the integration's name, and never a request error's own text
 *     (it can quote the credential store); errors render by code.
 */
import React from 'react'
import useTenantEvidence from './useTenantEvidence.js'
import RunCasePanel, { safeXdrUrl } from './RunCasePanel.jsx'
import { Button, Icon, StatusIcon } from './kit/index.js'
import '../../styles/destinations/run-detail.css'

void React

/** One sentence per degraded state (spec §7.5). The templates take the numbers. */
export const TENANT_EVIDENCE_SENTENCES = Object.freeze({
  TENANT_EVIDENCE_NOT_READ:
    'Not read from the tenant yet. Refresh reads it once; nothing is read automatically.',
  NO_TENANT_CONFIGURED:
    "No tenant credential is configured, so nothing was read. This says nothing about the customer's coverage.",
  UNSCOPED:
    "The run's host is unknown, so no issue is credited to this run; every issue below is unattributed.",
  NO_ATTRIBUTED_ISSUES:
    "The tenant answered, but no issue names this run's host in this window. Check ingestion latency, "
    + 'the hostname, the window and the Security domain filter before drawing any conclusion about detection.',
  // True for both causes SimCore answers it for: no step with an execution
  // time, and a push run (refused before its steps are looked at).
  NO_RUN_WINDOW:
    'This run has no execution window this instance can read: no step has an execution time, '
    + 'or it is a push run whose bundle ran offline. Nothing was read from the tenant.',
  TRUNCATED: 'The tenant holds more issues in this window than were read; counts are floors.',
  TRUNCATION_UNKNOWN: 'Truncation unknown: the tenant sent no FILTER_COUNT.',
  QUOTA_REFUSED: (n) => `The request budget for this tenant refused the read; ${
    n == null ? 'retry later' : `retry in ${n} s`
  }. Rows already read are kept and counts are floors.`,
  SECTION_FAILED: (section, code, remediation) =>
    `${section} read failed (${code})${remediation ? `: ${remediation}` : ''}`,
  TENANT_EVIDENCE_READ_RECENTLY: (n) => `This run was read from the tenant recently, so that read is shown. ${
    n == null ? 'A new read is possible shortly.' : `A new read is possible in ${n} s.`
  }`,
  // Request failures, by code. Never the error's own text.
  CREDENTIAL_ERROR: 'The tenant credential on this instance could not be opened, so nothing was read from the tenant.',
  INTEGRATION_NOT_FOUND: 'The tenant credential this read names is not on this instance, so nothing was read from the tenant.',
  RUN_NOT_FOUND: 'This instance has no record of this run.',
  REQUEST_TIMEOUT: 'This instance did not answer the read in time. It may still finish it, so the stored read is reloaded.',
  LOAD_FAILED_LINE: 'The stored read could not be loaded, so whether this run was read from the tenant is unknown.',
})

const S = TENANT_EVIDENCE_SENTENCES

/** Section codes that are a complete answer (core/connectors/tenant_evidence.py::_ANSWERED). */
const ANSWERED = new Set(['OK', 'UNSCOPED', 'ENDPOINT_NOT_FOUND', 'AMBIGUOUS_ENDPOINT_RECORDS'])
const QUOTA = 'QUOTA_REFUSED'
/** POST answers that read nothing and stored nothing: the GET after them cannot say why. */
const NOTHING_STORED = new Set(['NO_TENANT_CONFIGURED', 'NO_RUN_WINDOW'])

/**
 * One fixed sentence for a failed request, from its code (or HTTP status).
 * `during` is 'read' (the POST) or 'load' (the GET of the stored read).
 */
function errorSentence(err, during) {
  if (!err) return null
  const code = err.code === 'NO_INTEGRATION' ? 'NO_TENANT_CONFIGURED' : err.code
  if (code === 'RUN_NOT_FOUND') return S.RUN_NOT_FOUND
  if (during === 'read' && code && typeof S[code] === 'string') return S[code]
  const ref = code || (err.status ? `HTTP ${err.status}` : 'no answer from this instance')
  return during === 'read'
    ? `The read from the tenant failed (${ref}).`
    : `Loading the stored read failed (${ref}).`
}

/** A count the read did not measure is unknown, never 0. */
function count(n) {
  return typeof n === 'number' ? String(n) : 'unknown'
}

/** Stored timestamps are naive UTC ISO strings; never pass them through Date (it reads them as local). */
function utc(iso) {
  return typeof iso === 'string' && iso.length >= 16 ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : '—'
}

function text(v) {
  return v == null || v === '' ? '—' : String(v)
}

/** The snapshot payload, or null; `undefined` when the answer is a shape this console does not know. */
function payloadOf(data) {
  if (!data || typeof data !== 'object' || typeof data.code !== 'string') return undefined
  if (data.snapshot == null) return null
  const p = data.snapshot.payload
  return p && typeof p === 'object' && typeof p.code === 'string' ? p : undefined
}

function headStatus(p) {
  if (p.status === 'ok') return { status: 'up', word: 'Read' }
  if (p.status === 'degraded') return { status: 'degraded', word: 'Degraded' }
  return { status: 'unknown', word: 'Unknown' }
}

function notesFor(data, payload, lastRead, readError, loadError) {
  const notes = []
  for (const n of [errorSentence(readError, 'read'), errorSentence(loadError, 'load')]) {
    if (n && !notes.includes(n)) notes.push(n)
  }
  if (payload === undefined && data) {
    notes.push('This instance sent tenant evidence in a shape this console does not recognise, so none of it is shown.')
    return notes
  }
  const attempt = lastRead && lastRead.read === false ? lastRead : null
  if (attempt) {
    if (NOTHING_STORED.has(attempt.code)) notes.push(S[attempt.code])
    else if (attempt.code === 'TENANT_EVIDENCE_READ_RECENTLY') {
      notes.push(S.TENANT_EVIDENCE_READ_RECENTLY(attempt.retry_after_seconds))
    } else notes.push(`This instance made no new read of the tenant (${attempt.code}).`)
  }
  if (!payload) {
    if (data && !(attempt && NOTHING_STORED.has(attempt.code))) notes.push(S.TENANT_EVIDENCE_NOT_READ)
    return notes
  }

  if (payload.code === 'UNSCOPED') notes.push(S.UNSCOPED)
  if (payload.code === 'NO_ATTRIBUTED_ISSUES') notes.push(S.NO_ATTRIBUTED_ISSUES)
  const issues = payload.issues
  if (issues && issues.code === 'OK') {
    if (issues.truncated === true) notes.push(S.TRUNCATED)
    else if (issues.truncated == null) notes.push(S.TRUNCATION_UNKNOWN)
  }
  const sections = [['Issue', issues], ['Case', payload.cases], ['Endpoint', payload.endpoint]]
  const quota = sections.map(([, s]) => s).filter((s) => s && s.code === QUOTA)
  if (payload.code === QUOTA || quota.length > 0) {
    const n = quota.map((s) => s.retry_after_seconds).find((v) => typeof v === 'number')
      ?? (typeof lastRead?.retry_after_seconds === 'number' ? lastRead.retry_after_seconds : null)
    notes.push(S.QUOTA_REFUSED(n))
  }
  for (const [name, s] of sections) {
    if (s && typeof s.code === 'string' && !ANSWERED.has(s.code) && s.code !== QUOTA) {
      notes.push(S.SECTION_FAILED(name, s.code, s.remediation))
    }
  }
  const probe = payload.agreement?.id_space?.code
  if (typeof probe === 'string' && !ANSWERED.has(probe) && probe !== QUOTA) {
    notes.push(S.SECTION_FAILED('Issue id probe', probe, null))
  }
  return notes
}

export default function TenantEvidencePanel({ runId, showCaseLedger = true, onScored = () => {} }) {
  // `onScored` is for an action that may change the run's score (Reconcile,
  // Task 11). The read here never does, so it never calls it.
  void onScored
  const { data, lastRead, loading, reading, readError, loadError, read } = useTenantEvidence(runId)
  if (!runId) return null

  const payload = payloadOf(data)
  const notes = notesFor(data, payload, lastRead, readError, loadError)
  // Unknown is not "never read": with no stored answer and a failed GET, say so.
  const line = reading
    ? 'Reading from the tenant…'
    : payload
      ? `Read from the tenant at ${utc(payload.read_at)} UTC · display only, does not change the verdict`
      : !data && loadError
        ? S.LOAD_FAILED_LINE
        : loading && !data ? 'Loading…' : 'Not read from the tenant yet'

  return (
    <section className="tenant-evidence" aria-labelledby="tenant-evidence-title" data-testid="tenant-evidence">
      <div className="tenant-evidence__head">
        <h2 className="tenant-evidence__title" id="tenant-evidence-title">Tenant evidence</h2>
        {payload && <StatusIcon {...headStatus(payload)} />}
        <Button
          variant="secondary"
          className="tenant-evidence__refresh"
          aria-label="Refresh tenant evidence"
          title="Refresh tenant evidence"
          disabled={reading}
          aria-busy={reading || undefined}
          onClick={() => { read() }}
        >
          <Icon name="repeat" size={14} />
        </Button>
      </div>
      <p className="tenant-evidence__line">{line}</p>

      {notes.length > 0 && (
        <ul className="tenant-evidence__notes" role="status">
          {notes.map((n, i) => <li key={i} className="tenant-evidence__note">{n}</li>)}
        </ul>
      )}

      {payload && (
        <>
          <Agreement payload={payload} />
          <Issues payload={payload} />
          <Cases cases={payload.cases} />
        </>
      )}
      {showCaseLedger && data && payload !== undefined && (
        <div className="tenant-evidence__block tenant-evidence__block--case">
          <RunCasePanel ledger={data.case_ledger ?? null} headingLevel={3} />
        </div>
      )}
      {payload && (
        <>
          <Endpoint endpoint={payload.endpoint} bridge={data?.case_ledger?.bridge} />
          <p className="tenant-evidence__foot mono">
            {typeof payload.queries_issued === 'number'
              ? `${payload.queries_issued} tenant ${payload.queries_issued === 1 ? 'request' : 'requests'} in this read`
              : 'tenant requests in this read: unknown'}
          </p>
          {payload.data_notice && <p className="tenant-evidence__notice">{payload.data_notice}</p>}
        </>
      )}
    </section>
  )
}

function Block({ label, testId, children }) {
  return (
    <div className="tenant-evidence__block" data-testid={testId}>
      <div className="tenant-evidence__label">{label}</div>
      <div className="tenant-evidence__body">{children}</div>
    </div>
  )
}

function Agreement({ payload }) {
  const a = payload.agreement
  const statement = payload.statement
    || 'Two reads of the same tenant; agreement is not tenant verification.'
  let probe = null
  if (a) {
    const sp = a.id_space || {}
    probe = !sp.probed
      ? 'id probe: no alert id to probe'
      : typeof sp.hit === 'number'
        ? `id probe: ${sp.hit} of ${sp.probed} found (an id not found does not mean the ids differ)`
        : `id probe: ${sp.probed} probed, result unknown`
  }
  return (
    <Block label="Agreement" testId="te-agreement">
      {a ? (
        <>
          <p className="tenant-evidence__meta mono">
            both: by id {count(a.both?.id)} · by name, host and window {count(a.both?.name_host_window)}
            {' · '}matcher only {count(a.matcher_only)} · reader only {count(a.reader_only)}
          </p>
          <p className="tenant-evidence__meta mono">
            unjoinable {count(a.unjoinable)} · ambiguous {count(a.ambiguous)} · {probe}
          </p>
        </>
      ) : (
        <p className="tenant-evidence__meta">Not measured: the issue read did not complete.</p>
      )}
      <p className="tenant-evidence__statement">{statement}</p>
    </Block>
  )
}

function IssueTable({ rows, credited }) {
  return (
    <div className="tenant-evidence__scroll">
      <table className="tenant-evidence__table">
        <thead>
          <tr>
            <th scope="col">Issue</th>
            <th scope="col">Name</th>
            <th scope="col">Method</th>
            <th scope="col">Rule</th>
            <th scope="col">Severity</th>
            <th scope="col">Case</th>
            <th scope="col">After step</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.id}-${i}`} data-testid={credited ? 'te-issue-row' : 'te-unattributed-row'}>
              <td className="mono">{text(r.id)}</td>
              <td>{text(r.tenant_text?.name)}</td>
              <td className="mono">{text(r.detection_method)}</td>
              <td className="mono">{text(r.rule_id)}</td>
              <td>{text(r.severity)}</td>
              <td className="mono">{Array.isArray(r.case_ids) && r.case_ids.length ? r.case_ids.join(', ') : '—'}</td>
              <td className="mono">
                {typeof r.observed_after_step_seconds === 'number' ? `${r.observed_after_step_seconds} s` : '—'}
              </td>
              <td>{text(r.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Issues({ payload }) {
  const s = payload.issues
  const scope = s?.host_scope
  const rows = Array.isArray(s?.rows) ? s.rows : []
  const unattributed = Array.isArray(s?.unattributed_rows) ? s.unattributed_rows : []
  const window = Array.isArray(payload.window) ? payload.window : []
  const unknownMethods = s?.unrecognised_values?.['detection.method']
  return (
    <Block label="Issues" testId="te-issues">
      {scope ? (
        <p className="tenant-evidence__meta mono">
          host {scope.host ? scope.host : 'unknown'} · window {utc(window[0])} – {utc(window[1])} UTC
          {' · '}attributed {count(scope.attributed)} · other host {count(scope.other_host)}
          {' · '}no host {count(scope.without_host)} (not credited)
        </p>
      ) : (
        <p className="tenant-evidence__meta">Not read.</p>
      )}
      {rows.length > 0 && <IssueTable rows={rows} credited />}
      {Array.isArray(unknownMethods) && unknownMethods.length > 0 && (
        <p className="tenant-evidence__meta">
          Detection methods this console does not know, kept as the tenant sent them:{' '}
          <span className="mono">{unknownMethods.join(', ')}</span>
        </p>
      )}
      {unattributed.length > 0 && (
        <div className="tenant-evidence__sub" data-testid="te-not-credited">
          <h3 className="tenant-evidence__subtitle">Not credited</h3>
          <p className="tenant-evidence__meta">
            Issues in this window that this run cannot claim. They are listed for context only.
          </p>
          <IssueTable rows={unattributed} credited={false} />
        </div>
      )}
    </Block>
  )
}

function Cases({ cases }) {
  const rows = Array.isArray(cases?.rows) ? cases.rows : []
  let empty = null
  if (!cases) empty = 'Not read.'
  else if (cases.code === 'UNSCOPED') empty = 'No run host, so no case was read.'
  else if (rows.length === 0) empty = cases.code === 'OK' ? 'No credited issue names a case.' : 'Not read.'
  return (
    <Block label="Cases" testId="te-cases">
      {empty ? (
        <p className="tenant-evidence__meta">{empty}</p>
      ) : (
        <div className="tenant-evidence__scroll">
          <table className="tenant-evidence__table">
            <thead>
              <tr>
                <th scope="col">Case</th>
                <th scope="col">Name</th>
                <th scope="col">Methods</th>
                <th scope="col">Issues seen</th>
                <th scope="col">Link</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c, i) => {
                const href = safeXdrUrl(c.xdr_url)
                const methods = Array.isArray(c.methods_seen) && c.methods_seen.length
                  ? c.methods_seen.join(' + ') : '—'
                return (
                  <tr key={`${c.case_id}-${i}`} data-testid="te-case-row">
                    <td className="mono">{text(c.case_id)}</td>
                    <td>{text(c.tenant_text?.name)}</td>
                    <td className="mono">
                      {methods}
                      {c.grouped && <span className="tenant-evidence__chip">grouped</span>}
                    </td>
                    <td className="mono">{count(c.issues_seen)} of {count(c.issue_count)}</td>
                    <td>
                      {href ? (
                        <a href={href} target="_blank" rel="noopener noreferrer">open in XSIAM</a>
                      ) : c.xdr_url || c.xdr_url_withheld ? 'link withheld' : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {cases?.ids_capped && (
        <p className="tenant-evidence__meta">Only the first {count(cases.requested)} case ids were read.</p>
      )}
    </Block>
  )
}

const ENDPOINT_EMPTY = {
  UNSCOPED: 'No run host, so no endpoint record was read.',
  ENDPOINT_NOT_FOUND: "The tenant returned no endpoint record for this run's host.",
}

function Endpoint({ endpoint, bridge }) {
  const r = endpoint?.record
  let empty = null
  if (!r) {
    if (endpoint?.code === 'AMBIGUOUS_ENDPOINT_RECORDS') {
      const n = Array.isArray(endpoint.records) ? endpoint.records.length : null
      empty = `The tenant holds ${count(n)} endpoint records for this run's host; none is claimed as this run's endpoint.`
    } else {
      empty = ENDPOINT_EMPTY[endpoint?.code] || 'Not read.'
    }
  }
  const fields = r ? [
    ['status', r.endpoint_status],
    ['operational', r.operational_status],
    ['agent', r.endpoint_version],
    ['content', r.content_version],
    ['policy', r.tenant_text?.assigned_prevention_policy],
    ['last seen', r.last_seen ? `${utc(r.last_seen)} UTC` : null],
    ['isolated', r.is_isolated],
  ] : []
  return (
    <Block label="Endpoint" testId="te-endpoint">
      <p className="tenant-evidence__meta">as of read {endpoint?.as_of ? `${utc(endpoint.as_of)} UTC` : ''}</p>
      {r ? (
        <dl className="tenant-evidence__kv">
          {fields.map(([k, v]) => (
            <div key={k} className="tenant-evidence__kv-row">
              <dt>{k}</dt>
              <dd>{text(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="tenant-evidence__meta">{empty}</p>
      )}
      {bridge && typeof bridge.code === 'string' && (
        <dl className="tenant-evidence__kv">
          <div className="tenant-evidence__kv-row">
            <dt>ip-to-host bridge</dt>
            <dd className="mono">{bridge.code}</dd>
          </div>
        </dl>
      )}
    </Block>
  )
}
