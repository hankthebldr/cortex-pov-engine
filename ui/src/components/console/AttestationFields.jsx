import React, { useId } from 'react'

const PAIR_CODES = new Set(['PAIR_INVALID', 'CANARY_INVALID'])

/** Launch errors this panel owns (the server's refused-pair / refused-canary 409s). */
export function isAttestationError(lastRun) {
  return !!(lastRun && lastRun.status === 'error' && PAIR_CODES.has(lastRun.code))
}

function lastRunText(lastRun) {
  let text = lastRun.message || 'Launch failed'
  if (lastRun.code && !text.includes(lastRun.code)) text += ` [${lastRun.code}]`
  if (lastRun.detail && !text.includes(lastRun.detail)) text += `: ${lastRun.detail}`
  return text
}

/**
 * AttestationFields — the launch-time prevention context.
 *
 * Everything here is an operator DECLARATION. It rides on the run as
 * "attested, not verified"; the console never presents it as measured. Fields
 * left at their default are not sent at all.
 *
 * Props: `launch` — the useLaunchScenario() return value.
 */
export default function AttestationFields({ launch }) {
  const uid = useId()
  const id = (k) => `${uid}-${k}`
  const tri = (value, set, key, label) => (
    <label className="launch-field" htmlFor={id(key)}>
      <span className="launch-field__label">{label}</span>
      <select
        id={id(key)}
        className="launch-select"
        value={value}
        onChange={(e) => set(e.target.value)}
      >
        <option value="">not attested</option>
        <option value="enabled">enabled</option>
        <option value="disabled">disabled</option>
      </select>
    </label>
  )

  return (
    <fieldset className="launch-attest" data-testid="launch-attestation">
      <legend className="launch-attest__legend">Prevention context (attested, not verified)</legend>

      <label className="launch-field" htmlFor={id('mode')}>
        <span className="launch-field__label">Agent profile mode</span>
        <select
          id={id('mode')}
          className="launch-select"
          value={launch.profileMode}
          onChange={(e) => launch.setProfileMode(e.target.value)}
        >
          <option value="unknown">unknown</option>
          <option value="report">report</option>
          <option value="block">block</option>
        </select>
      </label>

      {tri(launch.basMode, launch.setBasMode, 'bas', 'BAS mode')}
      {tri(launch.soaklessMode, launch.setSoaklessMode, 'soak', 'Soakless')}

      <label className="launch-field" htmlFor={id('by')}>
        <span className="launch-field__label">Attested by</span>
        <input
          id={id('by')}
          className="launch-input"
          type="text"
          value={launch.attestedBy}
          onChange={(e) => launch.setAttestedBy(e.target.value)}
          autoComplete="off"
        />
      </label>

      <label className="launch-field" htmlFor={id('pair')}>
        <span className="launch-field__label">Pair with run</span>
        <select
          id={id('pair')}
          className="launch-select"
          value={launch.pairedWith}
          onChange={(e) => launch.setPairedWith(e.target.value)}
        >
          <option value="">none</option>
          {launch.pairOptions.map((r) => {
            const rid = r.run_id ?? r.id
            return (
              <option key={rid} value={rid}>
                {String(rid).slice(0, 12)} · {r.status}{r.started_at ? ` · ${r.started_at}` : ''}
              </option>
            )
          })}
        </select>
      </label>

      <div className="launch-field launch-attest__canary">
        <button
          type="button"
          className="btn btn--xs"
          onClick={launch.runCanary}
          disabled={launch.canaryBusy}
        >
          {launch.canaryBusy ? 'Running canary…' : 'Run canary first'}
        </button>
        {launch.canaryRunId && (
          <span className="mono" data-testid="launch-canary-run">
            Canary run {launch.canaryRunId}
          </span>
        )}
      </div>

      {launch.canaryError && (
        <div className="launch-result launch-result--error" role="alert">
          {launch.canaryError.message}
        </div>
      )}
      {isAttestationError(launch.lastRun) && (
        <div className="launch-result launch-result--error" role="alert">
          {lastRunText(launch.lastRun)}
        </div>
      )}
    </fieldset>
  )
}
