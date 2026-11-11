import React, { useCallback, useEffect, useState } from 'react'
import '../../styles/destinations/collector-targets.css'
import {
  listCollectorTargets,
  putCollectorTarget,
  deleteCollectorTarget,
  preflightCollectorTarget,
} from '../../api/client.js'
import { serverDate } from '../../api/time.js'

/**
 * CollectorTargetsPanel — the tenant-side ingestion doors this POV's emitters
 * POST to (the XSIAM HTTP log collector, or a forwarder in front of it).
 * Broker VM targets are not modelled yet.
 *
 * Entered once per instance; every campaign, offline bundle and Composer EAL
 * step resolves its destination from these. Three honesty rules shape it:
 *   - no targets is a stated gap, with the plugins it strands, never a blank;
 *   - "Check reachability" never writes to the tenant; "Send test record" says
 *     before it runs that it writes one record, and is the only path to
 *     "accepted";
 *   - the key field is write-only: no response carries a key, so none is shown.
 */

const KIND_LABEL = {
  xsiam_http_collector: 'XSIAM HTTP collector',
  http_forwarder: 'HTTP forwarder',
}

const VERDICT = {
  accepted:       { tone: 'ok',      text: 'Accepted a test record' },
  reachable:      { tone: 'warn',    text: 'Reachable · not proven' },
  refused:        { tone: 'fail',    text: 'Refused the test record' },
  unreachable:    { tone: 'fail',    text: 'Unreachable' },
  not_configured: { tone: 'fail',    text: 'Not configured' },
}

const BLANK = {
  name: '', kind: 'xsiam_http_collector', url: '', dataset: '',
  auth_header: 'Authorization', auth_scheme: '', plugins: [], is_default: false,
  token: '', clear_token: false,
}

function hostPath(url) {
  try {
    const u = new URL(url)
    return `${u.host}${u.pathname}`
  } catch {
    return url
  }
}

function errorText(err) {
  const detail = typeof err?.detail === 'string' ? err.detail : null
  return detail ? `${err.message} — ${detail}` : (err?.message || 'Request failed')
}

function CheckPill({ check }) {
  if (!check) {
    return <span className="ct-pill ct-pill--unknown">Never checked</span>
  }
  const v = VERDICT[check.verdict]
  if (!v) {
    // An unknown verdict is a contract break, not a healthy door.
    return <span className="ct-pill ct-pill--fail">Unknown result “{String(check.verdict)}”</span>
  }
  const when = check.checked_at ? serverDate(check.checked_at)?.toLocaleString() : null
  return (
    <span className={`ct-pill ct-pill--${v.tone}`} title={check.code || undefined}>
      {v.text}{check.code ? ` (${check.code})` : ''}{when ? ` · ${when}` : ''}
    </span>
  )
}

function TargetForm({ initial, editing, collectorPlugins, onCancel, onSaved }) {
  const [v, setV] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const set = (k) => (e) => setV({ ...v, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const togglePlugin = (p) => setV({
    ...v, plugins: v.plugins.includes(p) ? v.plugins.filter((x) => x !== p) : [...v.plugins, p],
  })

  async function save(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body = {
      kind: v.kind, url: v.url.trim(), dataset: v.dataset.trim() || null,
      auth_header: v.auth_header.trim() || 'Authorization', auth_scheme: v.auth_scheme.trim(),
      plugins: v.plugins, is_default: v.is_default,
    }
    if (v.token) body.token = v.token
    else if (editing && v.clear_token) body.clear_token = true
    try {
      await putCollectorTarget(v.name.trim(), body)
      onSaved()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="ct-form" data-testid="ct-form" onSubmit={save}>
      <div className="ct-form__grid">
        <label className="ct-field">
          <span>Name</span>
          <input value={v.name} onChange={set('name')} disabled={editing} required
                 placeholder="okta-hec" pattern="[a-z0-9][a-z0-9-]{1,47}" />
        </label>
        <label className="ct-field">
          <span>Kind</span>
          <select value={v.kind} onChange={set('kind')}>
            {Object.entries(KIND_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
        <label className="ct-field ct-field--wide">
          <span>URL</span>
          <input value={v.url} onChange={set('url')} required type="url"
                 placeholder="https://api-<tenant>/logs/v1/event" />
        </label>
        <label className="ct-field">
          <span>Dataset (label)</span>
          <input value={v.dataset} onChange={set('dataset')} placeholder="okta_sso_raw" />
        </label>
        <label className="ct-field">
          <span>Auth header</span>
          <input value={v.auth_header} onChange={set('auth_header')} />
        </label>
        <label className="ct-field">
          <span>Auth scheme</span>
          <input value={v.auth_scheme} onChange={set('auth_scheme')} placeholder="(none — XSIAM takes the bare key)" />
        </label>
        <label className="ct-field">
          <span>Collector key</span>
          <input type="password" autoComplete="new-password" value={v.token} onChange={set('token')}
                 placeholder={editing ? 'leave blank to keep the stored key' : ''} />
        </label>
      </div>

      <fieldset className="ct-form__plugins">
        <legend>Serves these emitters</legend>
        {collectorPlugins.map((p) => (
          <label key={p} className="ct-check">
            <input type="checkbox" checked={v.plugins.includes(p)} onChange={() => togglePlugin(p)} />
            <span>{p}</span>
          </label>
        ))}
      </fieldset>

      <div className="ct-form__row">
        <label className="ct-check">
          <input type="checkbox" checked={v.is_default} onChange={set('is_default')} />
          <span>Default target for emitters no target serves</span>
        </label>
        {editing && (
          <label className="ct-check">
            <input type="checkbox" checked={v.clear_token} onChange={set('clear_token')} disabled={Boolean(v.token)} />
            <span>Remove the stored key</span>
          </label>
        )}
      </div>

      {error && <div className="ct-alert ct-alert--error" role="alert">{error}</div>}
      <div className="ct-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  )
}

function TargetRow({ target, onEdit, onChanged }) {
  const [busy, setBusy] = useState(null)
  const [confirmWrite, setConfirmWrite] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function check(writeCanary) {
    setBusy(writeCanary ? 'write' : 'reach')
    setError(null)
    setConfirmWrite(false)
    try {
      setResult(await preflightCollectorTarget(target.name, { writeCanary }))
      onChanged()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    setBusy('delete')
    try {
      await deleteCollectorTarget(target.name)
      onChanged()
    } catch (err) {
      setError(errorText(err))
      setBusy(null)
    }
  }

  return (
    <li className="ct-row" data-testid={`ct-row-${target.name}`}>
      <div className="ct-row__top">
        <span className="ct-row__name">{target.name}</span>
        <span className="ct-row__kind">{KIND_LABEL[target.kind] || target.kind}</span>
        {target.is_default && <span className="ct-pill ct-pill--unknown">default</span>}
        <CheckPill check={target.last_check} />
      </div>
      <div className="ct-row__grid">
        <span className="ct-row__k">Destination</span><span className="ct-row__v ct-mono">{hostPath(target.url)}</span>
        <span className="ct-row__k">Dataset</span><span className="ct-row__v">{target.dataset || '—'}</span>
        <span className="ct-row__k">Serves</span>
        <span className="ct-row__v ct-mono">{target.plugins.length ? target.plugins.join(', ') : (target.is_default ? 'any emitter no other target serves' : 'nothing yet')}</span>
        <span className="ct-row__k">Key</span>
        <span className="ct-row__v">{target.has_key ? `Key stored (${target.auth_header}${target.auth_scheme ? `: ${target.auth_scheme} …` : ''})` : 'No key'}</span>
      </div>
      {target.warnings?.length > 0 && (
        <ul className="ct-warnings">{target.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
      )}
      {result && (
        <div className="ct-result" data-testid="ct-result">
          Last check: <strong>{VERDICT[result.verdict]?.text || result.verdict}</strong>
          {' · '}records written to the tenant: {result.records_written}
          {result.remediation ? ` · ${result.remediation}` : ''}
        </div>
      )}
      {error && <div className="ct-alert ct-alert--error" role="alert">{error}</div>}
      {confirmWrite && (
        <div className="ct-confirm" data-testid="ct-confirm-write">
          This writes one discard-safe record into the tenant through this collector, marked
          <code> cortexsim_preflight: true</code>. A 2xx proves the collector accepts records;
          it does not prove the record was parsed or that any detection fires.
          <div className="ct-actions">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => check(true)}>Send it</button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setConfirmWrite(false)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="ct-actions">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => check(false)} disabled={Boolean(busy)}>
          {busy === 'reach' ? 'Checking…' : 'Check reachability'}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setConfirmWrite(true)} disabled={Boolean(busy)}>
          {busy === 'write' ? 'Sending…' : 'Send test record'}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => onEdit(target)} disabled={Boolean(busy)}>Edit</button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={remove} disabled={Boolean(busy)}>Remove</button>
      </div>
    </li>
  )
}

export default function CollectorTargetsPanel() {
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [form, setForm] = useState(null) // null | { editing: bool, initial }

  const load = useCallback(async () => {
    try {
      setData(await listCollectorTargets())
      setLoadError(null)
    } catch (err) {
      setLoadError(errorText(err))
    }
  }, [])

  useEffect(() => { load() }, [load])

  const startEdit = (t) => setForm({
    editing: true,
    initial: { ...BLANK, ...t, dataset: t.dataset || '', auth_scheme: t.auth_scheme || '', token: '', clear_token: false },
  })

  return (
    <section className="ct-panel" data-testid="collector-targets">
      <h2 className="ct-panel__title">Collector targets</h2>
      <p className="ct-panel__lede">
        Where this POV's log emitters send records: the tenant's HTTP log collector, or a forwarder
        in front of it. Every campaign, offline bundle and Composer
        step takes its destination from here. Keys are stored encrypted and never shown again.
      </p>

      {loadError && <div className="ct-alert ct-alert--error" role="alert">{loadError}</div>}
      {data === null && !loadError && <div className="ct-muted">Loading collector targets…</div>}

      {data && data.count === 0 && (
        <div className="ct-alert ct-alert--warn" data-testid="ct-empty">
          No collector target is configured, so no campaign step that posts records can run live.
        </div>
      )}
      {data && data.unbound_plugins?.length > 0 && (
        <div className="ct-alert ct-alert--warn" data-testid="ct-unbound">
          No target serves these emitters, so a live launch that uses them is refused:{' '}
          <span className="ct-mono">{data.unbound_plugins.join(', ')}</span>.
          Bind them to a target, or make one target the default.
        </div>
      )}

      {data && data.targets.length > 0 && (
        <ul className="ct-list">
          {data.targets.map((t) => (
            <TargetRow key={t.name} target={t} onEdit={startEdit} onChanged={load} />
          ))}
        </ul>
      )}

      {form ? (
        <TargetForm
          key={form.initial.name || 'new'}
          initial={form.initial}
          editing={form.editing}
          collectorPlugins={data?.collector_plugins || []}
          onCancel={() => setForm(null)}
          onSaved={() => { setForm(null); load() }}
        />
      ) : (
        <div className="ct-actions">
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setForm({ editing: false, initial: BLANK })}>
            Add collector target
          </button>
        </div>
      )}
    </section>
  )
}
