import React from 'react'

/**
 * PinButton — small toggle that pins/unpins a scenario.
 *
 * Two visual modes:
 *   variant="card"      — appears in the corner of a scenario card; hidden
 *                         until card is hovered or scenario is pinned
 *   variant="inspector" — solid button with text used in the inspector header
 *
 * Props:
 *   pinned    — boolean
 *   onToggle  — () => void
 *   variant   — 'card' | 'inspector'
 *   disabled  — boolean
 */
export default function PinButton({
  pinned = false,
  onToggle = () => {},
  variant = 'card',
  disabled = false,
}) {
  const className = variant === 'inspector'
    ? 'btn pin-btn pin-btn--inspector' + (pinned ? ' is-pinned' : '')
    : 'pin-btn pin-btn--card' + (pinned ? ' is-pinned' : '')

  const label = pinned ? 'Pinned' : 'Pin'
  const ariaLabel = pinned ? 'Unpin scenario' : 'Pin scenario'

  // Use the same glyph for both states; color/fill conveys state.
  // ▣ filled vs ▢ outline reads cleanly in monospaced UIs.
  const glyph = pinned ? '◼' : '◻'   // ◼ vs ◻

  return (
    <button
      type="button"
      className={className}
      onClick={(e) => { e.stopPropagation(); onToggle() }}
      disabled={disabled}
      aria-label={ariaLabel}
      title={ariaLabel}
    >
      <span className="pin-btn__glyph" aria-hidden="true">{glyph}</span>
      {variant === 'inspector' && <span>{label}</span>}
    </button>
  )
}
