import React from 'react'
import EvidenceCollection from './EvidenceCollection.jsx'

/**
 * ConsoleHeader — the persistent global bar.
 *
 * Left→right: the Cortex product mark + POVengine wordmark + version, the
 * evidence collection, then (pushed right) the bound tenant, the active agent,
 * the run pill, ⌘K, the utility toggles, and the account chip.
 *
 * EVERY CHILD IS `flex: none; white-space: nowrap`.
 * This bar carries ten controls that each have to stay readable. Letting them
 * wrap produced a two-line header at exactly the width a projector runs at,
 * and the control that fell to the second line was the LIVE pill. The shell
 * declares a 1200px floor and scrolls horizontally below it instead — see the
 * note at the top of povengine-shell.css. The floor is 1200 because the
 * header's measured natural width at its widest state is 1194; it is not a
 * round number picked for looks.
 *
 * THE BRAND MARK IS THE REAL ASSET, AND IT SWAPS BY THEME.
 * `/assets/cortex-{green,mono}.png` are the design system's own Cortex product
 * marks — green for the on-dark set, mono for on-light. The wrong one is
 * invisible against its own header, which is why this is selected rather than
 * fixed. The DS forbids drawing or reconstructing a brand mark; POVengine
 * itself has no asset, so its wordmark stays as type.
 *
 * THERE IS DELIBERATELY NO PALO ALTO NETWORKS MASTER LOCKUP HERE.
 * NOTICE states this is an independent project and NOT an official PANW
 * product; a header flying the vendor lockup asserts the opposite in every
 * screenshot and recording. Cortex is named nominatively — it is the platform
 * under test. Guarded by ShellRedesign.test.jsx. (The design track asks for the
 * reversed PANW lockup on the Overview page under an "internal tooling for
 * Cortex Domain Consulting" framing. That framing and NOTICE's are mutually
 * exclusive, and choosing between them is an affiliation call rather than a
 * design one — so it is recorded as an open question in
 * docs/design/DESIGN-SYNC.md rather than silently applied.)
 *
 * TENANT AND AGENT ARE READ-OUTS HERE, NOT SWITCHERS.
 * One tenant per instance is a design decision, and it follows from the
 * product: the instance is deployed once for a POV and dies with the lab, so a
 * header dropdown offering to change tenant was modelling a thing that cannot
 * happen. Both chips navigate to the surface that owns them. Switching an
 * agent is still available from ⌘K and from the Agents surface, so the
 * capability is intact — only the duplicate control is gone.
 *
 * Props:
 *   health        — { hostname, version, ... }
 *   tenant        — resolved tenant object | null
 *   agent         — resolved agent object | null
 *   activeRun     — { runId, scenarioId, step, totalSteps, elapsed } | null
 *   lastRun       — { runId, scenarioId, status } | null
 *   onOpenPalette — () => void
 *   onNavigate    — (destinationId, params?) => void
 *   onStartTour   — () => void | null   (renders the ? button when provided)
 *   tourSeen      — boolean; false puts a beacon on the ? button
 *   colorTheme / onToggleColorTheme, theaterMode / onToggleTheater
 */
export default function ConsoleHeader({
  health = {},
  tenant = null,
  agent = null,
  activeRun = null,
  lastRun = null,
  onOpenPalette = () => {},
  onNavigate = () => {},
  onStartTour = null,
  tourSeen = true,
  userInitials = 'DC',
  theaterMode = false,
  onToggleTheater = null,
  colorTheme = 'dark',
  onToggleColorTheme = null,
  povName,
}) {
  // NOT derived from window.location. The previous env pill fell back to the
  // BROWSER's hostname when /api/health omitted one, so it read "LOCALHOST" on
  // a DC's laptop whether SimCore was local, in compose or on a jumpbox — a
  // status that is always the same is worse than none, because it occupies the
  // spot a real signal would occupy.
  const version = health.version ? `v${String(health.version).replace(/^v/, '')}` : 'v0.2.0'
  const dark = colorTheme === 'dark'
  const cortexMark = dark ? '/assets/cortex-green.png' : '/assets/cortex-mono.png'

  const tenantLabel = tenant ? (tenant.host || tenant.name || tenant.id) : 'no tenant bound'
  const agentLabel = agent ? (agent.hostname || agent.agent_id || agent.id) : 'no agent selected'

  return (
    <header className="pov-header" data-testid="console-header">
      <div className="pov-brand brand-marks">
        <img className="pov-brand__mark brand-marks__cortex" src={cortexMark} alt="" />
        <div className="pov-brand__word brand__wordmark">
          <b>POV</b><span>engine</span>
        </div>
        <span className="pov-brand__version brand__version mono">{version}</span>
      </div>

      <EvidenceCollection povName={povName} onNavigate={onNavigate} />

      <div className="pov-header__spacer" />

      <button
        type="button"
        className="pov-chip"
        data-testid="header-tenant"
        onClick={() => onNavigate('tenants')}
        title="Tenant — one per instance"
      >
        <span className={'pov-chip__dot' + (tenant ? '' : ' pov-chip__dot--off')} />
        <span>{tenantLabel}</span>
      </button>

      <button
        type="button"
        className="pov-chip"
        data-testid="header-agent"
        onClick={() => onNavigate('agents')}
        title="Active agent"
      >
        <span className={'pov-chip__dot' + (agent ? '' : ' pov-chip__dot--off')} />
        <span>{agentLabel}</span>
      </button>

      {/* The run view is unconditional. A DC is in the "a run is in flight"
          state for about three minutes of a two-hour session, so a pill that
          rendered only then left the header with no answer at all to "what is
          this doing / what did it last do" — the most common question between
          runs. All three states are the same control and all three land on
          Runs, so the way back to the evidence never moves. */}
      <RunPill activeRun={activeRun} lastRun={lastRun} onNavigate={onNavigate} />

      <button type="button" className="pov-chip" onClick={onOpenPalette} data-testid="header-palette"
        aria-label="Open command palette (search, launch, export)">
        <span className="pov-chip__kbd">⌘K</span>
        <span className="pov-chip__label">Search</span>
      </button>

      {onStartTour && (
        <button
          type="button"
          className="pov-chip tour-trigger"
          onClick={onStartTour}
          data-testid="header-tour-trigger"
          aria-label="Take the guided tour of the console"
          title="Guided tour"
        >
          ?
          {/* One beacon, on one genuinely new affordance, cleared for good on
              first use. `tourSeen` fails CLOSED in onboardingState (unreadable
              storage reports "seen"), so a browser we cannot remember never
              gets a permanent beacon. */}
          {!tourSeen && <span className="tour-trigger__beacon" aria-hidden="true" />}
        </button>
      )}

      {onToggleTheater && (
        <button
          type="button"
          className={'pov-chip theater-toggle' + (theaterMode ? ' is-active' : '')}
          onClick={onToggleTheater}
          aria-pressed={theaterMode}
          title={theaterMode ? 'Exit theater mode' : 'Theater mode — projector-friendly'}
        >
          {theaterMode ? 'Theater on' : 'Theater'}
        </button>
      )}

      {onToggleColorTheme && (
        <button
          type="button"
          className="pov-chip theme-toggle"
          onClick={onToggleColorTheme}
          aria-pressed={dark}
          aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
          title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
        >
          {dark ? 'Dark' : 'Light'}
        </button>
      )}

      <div className="pov-avatar user-avatar" title="Domain Consultant">{userInitials}</div>
    </header>
  )
}

function fmtElapsed(s) {
  if (s == null) return null
  if (typeof s === 'string') return s
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

function RunPill({ activeRun, lastRun, onNavigate }) {
  if (activeRun) {
    const elapsed = fmtElapsed(activeRun.elapsed)
    return (
      <button
        type="button"
        className="pov-chip pov-live live-pill"
        data-testid="header-run-view"
        data-run-state="live"
        onClick={() => onNavigate('runs', { run: activeRun.runId, tab: 'live' })}
        aria-label={`Live run ${activeRun.scenarioId || ''} — open in Runs`}
      >
        <span className="pov-chip__dot" />
        <span className="pov-live__tag">LIVE</span>
        <span>
          {activeRun.scenarioId} · {activeRun.step}/{activeRun.totalSteps}
          {elapsed ? ` · ${elapsed}` : ''}
        </span>
      </button>
    )
  }
  if (lastRun) {
    return (
      <button
        type="button"
        className="pov-chip run-pill run-pill--last"
        data-testid="header-run-view"
        data-run-state="last"
        onClick={() => onNavigate('runs', { run: lastRun.runId, tab: 'evidence' })}
        aria-label={`Last run ${lastRun.scenarioId || ''} — open its evidence`}
      >
        <span className={'pov-chip__dot' + (lastRun.status === 'completed' ? '' : ' pov-chip__dot--warn')} />
        <span>{lastRun.scenarioId || lastRun.runId} · {lastRun.status || 'done'}</span>
      </button>
    )
  }
  // Not a placeholder for a value we failed to fetch — genuinely no run has
  // ever completed on this SimCore. Says so, and still routes to the surface
  // that would show one.
  return (
    <button
      type="button"
      className="pov-chip run-pill run-pill--none"
      data-testid="header-run-view"
      data-run-state="none"
      onClick={() => onNavigate('runs')}
      aria-label="No runs yet — open Runs"
    >
      <span className="pov-chip__dot pov-chip__dot--off" />
      <span>no runs yet</span>
    </button>
  )
}
