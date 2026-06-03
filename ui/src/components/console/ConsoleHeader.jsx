import React from 'react'

/**
 * ConsoleHeader — 56px brand + environment + ⌘K trigger + user + PANW mark.
 *
 * Props:
 *   health          — { hostname, version, sensors: { xdr: 'healthy'|'warn'|'bad', ... } }
 *   onOpenPalette   — () => void
 *   userInitials    — string (defaults to 'DC')
 *   theaterMode     — boolean — when true, render the theater toggle as
 *                     active (filled). Otherwise outlined.
 *   onToggleTheater — () => void
 */
export default function ConsoleHeader({
  health = {},
  onOpenPalette,
  userInitials = 'DC',
  theaterMode = false,
  onToggleTheater = null,
}) {
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
      <div className="header__left">
        <div className="brand">
          <div className="brand__wordmark">cortex<em>sim</em></div>
          <div className="brand__subtitle">Detection Simulation Engine</div>
        </div>
        <span className="brand__version mono">{version}</span>
      </div>

      <div className="header__right">
        <div className="env-pill" title={sensorSummary}>
          <span className={
            'env-pill__dot' +
            (worstStatus === 'warn' ? ' env-pill__dot--warn' : '') +
            (worstStatus === 'bad'  ? ' env-pill__dot--bad'  : '')
          } />
          <span className="env-pill__label">{hostname.toUpperCase()}</span>
          <span className="env-pill__meta">/ {sensorSummary}</span>
        </div>

        <button className="cmd-trigger" onClick={onOpenPalette} aria-label="Open command palette (search, launch, export)" title="Search · launch · export">
          <span className="kbd">⌘K</span>
          <span className="cmd-trigger__label">Search</span>
        </button>

        {onToggleTheater && (
          <button
            type="button"
            className={'theater-toggle' + (theaterMode ? ' is-active' : '')}
            onClick={onToggleTheater}
            aria-pressed={theaterMode}
            aria-label={theaterMode ? 'Exit theater mode' : 'Enter theater mode for sales demos and briefings'}
            title={theaterMode ? 'Exit theater mode' : 'Theater mode — projector-friendly, hides debug chrome'}
          >
            {theaterMode ? '◼' : '◻'}<span className="theater-toggle__label">Theater</span>
          </button>
        )}

        <span className="header__divider" aria-hidden="true" />
        <div className="user-avatar" title="Domain Consultant">{userInitials}</div>
        <div className="panw-mark">palo alto <span>networks</span></div>
      </div>
    </header>
  )
}
