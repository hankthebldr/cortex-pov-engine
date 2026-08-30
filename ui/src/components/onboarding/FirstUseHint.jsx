import React from 'react'

export default function FirstUseHint({ show, text, onDismiss }) {
  if (!show) return null
  return (
    <span className="first-use-hint" role="note">
      {text}
      {onDismiss && (
        <button type="button" className="first-use-hint__x" aria-label="Dismiss hint" onClick={onDismiss}>×</button>
      )}
    </span>
  )
}
