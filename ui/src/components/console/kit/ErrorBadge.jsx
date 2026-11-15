/**
 * ErrorBadge — a short error name that takes you to the fix (design v26 §4.1).
 *
 * It is a button, not a label: pressing it opens Configure at the step that
 * fixes the problem (the caller passes `onClick`). The full error text is the
 * button's content, so its accessible name is complete; CSS truncates what is
 * shown at about 24 characters and `title` repeats the full text on hover.
 *
 *   tone="warn" — degraded / changed (yellow)
 *   tone="crit" — down / missing / broken (coral)
 */
import React from 'react'

void React

export default function ErrorBadge({ children, tone = 'warn', onClick, className = '' }) {
  const text = typeof children === 'string' ? children : undefined
  return (
    <button
      type="button"
      className={`kit-errbadge kit-errbadge--${tone === 'crit' ? 'crit' : 'warn'} ${className}`.trim()}
      onClick={onClick}
      title={text}
    >
      <span className="kit-errbadge__glyph" aria-hidden="true">!</span>
      <span className="kit-errbadge__text">{children}</span>
    </button>
  )
}
