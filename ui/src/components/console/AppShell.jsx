import React, { useState, useCallback, useEffect } from 'react'
import ConsoleHeader from './ConsoleHeader.jsx'
import TelemetryStrip from './TelemetryStrip.jsx'
import ConsoleRail from './ConsoleRail.jsx'
import ConsoleTabs from './ConsoleTabs.jsx'
import CommandStrip from './CommandStrip.jsx'
import CommandPalette from './CommandPalette.jsx'

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
 *   onAbortRun          — () => void
 *   tabBadges           — { operations: '19', inflight: 'LIVE', evidence: '4/12' }
 *   paletteItems        — items for ⌘K — see CommandPalette
 *   ticker              — string rendered in bottom strip
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
  onAbortRun = () => {},
  tabBadges = {},
  paletteItems = [],
  ticker = '',
  children,
}) {
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Global ⌘K / Ctrl+K handler
  useEffect(() => {
    const handler = (e) => {
      const key = e.key ? e.key.toLowerCase() : ''
      if ((e.metaKey || e.ctrlKey) && key === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      } else if (key === 'escape') {
        setPaletteOpen(false)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  const shellClass = `shell${activeRun ? '' : ' shell--no-telemetry'}`

  return (
    <div className={`theme-console ${shellClass}`}>
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
        />

        <section className="main">
          <ConsoleTabs
            activeTab={activeTab}
            onTabChange={onTabChange}
            badges={tabBadges}
          />

          <div className="view" key={activeTab}>
            {children}
          </div>
        </section>
      </div>

      <CommandStrip ticker={ticker} />

      <CommandPalette
        open={paletteOpen}
        items={paletteItems}
        onClose={() => setPaletteOpen(false)}
      />
    </div>
  )
}
