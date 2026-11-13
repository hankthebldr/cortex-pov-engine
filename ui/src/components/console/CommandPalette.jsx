import React, { useState, useMemo, useEffect, useRef } from 'react'
import { PageIcon } from './kit/index.js'
import { trapTabKey } from './focusTrap.js'

void React

// With an empty query the list would carry every scenario and every TTP card.
// The first page of results is what fits; typing narrows the rest.
const MAX_SHOWN = 40

/**
 * CommandPalette — ⌘K search (design v26 §4.5, §8).
 *
 * A dialog named "Search" with a labelled search box. It keeps Tab inside
 * itself, closes on Escape, and hands focus back to whatever opened it. It
 * renders nothing while closed, so a closed palette is not an invisible dialog
 * sitting in the accessibility tree.
 *
 * Each result shows the page's icon, a label and its kind (Page · Component ·
 * Scenario · TTP card · Action …), in one flat list: the kind on the row says
 * what a section header used to.
 *
 * Props:
 *   open       — boolean
 *   onClose    — () => void
 *   items      — array of:
 *                { id: string,
 *                  title: string,
 *                  meta: string,                 // optional, searchable, shown muted
 *                  kind: string,                 // what the result is; falls back to `section`
 *                  icon: string,                 // a rail icon name (/icons/<name>.png)
 *                  shortcut: [string, string],   // optional keyboard combo
 *                  onSelect: () => void }
 *   placeholder — string
 */
export default function CommandPalette({
  open,
  onClose = () => {},
  items = [],
  placeholder = 'Go to a page, component, scenario or TTP card…',
}) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef(null)
  const panelRef = useRef(null)
  const openerRef = useRef(null)

  // Remember what had focus when the palette opened and give it back on close.
  useEffect(() => {
    if (!open) return undefined
    openerRef.current = typeof document !== 'undefined' ? document.activeElement : null
    setQuery('')
    setActiveIndex(0)
    const t = setTimeout(() => inputRef.current && inputRef.current.focus(), 20)
    return () => {
      clearTimeout(t)
      const opener = openerRef.current
      if (opener && typeof opener.focus === 'function' && opener.isConnected) opener.focus()
    }
  }, [open])

  const filtered = useMemo(() => {
    if (!query) return items
    const q = query.toLowerCase()
    return items.filter((it) =>
      (it.title && it.title.toLowerCase().includes(q)) ||
      (it.meta && it.meta.toLowerCase().includes(q)) ||
      (it.id && it.id.toLowerCase().includes(q))
    )
  }, [items, query])

  const shown = useMemo(() => filtered.slice(0, MAX_SHOWN), [filtered])

  // Keyboard: arrows move, Enter runs, Escape closes, Tab stays inside.
  useEffect(() => {
    if (!open) return undefined
    const handler = (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIndex((i) => Math.min(shown.length - 1, i + 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIndex((i) => Math.max(0, i - 1))
      } else if (e.key === 'Enter') {
        const sel = shown[activeIndex]
        if (sel && sel.onSelect) {
          e.preventDefault()
          sel.onSelect()
          onClose()
        }
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'Tab') {
        trapTabKey(e, panelRef.current)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, shown, activeIndex, onClose])

  if (!open) return null

  const onBackdropClick = (e) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div className="cmd-palette-backdrop is-open" onClick={onBackdropClick}>
      <div
        ref={panelRef}
        className="cmd-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Search"
      >
        <div className="cmd-palette__input">
          <PageIcon icon="doc-search" />
          <input
            ref={inputRef}
            type="search"
            aria-label="Search pages, components, scenarios and TTP cards"
            placeholder={placeholder}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActiveIndex(0) }}
          />
          <span className="kbd" aria-hidden="true">esc</span>
        </div>

        <div className="cmd-palette__results">
          {shown.length === 0 && (
            <div className="cmd-section-label cmd-palette__empty">
              Nothing matches — try a page name, a scenario id or a TTP card id.
            </div>
          )}
          {shown.map((it, rowIndex) => {
            const isActive = rowIndex === activeIndex
            const kind = it.kind || it.section || null
            return (
              <button
                key={it.id || it.title}
                type="button"
                className={'cmd-result' + (isActive ? ' cmd-result--active' : '')}
                onMouseEnter={() => setActiveIndex(rowIndex)}
                onFocus={() => setActiveIndex(rowIndex)}
                onClick={() => {
                  if (it.onSelect) it.onSelect()
                  onClose()
                }}
              >
                <span className="cmd-result__icon"><PageIcon icon={it.icon} /></span>
                <span className="cmd-result__body">
                  <span className="cmd-result__title">{it.title}</span>
                  {it.meta && <span className="cmd-result__meta">{it.meta}</span>}
                </span>
                {kind && <span className="cmd-result__kind">{kind}</span>}
                {(it.shortcut || []).length > 0 && (
                  <span className="cmd-result__shortcut">
                    {it.shortcut.map((k) => (
                      <span key={k} className="kbd">{k}</span>
                    ))}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        <div className="cmd-palette__footer">
          <span>↑↓ move · ↵ open · esc close</span>
          <span>
            {filtered.length > shown.length
              ? `${shown.length} of ${filtered.length} — keep typing to narrow`
              : `${filtered.length} result${filtered.length === 1 ? '' : 's'}`}
          </span>
        </div>
      </div>
    </div>
  )
}
