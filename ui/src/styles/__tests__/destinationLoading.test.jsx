/**
 * I-3 — `.destination-loading`'s only styling was a static inline `style`
 * prop on `DestinationLoading` (destinations.jsx), and the class itself had
 * ZERO CSS rules anywhere in `ui/src`. Lifting the inline style without
 * writing the rule would have shipped every lazy-chunk Suspense fallback
 * unstyled. This uses the same real-stylesheet cascade resolver as
 * console-contrast.test.jsx (jsdom's getComputedStyle does not resolve
 * `var()`, so a naive check would pass on the unfixed state) to prove the
 * rule exists and actually applies — plus renders the real component to
 * prove the inline `style` prop is gone.
 */
import { describe, it, expect } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import { render } from '@testing-library/react'
import { resolveProperty } from '../../test/cssCascade.js'
import { DestinationLoading } from '../../app/destinations.jsx'

function mountShell(innerHTML) {
  const shell = document.createElement('div')
  shell.className = 'theme-console'
  shell.innerHTML = innerHTML
  document.body.appendChild(shell)
  return shell.querySelector('.destination-loading')
}

describe('.destination-loading — real CSS, not an inline style (I-3)', () => {
  it('gets real padding/opacity/color from cortex-console.css, not from an inline style', () => {
    const el = mountShell('<div class="destination-loading" role="status" aria-live="polite">loading…</div>')
    // Before the fix, none of these had ANY rule anywhere in ui/src — the
    // resolver returns null for a property nothing declares.
    expect(resolveProperty(el, 'padding')).not.toBeNull()
    expect(resolveProperty(el, 'opacity')).toBe('0.6')
    expect(resolveProperty(el, 'color')).not.toBeNull()
  })

  it('the rendered component itself carries no inline style attribute', () => {
    const { container } = render(<DestinationLoading />)
    const el = container.querySelector('.destination-loading')
    expect(el).not.toBeNull()
    expect(el.getAttribute('style')).toBeNull()
  })
})
