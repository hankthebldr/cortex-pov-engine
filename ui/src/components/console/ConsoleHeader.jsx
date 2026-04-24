import React from 'react'

/**
 * ConsoleHeader — 56px brand + environment + ⌘K trigger + user + PANW mark.
 *
 * Props:
 *   health          — { hostname, version, sensors: { xdr: 'healthy'|'warn'|'bad', ... } }
 *   onOpenPalette   — () => void
 *   userInitials    — string (defaults to 'DC')
 */
export default function ConsoleHeader({ health = {}, onOpenPalette, userInitials = 'DC' }) {
  const hostname = health.hostname || (typeof window !== 'undefined' ? window.location.hostname : 'lab')
  const version  = health.version  || 'v1.0'
  const sensors  = health.sensors  || {}

  const worstStatus = Object.values(sensors).reduce((acc, s) => {
    if (s === 'bad')  return 'bad'
    if (s === 'warn' && acc !== 'bad') return 'warn'
    return acc
  }, 'healthy')

  const sensorSummary = Object.keys(sensors).length
    ? Object.entries(sensors).map(([k, v]) => `${k} ${v}`).join(' · ')
    : 'sensors pending'

  return (
    <header className="header">
      <div className="brand">
        <div className="brand__wordmark">cortex<em>sim</em></div>
        <div className="brand__subtitle">Detection Simulation Engine · {version}</div>
      </div>

      <div />

      <div className="env-pill" title={sensorSummary}>
        <span className={
          'env-pill__dot' +
          (worstStatus === 'warn' ? ' env-pill__dot--warn' : '') +
          (worstStatus === 'bad'  ? ' env-pill__dot--bad'  : '')
        } />
        <span className="env-pill__label">{hostname.toUpperCase()}</span>
        <span className="env-pill__meta">/ {sensorSummary}</span>
      </div>

      <button className="cmd-trigger" onClick={onOpenPalette} aria-label="Open command palette">
        <span>⌘K</span>
        <span style={{ color: 'var(--c-text-muted)' }}>search · launch · export</span>
      </button>

      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div className="user-avatar">{userInitials}</div>
        <div className="panw-mark">palo alto <span>networks</span></div>
      </div>
    </header>
  )
}
