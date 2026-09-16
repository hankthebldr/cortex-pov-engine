import React, { useCallback, useEffect, useRef, useState } from 'react'

/**
 * DestinationNav — the persistent, grouped destination sidebar.
 *
 * Replaces ConsoleStepper as the console's PRIMARY navigation. Every
 * first-class destination is one click away at any time (no More▾ overflow,
 * no linear stepper). Reuses the existing rail tokens/classes
 * (`--w-rail` / `--w-rail-collapsed`, `.rail`, `.rail__group`,
 * `.rail__section-title`, `.plane-item*`) so it inherits the Cortex design
 * system without inventing new visuals.
 *
 * OVERFLOW IS ANNOUNCED, NOT LEFT TO THE SCROLLBAR
 * ------------------------------------------------
 * The shell stacks four fixed bands above the workspace (safety gate, header,
 * phase bar, readiness line) — 353px on first load — so on a 1280x720 laptop
 * this rail gets 337px of scrollport for 663px of destinations. Chromium
 * paints an OVERLAY scrollbar there (measured: `offsetWidth - clientWidth`
 * = 1px), so the six destinations below the fold — "Tools & Payloads" and
 * "UC / TC Index" among them at the shortest viewports — did not read as
 * scrolled-off. They read as ABSENT, which is how a DC concludes the console
 * does not have a payload surface.
 *
 * So the rail reports its own truncation: `data-overflow` is one of
 * `none | top | bottom | both`, recomputed on scroll, on resize, and whenever
 * the group set or collapse state changes. The stylesheet turns that into a
 * visible edge fade, and it fails toward `both` — an unmeasurable rail shows
 * the cue rather than hiding it, because a false "there is more" costs a
 * glance and a false "that is everything" costs a destination.
 *
 * THE RAIL IS THE PHASE MODEL
 * ---------------------------
 * Groups are the POV phases, in run order, and each carries its phase numeral
 * in the accent color. There used to be a separate phase bar above the
 * workspace answering the same question in a different vocabulary; two
 * wayfinding systems that could disagree about the same fourteen destinations
 * was the redesign's original complaint. The bar is gone and this is the
 * single answer, with the flow bar at the foot of the shell naming the next
 * action (see FlowBar.jsx).
 *
 * ICONS ARE REAL ASSETS, NOT GLYPHS
 * ---------------------------------
 * Every item carries one of the design system's own thin-stroke line icons as
 * a CSS background image, inverted for the dark rail, full opacity when active
 * and 50% at rest. They are backgrounds rather than <img> so nothing requests
 * an unresolved path during first paint. The DS forbids Unicode-glyph icons in
 * brand material and the previous rail was built entirely from them
 * (▤ ⌗ ⚙ ≣ ✓ ◈ ∿ ≋ ▦ ◆). `iconUrl` in the registry returns null rather than an
 * interpolated undefined — an earlier revision emitted `url("icons/undefined")`
 * and fired a 404 per render.
 *
 * Props:
 *   groups        — [{ label, num, items: [{ id, label, iconUrl, badge, badgeVariant }] }]
 *   active        — current destination id
 *   onNavigate    — (destinationId) => void
 *   collapsed     — boolean (rail collapse persisted by the shell)
 *   onToggleCollapse — () => void
 */

/** Pure so the edge logic is testable without layout: jsdom reports every
 *  scroll metric as 0, which would otherwise assert `none` forever. */
export function overflowEdges({ scrollTop, scrollHeight, clientHeight }, slack = 2) {
  if (!Number.isFinite(scrollTop) || !Number.isFinite(scrollHeight) || !Number.isFinite(clientHeight)) {
    return 'both'
  }
  if (scrollHeight <= clientHeight + slack) return 'none'
  const atTop = scrollTop <= slack
  const atBottom = scrollTop + clientHeight >= scrollHeight - slack
  if (atTop && atBottom) return 'none'
  if (atTop) return 'bottom'
  if (atBottom) return 'top'
  return 'both'
}
export default function DestinationNav({
  groups = [],
  active = null,
  onNavigate = () => {},
  collapsed = false,
  onToggleCollapse = null,
}) {
  const navRef = useRef(null)
  const [overflow, setOverflow] = useState('none')

  const measure = useCallback(() => {
    const el = navRef.current
    if (!el) return
    setOverflow(overflowEdges({
      scrollTop: el.scrollTop,
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    }))
  }, [])

  useEffect(() => {
    measure()
    const el = navRef.current
    if (!el) return undefined
    let ro = null
    try {
      ro = new ResizeObserver(measure)
      ro.observe(el)
    } catch { /* no ResizeObserver — the resize listener below still fires */ }
    window.addEventListener('resize', measure)
    return () => {
      if (ro) ro.disconnect()
      window.removeEventListener('resize', measure)
    }
    // `groups`/`collapsed` change the content height, so re-measure on both.
  }, [measure, groups, collapsed])

  return (
    <nav
      ref={navRef}
      onScroll={measure}
      data-overflow={overflow}
      className={'pov-rail rail rail--nav' + (collapsed ? ' rail--collapsed' : '')}
      aria-label="Console destinations"
    >
      {onToggleCollapse && (
        <button
          type="button"
          className="rail__toggle"
          onClick={onToggleCollapse}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          {collapsed ? '▶' : '◀'}
        </button>
      )}

      {groups.map((group) => (
        <div className="pov-rail__group rail__group" key={group.label}>
          <div className="pov-rail__head rail__section-title">
            {/* '' for "Start here", which sits before the run order rather
                than inside it, and for any future group without a phase. */}
            {group.num ? <span className="pov-rail__num">{group.num}</span> : null}
            {!collapsed && <span className="pov-rail__label">{group.label}</span>}
            {!collapsed && <span className="pov-rail__hr" />}
          </div>
          {group.items.map((item) => {
            const isActive = item.id === active
            return (
              <button
                key={item.id}
                type="button"
                data-testid={`dest-button-${item.id}`}
                data-tour-id={`nav-${item.id}`}
                className={'pov-rail__item plane-item' + (isActive ? ' pov-rail__item--on plane-item--active' : '')}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => onNavigate(item.id)}
                title={item.label}
              >
                <span
                  className="pov-rail__icon"
                  aria-hidden="true"
                  style={{ backgroundImage: item.iconUrl ? `url("${item.iconUrl}")` : 'none' }}
                />
                <span className="pov-rail__text plane-item__name">{item.label}</span>
                {item.badge != null && item.badge !== '' && (
                  <span
                    className={
                      'pov-rail__badge plane-item__count'
                      + (item.badgeVariant === 'live' ? ' pov-rail__badge--live plane-item__count--live' : '')
                    }
                  >
                    {item.badge}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
