import React from 'react'

/**
 * CommandStrip — 32px bottom strip with keyboard hints + live event ticker.
 *
 * Props:
 *   ticker   — string — last event, e.g. "12:43:24Z · CDR · T1580 pending"
 *   hints    — array of { keys: [string], label: string } (defaults to standard set)
 */
const DEFAULT_HINTS = [
  { keys: ['⌘', 'K'], label: 'search' },
  { keys: ['⌘', 'L'], label: 'launch' },
  { keys: ['⌘', 'E'], label: 'export' },
  { keys: ['⌘', '/'], label: 'help'   },
]

export default function CommandStrip({ ticker = '', hints = DEFAULT_HINTS }) {
  return (
    <div
      className="command-strip"
      role="contentinfo"
      aria-label="Keyboard shortcuts and event ticker"
    >
      <div className="cs-hints" aria-hidden="true">
        {hints.map((h, i) => (
          <span key={i} className="cs-hint">
            {h.keys.map((k) => (
              <span key={k} className="kbd">{k}</span>
            ))}
            <span>{h.label}</span>
          </span>
        ))}
      </div>
      <div
        className="cs-ticker"
        role="status"
        aria-live="polite"
        aria-label="Latest event"
      >
        {ticker || 'idle'}
      </div>
    </div>
  )
}
