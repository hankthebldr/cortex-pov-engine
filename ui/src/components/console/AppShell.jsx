import React, { useState, useCallback, useEffect, useRef } from 'react'
import ConsoleHeader from './ConsoleHeader.jsx'
import DestinationNav from './DestinationNav.jsx'
import CommandPalette from './CommandPalette.jsx'
import SafetyBanner from './SafetyBanner.jsx'
import HelpOverlay, { markFirstRunSeen } from './HelpOverlay.jsx'
import { useTour } from '../onboarding/useTour.js'
import TourSpotlight from '../onboarding/TourSpotlight.jsx'
import { TOUR_STOPS } from '../onboarding/tourStops.js'
import { tourSeen as readTourSeen } from '../onboarding/onboardingState.js'

/**
 * AppShell — Mission Ops Console layout wrapper.
 *
 * Two regions — header and workspace — plus the safety gate row above them.
 * The primary nav is the DestinationNav task rail. There is no footer bar: the
 * flow bar that used to sit there restated the rail in a second vocabulary,
 * and "what do I do next" is now answered by Get started, on real state.
 *
 * Props:
 *   destination   — current destination id (was activeTab)
 *   onNavigate    — (destinationId, params?) => void (was onTabChange)
 *   navGroups     — [{ label, items: [{ id, label, icon, badge }] }] for the nav
 *   activeRun     — { scenarioId, step, totalSteps, elapsed, ... } | null
 *   health        — { hostname, version, sensors, tenantHealth }
 *   onAbortRun    — () => void
 *   paletteItems  — items for ⌘K
 *   onExportPOV   — () => void  triggered by ⌘E from anywhere
 *   children      — the mounted destination surface
 */
export default function AppShell({
  destination = 'library',
  onNavigate = () => {},
  navGroups = [],
  activeRun = null,
  lastRun = null,
  health = {},
  tenant = null,
  agent = null,
  tenantId = null,
  povName = undefined,
  // The rail item that owns the open page (a tab's parent task).
  activeNav = null,
  onAbortRun = () => {},
  paletteItems = [],
  onExportPOV = null,
  // Shell-level notice row (the SimCore health/degraded banner). It is a slot
  // rather than a child on purpose: rendered as a child it lands INSIDE
  // `.view`, above the destination's own content, which pushed every in-view
  // filter rail down by its height while the nav rail stayed pinned to the top
  // of `.workspace` — two sidebars with two different tops, measured at 159px
  // apart. It is also a statement about SimCore, not about whichever
  // destination happens to be open, so the shell is where it belongs.
  banner = null,
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
  // railCollapsed/theaterMode above. Defaults to DARK, which is a change: the
  // console is a dark surface in the design, and the light set it used to
  // default to was drawn for a different accent contract. Light is still a
  // complete, AA-clean token set and one click away; it is opt-in now rather
  // than the default, so `[data-theme]` is written on every render instead of
  // only for dark. Note this is a different key/concept than `cortexsim.theme`
  // in main.jsx, which picks between this console and the legacy shell — this
  // one picks the colour palette *within* the console.
  const [colorTheme, setColorTheme] = useState(() => {
    try { return window.localStorage.getItem('cortexsim.colorTheme') === 'light' ? 'light' : 'dark' } catch { return 'dark' }
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

  // The tour no longer auto-starts. Get started is the first-run guidance now,
  // and it is built on what this instance actually has; a spotlight popping
  // over it on first load was a second, competing set of instructions. The
  // tour is still one click away on the ? button.
  const tour = useTour({
    stops: TOUR_STOPS,
    onNavigate: tourNavigate,
    autoStart: false,
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

  // Header / workspace, with the safety gate above while it is shown.
  const shellClass = 'pov-shell shell shell--no-phasebar'

  // Theater mode lost its header button (one fewer control in a bar that had
  // ten) and lives in ⌘K instead, beside the other view toggles.
  const allPaletteItems = [
    ...paletteItems,
    {
      section: 'View',
      id: 'toggle-theater',
      title: theaterMode ? 'Exit theater mode' : 'Theater mode — projector-friendly',
      meta: 'larger type, hides the rail',
      icon: '▭',
      onSelect: toggleTheater,
    },
  ]
  const themeClass = `theme-console ${theaterMode ? 'theme-console--theater' : ''}`

  return (
    // `data-theme` is written on EVERY render now, not only for dark. Dark is
    // the default and light is opt-in, so "no attribute" can no longer mean
    // "light" — it would mean the token layer's :root light set leaking under
    // a shell that expects dark surfaces.
    <div className={`${themeClass} ${shellClass}`} data-theme={colorTheme}>
      {/* Skip link — keyboard users land here on Tab; jumps past bar/nav to the
          main workspace. Invisible until focused. */}
      <a href="#cortexsim-main" className="skip-link">
        Skip to workspace
      </a>

      {/* Above the header on purpose: it is a consent gate for the whole
          console, not a property of any one surface. It removes itself from
          the grid entirely once acknowledged for this tenant, so the row
          template below must not reserve space for it. */}
      <SafetyBanner tenantId={tenantId} onNavigate={onNavigate} />

      <ConsoleHeader
        health={health}
        tenant={tenant}
        agent={agent}
        activeRun={activeRun}
        lastRun={lastRun}
        povName={povName}
        onOpenPalette={() => setPaletteOpen(true)}
        onNavigate={onNavigate}
        onStartTour={() => tour.start()}
        tourSeen={readTourSeen()}
        colorTheme={colorTheme}
        onToggleColorTheme={toggleColorTheme}
      />

      {banner}

      <div className={'pov-workspace workspace' + (railCollapsed ? ' pov-workspace--rail-collapsed workspace--rail-collapsed' : '')}>
        <DestinationNav
          groups={navGroups}
          active={activeNav || destination}
          onNavigate={onNavigate}
          collapsed={railCollapsed}
          onToggleCollapse={toggleRail}
        />

        <main className="pov-main main" id="cortexsim-main" aria-label="POVengine workspace">
          <div className="view" key={destination}>
            {children}
          </div>
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        items={allPaletteItems}
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
