/**
 * focusTrap.js — keep Tab inside an open modal.
 *
 * Pure DOM, no React. `aria-modal="true"` tells assistive tech the page behind
 * is inert; it does not stop the Tab key, so a dialog that only moves focus in
 * lets a keyboard user walk out behind the backdrop.
 *
 * Only the two edges are handled. In the middle the browser's own sequential
 * order is left alone, so nothing here re-implements tab order.
 */

const SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]',
].join(', ')

/** Controls a keyboard can reach inside `root`, in document order. */
export function focusableIn(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return []
  return Array.from(root.querySelectorAll(SELECTOR)).filter((el) => {
    const ti = el.getAttribute('tabindex')
    if (ti !== null && Number(ti) < 0) return false
    return !el.hasAttribute('hidden') && el.getAttribute('aria-hidden') !== 'true'
  })
}

/**
 * Handle one keydown for a trap around `root`.
 * @returns {boolean} true when focus was moved (and the event's default prevented)
 */
export function trapTabKey(event, root) {
  if (!event || event.key !== 'Tab' || !root) return false
  const items = focusableIn(root)
  if (!items.length) return false
  const first = items[0]
  const last = items[items.length - 1]
  const active = typeof document !== 'undefined' ? document.activeElement : null
  let target = null
  if (!root.contains(active)) target = event.shiftKey ? last : first
  else if (event.shiftKey && active === first) target = last
  else if (!event.shiftKey && active === last) target = first
  if (!target) return false
  event.preventDefault()
  target.focus()
  return true
}
