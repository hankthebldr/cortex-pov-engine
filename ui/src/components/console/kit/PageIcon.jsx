/**
 * PageIcon — the rail's icon set, reused wherever a page, component or kind
 * is named (design v26 §4.1): page titles, component rows, Get started steps,
 * sheet headers, search results.
 *
 *   size="tile"   30px tile on --s2 with a --bd border (page headers)
 *   size="inline" 15px glyph (rows, results)
 *
 * The icons are the DS's own thin black line set under /icons; they get the
 * same neutral treatment as `.pov-rail__icon` (grayscale, inverted in dark).
 * Decorative: the label next to it carries the meaning.
 */
import React from 'react'

void React

export default function PageIcon({ icon, size = 'inline', className = '' }) {
  if (!icon) return null
  return (
    <span className={`kit-pageicon kit-pageicon--${size === 'tile' ? 'tile' : 'inline'} ${className}`.trim()} aria-hidden="true">
      <img className="kit-pageicon__img" src={`/icons/${icon}.png`} alt="" />
    </span>
  )
}
