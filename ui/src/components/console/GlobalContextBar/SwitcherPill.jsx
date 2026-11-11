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
 * STATUS IS SAID THREE WAYS, NEVER BY COLOUR ALONE. The dot used to be an
 * empty span whose only difference was its hue, under an aria-label that named
 * the selection and not its state — so a colour-blind DC and a screen-reader
 * user both read "WIN22" whether the beacon was polling or gone. Now:
 *   - the accessible name carries the state word (`Agent: WIN22 — offline`);
 *   - a state that is not healthy shows that word beside the dot;
 *   - the dot's SHAPE differs per state (disc / ring / square — see the CSS),
 *     and it is aria-hidden, because the words are the signal.
 * A `null` status is a neutral dot, not the healthy green one: "nothing was
 * reported" must not look like "ok".
 *
 * Props:
 *   kind        — 'tenant' | 'agent' (for aria labelling)
 *   label       — current selection label (e.g. tenant name)
 *   status      — 'healthy' | 'warn' | 'bad' | null  (dot colour + shape)
 *   statusLabel — the state in words ('online', 'offline', 'degraded', …)
 *   empty       — boolean; render the muted "none selected" state
 *   options     — [{ id, label, meta, status, statusLabel }]
 *   activeId    — currently-selected option id
 *   onSelect    — (id) => void
 *   manageLabel — footer action label
 *   onManage    — () => void
 */

const KNOWN = new Set(['healthy', 'warn', 'bad'])

function dotClassFor(status) {
  return 'env-pill__dot'
    + (status === 'warn' ? ' env-pill__dot--warn' : '')
    + (status === 'bad' ? ' env-pill__dot--bad' : '')
    + (!KNOWN.has(status) ? ' env-pill__dot--none' : '')
}

function StatusDot({ status, className = '' }) {
  return (
    <span
      className={dotClassFor(status) + (className ? ` ${className}` : '')}
      data-status={KNOWN.has(status) ? status : 'none'}
      aria-hidden="true"
    />
  )
}

export default function SwitcherPill({
  kind = 'tenant',
  label,
  status = null,
  statusLabel = null,
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

  const kindWord = kind === 'tenant' ? 'Tenant' : 'Agent'
  const name = empty
    ? `${kindWord}: none selected`
    : `${kindWord}: ${label}${statusLabel ? ` — ${statusLabel}` : ''}`
  // Healthy stays quiet on screen; anything else shows its word beside the dot.
  const showWord = !empty && statusLabel && (status === 'warn' || status === 'bad')

  return (
    <div className="switcher" ref={rootRef}>
      <button
        type="button"
        className={'env-pill switcher__pill' + (empty ? ' switcher__pill--empty' : '')}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={name}
        onClick={() => setOpen((v) => !v)}
      >
        <StatusDot status={empty ? null : status} />
        <span className="env-pill__meta switcher__meta">
          {kind}
        </span>
        <span className="env-pill__label">{empty ? 'none' : label}</span>
        {showWord && (
          <span className={`env-pill__state env-pill__state--${status}`}>{statusLabel}</span>
        )}
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
            // The meta line already shows a non-healthy state; say it once.
            const stateInMeta = !!(opt.statusLabel && opt.meta && String(opt.meta).includes(opt.statusLabel))
            return (
              <button
                key={opt.id}
                type="button"
                role="option"
                aria-selected={isActive}
                onClick={() => { onSelect(opt.id); setOpen(false) }}
                className={'switcher__option' + (isActive ? ' switcher__option--active' : '')}
              >
                <StatusDot status={opt.status} className="switcher__option-dot" />
                <span className="switcher__option-info">
                  <span className={'switcher__option-label' + (isActive ? ' switcher__option-label--active' : '')}>
                    {opt.label}
                  </span>
                  {opt.meta && (
                    <span className="switcher__option-meta">
                      {opt.meta}
                    </span>
                  )}
                  {opt.statusLabel && !stateInMeta && (
                    <span className="sr-only"> · {opt.statusLabel}</span>
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
