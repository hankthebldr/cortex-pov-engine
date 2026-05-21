import React, { useState, useCallback, useEffect } from 'react'
import ConsoleHeader from './ConsoleHeader.jsx'
import TelemetryStrip from './TelemetryStrip.jsx'
import ConsoleRail from './ConsoleRail.jsx'
import ConsoleTabs from './ConsoleTabs.jsx'
import CommandStrip from './CommandStrip.jsx'
import CommandPalette from './CommandPalette.jsx'
import HelpOverlay, { shouldShowOnFirstRun, markFirstRunSeen } from './HelpOverlay.jsx'

/**
 * AppShell — Mission Ops Console layout wrapper.
 *
 * Provides the 4-region shell (header · telemetry · workspace · command strip)
 * with tabs for Operations / In-Flight / Evidence / Lab / Coverage.
 *
 * Props:
 *   activeTab           — controlled tab id
 *   onTabChange         — (tabId) => void
 *   activeRun           — { scenarioId, step, totalSteps, elapsed, detected, total, nextStep }
 *   health              — { hostname, version, sensors: { xdr, cdr, ndr } }
 *   planes              — array of { code, name, count, isActive } for the rail
 *   onSelectPlane       — (planeCode) => void
 *   pinned              — [{ id, name }]
 *   onSelectPinned      — (scenarioId) => void
 *   onUnpinScenario     — (scenarioId) => void
 *   onAbortRun          — () => void
 *   tabBadges           — { operations: '19', inflight: 'LIVE', evidence: '4/12' }
 *   paletteItems        — items for ⌘K — see CommandPalette
 *   ticker              — string rendered in bottom strip
 *   onExportPOV         — () => void  triggered by ⌘E from anywhere
 *   children            — tab content (rendered in the main workspace area)
 */
export default function AppShell({
  activeTab = 'operations',
  onTabChange = () => {},
  activeRun = null,
  health = {},
  planes = [],
  onSelectPlane = () => {},
  pinned = [],
  onSelectPinned = () => {},
  onUnpinScenario = null,
  onAbortRun = () => {},
  tabBadges = {},
  paletteItems = [],
  ticker = '',
  onExportPOV = null,
  children,
}) {
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [helpOpen, setHelpOpen]       = useState(false)

  // First-run help overlay — appears once per browser, then suppressed.
  useEffect(() => {
    if (shouldShowOnFirstRun()) {
      // Defer so it doesn't race with the initial render's keyboard handlers.
      const t = setTimeout(() => setHelpOpen(true), 400)
      return () => clearTimeout(t)
    }
    return undefined
  }, [])

  // Global ⌘K / ⌘/ / Ctrl+K / Ctrl+/ handler
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
        // ⌘E — global POV report export (preempts the browser's "view page
        // source" / Firefox print-preview default; only when we have an
        // export handler wired)
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

  return (
    <div className={`theme-console ${shellClass}`}>
      {/* Skip link — keyboard users land here on Tab; jumps past header/rail
          to the main workspace. Invisible until focused. */}
      <a href="#cortexsim-main" className="skip-link">
        Skip to workspace
      </a>

      <ConsoleHeader
        health={health}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      {activeRun && (
        <TelemetryStrip run={activeRun} onAbort={onAbortRun} />
      )}

      <div className="workspace">
        <ConsoleRail
          planes={planes}
          pinned={pinned}
          onSelectPlane={onSelectPlane}
          onSelectPinned={onSelectPinned}
          onUnpin={onUnpinScenario}
        />

        <main className="main" id="cortexsim-main" aria-label="CortexSim workspace">
          <ConsoleTabs
            activeTab={activeTab}
            onTabChange={onTabChange}
            badges={tabBadges}
          />

          <div className="view" key={activeTab}>
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
