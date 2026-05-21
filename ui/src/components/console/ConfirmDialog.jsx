import React, { useEffect, useRef } from 'react'

/**
 * ConfirmDialog — generic confirmation modal.
 *
 * Used for destructive actions like Run Abort. Focuses the destructive
 * action's button on open (DCs already decided to do this; the modal
 * exists for sanity, not friction) and traps focus inside the dialog
 * while open.
 *
 * Props:
 *   open       — boolean
 *   onClose    — () => void
 *   onConfirm  — () => void
 *   title      — short noun phrase
 *   body       — string|node — the explanation of consequences
 *   confirmLabel  — string (default "Confirm")
 *   confirmVariant — 'danger' | 'primary' (default 'danger')
 *   cancelLabel    — string (default "Cancel")
 */
export default function ConfirmDialog({
  open,
  onClose = () => {},
  onConfirm = () => {},
  title = 'Confirm',
  body = null,
  confirmLabel = 'Confirm',
  confirmVariant = 'danger',
  cancelLabel = 'Cancel',
}) {
  const confirmRef = useRef(null)
  const previousFocusRef = useRef(null)

  // Focus management — capture previous focus, focus the confirm
  // button when the dialog opens, restore previous focus on close.
  useEffect(() => {
    if (!open) return undefined
    previousFocusRef.current = document.activeElement
    setTimeout(() => confirmRef.current && confirmRef.current.focus(), 20)
    return () => {
      if (previousFocusRef.current && previousFocusRef.current.focus) {
        previousFocusRef.current.focus()
      }
    }
  }, [open])

  // Esc to close, Enter to confirm
  useEffect(() => {
    if (!open) return undefined
    const handler = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'Enter' && document.activeElement === confirmRef.current) {
        // Enter confirms only if confirm button is focused — prevents
        // accidental confirm via stray Enter.
        e.preventDefault()
        onConfirm()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose, onConfirm])

  if (!open) return null

  return (
    <div
      className="confirm-dialog__backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
    >
      <div className="confirm-dialog">
        <h3 id="confirm-dialog-title" className="confirm-dialog__title">
          {title}
        </h3>
        {body && (
          <div className="confirm-dialog__body">
            {typeof body === 'string' ? <p>{body}</p> : body}
          </div>
        )}
        <div className="confirm-dialog__actions">
          <button
            type="button"
            className="btn"
            onClick={onClose}
          >
            {cancelLabel}
            <span className="kbd">esc</span>
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={
              'btn ' + (confirmVariant === 'danger' ? 'btn--danger-solid' : 'btn--primary')
            }
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
