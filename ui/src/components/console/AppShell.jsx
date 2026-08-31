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
  // caller to memoize. useTour's navigate effect lists `onNavigate` in its
  // dependency array, so an unstable identity re-fires it every render. A
  // ref-backed wrapper is stable regardless of what the caller passes, so it
  // is handed to useTour instead of the raw prop.
  const onNavigateRef = useRef(onNavigate)
  useEffect(() => { onNavigateRef.current = onNavigate }, [onNavigate])
  const stableOnNavigate = useCallback((...args) => onNavigateRef.current(...args), [])

  // First-run tour — appears once per browser (unless the help overlay was
  // already dismissed first), then suppressed. Replaces the old first-run
  // help-overlay auto-open.
  const tour = useTour({
    stops: TOUR_STOPS,
    onNavigate: stableOnNavigate,
    autoStart: shouldShowOnFirstRun(),
  })

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
