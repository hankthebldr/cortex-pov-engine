import React, { useState, useRef, useEffect } from 'react'

/**
 * ConsoleStepper — guided POV-workflow navigation (redesign v2).
 *
 * Replaces the flat tab bar with a numbered stepper that mirrors a Domain
 * Consultant's journey: Targets → Library → Launch → Live → Evidence.
 * Secondary surfaces (Coverage, Environments, TTP authoring, …) live behind
 * a `More ▾` menu so the primary path stays legible.
 *
 * See docs/design/console-redesign-v2.md.
 *
 * Props:
 *   steps        — [{ id, label, badge? }] primary numbered steps (in order)
 *   moreItems    — [{ id, label }] secondary views under the More menu
 *   activeTab    — current view id (may be a step or a More item)
 *   onTabChange  — (id) => void
 */
export default function ConsoleStepper({
  steps = [],
  moreItems = [],
  activeTab,
  onTabChange = () => {},
}) {
  const [moreOpen, setMoreOpen] = useState(false)
  const moreRef = useRef(null)

  useEffect(() => {
    if (!moreOpen) return undefined
    const onDoc = (e) => { if (moreRef.current && !moreRef.current.contains(e.target)) setMoreOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [moreOpen])

  const activeIdx = steps.findIndex((s) => s.id === activeTab)
  const moreActive = moreItems.some((m) => m.id === activeTab)

  return (
    <nav className="stepper" role="tablist" aria-label="POV workflow">
      <ol className="stepper__track">
        {steps.map((s, i) => {
          const isActive = s.id === activeTab
          const isDone = activeIdx > -1 && i < activeIdx
          const badge = s.badge
          const badgeText = typeof badge === 'object' && badge ? badge.text : badge
          const badgeVariant = typeof badge === 'object' && badge ? badge.variant : null
          return (
            <li key={s.id} className="stepper__item">
              <button
                type="button"
                role="tab"
                aria-selected={isActive}
                className={
                  'step' +
                  (isActive ? ' step--active' : '') +
                  (isDone ? ' step--done' : '')
                }
                onClick={() => onTabChange(s.id)}
              >
                <span className="step__num">{i + 1}</span>
                <span className="step__label">{s.label}</span>
                {badgeText != null && badgeText !== '' && (
                  <span className={'step__badge' + (badgeVariant === 'live' ? ' step__badge--live' : '')}>
                    {badgeText}
                  </span>
                )}
              </button>
              {i < steps.length - 1 && <span className="stepper__sep" aria-hidden="true">›</span>}
            </li>
          )
        })}
      </ol>

      {moreItems.length > 0 && (
        <div className="stepper__more" ref={moreRef}>
          <button
            type="button"
            className={'step step--more' + (moreActive ? ' step--active' : '')}
            aria-haspopup="menu"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((v) => !v)}
          >
            <span className="step__label">
              {moreActive ? (moreItems.find((m) => m.id === activeTab)?.label || 'More') : 'More'}
            </span>
            <span className="step__caret" aria-hidden="true">▾</span>
          </button>
          {moreOpen && (
            <div className="stepper__menu" role="menu">
              {moreItems.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  role="menuitem"
                  className={'stepper__menu-item' + (m.id === activeTab ? ' is-active' : '')}
                  onClick={() => { onTabChange(m.id); setMoreOpen(false) }}
                >
                  {m.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </nav>
  )
}
