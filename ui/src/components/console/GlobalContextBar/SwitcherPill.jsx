import React, { useEffect, useRef, useState } from 'react'

/**
 * SwitcherPill — a recessive dropdown pill for the global context bar.
 *
 * Shared shell for the Tenant + Agent switchers: an always-visible pill
 * (status dot · label) that opens a menu of options plus a "Manage…" footer
 * that deep-links to the management destination. Reuses the `.env-pill`
 * visual language; the menu is token-styled inline so no new CSS class is
 * introduced.
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
    <div className="switcher" ref={rootRef} style={{ position: 'relative' }}>
      <button
        type="button"
        className={'env-pill switcher__pill' + (empty ? ' switcher__pill--empty' : '')}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${kind === 'tenant' ? 'Tenant' : 'Agent'}: ${empty ? 'none selected' : label}`}
        onClick={() => setOpen((v) => !v)}
        style={empty ? { opacity: 0.65 } : undefined}
      >
        <span className={dotClass} />
        <span className="env-pill__meta" style={{ textTransform: 'uppercase', fontSize: 9, letterSpacing: '0.08em' }}>
          {kind}
        </span>
        <span className="env-pill__label">{empty ? 'none' : label}</span>
        <span aria-hidden="true" style={{ color: 'var(--c-text-muted)', fontSize: 9 }}>▾</span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-label={`${kind} options`}
          className="switcher__menu"
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 210,
            minWidth: 240, maxHeight: 340, overflowY: 'auto',
            background: 'var(--c-surface-modal, #0b1220)',
            border: '1px solid var(--c-hairline-strong, rgba(255,255,255,0.14))',
            borderRadius: 4, padding: 4,
            boxShadow: '0 12px 32px rgba(0,0,0,0.45)',
          }}
        >
          {options.length === 0 && (
            <div style={{
              padding: '10px 12px', color: 'var(--c-text-muted)',
              fontFamily: 'var(--font-mono)', fontSize: 11,
            }}>
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
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, width: '100%',
                  padding: '8px 10px', border: 'none', textAlign: 'left',
                  background: isActive ? 'var(--c-surface-raised, rgba(255,255,255,0.06))' : 'transparent',
                  color: 'var(--c-text)', cursor: 'pointer', borderRadius: 3,
                }}
              >
                <span className={oDot} style={{ flexShrink: 0 }} />
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <span style={{ fontSize: 12, fontWeight: isActive ? 600 : 400, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {opt.label}
                  </span>
                  {opt.meta && (
                    <span style={{ fontSize: 10, color: 'var(--c-text-muted)', fontFamily: 'var(--font-mono)' }}>
                      {opt.meta}
                    </span>
                  )}
                </span>
                {isActive && (
                  <span aria-hidden="true" style={{ marginLeft: 'auto', color: 'var(--cortex-teal, #00C0E8)', fontSize: 11 }}>✓</span>
                )}
              </button>
            )
          })}
          <div style={{ borderTop: '1px solid var(--c-hairline, rgba(255,255,255,0.08))', marginTop: 4, paddingTop: 4 }}>
            <button
              type="button"
              onClick={() => { onManage(); setOpen(false) }}
              style={{
                display: 'block', width: '100%', padding: '8px 10px', border: 'none',
                background: 'transparent', color: 'var(--cortex-teal, #00C0E8)',
                cursor: 'pointer', textAlign: 'left', fontSize: 11,
                fontFamily: 'var(--font-mono)', letterSpacing: '0.03em',
              }}
            >
              {manageLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
