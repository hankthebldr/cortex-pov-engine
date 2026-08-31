/**
 * M-3 — three new classes had no CSS rule anywhere in `ui/src`:
 * `eal-console__heading`, `tools-destination__head-copy` (structural
 * wrappers) and `ttpb-callout--warn` (the Remediation-guidance callout's
 * warn treatment, which never landed).
 *
 * This does NOT use cssCascade.js's `resolveProperty` — that resolver
 * deliberately walks up the ancestor chain for ANY property (built for
 * naturally-inherited properties like `color`), which makes it a false
 * green here: e.g. `.eal-console` (an ancestor of `.eal-console__heading`
 * in real markup) already declares `flex-direction: column` for its own
 * unrelated layout, so a property-cascade check would pass even with zero
 * rule on the class itself — verified empirically while writing this file.
 * Instead this looks up the EXACT authored selector directly in the real,
 * loaded stylesheets — a lookup only a real rule on that precise class can
 * satisfy.
 */
import { describe, it, expect } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import '../destinations/eal.css'
import '../destinations/adapters.css'
import '../destinations/ttps.css'

function findRule(selectorText) {
  for (const sheet of document.styleSheets) {
    let rules
    try { rules = sheet.cssRules } catch { continue }
    if (!rules) continue
    for (const rule of rules) {
      if (rule.selectorText === selectorText) return rule
    }
  }
  return null
}

describe('M-3 — previously ruleless classes now have real CSS', () => {
  it('.eal-console__heading has its own rule', () => {
    const rule = findRule('.eal-console__heading')
    expect(rule).not.toBeNull()
    expect(rule.style.getPropertyValue('display')).toBe('flex')
  })

  it('.theme-console .tools-destination__head-copy has its own rule', () => {
    const rule = findRule('.theme-console .tools-destination__head-copy')
    expect(rule).not.toBeNull()
    expect(rule.style.getPropertyValue('display')).toBe('flex')
  })

  it('.ttpb-callout--warn has its own rule adding a warn treatment beyond the base .ttpb-callout', () => {
    const rule = findRule('.ttpb-callout--warn')
    expect(rule).not.toBeNull()
    // Must add something of its own on top of the base class's border-left,
    // or the modifier "never lands" (M-3) even once it parses.
    expect(rule.style.getPropertyValue('background')).toBeTruthy()
  })
})
