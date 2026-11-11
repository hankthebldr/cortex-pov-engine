import React, { useId, useState } from 'react'
import { firewallPreflight } from '../../../api/client.js'
import { chipFor, statusLabel } from './fwStatus.js'

/**
 * Step 2 · Check — run the read-only preflight and render the firewall-wide
 * stages as a ladder.
 *
 * Every stage is rendered, in the order the server sent it, with its status,
 * code, detail and remediation. Nothing is folded behind a disclosure: a
 * collapsed blocked stage reads as a passed one at a glance. `queries issued`
 * is printed verbatim, and a report that issued none says it was not answered
 * by a firewall — an injected or short-circuited report must not read as proof.
 */

export const PROVES_FOOTER =
  'Proves: the firewall is configured to write and forward these records. Does not prove: the tenant ingested them, or they stitched — tenant-verified: 0'
export const NOT_EVALUATED_LINE =
  'url-filtering and wildfire-analysis profile actions are not evaluated in this version; a rule that attaches them reads "not evaluated", not alert or block.'

export default function CheckStep({ firewall, agents = [], report, onReport }) {
  const uid = useId()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [picked, setPicked] = useState([])

  if (!firewall) {
    return (
      <div className="fw-empty" data-status="degraded">
        <span className="chip chip--pending">DEGRADED</span>{' '}
        No firewall is registered or selected, so nothing about its logging or policy path has
        been checked. Register one above.
      </div>
    )
  }

  const agentIds = (agents || []).map((a) => a?.agent_id).filter(Boolean)
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))

  const run = async () => {
    setBusy(true)
    setError(null)
    // Drop the previous verdict: a stale ladder under "Checking…" reads as current.
    onReport?.(null)
    try {
      const body = await firewallPreflight(firewall, picked.length ? picked : null)
      onReport?.(body)
    } catch (err) {
      onReport?.(null)
      setError({ message: err?.message || 'preflight failed', code: err?.code || null })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fw-checkstep">
      <div className="fw-checkstep__controls">
        {agentIds.length > 0 && (
          <fieldset className="fw-checkstep__agents">
            <legend className="pov-field__k">Agents to check (none ticked = every enrolled agent)</legend>
            {agentIds.map((id) => (
              <label key={id} className="fw-check" htmlFor={`${uid}-${id}`}>
                <input id={`${uid}-${id}`} type="checkbox" checked={picked.includes(id)}
                  onChange={() => toggle(id)} />
                <span className="mono">{id}</span>
              </label>
            ))}
          </fieldset>
        )}
        <button type="button" className="btn btn--primary" onClick={run} disabled={busy}>
          {busy ? 'Checking…' : `Run preflight on ${firewall}`}
        </button>
        <p className="fw-note">
          Read-only: <span className="mono">show</span> and <span className="mono">test</span>{' '}
          commands and config reads only. Policy lookups send no packets.
        </p>
      </div>

      {error && (
        <div className="fw-error" role="alert">
          preflight failed: {error.message}
          {error.code && !error.message.includes(error.code) && <> [<span className="mono">{error.code}</span>]</>}
        </div>
      )}

      {/* Mounted before any report so assistive tech is already listening when
          the verdict lands; it holds the busy state, then the verdict line. */}
      <div className="fw-report__live" role="status" aria-live="polite" data-testid="fw-check-live">
        {busy ? 'Checking the firewall…' : (report ? <ReportHead report={report} /> : null)}
      </div>
      {!busy && report && report.overall === 'blocked' && (
        <div className="fw-sr-alert" role="alert">Preflight result: blocked.</div>
      )}

      {report && <Ladder report={report} />}
    </div>
  )
}

/** A report nobody answered never reads green, whatever `overall` says. */
function unanswered(report) {
  return typeof report.queries_issued !== 'number' || report.queries_issued === 0
}

function ReportHead({ report }) {
  const n = report.queries_issued
  const chip = unanswered(report) ? 'chip chip--pending' : chipFor(report.overall)
  return (
    <div className="fw-report__head">
      <span className={chip} data-testid="fw-overall">{statusLabel(report.overall)}</span>
      <span className="mono">
        {typeof n === 'number' ? `queries issued: ${n}` : 'queries issued: not reported'}
      </span>
      {unanswered(report) && (
        <span className="fw-report__unanswered">this report was not answered by a firewall</span>
      )}
      {report.mgmt_host && <span className="mono">host {report.mgmt_host}</span>}
      {report.panos_version && <span className="mono">PAN-OS {report.panos_version}</span>}
      {report.config_source && <span className="mono">config: {report.config_source}</span>}
    </div>
  )
}

function Ladder({ report }) {
  const stages = Array.isArray(report.stages) ? report.stages : []
  return (
    <div className="fw-report">
      {stages.length === 0 ? (
        <div className="fw-empty" data-status="degraded">
          <span className="chip chip--pending">DEGRADED</span> the report carried no stages, so
          nothing was checked.
        </div>
      ) : (
        <ol className="fw-ladder" data-testid="fw-ladder">
          {stages.map((s, i) => (
            <li key={`${s.id}-${i}`} className={`rdy-stage rdy-stage--${s.status} fw-stage fw-stage--${s.status}`}
              data-status={s.status}>
              <span className="rdy-stage__id mono">{s.id}</span>
              <span className={chipFor(s.status)}>{statusLabel(s.status)}</span>
              {s.code && <span className="mono fw-stage__code">{s.code}</span>}
              <span className="rdy-stage__detail">{s.detail || ''}</span>
              {s.remediation && <div className="rdy-stage__fix">{s.remediation}</div>}
            </li>
          ))}
        </ol>
      )}

      <div className="readiness__invariant fw-proves" data-testid="fw-proves">
        <p className="fw-proves__line">{PROVES_FOOTER}</p>
        <p className="fw-proves__line">Does not prove: {NOT_EVALUATED_LINE}</p>
        {report.proves && <p className="fw-proves__server mono">{report.proves}</p>}
      </div>
    </div>
  )
}
