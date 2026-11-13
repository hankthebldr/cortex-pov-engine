/**
 * Toast — every action that finishes in place says so (design v26 §4.1).
 *
 * Bottom-centre, one line, gone after 2.4s (the caller owns the timer). The
 * live region is always mounted and only its content changes: a region that
 * appears together with its text is often not announced at all, so screen
 * readers would miss exactly the confirmations this exists to give.
 *
 * `toast` is { message, type } or null; type is success | info | warn | error.
 * success/info confirm (✓, green ring); warn (!, yellow) and error (!, coral)
 * report a problem in the same place.
 */
import React from 'react'

void React

export const TOAST_MS = 2400

const TONE = { success: 'ok', info: 'ok', warn: 'warn', error: 'crit' }

export default function Toast({ toast = null }) {
  const tone = toast ? (TONE[toast.type] || 'ok') : 'ok'
  return (
    <div className="kit-toast-region" role="status" aria-live="polite">
      {toast && toast.message && (
        <div className={`kit-toast kit-toast--${tone}`}>
          <span className="kit-toast__glyph" aria-hidden="true">{tone === 'ok' ? '✓' : '!'}</span>
          <span className="kit-toast__text">{toast.message}</span>
        </div>
      )}
    </div>
  )
}
