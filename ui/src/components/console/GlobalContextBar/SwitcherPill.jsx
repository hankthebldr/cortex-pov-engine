import React, { useEffect, useRef, useState } from 'react'

/**
 * SwitcherPill — a recessive dropdown pill for the global context bar.
 *
 * Shared shell for the Tenant + Agent switchers: an always-visible pill
 * (status dot · label) that opens a menu of options plus a "Manage…" footer
 * that deep-links to the management destination. Reuses the `.env-pill`
 * visual language; menu/option styling lives under `.switcher*` in
 * `ui/src/styles/cortex-console.css`.
 *
 * Props:
 *   kind        — 'tenant' | 'agent' (for aria labelling)
 *   label       — current selection label (e.g. tenant name)
 *   status      — 'healthy' | 'warn' | 'bad' | null  (dot colour)
 *   empty       — boolean; render the muted "none selected" state
 *   options     — [{ id, label, meta, status }]
 *   activeId    — currently-selected option id
 *   onSelect    — (id) => void
 *   manageLabel — footer action label
 *   onManage    — () => void
 */
export default function SwitcherPill({
  kind = 'tenant',
  label,
  status = null,
  empty = false,
  options = [],
  activeId = null,
  onSelect = () => {},
  manageLabel = 'Manage…',
  onManage = () => {},
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    const onDoc = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const dotClass =
    'env-pill__dot' +
    (status === 'warn' ? ' env-pill__dot--warn' : '') +
    (status === 'bad' ? ' env-pill__dot--bad' : '')

  return (
    <div className="switcher" ref={rootRef}>
      <button
        type="button"
        className={'env-pill switcher__pill' + (empty ? ' switcher__pill--empty' : '')}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${kind === 'tenant' ? 'Tenant' : 'Agent'}: ${empty ? 'none selected' : label}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={dotClass} />
        <span className="env-pill__meta switcher__meta">
          {kind}
        </span>
        <span className="env-pill__label">{empty ? 'none' : label}</span>
        <span aria-hidden="true" className="switcher__caret">▾</span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-label={`${kind} options`}
          className="switcher__menu"
        >
          {options.length === 0 && (
            <div className="switcher__empty-msg">
              none registered
            </div>
          )}
          {options.map((opt) => {
            const isActive = opt.id === activeId
            const oDot =
              'env-pill__dot' +
              (opt.status === 'warn' ? ' env-pill__dot--warn' : '') +
              (opt.status === 'bad' ? ' env-pill__dot--bad' : '')
            return (
              <button
                key={opt.id}
                type="button"
                role="option"
                aria-selected={isActive}
                onClick={() => { onSelect(opt.id); setOpen(false) }}
                className={'switcher__option' + (isActive ? ' switcher__option--active' : '')}
              >
                <span className={oDot + ' switcher__option-dot'} />
                <span className="switcher__option-info">
                  <span className={'switcher__option-label' + (isActive ? ' switcher__option-label--active' : '')}>
                    {opt.label}
                  </span>
                  {opt.meta && (
                    <span className="switcher__option-meta">
                      {opt.meta}
                    </span>
                  )}
                </span>
                {isActive && (
                  <span aria-hidden="true" className="switcher__option-check">✓</span>
                )}
              </button>
            )
          })}
          <div className="switcher__footer">
            <button
              type="button"
              onClick={() => { onManage(); setOpen(false) }}
              className="switcher__manage"
            >
              {manageLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
