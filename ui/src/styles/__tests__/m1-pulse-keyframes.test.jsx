/**
 * M-1 — ToolStatusPanel correctly dropped its inline `<style>{'@keyframes
 * pulse {…}'}</style>` block (its dot now uses `tsp-pulse`), but that block
 * was the ONLY definition of `@keyframes pulse` in the app, and
 * `.tenant-mgr__pill--testing` (cortex-console.css) still references
 * `animation: pulse 1s ease-in-out infinite`. Without a `@keyframes pulse`
 * definition somewhere, the TenantManager "TESTING…" pill's in-flight
 * loading affordance silently does not animate.
 */
import { describe, it, expect } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'

function hasKeyframes(name) {
  for (const sheet of document.styleSheets) {
    let rules
    try { rules = sheet.cssRules } catch { continue }
    if (!rules) continue
    for (const rule of rules) {
      if (rule.type === CSSRule.KEYFRAMES_RULE && rule.name === name) return true
    }
  }
  return false
}

describe('@keyframes pulse (M-1)', () => {
  it('is defined somewhere in the shipped CSS — .tenant-mgr__pill--testing depends on it', () => {
    expect(hasKeyframes('pulse')).toBe(true)
  })
})
