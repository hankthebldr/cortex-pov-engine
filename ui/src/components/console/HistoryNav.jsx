import React, { useEffect, useRef } from 'react'
import { Icon } from './kit/index.js'

void React

/** True on a Mac, where the shortcut is ⌘ rather than Ctrl. */
export function isMacPlatform() {
  if (typeof navigator === 'undefined') return false
  const platform = navigator.userAgentData?.platform || navigator.platform || ''
  return /mac|iphone|ipad|ipod/i.test(platform)
}

// Inputs that take no typed text: a shortcut pressed on one still navigates.
const NON_TEXT_INPUTS = new Set(['button', 'checkbox', 'radio', 'submit', 'reset', 'range', 'color', 'file', 'image'])

/** True while focus is somewhere the DC types: ⌘[ there belongs to the field. */
export function isTextEntry(el) {
  if (!el || !el.tagName) return false
  if (el.isContentEditable) return true
  if (el.tagName === 'TEXTAREA') return true
  if (el.tagName !== 'INPUT') return false
  return !NON_TEXT_INPUTS.has(String(el.type || 'text').toLowerCase())
}

/**
 * HistoryNav — the header's Back and Forward (design C9).
 *
 * Two icon buttons that walk the console's own history: every page, tab, run
 * and deep dive the URL has named, in the order the DC opened them
 * (useConsoleRouter + navHistory.js). Each is disabled when there is nowhere
 * to go. ⌘[ / ⌘] on a Mac and Ctrl+[ / Ctrl+] elsewhere do the same, except
 * while focus is in a text field, where the keys belong to the field.
 *
 * Props:
 *   canBack / canForward  — whether each direction has somewhere to go
 *   onBack / onForward    — () => void
 */
export default function HistoryNav({ canBack = false, canForward = false, onBack = () => {}, onForward = () => {} }) {
  const mac = isMacPlatform()
  const mod = mac ? '⌘' : 'Ctrl+'

  // Read through a ref so the listener subscribes once, at mount.
  const latest = useRef({ canBack, canForward, onBack, onForward })
  useEffect(() => { latest.current = { canBack, canForward, onBack, onForward } })

  useEffect(() => {
    const onKey = (e) => {
      if (e.defaultPrevented || (e.key !== '[' && e.key !== ']')) return
      if (!(mac ? e.metaKey : e.ctrlKey) || e.altKey) return
      if (isTextEntry(e.target) || isTextEntry(document.activeElement)) return
      // The console owns the shortcut: with nowhere to go it does nothing,
      // rather than letting the browser leave the console.
      e.preventDefault()
      const now = latest.current
      if (e.key === '[' && now.canBack) now.onBack()
      if (e.key === ']' && now.canForward) now.onForward()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mac])

  return (
    <div className="pov-history" role="group" aria-label="History" data-testid="header-history">
      <button
        type="button"
        className="pov-history__btn"
        data-testid="header-back"
        aria-label="Back"
        aria-keyshortcuts={mac ? 'Meta+[' : 'Control+['}
        title={`Back (${mod}[)`}
        disabled={!canBack}
        onClick={onBack}
      >
        <Icon name="back" />
      </button>
      <button
        type="button"
        className="pov-history__btn"
        data-testid="header-forward"
        aria-label="Forward"
        aria-keyshortcuts={mac ? 'Meta+]' : 'Control+]'}
        title={`Forward (${mod}])`}
        disabled={!canForward}
        onClick={onForward}
      >
        <Icon name="forward" />
      </button>
    </div>
  )
}
