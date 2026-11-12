import React from 'react'

/**
 * RunCasePanel — which XSIAM cases this run's alerts landed in.
 *
 * Renders `run.case_ledger` exactly as stored; it invents nothing. Tenant
 * disposition is context, never detection evidence, so there is deliberately
 * no detected/missed or pass/fail styling here, and the disclaimer is always
 * present. Keep this string identical to `CASE_DISCLAIMER` in
 * core/connectors/case_ledger.py (pinned by RunCasePanel.test.jsx).
 */
export const CASE_DISCLAIMER =
  'Tenant disposition is shown for context. It is not detection evidence.'

/**
 * NO_CASE_YET advances only on a reconcile: a refresh re-reads the cases a
 * ledger names, and this one names none. Keep identical to `NO_CASE_YET_HINT`
 * in core/connectors/case_ledger.py.
 */
export const NO_CASE_YET_HINT =
  'No case yet: the next reconcile re-reads these alerts and rebuilds the ledger once the ' +
  'tenant has grouped them into a case. A refresh re-reads cases only, and this ledger names none.'

/**
 * xdr_url comes from the tenant: link only plain https on paloaltonetworks.com.
 * Mirrors the backend (core/api/runs.py::_safe_xdr_url): whitespace, control
 * characters, backslash, markup metacharacters, userinfo and a port all reject
 * (`new URL` alone would accept a backslash and read userinfo past it).
 */
export function safeXdrUrl(url) {
  if (typeof url !== 'string' || !url) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u0020\u007f\\()<>`|[\]"']/.test(url)) return null
  try {
    const u = new URL(url)
    const host = u.hostname.toLowerCase()
    if (u.protocol !== 'https:' || u.port || u.username || u.password) return null
    if (host !== 'paloaltonetworks.com' && !host.endsWith('.paloaltonetworks.com')) return null
    return u.href
  } catch {
    return null
  }
}

/** A stored ledger missing a required key (or with the wrong type) is degraded. */
export function ledgerUnreadableReason(l) {
  if (!l || typeof l !== 'object' || Array.isArray(l)) return 'stored ledger is not an object'
  if (typeof l.merge_class !== 'string' || !l.merge_class) return 'merge_class missing or not a string'
  if (!Array.isArray(l.cases)) return 'cases missing or not a list'
  if (!l.cases.every((c) => c && typeof c === 'object' && !Array.isArray(c))) {
    return 'cases contains a non-object entry'
  }
  if (typeof l.code !== 'string' || !l.code) return 'code missing or not a string'
  return null
}

export default function RunCasePanel({ ledger = null }) {
  if (!ledger || typeof ledger !== 'object') {
    return (
      <section className="run-case" aria-label="Case" data-testid="run-case">
        <h2 className="run-case__h">Case</h2>
        <p className="run-case__empty">Not read yet — reconcile the run.</p>
        <p className="run-case__disclaimer">{CASE_DISCLAIMER}</p>
      </section>
    )
  }
  const unreadable = ledgerUnreadableReason(ledger)
  if (unreadable) {
    return (
      <section className="run-case" aria-label="Case" data-testid="run-case">
        <h2 className="run-case__h">Case</h2>
        <p className="run-case__warn" role="alert" data-testid="run-case-unreadable">
          Ledger unreadable: {unreadable}. Reconcile the run to rebuild it.
        </p>
        <p className="run-case__disclaimer">{CASE_DISCLAIMER}</p>
      </section>
    )
  }
  const cases = ledger.cases
  const sharedOther = cases.reduce((n, c) => n + (Number(c.issues_other) || 0), 0)
  const issueRead = ledger.issue_read
  const issueReadFailed = issueRead && typeof issueRead === 'object' && issueRead.ok === false

  return (
    <section className="run-case" aria-label="Case" data-testid="run-case">
      <h2 className="run-case__h">Case</h2>
      <div className="run-case__merge">
        <span className="run-case__chip mono">{ledger.merge_class}</span>
        <span className="run-case__reason">{ledger.merge_reason || '—'}</span>
      </div>
      <p className="run-case__meta mono">
        {/* Stored timestamps are naive UTC ISO strings: say so where they are shown. */}
        read {ledger.read_at ? `${ledger.read_at} UTC` : '—'} · {ledger.integration || '—'} · {ledger.basis || '—'}
      </p>

      {ledger.merge_class === 'NO_CASE_YET' && (
        <p className="run-case__meta" data-testid="run-case-no-case-yet">{NO_CASE_YET_HINT}</p>
      )}
      {ledger.code !== 'CASE_LEDGER_OK' && (
        <p className="run-case__warn" role="status">
          Ledger status: <span className="mono">{ledger.code}</span>
        </p>
      )}
      {issueReadFailed && (
        <p className="run-case__warn" role="status">
          Issue-status read failed: <span className="mono">{issueRead.code || '—'}</span>
          {' — '}{issueRead.remediation || 'no remediation recorded'}
        </p>
      )}

      {cases.length > 0 && (
        <table className="run-case__table">
          <thead>
            <tr>
              <th scope="col">Case</th>
              <th scope="col">Status</th>
              <th scope="col">Closed by</th>
              <th scope="col">Sealed</th>
              <th scope="col">Issues (run / other)</th>
              <th scope="col">Link</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => {
              const safe = safeXdrUrl(c.xdr_url)
              return (
                <tr key={c.case_id} data-testid="run-case-row">
                  <td className="mono">{c.case_id}</td>
                  <td>{c.status || '—'}</td>
                  <td><span className="run-case__chip mono">{c.closed_by || '—'}</span></td>
                  <td>{c.sealed ? 'yes' : 'no'}</td>
                  <td className="mono">{c.issues_from_run ?? 0} / {c.issues_other ?? 0}</td>
                  <td>
                    {safe ? (
                      <a href={safe} target="_blank" rel="noopener noreferrer">open in XSIAM</a>
                    ) : c.xdr_url ? (
                      <span className="mono">{String(c.xdr_url)}</span>
                    ) : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {ledger.shared_with_other_runs && (
        <p className="run-case__shared" data-testid="run-case-shared">
          Shared with other runs: {sharedOther} issues from other runs sit in these cases.
        </p>
      )}
      <p className="run-case__disclaimer">{CASE_DISCLAIMER}</p>
    </section>
  )
}
