import React from 'react'
import TenantSwitcher from './GlobalContextBar/TenantSwitcher.jsx'
import AgentSwitcher from './GlobalContextBar/AgentSwitcher.jsx'

/**
 * ConsoleHeader — the persistent GLOBAL CONTEXT BAR.
 *
 * Always visible, top of shell. Left→right:
 *   1. Brand mark + version
 *   2. Tenant switcher pill (provider-backed; status dot + Manage… footer)
 *   3. Agent switcher pill  (provider-backed; liveness dot + Manage… footer)
 *   4. Connectivity/health chip — the real sensor summary for the active tenant
 *   5. Global LIVE run pill — only when a run is in flight; deep-links to Runs
 *   6. ⌘K command-palette trigger
 *   7. Theater-mode toggle
 *
 * The two switchers + the ⌘K palette all write to the SAME provider, so the
 * active tenant/agent is switchable from three places and reflected everywhere.
 *
 * Props:
 *   health          — { hostname, version, sensors: {..}, tenantHealth }
 *   activeRun       — { scenarioId, step, totalSteps, elapsed } | null
 *   onOpenPalette   — () => void
 *   onNavigate      — (destinationId, params?) => void  (Manage… + LIVE pill)
 *   userInitials    — string
 *   theaterMode     — boolean
 *   onToggleTheater — () => void
 */
export default function ConsoleHeader({
  health = {},
  activeRun = null,
  onOpenPalette,
  onNavigate = () => {},
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

  const fmtElapsed = (s) => {
    if (s == null) return '0:00'
    const m = Math.floor(s / 60)
    const sec = String(s % 60).padStart(2, '0')
    return `${m}:${sec}`
  }

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
        <TenantSwitcher onManage={() => onNavigate('tenants')} />
        <AgentSwitcher onManage={() => onNavigate('agents')} />

        <div className="env-pill" title={sensorSummary}>
          <span className={
            'env-pill__dot' +
            (worstStatus === 'warn' ? ' env-pill__dot--warn' : '') +
            (worstStatus === 'bad'  ? ' env-pill__dot--bad'  : '')
          } />
          <span className="env-pill__label">{hostname.toUpperCase()}</span>
          <span className="env-pill__meta">/ {sensorSummary}</span>
        </div>

        {activeRun && (
          <button
            type="button"
            className="env-pill live-pill"
            onClick={() => onNavigate('runs', { run: activeRun.runId, tab: 'live' })}
            title="Jump to the live run"
            aria-label={`Live run ${activeRun.scenarioId || ''} — open in Runs & Proof`}
          >
            <span className="env-pill__dot live-pill__dot" />
            <span className="env-pill__label live-pill__label">LIVE</span>
            <span className="env-pill__meta mono">
              {activeRun.scenarioId} · {activeRun.step}/{activeRun.totalSteps} · {fmtElapsed(activeRun.elapsed)}
            </span>
          </button>
        )}

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
