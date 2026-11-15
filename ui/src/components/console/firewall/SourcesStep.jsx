import React, { useEffect, useId, useRef, useState } from 'react'
import { putFirewallSource } from '../../../api/client.js'
import {
  chipFor, statusLabel, sourceStatus, srcProvenanceLabel, addrProvenanceLabel,
  EVALUATED_PROFILES, NOT_EVALUATED_PROFILES,
} from './fwStatus.js'

/**
 * Step 3 · Sources — one row per enrolled agent: does the firewall see this
 * host the way the XDR agent records it, and what does its policy path do to
 * the probe destinations.
 *
 * The stitch contract: endpoint and NGFW records stitch only when the 5-tuple
 * agrees. A NAT between the agent and the firewall, or two agents behind one
 * observed address, means no stitch can form, so those rows render blocked
 * whatever else is green. Every per-source stage is shown, never collapsed.
 */

export const SOURCES_FOOTER =
  'Proves: what this firewall\'s running config does to these sources and probe destinations. Does not prove: that any session was logged, ingested or stitched; url-filtering or wildfire-analysis actions (not evaluated) — tenant-verified: 0'

function summary(report) {
  if (!report) return 'Run the check to see each enrolled agent\'s path through the firewall.'
  const rows = (Array.isArray(report.sources) ? report.sources : []).filter((x) => x && x.agent_id)
  if (rows.length === 0) return 'No agent source rows in this report.'
  const count = (st) => rows.filter((r) => sourceStatus(r) === st).length
  return `${rows.length} source${rows.length === 1 ? '' : 's'}: ${count('blocked')} blocked, `
    + `${count('degraded')} degraded, ${count('ok')} ok.`
}

export default function SourcesStep({ firewall, report }) {
  // Mounted before any report, so the arrival of source rows is announced.
  const live = (
    <p className="fw-sources__live" role="status" aria-live="polite" data-testid="fw-sources-live">
      {summary(report)}
    </p>
  )
  if (!report) return live
  const sources = Array.isArray(report.sources) ? report.sources : []
  if (sources.length === 0) {
    return (
      <>
        {live}
        <div className="fw-empty" data-status="degraded" data-testid="fw-sources-entry">
          <span className="chip chip--pending">DEGRADED</span> the report carried no sources
          section, so no agent&apos;s path was checked.
        </div>
      </>
    )
  }
  return (
    <div className="fw-sources">
      {live}
      {sources.map((src, i) => (src && src.agent_id
        ? <SourceRow key={src.agent_id} firewall={firewall} src={src} />
        : <SourceEntry key={`entry-${i}`} entry={src || {}} />))}
      <div className="readiness__invariant fw-proves">
        <p className="fw-proves__line">{SOURCES_FOOTER}</p>
      </div>
    </div>
  )
}

/** A sources entry that is not an agent (PN_NO_AGENTS, PN_SKIPPED_CONFIG). */
function SourceEntry({ entry }) {
  const status = entry.status || 'unknown'
  return (
    <div className={`fw-empty fw-empty--${status}`} data-status={status} data-testid="fw-sources-entry">
      <span className={chipFor(status)}>{statusLabel(status)}</span>{' '}
      {entry.code && <span className="mono">{entry.code}</span>}{' '}
      {entry.detail || ''}
    </div>
  )
}

function SourceRow({ firewall, src }) {
  const status = sourceStatus(src)
  const host = src.hostname || src.agent_id
  const addrs = Array.isArray(src.agent_addresses) ? src.agent_addresses : []
  const collisions = Array.isArray(src.collision_with) ? src.collision_with : []
  const stages = Array.isArray(src.stages) ? src.stages : []
  const probes = Array.isArray(src.probes) ? src.probes : []
  return (
    <section className={`fw-source fw-source--${status}`} data-status={status}
      data-testid={`fw-source-${src.agent_id}`} aria-label={`Source ${host}`}>
      <div className="fw-source__head">
        <span className="fw-source__host">{host}</span>
        <span className="mono fw-source__id">{src.agent_id}</span>
        <span className={chipFor(status)}>{statusLabel(status)}</span>
      </div>

      <dl className="fw-facts">
        <div>
          <dt>Agent-side address</dt>
          <dd>
            <span className="mono">{addrs.length ? addrs.join(', ') : 'unknown'}</span>{' '}
            <span className="fw-prov">({addrProvenanceLabel(src.agent_addr_provenance)})</span>
          </dd>
        </div>
        <div>
          <dt>Firewall-observed source</dt>
          <dd>
            <span className="mono">{src.fw_observed_src || 'unknown'}</span>{' '}
            <span className="fw-prov">({srcProvenanceLabel(src.fw_src_provenance)})</span>
          </dd>
        </div>
        <div>
          <dt>NAT verdict</dt>
          <dd>
            <span className={src.nat_verdict === 'tuple_intact' ? 'chip chip--detected'
              : src.nat_verdict === 'nat_between' ? 'chip chip--missed' : 'chip chip--pending'}>
              {src.nat_verdict || 'unknown'}
            </span>
          </dd>
        </div>
        <div>
          <dt>Collision</dt>
          <dd>
            {collisions.length
              ? <span className="fw-collision">same observed source as <span className="mono">{collisions.join(', ')}</span></span>
              : <span className="mono">none</span>}
          </dd>
        </div>
      </dl>

      <SourceEditor firewall={firewall} src={src} host={host} />

      {stages.length > 0 && (
        <ol className="fw-ladder fw-ladder--source">
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

      {probes.length > 0 && <ProbeTable probes={probes} host={host} />}
    </section>
  )
}

function bucketOf(p) {
  if (!p) return 'none'
  return p.bucket || 'unknown'
}

function ProbeTable({ probes, host }) {
  return (
    <div className="fw-probes">
      <table className="fw-probes__table">
        <caption className="fw-sub">Policy path per probe for {host} (lookups only, no packets sent)</caption>
        <thead>
          <tr>
            <th scope="col">Probe</th>
            <th scope="col">Rule</th>
            <th scope="col">Action</th>
            <th scope="col">Profiles</th>
            <th scope="col">DNS Security</th>
            <th scope="col">Log forwarding</th>
          </tr>
        </thead>
        <tbody>
          {probes.map((p, i) => {
            const profiles = p.profiles || {}
            return (
              <tr key={`${p.destination}-${p.port}-${p.protocol}-${i}`}>
                <td className="mono">
                  {p.destination}:{p.port}/{p.protocol} {p.application}
                  {p.skipped && <div className="fw-note">not checked: {p.skipped}</div>}
                </td>
                <td className="mono">
                  {p.rule ? `${p.rule}${p.rule_index != null ? ` (#${p.rule_index})` : ''}` : 'no match'}
                  {p.profile_group && <div className="fw-note">group {p.profile_group}</div>}
                </td>
                <td className="mono">{p.action || '—'}</td>
                <td>
                  <ul className="fw-profiles">
                    {EVALUATED_PROFILES.map((t) => {
                      const prof = profiles[t]
                      const scoped = Array.isArray(prof?.scoped_rules) ? prof.scoped_rules.length : 0
                      return (
                        <li key={t}>
                          <span className="mono">{t}</span>: {prof?.profile ? <span className="mono">{prof.profile}</span> : 'none'}
                          {' → '}<span className="mono">{bucketOf(prof)}</span>
                          {scoped > 0 && <span className="fw-note"> ({scoped} scoped rule{scoped === 1 ? '' : 's'})</span>}
                        </li>
                      )
                    })}
                    {NOT_EVALUATED_PROFILES.map((t) => (
                      <li key={t}>
                        <span className="mono">{t}</span>: {profiles[t]?.profile ? <span className="mono">{profiles[t].profile}</span> : 'none'}
                        {' → '}<span className="fw-prov">not evaluated</span>
                      </li>
                    ))}
                  </ul>
                </td>
                <td>
                  <span className="mono">{p.dns_security?.profile || 'none'}</span>
                  {' → '}<span className="mono">{p.dns_security?.bucket || 'unknown'}</span>
                  {p.dns_security?.note && <div className="fw-note">{p.dns_security.note}</div>}
                </td>
                <td className="mono">{p.log_setting || 'none'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function parseList(text) {
  return text.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
}

function SourceEditor({ firewall, src, host }) {
  const uid = useId()
  const [open, setOpen] = useState(false)
  const [observed, setObserved] = useState('')
  const [addrs, setAddrs] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(null)
  const editRef = useRef(null)
  const firstRef = useRef(null)
  const returnFocus = useRef(false)

  // A new report is a new source object: the old "Saved" note no longer applies.
  useEffect(() => { setSaved(null) }, [src])

  useEffect(() => {
    if (open) {
      firstRef.current?.focus()
    } else if (returnFocus.current) {
      returnFocus.current = false
      editRef.current?.focus()
    }
  }, [open])

  const close = () => {
    returnFocus.current = true
    setOpen(false)
  }

  const start = () => {
    setObserved(src.fw_src_provenance === 'entered' ? (src.fw_observed_src || '') : '')
    setAddrs(src.agent_addr_provenance === 'entered' ? (src.agent_addresses || []).join(', ') : '')
    setError(null)
    setSaved(null)
    setOpen(true)
  }

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await putFirewallSource(firewall, src.agent_id, {
        observed_source: observed.trim() || null,
        agent_addresses: parseList(addrs),
      })
      setSaved(res || {})
      close()
    } catch (err) {
      setError({ message: err?.message || 'save failed', detail: typeof err?.detail === 'string' ? err.detail : null })
    } finally {
      setBusy(false)
    }
  }

  if (!open) {
    return (
      <div className="fw-edit">
        <button type="button" className="btn btn--xs" onClick={start} ref={editRef}
          aria-label={`Edit addresses for ${host}`}>
          Edit addresses
        </button>
        {saved && (
          <span className="fw-note" role="status">
            Saved. These overrides are not checked until you run the check again.
          </span>
        )}
      </div>
    )
  }
  return (
    <form className="fw-edit fw-edit--open" onSubmit={save} aria-label={`Addresses for ${host}`}>
      <div className="pov-field">
        <label className="pov-field__k" htmlFor={`${uid}-obs`}>Firewall-observed source for {host}</label>
        <input id={`${uid}-obs`} ref={firstRef} className="pov-field__input" type="text" value={observed}
          onChange={(e) => setObserved(e.target.value)}
          placeholder={src.fw_observed_src || 'blank = infer from last_ip'} autoComplete="off" />
      </div>
      <div className="pov-field">
        <label className="pov-field__k" htmlFor={`${uid}-addrs`}>Agent-side addresses for {host}</label>
        <input id={`${uid}-addrs`} className="pov-field__input" type="text" value={addrs}
          onChange={(e) => setAddrs(e.target.value)} placeholder="comma-separated; blank = unknown"
          autoComplete="off" />
      </div>
      <p className="fw-note">Blank clears the override. Overrides apply to this firewall only.</p>
      <div className="fw-edit__actions">
        <button type="submit" className="btn btn--xs btn--primary" disabled={busy}
          aria-label={`Save addresses for ${host}`}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn btn--xs btn--ghost" onClick={close}
          aria-label={`Cancel editing addresses for ${host}`}>
          Cancel
        </button>
      </div>
      {error && (
        <div className="fw-error" role="alert">
          {error.message}{error.detail && <div className="fw-error__detail">{error.detail}</div>}
        </div>
      )}
    </form>
  )
}
