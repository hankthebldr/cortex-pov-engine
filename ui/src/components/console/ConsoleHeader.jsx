import React from 'react'
import TenantSwitcher from './GlobalContextBar/TenantSwitcher.jsx'
import AgentSwitcher from './GlobalContextBar/AgentSwitcher.jsx'

/**
 * ConsoleHeader — the persistent GLOBAL CONTEXT BAR.
 *
 * Always visible, top of shell. Left→right:
 *   1. PANW + Cortex brand marks, then the product wordmark + version
 *   2. Tenant switcher pill (provider-backed; status dot + Manage… footer)
 *   3. Agent switcher pill  (provider-backed; liveness dot + Manage… footer)
 *   4. Connectivity/health chip — the real sensor summary for the active tenant
 *   5. Global RUN view — always present; LIVE when in flight, last-run when idle
 *   6. ⌘K command-palette trigger
 *   7. Guided-tour trigger (carries a beacon until the tour is first taken)
 *   8. Theater-mode + colour-theme toggles
 *
 * The two switchers + the ⌘K palette all write to the SAME provider, so the
 * active tenant/agent is switchable from three places and reflected everywhere.
 *
 * WHY THE RUN VIEW IS UNCONDITIONAL
 * ---------------------------------
 * It used to render only while `activeRun` was non-null. That is the state a DC
 * is in for about three minutes of a two-hour session, so in practice the
 * header carried scope (tenant, agent) and no answer at all to "what is this
 * thing doing right now / what did it last do" — the single most common
 * question between runs. It now always renders: LIVE + step counter while a run
 * is in flight, the last terminal run when idle, and an explicit "no runs yet"
 * when there is genuinely nothing. All three are the same control and all three
 * deep-link into Runs & Proof, so the way back to the evidence never moves.
 *
 * Brand marks are two <img> lockups, not text. `panw-mark.png` is the
 * WHITE-wordmark lockup (correct on dark, invisible on the light header) and
 * `panw-primary.png` is the black-wordmark/orange-glyph one — so the pair is
 * selected on `colorTheme`, never fixed. Same for the Cortex mark
 * (mono → green). Both are decorative next to the text wordmark that follows,
 * hence `alt=""` on the Cortex mark and a real `alt` only on the PANW lockup.
 *
 * Props:
 *   health          — { hostname, version, sensors: {..}, tenantHealth }
 *   activeRun       — { scenarioId, step, totalSteps, elapsed } | null
 *   lastRun         — { runId, scenarioId, status } | null  (idle run view)
 *   onOpenPalette   — () => void
 *   onNavigate      — (destinationId, params?) => void  (Manage… + run view)
 *   onStartTour     — () => void | null   (renders the ? button when provided)
 *   tourSeen        — boolean; false puts a beacon on the ? button
 *   userInitials    — string
 *   theaterMode     — boolean
 *   onToggleTheater — () => void
 *   colorTheme      — 'light' | 'dark'
 *   onToggleColorTheme — () => void
 */
export default function ConsoleHeader({
  health = {},
  activeRun = null,
  lastRun = null,
  onOpenPalette,
  onNavigate = () => {},
  onStartTour = null,
  tourSeen = true,
  userInitials = 'DC',
  theaterMode = false,
  onToggleTheater = null,
  colorTheme = 'light',
  onToggleColorTheme = null,
}) {
  // REMOVED: the `LOCALHOST / sensors pending` env pill.
  //
  // It was not merely decorative, it was WRONG in two ways at once, and both
  // were permanent rather than transient:
  //   - `GET /api/health` returns no `hostname` key, so it fell back to
  //     `window.location.hostname` — i.e. it displayed the BROWSER's host and
  //     labelled it as the environment. On a DC's laptop that reads
  //     "LOCALHOST" whether SimCore is local, in compose, or on a jumpbox.
  //   - `GET /api/health` returns no `sensors` key either, so the summary was
  //     the literal string "sensors pending" on every deployment that has ever
  //     existed. "Pending" reads as a state that will resolve. It cannot.
  // A status pill that always shows the same never-resolving status is worse
  // than no pill: it occupies the spot a real health signal would occupy and
  // trains the reader to ignore it. `/api/health` already has a truthful
  // surface in ReadinessBanner + the Readiness destination; this duplicated it
  // badly. If SimCore ever reports hostname/sensors, reinstate it from that
  // data — not from `window.location`.
  const version = health.version || 'v1.0'

  const fmtElapsed = (s) => {
    if (s == null) return '0:00'
    const m = Math.floor(s / 60)
    const sec = String(s % 60).padStart(2, '0')
    return `${m}:${sec}`
  }

  const dark = colorTheme === 'dark'
  const panwMark = dark ? '/assets/panw-mark.png' : '/assets/panw-primary.png'
  const cortexMark = dark ? '/assets/cortex-green.png' : '/assets/cortex-mono.png'

  return (
    <header className="header">
      <div className="header__left">
        <div className="brand-marks">
          <img className="brand-marks__panw" src={panwMark} alt="Palo Alto Networks" />
          <span className="brand-marks__divider" aria-hidden="true" />
          <img className="brand-marks__cortex" src={cortexMark} alt="" />
        </div>
        <div className="brand">
          <div className="brand__wordmark">cortex<em>sim</em></div>
          <div className="brand__subtitle">Detection Simulation Engine</div>
        </div>
        <span className="brand__version mono">{version}</span>
      </div>

      <div className="header__right">
        <TenantSwitcher onManage={() => onNavigate('tenants')} />
        <AgentSwitcher onManage={() => onNavigate('agents')} />

        {/* The run view — see "WHY THE RUN VIEW IS UNCONDITIONAL" above. Three
            mutually exclusive states, one control, one destination. */}
        {activeRun ? (
          <button
            type="button"
            className="env-pill live-pill"
            data-testid="header-run-view"
            data-run-state="live"
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
        ) : lastRun ? (
          <button
            type="button"
            className="env-pill run-pill run-pill--last"
            data-testid="header-run-view"
            data-run-state="last"
            onClick={() => onNavigate('runs', { run: lastRun.runId, tab: 'evidence' })}
            title="Open the evidence for the most recent run"
            aria-label={`Last run ${lastRun.scenarioId || ''} — open its evidence`}
          >
            <span className="env-pill__dot run-pill__dot" />
            <span className="env-pill__label run-pill__label">LAST RUN</span>
            <span className="env-pill__meta mono">
              {lastRun.scenarioId} · {lastRun.status}
            </span>
          </button>
        ) : (
          /* Not a placeholder for a value we failed to fetch — genuinely no run
             has ever completed on this SimCore. Says so, and still routes to
             the surface that would show one. */
          <button
            type="button"
            className="env-pill run-pill run-pill--none"
            data-testid="header-run-view"
            data-run-state="none"
            onClick={() => onNavigate('runs')}
            title="No run has completed yet — open Runs & Proof"
            aria-label="No runs yet — open Runs & Proof"
          >
            <span className="env-pill__dot run-pill__dot" />
            <span className="env-pill__label run-pill__label">NO RUNS</span>
            <span className="env-pill__meta mono">nothing launched yet</span>
          </button>
        )}

        <button className="cmd-trigger" onClick={onOpenPalette} aria-label="Open command palette (search, launch, export)" title="Search · launch · export">
          <span className="kbd">⌘K</span>
          <span className="cmd-trigger__label">Search</span>
        </button>

        {onStartTour && (
          <button
            type="button"
            className="tour-trigger"
            onClick={onStartTour}
            data-testid="header-tour-trigger"
            aria-label="Take the guided tour of the console"
            title="Guided tour"
          >
            ?
            {/* One beacon, on one genuinely new affordance, cleared for good on
                first use — the design's own rule. `tourSeen` fails CLOSED in
                onboardingState (unreadable storage reports "seen"), so a
                browser we cannot remember never gets a permanent beacon. */}
            {!tourSeen && <span className="tour-trigger__beacon" aria-hidden="true" />}
          </button>
        )}

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

        {onToggleColorTheme && (
          <button
            type="button"
            className="theme-toggle"
            onClick={onToggleColorTheme}
            aria-pressed={colorTheme === 'dark'}
            aria-label={colorTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            title={colorTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            {colorTheme === 'dark' ? '🌙' : '☀'}<span className="theme-toggle__label">{colorTheme === 'dark' ? 'Dark' : 'Light'}</span>
          </button>
        )}

        <span className="header__divider" aria-hidden="true" />
        <div className="user-avatar" title="Domain Consultant">{userInitials}</div>
        <div className="panw-mark">palo alto <span>networks</span></div>
      </div>
    </header>
  )
}
