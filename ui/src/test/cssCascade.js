/**
 * cssCascade.js — a minimal, real-stylesheet-driven CSS cascade + custom
 * property resolver for jsdom.
 *
 * WHY THIS EXISTS: jsdom's `getComputedStyle` does not resolve `var()`
 * references (it returns the literal string `"var(--x)"`) and does NOT
 * apply CSS specificity when two rules match the same element — it was
 * empirically verified (see docs/design/console-redesign-repair.md) to
 * pick whichever matching rule appears LAST in `document.styleSheets`,
 * regardless of selector specificity. That is the exact opposite of a
 * real browser for the console's actual bug (a compound selector losing
 * to a higher-specificity one), so naively rendering components and
 * reading `getComputedStyle(...).color` in jsdom would not just fail to
 * catch the regression — it would report the WRONG answer.
 *
 * This module still uses the REAL parsed stylesheets (`document.styleSheets`,
 * populated by vitest's `css: true`) and REAL selector matching
 * (`Element.matches()`, backed by jsdom's nwsapi, which correctly
 * implements CSS selector semantics) — nothing about which files are
 * loaded or how selectors match is reimplemented or faked. The only two
 * things done by hand are (a) computing standard CSS specificity for the
 * matching selectors and picking a winner by (specificity, source order)
 * the way a real cascade does, and (b) substituting `var(--x[, fallback])`
 * chains, always relative to the originally-queried element (per spec,
 * variable substitution uses the querying element's own inherited/cascaded
 * environment, not the element where the referencing declaration lives).
 *
 * Scope: no @media/@supports/@layer evaluation (this app does not gate
 * theme-relevant rules behind any of those — cortex-tokens.css uses
 * `:root` / `[data-theme="dark"]` attribute selectors only) and no
 * pseudo-class state (:hover etc. — irrelevant to static contrast). Not a
 * general-purpose CSS engine; scoped precisely to what this guard needs.
 *
 * That "no @media evaluation" line used to be aspirational, not real: a
 * `CSSMediaRule` (and a `CSSSupportsRule`) has no `selectorText` either,
 * so it fell into the generic "this is a container, recurse into its
 * children" branch below and its rules were collected UNCONDITIONALLY —
 * i.e. actually applied on every run, regardless of any condition. A
 * conditional group rule is the one JS-visible signal for "skip me": it
 * carries a `conditionText` (both `CSSMediaRule` and `CSSSupportsRule`
 * define it; a plain nested grouping rule, which this codebase doesn't
 * use, would not). Skipping those keeps the resolver's behavior matching
 * this doc comment instead of silently contradicting it.
 */

function collectRules() {
  const rules = []
  const walk = (list) => {
    for (const rule of list) {
      if (rule.selectorText && rule.style) {
        rules.push(rule)
      } else if (rule.cssRules && typeof rule.conditionText !== 'string') {
        walk(rule.cssRules)
      }
    }
  }
  for (const sheet of document.styleSheets) {
    let cssRules
    try {
      cssRules = sheet.cssRules
    } catch {
      continue
    }
    if (cssRules) walk(cssRules)
  }
  return rules
}

// Standard (a, b, c) CSS specificity: ids, then classes/attrs/pseudo-classes,
// then type selectors + pseudo-elements. Good enough for this codebase's
// selectors (no :is()/:where()/:not(complex) usage to worry about).
function specificity(selector) {
  let sel = selector
  const pseudoElRe = /::[\w-]+|:(?:before|after|first-line|first-letter)\b/g
  const pseudoElCount = (sel.match(pseudoElRe) || []).length
  sel = sel.replace(pseudoElRe, ' ')
  const idCount = (sel.match(/#[\w-]+/g) || []).length
  const bCount = (sel.match(/\.[\w-]+|\[[^\]]*\]|:[\w-]+(\([^)]*\))?/g) || []).length
  sel = sel
    .replace(/#[\w-]+/g, ' ')
    .replace(/\.[\w-]+/g, ' ')
    .replace(/\[[^\]]*\]/g, ' ')
    .replace(/:[\w-]+(\([^)]*\))?/g, ' ')
  const typeCount = (sel.match(/(^|[\s>+~])([a-zA-Z][\w-]*)/g) || []).length
  return [idCount, bCount, typeCount + pseudoElCount]
}

function cmpSpec(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i]
  }
  return 0
}

function splitSelectorList(selectorText) {
  // Top-level comma split. Fine here — this codebase has no :is()/:not()
  // with comma-separated arguments in any theme-relevant rule.
  return selectorText.split(',').map((s) => s.trim())
}

let cachedRules = null
let cachedCount = -1
function getRules() {
  if (cachedRules && cachedCount === document.styleSheets.length) return cachedRules
  cachedRules = collectRules()
  cachedCount = document.styleSheets.length
  return cachedRules
}

/** Invalidate the rule cache (call if stylesheets change between assertions). */
export function invalidateRuleCache() {
  cachedRules = null
  cachedCount = -1
}

/** The winning RAW (possibly var()) value of `prop` declared directly on `node` (no inheritance walk). */
function winningAt(node, prop) {
  let best = null
  const rules = getRules()
  let order = 0
  for (const rule of rules) {
    order += 1
    let selectors
    try {
      selectors = splitSelectorList(rule.selectorText)
    } catch {
      continue
    }
    for (const sel of selectors) {
      let matched = false
      try {
        matched = node.matches(sel)
      } catch {
        matched = false
      }
      if (!matched) continue
      const val = rule.style.getPropertyValue(prop)
      if (!val) continue
      const spec = specificity(sel)
      if (
        !best ||
        cmpSpec(spec, best.spec) > 0 ||
        (cmpSpec(spec, best.spec) === 0 && order >= best.order)
      ) {
        best = { spec, order, value: val.trim() }
      }
    }
  }
  return best ? best.value : null
}

/** Cascaded RAW value of `prop` at `el`, walking up for inheritance when nothing matches `el` itself. */
function cascadedValue(el, prop) {
  let node = el
  while (node && node.nodeType === 1) {
    const v = winningAt(node, prop)
    if (v != null) return v
    node = node.parentElement
  }
  return null
}

/** Resolves a raw declaration value (possibly `var(--x[, fallback])`, possibly chained) relative to `el`. */
export function resolveValue(el, raw, seen = new Set()) {
  if (raw == null) return null
  const trimmed = String(raw).trim()
  const m = trimmed.match(/^var\(\s*(--[\w-]+)\s*(?:,\s*([\s\S]+))?\)$/)
  if (!m) return trimmed
  const [, varName, fallback] = m
  if (seen.has(varName)) {
    throw new Error(`cssCascade: var() cycle detected at ${varName}`)
  }
  const nextSeen = new Set(seen)
  nextSeen.add(varName)
  const declared = cascadedValue(el, varName)
  if (declared == null) {
    if (fallback != null) return resolveValue(el, fallback, nextSeen)
    throw new Error(
      `cssCascade: --${varName} has no cascaded value at <${el.tagName?.toLowerCase()} class="${el.className}">`
    )
  }
  return resolveValue(el, declared, nextSeen)
}

/**
 * Resolves the effective (fully var()-substituted) value of a real CSS
 * property (e.g. "color", "background") at `el`, using the real cascade
 * across every loaded stylesheet.
 */
export function resolveProperty(el, prop) {
  const raw = cascadedValue(el, prop)
  if (raw == null) return null
  return resolveValue(el, raw)
}
