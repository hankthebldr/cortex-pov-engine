import React, { useState, useCallback, useEffect, useRef } from 'react'
import ConsoleHeader from './ConsoleHeader.jsx'
import TelemetryStrip from './TelemetryStrip.jsx'
import DestinationNav from './DestinationNav.jsx'
import CommandStrip from './CommandStrip.jsx'
import CommandPalette from './CommandPalette.jsx'
import HelpOverlay, { shouldShowOnFirstRun, markFirstRunSeen } from './HelpOverlay.jsx'
import { useTour } from '../onboarding/useTour.js'
import TourSpotlight from '../onboarding/TourSpotlight.jsx'
import { TOUR_STOPS } from '../onboarding/tourStops.js'

/**
 * AppShell — Mission Ops Console layout wrapper.
 *
 * Provides the 4-region shell (global context bar · telemetry · workspace ·
 * command strip). The primary nav is the PERSISTENT DestinationNav sidebar —
 * every destination is one click away at any time (the linear ConsoleStepper +
 * More▾ overflow are gone). The active tenant/agent live in the global bar's
 * switchers, not as destinations.
 *
 * Props:
 *   destination   — current destination id (was activeTab)
 *   onNavigate    — (destinationId, params?) => void (was onTabChange)
 *   navGroups     — [{ label, items: [{ id, label, icon, badge }] }] for the nav
 *   activeRun     — { scenarioId, step, totalSteps, elapsed, ... } | null
 *   health        — { hostname, version, sensors, tenantHealth }
 *   onAbortRun    — () => void
 *   paletteItems  — items for ⌘K
 *   ticker        — string for the bottom strip
 *   onExportPOV   — () => void  triggered by ⌘E from anywhere
 *   children      — the mounted destination surface
 */
export default function AppShell({
  destination = 'library',
  onNavigate = () => {},
  navGroups = [],
  activeRun = null,
  health = {},
  onAbortRun = () => {},
  paletteItems = [],
  ticker = '',
  onExportPOV = null,
  children,
}) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [helpOpen, setHelpOpen]       = useState(false)
  // Rail collapse — persisted so a DC's preference survives reloads.
  const [railCollapsed, setRailCollapsed] = useState(() => {
    try { return window.localStorage.getItem('cortexsim.railCollapsed') === 'true' } catch { return false }
  })
  const toggleRail = useCallback(() => {
    setRailCollapsed((v) => {
      const next = !v
      try { window.localStorage.setItem('cortexsim.railCollapsed', String(next)) } catch {}
      return next
    })
  }, [])
  // Theater mode — projector-friendly view for sales briefings.
  const [theaterMode, setTheaterMode] = useState(() => {
    try { return window.localStorage.getItem('cortexsim.theaterMode') === 'true' } catch { return false }
  })
  const toggleTheater = useCallback(() => {
    setTheaterMode((v) => {
      const next = !v
      try { window.localStorage.setItem('cortexsim.theaterMode', String(next)) } catch {}
      return next
    })
  }, [])
  // Colour theme (light/dark) — same persisted-preference pattern as
  // railCollapsed/theaterMode above. Defaults to LIGHT: the token layer
  // (cortex-tokens.css) treats :root as the light theme and [data-theme]
  // as opt-in, so no attribute at all IS light. Note this is a different
  // key/concept than `cortexsim.theme` in main.jsx, which picks between
  // the Mission Ops Console and the legacy shell — this one picks the
  // colour palette *within* the console shell.
  const [colorTheme, setColorTheme] = useState(() => {
    try { return window.localStorage.getItem('cortexsim.colorTheme') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
  })
  const toggleColorTheme = useCallback(() => {
    setColorTheme((v) => {
      const next = v === 'dark' ? 'light' : 'dark'
      try { window.localStorage.setItem('cortexsim.colorTheme', next) } catch {}
      return next
    })
  }, [])

  // `onNavigate` reaches AppShell as a prop — its identity is stable when the
  // caller (AppConsole) is wired correctly, but AppShell's own default value
  // (`() => {}`) is a fresh function every render, and nothing here forces a
  // caller to memoize. useTour's internal callbacks list `onNavigate` in
  // their dependency arrays, so an unstable identity would recreate them
  // every render. A ref-backed wrapper is stable regardless of what the
  // caller passes, so it is handed to useTour instead of the raw prop.
  const onNavigateRef = useRef(onNavigate)
  useEffect(() => { onNavigateRef.current = onNavigate }, [onNavigate])

  // Tracks the destination the TOUR itself most recently asked for, so the
  // effect below can tell "the tour navigated" apart from "something else
  // did" (I4 / spec §6: "Any navigation the tour did not initiate → exit").
  // The cutout is `pointer-events: none` and stops 1/3/5 spotlight a real
  // nav button, so a user clicking the highlighted control navigates the
  // app while the tour is still up — previously nothing noticed, and
  // Next/Back went on to probe anchors in the wrong destination.
  const tourNavTargetRef = useRef(null)
  const tourNavigate = useCallback((destId, ...rest) => {
    tourNavTargetRef.current = destId
    onNavigateRef.current(destId, ...rest)
  }, [])

  // First-run tour — appears once per browser (unless the help overlay was
  // already dismissed first), then suppressed. Replaces the old first-run
  // help-overlay auto-open.
  const tour = useTour({
    stops: TOUR_STOPS,
    onNavigate: tourNavigate,
    autoStart: shouldShowOnFirstRun(),
  })

  // See tourNavTargetRef above: while the tour is active, any change to the
  // CURRENT destination that the tour did not itself request means the user
  // navigated on their own (nav rail, ⌘K, a breadcrumb, …) — exit rather
  // than leave the tour spotlighting a control on a surface it no longer
  // matches.
  useEffect(() => {
    if (!tour.active) return
    if (tourNavTargetRef.current === null) return
    if (destination !== tourNavTargetRef.current) tour.exit()
  }, [destination, tour.active, tour.exit])

  // Global ⌘K / ⌘/ / ⌘E handlers (preserved from the stepper shell).
  useEffect(() => {
    const handler = (e) => {
      const key = e.key ? e.key.toLowerCase() : ''
      const mod = e.metaKey || e.ctrlKey
      if (mod && key === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      } else if (mod && (key === '/' || key === '?')) {
        e.preventDefault()
        setHelpOpen((v) => !v)
      } else if (mod && key === 'e' && !e.shiftKey) {
        if (onExportPOV) {
          e.preventDefault()
          onExportPOV()
        }
      } else if (key === 'escape') {
        setPaletteOpen(false)
        setHelpOpen(false)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onExportPOV])

  const handleCloseHelp = useCallback(() => {
    setHelpOpen(false)
    markFirstRunSeen()
  }, [])

  const shellClass = `shell${activeRun ? '' : ' shell--no-telemetry'}`
  const themeClass = `theme-console ${theaterMode ? 'theme-console--theater' : ''}`

  return (
    <div className={`${themeClass} ${shellClass}`} data-theme={colorTheme === 'dark' ? 'dark' : undefined}>
      {/* Skip link — keyboard users land here on Tab; jumps past bar/nav to the
          main workspace. Invisible until focused. */}
      <a href="#cortexsim-main" className="skip-link">
        Skip to workspace
      </a>

      <ConsoleHeader
        health={health}
        activeRun={activeRun}
        onOpenPalette={() => setPaletteOpen(true)}
        onNavigate={onNavigate}
        theaterMode={theaterMode}
        onToggleTheater={toggleTheater}
        colorTheme={colorTheme}
        onToggleColorTheme={toggleColorTheme}
      />

      {activeRun && (
        <TelemetryStrip run={activeRun} onAbort={onAbortRun} />
      )}

      <div className={'workspace' + (railCollapsed ? ' workspace--rail-collapsed' : '')}>
        <DestinationNav
          groups={navGroups}
          active={destination}
          onNavigate={onNavigate}
          collapsed={railCollapsed}
          onToggleCollapse={toggleRail}
        />

        <main className="main" id="cortexsim-main" aria-label="CortexSim workspace">
          <div className="view" key={destination}>
            {children}
          </div>
        </main>
      </div>

      <CommandStrip ticker={ticker} />

      <CommandPalette
        open={paletteOpen}
        items={paletteItems}
        onClose={() => setPaletteOpen(false)}
      />

      <HelpOverlay
        open={helpOpen}
        onClose={handleCloseHelp}
        onTour={() => { setHelpOpen(false); tour.start() }}
      />

      {tour.active && (
        <TourSpotlight
          stop={tour.stop}
          index={tour.index}
          total={tour.total}
          onNext={tour.next}
          onPrev={tour.prev}
          onExit={tour.exit}
        />
      )}
    </div>
  )
}
