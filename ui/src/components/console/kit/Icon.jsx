/**
 * Icon — the console's control glyphs, drawn inline so they take the text
 * colour and scale with it. A closed set: an unknown name renders nothing.
 *
 * Decorative by default (`aria-hidden`): the control that carries an icon
 * names itself (visible text, or `aria-label` on an icon-only button).
 *
 *   play     start a run / the chain's Start point
 *   stop     stop a run / the chain's Stop point
 *   repeat   run again
 *   plus     add to the chain
 *   sliders  the inspector (a step's settings)
 *   star     one of the system's best scenarios (Favorites)
 */
import React from 'react'

void React

const STROKE = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' }

const SHAPES = {
  play: <polygon points="5,3 13,8 5,13" fill="currentColor" />,
  stop: <rect x="4" y="4" width="8" height="8" rx="1.2" fill="currentColor" />,
  repeat: (
    <g {...STROKE}>
      <path d="M3 7V6a2 2 0 0 1 2-2h7" />
      <path d="M10 2l2 2-2 2" />
      <path d="M13 9v1a2 2 0 0 1-2 2H4" />
      <path d="M6 14l-2-2 2-2" />
    </g>
  ),
  plus: <path d="M8 3v10M3 8h10" {...STROKE} />,
  star: <polygon points="8,1.6 9.9,5.8 14.5,6.2 11,9.3 12.1,13.8 8,11.4 3.9,13.8 5,9.3 1.5,6.2 6.1,5.8" fill="currentColor" />,
  sliders: (
    <g {...STROKE}>
      <path d="M2.5 5h5M12 5h1.5M2.5 11h1.5M8.5 11h5" />
      <circle cx="9.75" cy="5" r="1.75" />
      <circle cx="6.25" cy="11" r="1.75" />
    </g>
  ),
}

export const ICONS = Object.freeze(Object.keys(SHAPES))

export default function Icon({ name, size = 14, className = '' }) {
  const shape = SHAPES[name]
  if (!shape) return null
  return (
    <svg
      className={`kit-icon ${className}`.trim()}
      data-icon={name}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      {shape}
    </svg>
  )
}
