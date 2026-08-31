/**
 * mono-specificity.test.jsx — T1 repair guard.
 *
 * `.theme-console code, .theme-console kbd, .theme-console .mono` (a
 * (0,2,0)-specificity compound selector) used to set `font-size: 0.92em`
 * and `letter-spacing: var(--tracking-mono)` alongside `font-family`.
 * Every destination rule that styles an element which ALSO carries class
 * `mono` in its JSX — e.g. `<div className="ttpb-stat__value mono">`
 * (TtpBrowserView.jsx) styled by `.ttpb-stat__value { font-size: 20px }`
 * (ttps.css) — is bare single-class, (0,1,0), and lost to the shared
 * rule: the hero stat tiles rendered at ~12px (20px * 0.92em, relative
 * to the ambient 13px body size) instead of the authored 20px.
 *
 * Root fix: `.mono` carries font-family only; `code`/`kbd` (native
 * elements with no competing destination rule) keep the original
 * family+size+tracking. Real markup pulled directly from
 * TtpBrowserView.jsx, same convention as console-contrast.test.jsx.
 */
import { describe, it, expect, afterEach } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import '../destinations/ttps.css'
import '../destinations/adapters.css'
import { resolveProperty } from '../../test/cssCascade.js'

function mountShell(innerHTML) {
  const shell = document.createElement('div')
  shell.className = 'theme-console'
  shell.innerHTML = innerHTML
  document.body.appendChild(shell)
  return shell
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('.mono does not clobber a destination rule\'s own font-size/letter-spacing', () => {
  it('TTP Cards hero stat tile — .ttpb-stat__value.mono keeps its authored 20px', () => {
    const shell = mountShell(`
      <div class="ttpb">
        <div class="ttpb-stat">
          <div class="ttpb-stat__value mono">169</div>
        </div>
      </div>
    `)
    const el = shell.querySelector('.ttpb-stat__value')
    const fontSize = resolveProperty(el, 'font-size')
    expect(
      fontSize,
      `.ttpb-stat__value.mono resolved font-size="${fontSize}" — the shared ` +
        `".theme-console .mono" rule (0,2,0) is still overriding the destination's ` +
        `own (0,1,0) ".ttpb-stat__value { font-size: 20px }" rule`
    ).toBe('20px')
  })

  it('.mono elements without a destination size rule still render as monospace (font-family only)', () => {
    const shell = mountShell(`<span class="mono">cxs_abc123</span>`)
    const el = shell.querySelector('.mono')
    const fontFamily = resolveProperty(el, 'font-family')
    expect(fontFamily).toBe(`'JetBrains Mono', ui-monospace, 'SF Mono', monospace`)
  })

  it('bare <code> keeps the original compact monospace sizing (no destination rule competes with it)', () => {
    const shell = mountShell(`<p>Run <code>make test</code></p>`)
    const el = shell.querySelector('code')
    expect(resolveProperty(el, 'font-size')).toBe('0.92em')
  })

  it('the tools-destination local .mono override (removed as redundant) is no longer needed for family to resolve', () => {
    // Regression guard for the cleanup half of T1: destinations/adapters.css
    // used to carry its own `.theme-console .tools-destination .mono {
    // font-family: var(--font-mono); }` (0,3,0) override — evidence someone
    // hit this exact bug and patched their own destination locally instead
    // of fixing the shared rule. Once the root .mono rule carries only
    // font-family, that local override is byte-identical dead weight and
    // was removed; this proves the root rule alone still does the job.
    const shell = mountShell(`
      <div class="tools-destination">
        <span class="mono">cxs_abc123</span>
      </div>
    `)
    const el = shell.querySelector('.tools-destination .mono')
    expect(resolveProperty(el, 'font-family')).toBe(`'JetBrains Mono', ui-monospace, 'SF Mono', monospace`)
  })
})
