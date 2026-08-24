import React, { useState, useCallback, useEffect } from 'react'
import ConsoleHeader from './ConsoleHeader.jsx'
import TelemetryStrip from './TelemetryStrip.jsx'
import DestinationNav from './DestinationNav.jsx'
import CommandStrip from './CommandStrip.jsx'
import CommandPalette from './CommandPalette.jsx'
import HelpOverlay, { shouldShowOnFirstRun, markFirstRunSeen } from './HelpOverlay.jsx'

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

  // First-run help overlay — appears once per browser, then suppressed.
  useEffect(() => {
    if (shouldShowOnFirstRun()) {
      const t = setTimeout(() => setHelpOpen(true), 400)
      return () => clearTimeout(t)
    }
    return undefined
  }, [])

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
    <div className={`${themeClass} ${shellClass}`}>
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
      />
    </div>
  )
}
