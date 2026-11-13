/**
 * StatusIcon — a state as a shape AND a word (design v26 §4.1).
 *
 * Green now means both "primary action" and "healthy" (v26 §9.1), so a state
 * is never colour alone: every status carries a glyph and one word. The glyph
 * sits in a soft circle and is exposed to assistive tech as role="img" with
 * the word as its name; the visible word beside it is hidden from that tree
 * so a screen reader hears the state once.
 *
 *   up        ✓  Up        (healthy, done)
 *   degraded  !  Degraded  (works, with a problem)
 *   down      ✕  Down      (not working)
 *   off       –  Off       (never configured)
 *   unknown   ?  Unknown   (POVengine cannot see it) — never green
 *
 * `word` overrides the label for the same tone (configuration rows say
 * OK · Missing · Changed · Can't check). An unrecognised status renders as
 * Unknown rather than borrowing a colour it has not earned.
 */
import React from 'react'

void React

export const STATUS = {
  up:       { word: 'Up',       glyph: '✓', tone: 'up' },
  degraded: { word: 'Degraded', glyph: '!', tone: 'degraded' },
  down:     { word: 'Down',     glyph: '✕', tone: 'down' },
  off:      { word: 'Off',      glyph: '–', tone: 'off' },
  unknown:  { word: 'Unknown',  glyph: '?', tone: 'unknown' },
}

export default function StatusIcon({ status = 'unknown', word, size = 'row', showWord = true, className = '' }) {
  const meta = STATUS[status] || STATUS.unknown
  const label = word || meta.word
  return (
    <span className={`kit-status kit-status--${meta.tone} kit-status--${size === 'list' ? 'list' : 'row'} ${className}`.trim()}>
      <span className="kit-status__icon" role="img" aria-label={label}>{meta.glyph}</span>
      {showWord && <span className="kit-status__word" aria-hidden="true">{label}</span>}
    </span>
  )
}
