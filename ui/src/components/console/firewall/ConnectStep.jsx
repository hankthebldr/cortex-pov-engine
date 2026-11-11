import React, { useId, useState } from 'react'
import { putFirewall, firewallKeygen, deleteFirewall } from '../../../api/client.js'

/**
 * Step 1 · Connect — register a PAN-OS firewall, by pasted API key or by
 * generating one from an admin login, and list the ones already registered.
 *
 * Password hygiene (spec §4.2, binding): the admin password lives in component
 * state only while it is being typed. It is sent once in the keygen body and
 * cleared from state the moment that call returns, success or failure. It is
 * never rendered, never logged, never put in a URL or browser storage. The
 * pasted API key gets the same treatment.
 */

export const KEYGEN_NOTE =
  "Your password is used once to generate an API key and is not stored. Generating a key does not revoke this admin's other keys. Use a read-only API admin role."
export const TLS_OFF_NOTE =
  'Self-signed management certificates are common in labs. Every preflight will report this connection as unverified.'
export const TLS_UNVERIFIED_NOTE =
  'TLS unverified: every preflight will report this connection as unverified.'
export const OVERRIDES_CLEARED_NOTE =
  'address overrides from the previous firewall were cleared'

const PATHS = [
  ['auto', 'auto — whichever path is configured'],
  ['sls', 'sls — Strata Logging Service'],
  ['syslog', 'syslog — syslog server profile'],
  ['both', 'both — SLS and syslog'],
]

function errorView(err) {
  if (!err) return null
  return {
    message: err.message || 'request failed',
    code: err.code || null,
    detail: typeof err.detail === 'string' ? err.detail : null,
  }
}

export default function ConnectStep({ firewalls, loadError, selected, onSelect, onChanged }) {
  const uid = useId()
  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [verifyTls, setVerifyTls] = useState(true)
  const [vsys, setVsys] = useState('vsys1')
  const [forwardingPath, setForwardingPath] = useState('auto')
  const [mode, setMode] = useState('key')
  const [apiKey, setApiKey] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [outcome, setOutcome] = useState(null)

  const ready = name.trim() && host.trim()
    && (mode === 'key' ? apiKey.length > 0 : username.trim() && password.length > 0)

  // Switching mode drops whatever secret was typed for the other mode, so a
  // password never sits in state behind a hidden field.
  const switchMode = (next) => {
    setPassword('')
    setApiKey('')
    setMode(next)
  }

  // No <form>: a form submit carrying a password field is what prompts the
  // browser to offer saving the admin credential. Enter still submits.
  const onEnter = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
  }

  const submit = async () => {
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    setOutcome(null)
    const fwName = name.trim()
    try {
      if (mode === 'keygen') {
        const body = {
          name: fwName, host: host.trim(), verify_tls: verifyTls,
          username: username.trim(), password, vsys: vsys.trim() || 'vsys1',
        }
        try {
          const res = await firewallKeygen(body)
          setOutcome({
            name: res?.name || fwName,
            warnings: Array.isArray(res?.warnings) ? res.warnings : [],
            overridesCleared: res?.overrides_cleared === true,
          })
        } finally {
          body.password = null
          setPassword('')
        }
      } else {
        try {
          const res = await putFirewall({
            name: fwName, host: host.trim(), verify_tls: verifyTls,
            vsys: vsys.trim() || 'vsys1', forwarding_path: forwardingPath, api_key: apiKey,
          })
          setOutcome({
            name: res?.name || fwName,
            // The server warns about an unverified keygen; the paste path has no
            // password to warn about, but the connection is still unverified.
            warnings: verifyTls ? [] : [TLS_UNVERIFIED_NOTE],
            overridesCleared: res?.overrides_cleared === true,
          })
        } finally {
          setApiKey('')
        }
      }
      onSelect?.(fwName)
      await onChanged?.()
    } catch (err) {
      setError(errorView(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fw-connect">
      <div className="fw-form" role="group" aria-label="Register a firewall">
        <div className="fw-form__grid">
          <div className="pov-field">
            <label className="pov-field__k" htmlFor={`${uid}-name`}>Name</label>
            <input id={`${uid}-name`} className="pov-field__input" type="text" value={name}
              onChange={(e) => setName(e.target.value)} placeholder="pa-440" autoComplete="off" />
          </div>
          <div className="pov-field">
            <label className="pov-field__k" htmlFor={`${uid}-host`}>Management host</label>
            <input id={`${uid}-host`} className="pov-field__input" type="text" value={host}
              onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.1 or fw.lab.example"
              autoComplete="off" />
          </div>
          <div className="pov-field">
            <label className="pov-field__k" htmlFor={`${uid}-vsys`}>vsys</label>
            <input id={`${uid}-vsys`} className="pov-field__input" type="text" value={vsys}
              onChange={(e) => setVsys(e.target.value)} autoComplete="off" />
          </div>
        </div>

        <div className="fw-form__tls">
          <label className="fw-check">
            <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
            Verify TLS
          </label>
          {!verifyTls && <p className="fw-note fw-note--warn">{TLS_OFF_NOTE}</p>}
        </div>

        <fieldset className="fw-form__mode">
          <legend className="pov-field__k">Credential</legend>
          <label className="fw-check">
            <input type="radio" name={`${uid}-mode`} value="key" checked={mode === 'key'}
              onChange={() => switchMode('key')} />
            Paste API key
          </label>
          <label className="fw-check">
            <input type="radio" name={`${uid}-mode`} value="keygen" checked={mode === 'keygen'}
              onChange={() => switchMode('keygen')} />
            Generate key from admin login
          </label>
        </fieldset>

        {mode === 'key' ? (
          <div className="fw-form__grid">
            <div className="pov-field">
              <label className="pov-field__k" htmlFor={`${uid}-key`}>API key</label>
              <input id={`${uid}-key`} className="pov-field__input" type="password" value={apiKey}
                onChange={(e) => setApiKey(e.target.value)} onKeyDown={onEnter}
                autoComplete="new-password" data-lpignore="true" data-1p-ignore="" />
            </div>
            <div className="pov-field">
              <label className="pov-field__k" htmlFor={`${uid}-path`}>Forwarding path</label>
              <select id={`${uid}-path`} className="pov-field__input" value={forwardingPath}
                onChange={(e) => setForwardingPath(e.target.value)}>
                {PATHS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            <p className="fw-note fw-form__full">
              Re-registering a firewall keeps its per-agent address overrides when the host is
              unchanged, and clears them when it changes.
            </p>
          </div>
        ) : (
          <div className="fw-form__grid">
            <div className="pov-field">
              <label className="pov-field__k" htmlFor={`${uid}-user`}>Admin username</label>
              <input id={`${uid}-user`} className="pov-field__input" type="text" value={username}
                onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
            </div>
            <div className="pov-field">
              <label className="pov-field__k" htmlFor={`${uid}-pw`}>Admin password</label>
              <input id={`${uid}-pw`} className="pov-field__input" type="password" value={password}
                onChange={(e) => setPassword(e.target.value)} onKeyDown={onEnter}
                autoComplete="new-password" data-lpignore="true" data-1p-ignore="" />
            </div>
            <p className="fw-note fw-form__full">{KEYGEN_NOTE}</p>
            <p className="fw-note fw-form__full">
              Forwarding path: a new firewall registers with <span className="mono">auto</span>; a
              registered one keeps its setting.
            </p>
          </div>
        )}

        <div className="fw-form__actions">
          <button type="button" className="btn btn--primary" disabled={!ready || busy} onClick={submit}>
            {busy
              ? (mode === 'keygen' ? 'Generating…' : 'Registering…')
              : (mode === 'keygen' ? 'Generate key and register' : 'Register firewall')}
          </button>
        </div>

        {error && (
          <div className="fw-error" role="alert">
            {error.message}
            {error.code && !error.message.includes(error.code) && <> [<span className="mono">{error.code}</span>]</>}
            {error.detail && <div className="fw-error__detail">{error.detail}</div>}
          </div>
        )}
        {outcome && (
          <div className="fw-outcome" role="status" data-testid="fw-outcome">
            Registered <span className="mono">{outcome.name}</span>. Nothing about its logging or
            policy path has been checked yet; run the check below.
            {outcome.warnings.map((w) => (
              <div key={w} className="fw-note fw-note--warn">{w}</div>
            ))}
            {outcome.overridesCleared && (
              <div className="fw-note fw-note--warn">{OVERRIDES_CLEARED_NOTE}</div>
            )}
          </div>
        )}
      </div>

      <RegisteredList firewalls={firewalls} loadError={loadError} selected={selected}
        onSelect={onSelect} onChanged={onChanged} />
    </div>
  )
}

function lastResult(fw) {
  if (fw.last_verified_ok === true) return { label: 'last preflight: not blocked', cls: 'chip chip--detected' }
  if (fw.last_verified_ok === false) {
    return { label: `last preflight: blocked${fw.last_verified_error ? ` (${fw.last_verified_error})` : ''}`, cls: 'chip chip--missed' }
  }
  return { label: 'never checked', cls: 'chip' }
}

function RegisteredList({ firewalls, loadError, selected, onSelect, onChanged }) {
  const [confirming, setConfirming] = useState(null)
  const [error, setError] = useState(null)

  if (loadError) {
    return (
      <div className="fw-error" role="alert">
        Could not list registered firewalls: {loadError}
      </div>
    )
  }
  if (!firewalls) return <div className="fw-note" role="status">loading registered firewalls…</div>
  if (firewalls.length === 0) {
    return <p className="fw-note">No firewall is registered on this SimCore yet.</p>
  }

  const remove = async (name) => {
    setError(null)
    try {
      await deleteFirewall(name)
      setConfirming(null)
      await onChanged?.()
    } catch (err) {
      setError(err?.message || 'remove failed')
    }
  }

  return (
    <div className="fw-registered">
      <h3 className="fw-sub">Registered firewalls</h3>
      <ul className="fw-registered__list">
        {firewalls.map((fw) => {
          const r = lastResult(fw)
          const isSel = fw.name === selected
          return (
            <li key={fw.name} className={`fw-registered__row${isSel ? ' fw-registered__row--sel' : ''}`}
              data-testid={`fw-registered-${fw.name}`}>
              <span className="mono fw-registered__name">{fw.name}</span>
              <span className="mono">{fw.host}</span>
              <span className="mono">{fw.vsys}</span>
              <span className="mono">path: {fw.forwarding_path}</span>
              <span className={fw.verify_tls ? 'chip' : 'chip chip--pending'}>
                {fw.verify_tls ? 'TLS verified' : 'TLS unverified'}
              </span>
              <span className={r.cls}>{r.label}</span>
              {fw.last_verified_at && <span className="mono fw-registered__at">{fw.last_verified_at}</span>}
              <span className="fw-registered__actions">
                <button type="button" className="btn btn--xs" aria-pressed={isSel}
                  aria-label={`Select firewall ${fw.name}`} onClick={() => onSelect?.(fw.name)}>
                  {isSel ? 'Selected' : 'Select'}
                </button>
                {confirming === fw.name ? (
                  <>
                    <button type="button" className="btn btn--xs btn--danger"
                      aria-label={`Confirm remove firewall ${fw.name}`} onClick={() => remove(fw.name)}>
                      Confirm remove
                    </button>
                    <button type="button" className="btn btn--xs btn--ghost"
                      aria-label={`Keep firewall ${fw.name}`} onClick={() => setConfirming(null)}>
                      Keep
                    </button>
                  </>
                ) : (
                  <button type="button" className="btn btn--xs btn--ghost"
                    aria-label={`Remove firewall ${fw.name}`} onClick={() => setConfirming(fw.name)}>
                    Remove
                  </button>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      {error && <div className="fw-error" role="alert">{error}</div>}
    </div>
  )
}
