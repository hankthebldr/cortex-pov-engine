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
 *   check    Test (the verb), and "What proves it"
 *   gear     Configure (the verb), and "What it needs"
 *   pencil   Customize (the verb)
 *   rotate   Review (the verb)
 *   external Open results (the verb)
 *   target   a deep dive's thesis ("Why this matters")
 *   back     the header's Back: the previous place in the console's history
 *   forward  the header's Forward: the next place, after a Back
 *   trash    Delete (the verb)
 *   download Download (the verb)
 *   refresh  Refresh (the verb)
 *   upload   Deploy (the verb)
 *   copy     Copy (the verb)
 *   select   Select (the verb): pick rows for a bulk action
 *   swap     Switch (the verb): make another agent the active one
 *   pause    Pause (the verb): hold a live stream
 *   clear    Clear (the verb): empty a buffer this browser holds
 *   camera   Screenshot (the verb)
 *   down     jump to the newest line of a log
 *   x        close a sheet; mark a detection missed
 *   check-all  Validate all (the verb)
 *
 * `target` also draws Set active (the verb).
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
  check: <path d="M3 8.5l3.2 3.2L13 4.8" {...STROKE} />,
  gear: (
    <g {...STROKE}>
      <circle cx="8" cy="8" r="2.4" />
      <path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6L5 5M11 11l1.4 1.4M3.6 12.4L5 11M11 5l1.4-1.4" />
    </g>
  ),
  pencil: (
    <g {...STROKE}>
      <path d="M10.6 2.6l2.8 2.8L6 12.8H3.2V10z" />
      <path d="M9 4.2L11.8 7" />
    </g>
  ),
  rotate: (
    <g {...STROKE}>
      <path d="M3.4 8a4.6 4.6 0 1 0 1.5-3.4" />
      <path d="M3.2 2.4v2.8H6" />
    </g>
  ),
  external: (
    <g {...STROKE}>
      <path d="M9.2 2.8h4v4" />
      <path d="M13.2 2.8L7.6 8.4" />
      <path d="M11.4 9.4v3.8H2.8V4.6h3.8" />
    </g>
  ),
  target: (
    <g {...STROKE}>
      <circle cx="8" cy="8" r="5.6" />
      <circle cx="8" cy="8" r="2.2" />
    </g>
  ),
  back: <path d="M13 8H3M7 4L3 8l4 4" {...STROKE} />,
  forward: <path d="M3 8h10M9 4l4 4-4 4" {...STROKE} />,
  trash: (
    <g {...STROKE}>
      <path d="M2.8 4.2h10.4M6.2 4.2V2.6h3.6v1.6" />
      <path d="M4 4.2l.7 9.2h6.6l.7-9.2" />
      <path d="M6.6 6.8v4.4M9.4 6.8v4.4" />
    </g>
  ),
  download: (
    <g {...STROKE}>
      <path d="M8 2.4v7.4M4.8 6.8L8 10l3.2-3.2" />
      <path d="M2.8 12.6h10.4" />
    </g>
  ),
  refresh: (
    <g {...STROKE}>
      <path d="M12.8 6.4A5 5 0 0 0 3.6 5.2" />
      <path d="M3.2 9.6a5 5 0 0 0 9.2 1.2" />
      <path d="M3.4 2.6v2.8h2.8M12.6 13.4v-2.8H9.8" />
    </g>
  ),
  upload: (
    <g {...STROKE}>
      <path d="M8 10.4V3M4.8 6.2L8 3l3.2 3.2" />
      <path d="M2.8 12.6h10.4" />
    </g>
  ),
  copy: (
    <g {...STROKE}>
      <rect x="5.4" y="5.4" width="7.8" height="7.8" rx="1.2" />
      <path d="M10.6 5.4V3.8a1 1 0 0 0-1-1H3.8a1 1 0 0 0-1 1v5.8a1 1 0 0 0 1 1h1.6" />
    </g>
  ),
  select: (
    <g {...STROKE}>
      <rect x="2.6" y="2.6" width="10.8" height="10.8" rx="2" />
      <path d="M5.2 8.2l2 2 3.6-4" />
    </g>
  ),
  swap: (
    <g {...STROKE}>
      <path d="M2.5 5.5h10M10 3l2.5 2.5L10 8" />
      <path d="M13.5 10.5h-10M6 8l-2.5 2.5L6 13" />
    </g>
  ),
  pause: (
    <g fill="currentColor">
      <rect x="4" y="3.5" width="2.6" height="9" rx="0.6" />
      <rect x="9.4" y="3.5" width="2.6" height="9" rx="0.6" />
    </g>
  ),
  clear: (
    <g {...STROKE}>
      <circle cx="8" cy="8" r="5.4" />
      <path d="M4.2 11.8l7.6-7.6" />
    </g>
  ),
  camera: (
    <g {...STROKE}>
      <path d="M2.4 5.4h2.4l1.2-1.8h4l1.2 1.8h2.4v7H2.4z" />
      <circle cx="8" cy="8.8" r="2.2" />
    </g>
  ),
  down: <path d="M8 3v10M4 9l4 4 4-4" {...STROKE} />,
  x: <path d="M4 4l8 8M12 4l-8 8" {...STROKE} />,
  'check-all': <path d="M1.8 8.6l2.6 2.6 5-5.6M7.4 10.4l.8.8 6-6.8" {...STROKE} />,
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
