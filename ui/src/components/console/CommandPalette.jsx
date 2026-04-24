import React, { useState, useMemo, useEffect, useRef } from 'react'

/**
 * CommandPalette — ⌘K overlay.
 *
 * Props:
 *   open       — boolean
 *   onClose    — () => void
 *   items      — array of:
 *                { section: 'Scenarios'|'Actions'|...,
 *                  id: string,
 *                  title: string,
 *                  meta: string,
 *                  icon: string,                 // single glyph
 *                  shortcut: [string, string],   // optional keyboard combo shown right-aligned
 *                  onSelect: () => void }
 *   placeholder — string
 */
export default function CommandPalette({
  open,
  onClose = () => {},
  items = [],
  placeholder = "Search scenarios, TIDs, actors \u00b7 try 'launch SIM-' to run",
}) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setActiveIndex(0)
      setTimeout(() => inputRef.current && inputRef.current.focus(), 20)
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

  // Group by section, preserving insertion order.
  const grouped = useMemo(() => {
    const m = new Map()
    filtered.forEach((it) => {
      const k = it.section || 'Results'
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(it)
    })
    return Array.from(m.entries())
  }, [filtered])

  // Keyboard nav
  useEffect(() => {
    if (!open) return
    const flat = filtered
    const handler = (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIndex((i) => Math.min(flat.length - 1, i + 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIndex((i) => Math.max(0, i - 1))
      } else if (e.key === 'Enter') {
        const sel = flat[activeIndex]
        if (sel && sel.onSelect) {
          sel.onSelect()
          onClose()
        }
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, filtered, activeIndex, onClose])

  const onBackdropClick = (e) => {
    if (e.target === e.currentTarget) onClose()
  }

  let runningIndex = -1

  return (
    <div
      className={'cmd-palette-backdrop' + (open ? ' is-open' : '')}
      onClick={onBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div className="cmd-palette">
        <div className="cmd-palette__input">
          <span className="cmd-palette__prompt">▸</span>
          <input
            ref={inputRef}
            type="text"
            placeholder={placeholder}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActiveIndex(0) }}
          />
          <span className="kbd">esc</span>
        </div>

        <div className="cmd-palette__results">
          {grouped.length === 0 && (
            <div className="cmd-section-label" style={{ paddingBottom: 24 }}>
              no matches — try a different scenario id or TID
            </div>
          )}
          {grouped.map(([section, list]) => (
            <div key={section}>
              <div className="cmd-section-label">{section}</div>
              {list.map((it) => {
                runningIndex += 1
                const isActive = runningIndex === activeIndex
                return (
                  <button
                    key={it.id || it.title}
                    type="button"
                    className={'cmd-result' + (isActive ? ' cmd-result--active' : '')}
                    onMouseEnter={() => setActiveIndex(runningIndex)}
                    onClick={() => {
                      if (it.onSelect) it.onSelect()
                      onClose()
                    }}
                  >
                    <span className="cmd-result__icon">{it.icon || '▸'}</span>
                    <span className="cmd-result__body">
                      <span className="cmd-result__title">{it.title}</span>
                      {it.meta && <span className="cmd-result__meta">{it.meta}</span>}
                    </span>
                    <span className="cmd-result__shortcut">
                      {(it.shortcut || []).map((k) => (
                        <span key={k} className="kbd">{k}</span>
                      ))}
                    </span>
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        <div className="cmd-palette__footer">
          <span>↑↓ navigate · ↵ select · esc close</span>
          <span>{filtered.length} result{filtered.length === 1 ? '' : 's'}</span>
        </div>
      </div>
    </div>
  )
}
