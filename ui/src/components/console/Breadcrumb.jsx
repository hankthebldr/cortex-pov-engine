import React from 'react'
import { hashFor } from '../../app/useConsoleRouter.js'

void React

/**
 * Breadcrumb — journey › page › object, in the header (design C9).
 *
 * The crumbs come from `app/breadcrumb.js::breadcrumbFor`. Every crumb but
 * the last is a link: it carries a real `href` (so it can open in a new tab)
 * and a plain click goes through the router like any other navigation. The
 * last crumb is where the DC is (`aria-current="page"`), drawn in the
 * selected state, and is not a link.
 *
 * Props:
 *   crumbs      — [{ label, title?, destination?, params? }]
 *   onNavigate  — (destinationId, params?) => void
 */
export default function Breadcrumb({ crumbs = [], onNavigate = () => {} }) {
  if (!crumbs.length) return null
  const last = crumbs.length - 1
  return (
    <nav className="pov-crumbs" aria-label="Breadcrumb" data-testid="header-breadcrumb">
      <ol className="pov-crumbs__list">
        {crumbs.map((c, i) => (
          <li className="pov-crumbs__item" key={`${i}:${c.label}`}>
            {i > 0 && <span className="pov-crumbs__sep" aria-hidden="true">›</span>}
            {i === last || !c.destination ? (
              <span className="pov-crumbs__current" aria-current={i === last ? 'page' : undefined} title={c.title || c.label}>
                {c.label}
              </span>
            ) : (
              <a
                className="pov-crumbs__link"
                href={hashFor(c.destination, c.params)}
                title={c.label}
                onClick={(e) => {
                  // A modified click opens the link the browser's way.
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
                  e.preventDefault()
                  onNavigate(c.destination, c.params || null)
                }}
              >
                {c.label}
              </a>
            )}
          </li>
        ))}
      </ol>
    </nav>
  )
}
