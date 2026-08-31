/**
 * cssCascade.test.js — guards the "no @media evaluation" contract the
 * module doc on cssCascade.js claims (src/test/cssCascade.js:28-31:
 * "Scope: no @media/@supports/@layer evaluation").
 *
 * Before this fix, `collectRules()`'s walker fell into the generic
 * `else if (rule.cssRules)` branch for ANY container rule without a
 * `selectorText` — which is true of a `CSSMediaRule` just as much as any
 * other grouping construct — and collected its children UNCONDITIONALLY.
 * So an `@media (max-width: 980px) { .foo { ... } }` block was applied
 * during every test run regardless of the (non-existent, jsdom-default)
 * viewport, silently contradicting the module's own doc comment.
 *
 * `destinations/ttps.css` already has a real instance of this shape:
 * the unconditional `.ttpb-detail { position: sticky; ...; max-height:
 * calc(100vh - 48px); }` (~line 270) is followed by an
 * `@media (max-width: 980px) { .ttpb-detail { position: static; ...
 * max-height: none; } }` block (~line 530) whose own comment says it is
 * "placed last so it wins the cascade... at equal specificity" — i.e. it
 * is deliberately written to win a same-specificity, source-order
 * tie-break IF its condition matches. Under the bug, the walker ignored
 * the condition and matched it every time, so `.ttpb-detail` resolved to
 * the *narrow-viewport* values unconditionally, in a guard that never
 * evaluates a real viewport at all.
 */
import { describe, it, expect, afterEach } from 'vitest'
import '../../styles/destinations/ttps.css'
import { resolveProperty, invalidateRuleCache } from '../cssCascade.js'

function mount(html) {
  const div = document.createElement('div')
  div.innerHTML = html
  document.body.appendChild(div)
  return div
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('cssCascade — @media rules are not applied (per module contract)', () => {
  it('does not apply an @media-gated rule to the unconditional selector it targets', () => {
    invalidateRuleCache()
    const root = mount('<div class="ttpb-detail">detail rail</div>')
    const el = root.querySelector('.ttpb-detail')

    // The unconditional `.ttpb-detail` rule (ttps.css ~line 270) declares
    // `position: sticky`. The `@media (max-width: 980px)` block (~line 530)
    // overrides it to `position: static` — but only when that condition
    // holds, which this guard has no viewport to evaluate. A resolver that
    // "skips" (or treats the media condition as false, per this app's jsdom
    // `matchMedia` stub) must therefore report the unconditional value.
    const position = resolveProperty(el, 'position')
    expect(
      position,
      `resolved position="${position}" — the @media (max-width: 980px) block was applied ` +
        `unconditionally, contradicting the "no @media evaluation" contract in this module's doc comment`
    ).toBe('sticky')

    const maxHeight = resolveProperty(el, 'max-height')
    expect(maxHeight).toBe('calc(100vh - 48px)')
  })
})
