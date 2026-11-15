import React, { useCallback, useEffect, useId, useRef, useState } from 'react'
import { StatusIcon } from './kit/index.js'

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
 * THE RAIL IS THE THREE JOURNEYS
 * ------------------------------
 * 1 Onboard & connect · 2 Compose & simulate · 3 Run & observe, then
 * Reference (design v26 §1; the registry note in app/destinations.jsx).
 * `active` is the rail item that OWNS the open page, so a tab inside a task —
 * Coverage inside Results — still lights up its task.
 *
 * COMPONENTS EXPANDS
 * ------------------
 * An item with `children` (Components) is two buttons: the label opens its
 * page, the chevron shows or hides the eight components under it (v26 §5).
 * The submenu opens by itself while one of its pages is open, so the current
 * component is never hidden; a DC's own toggle wins until they leave. Each
 * child carries its status as a StatusIcon — a glyph with the word as its
 * accessible name — because a coloured dot alone is the colour-only status
 * v26 §8 rules out.
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
 *   groups        — [{ num, label, items: [{ id, label, iconUrl, badge, badgeVariant,
 *                     children?: [{ key, label, destination, params, status }] }] }]
 *   active        — the rail item that owns the open page
 *   activeChild   — the child key (a component) the open page belongs to
 *   onNavigate    — (destinationId, params?) => void
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
  activeChild = null,
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
        <div className="pov-rail__group rail__group" key={group.label || group.num || 'rail'}>
          {/* The journeys are numbered (1 · 2 · 3); Reference is not a step in
              the journey, so it carries a label and no number. */}
          {group.label ? (
            <div className="pov-rail__head rail__section-title">
              {!collapsed && group.num ? <span className="pov-rail__num">{group.num}</span> : null}
              {!collapsed && <span className="pov-rail__label">{group.label}</span>}
              {!collapsed && <span className="pov-rail__hr" />}
            </div>
          ) : null}
          {group.items.map((item) => (
            item.children ? (
              <ParentItem
                key={item.id}
                item={item}
                isActive={item.id === active}
                activeChild={activeChild}
                collapsed={collapsed}
                onNavigate={onNavigate}
              />
            ) : (
              <RailButton key={item.id} item={item} isActive={item.id === active} onNavigate={onNavigate} />
            )
          ))}
        </div>
      ))}
    </nav>
  )
}

function RailButton({ item, isActive, onNavigate }) {
  return (
    <button
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
}

/**
 * A rail item with a submenu (Components). Open while one of its pages is the
 * open page; the chevron overrides that until the DC enters or leaves it.
 */
function ParentItem({ item, isActive, activeChild, collapsed, onNavigate }) {
  const listId = useId()
  const [override, setOverride] = useState(null)
  // Entering (or leaving) the item's pages hands control back to the route, so
  // navigating to a component from ⌘K or a link always shows where you are.
  useEffect(() => { setOverride(null) }, [isActive])
  const open = !collapsed && (override ?? isActive)
  const noun = item.label.toLowerCase()

  return (
    <div className={'pov-rail__parent' + (isActive ? ' pov-rail__parent--on' : '')}>
      <div className="pov-rail__parent-row">
        <RailButton item={item} isActive={isActive} onNavigate={onNavigate} />
        {!collapsed && (
          <button
            type="button"
            className="pov-rail__caret"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-label={open ? `Hide ${noun}` : `Show ${noun}`}
            title={open ? `Hide ${noun}` : `Show ${noun}`}
            onClick={() => setOverride(!open)}
          >
            <span aria-hidden="true">{open ? '▾' : '▸'}</span>
          </button>
        )}
      </div>
      {open && (
        <div id={listId} role="group" aria-label={item.label} className="pov-rail__kids">
          {item.children.map((child) => {
            const here = isActive && child.key === activeChild
            const st = child.status || { status: 'unknown' }
            return (
              <button
                key={child.key}
                type="button"
                data-testid={`dest-child-${child.key}`}
                data-tour-id={`nav-component-${child.key}`}
                className={'pov-rail__kid' + (here ? ' pov-rail__kid--on' : '')}
                aria-current={here ? 'page' : undefined}
                title={st.detail ? `${child.label} — ${st.detail}` : child.label}
                onClick={() => onNavigate(child.destination, child.params)}
              >
                <StatusIcon status={st.status} word={st.word} showWord={false} className="pov-rail__kid-status" />
                <span className="pov-rail__kid-text">{child.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
