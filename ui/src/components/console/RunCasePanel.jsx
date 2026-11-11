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

/** xdr_url comes from the tenant: link only https on *.paloaltonetworks.com. */
export function safeXdrUrl(url) {
  if (typeof url !== 'string') return null
  try {
    const u = new URL(url)
    return u.protocol === 'https:' && u.hostname.toLowerCase().endsWith('.paloaltonetworks.com')
      ? url
      : null
  } catch {
    return null
  }
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
  const cases = Array.isArray(ledger.cases) ? ledger.cases : []
  const sharedOther = cases.reduce((n, c) => n + (Number(c.issues_other) || 0), 0)
  const issueRead = ledger.issue_read
  const issueReadFailed = issueRead && typeof issueRead === 'object' && issueRead.ok === false

  return (
    <section className="run-case" aria-label="Case" data-testid="run-case">
      <h2 className="run-case__h">Case</h2>
      <div className="run-case__merge">
        <span className="run-case__chip mono">{ledger.merge_class || '—'}</span>
        <span className="run-case__reason">{ledger.merge_reason || '—'}</span>
      </div>
      <p className="run-case__meta mono">
        read {ledger.read_at || '—'} · {ledger.integration || '—'} · {ledger.basis || '—'}
      </p>

      {ledger.code && ledger.code !== 'CASE_LEDGER_OK' && (
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
