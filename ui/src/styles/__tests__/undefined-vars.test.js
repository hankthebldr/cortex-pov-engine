/**
 * undefined-vars.test.js — guards against a `var(--name)` reference with
 * NO fallback whose `--name` is never declared anywhere under
 * `ui/src/styles/**`.
 *
 * WHY THIS MATTERS: an undefined custom property is not a parse error —
 * it is invalid *at computed-value time* per the CSS spec, so the
 * declaration that referenced it falls back to its property's own
 * default (`background` -> transparent, `color`/`border-color` ->
 * inherited) with no warning anywhere. That was silently survivable
 * while the console shell was unconditionally dark (an inherited/
 * transparent value usually still read as "dark enough"); now that
 * light is the theme default, a `background` silently resolving to
 * transparent changes what actually shows through.
 *
 * Found 2026-08-31: `--c-cortex-teal` (12 uses), `--c-bg-elevated` (6),
 * `--c-text-primary` (5), `--c-bg-base` (5), `--c-border` (5) — 33 bare
 * references in cortex-console.css, none ever declared. Fixed by
 * aliasing each onto the correct existing token in the `.theme-console`
 * block (see that file's own comments at each alias for why each
 * mapping was chosen — notably --c-cortex-teal -> --c-signal, NOT
 * cortex-theme.css's --cortex-teal, which was deliberately re-pointed at
 * green in the v2 redesign and would have silently repainted every one
 * of those 12 call sites).
 *
 * `--x` (3 "uses") is NOT a seventh undefined var: all three sightings
 * are inside `/* ... *\/` doc-comment prose (`library.css`,
 * `run-detail.css`) describing the "this file only consumes var(--x)"
 * convention in prose — literally quoting cssCascade.js's own doc
 * comment's placeholder notation — never an actual CSS declaration.
 * Comment-stripping below correctly removes them from consideration;
 * left as prose, not "fixed", because there is nothing to fix.
 *
 * TWO layers, per the task's "reuse cssCascade.js, don't reimplement it":
 *
 *  1. A full-tree static sweep (this file, `findBareVarUsages` /
 *     `findDeclaredProps`) — the guard that actually covers all 16
 *     files. cssCascade.js's resolver is DOM/CSSOM-cascade-shaped (an
 *     element, walked through matching selectors) and jsdom's `css:
 *     true` is only wired for a handful of files (see
 *     `vitest.config.js`'s `test.css.include` comment) — plumbing every
 *     destination stylesheet through a real per-selector mount for a
 *     repo-wide sweep would itself be a from-scratch reimplementation of
 *     what this layer already does correctly in ~20 lines: a declared
 *     custom property anywhere in the tree makes a bare reference to it
 *     valid, regardless of selector scope (a destination-local
 *     redefinition like `agents.css`'s own `--c-void` block is additive
 *     to, never a precondition for, the global `.theme-console` one).
 *
 *  2. A cssCascade.js-driven spot-check (below) that mounts the real
 *     global token stylesheets, and — via the SAME `resolveValue` this
 *     module already throws `cssCascade: --NAME has no cascaded value`
 *     from in `console-contrast.test.jsx` — resolves the five
 *     previously-broken names (`--x` excluded, see above) end-to-end.
 *     This is the oracle that would have caught the original defect
 *     directly, at the exact call sites that had it.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import { resolveValue } from '../../test/cssCascade.js'

const STYLES_DIR = path.resolve(__dirname, '..')

function walkCssFiles(dir) {
  let out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out = out.concat(walkCssFiles(full))
    else if (entry.name.endsWith('.css')) out.push(full)
  }
  return out
}

/** Blank out /* ... *\/ comments in place (preserves newlines, so line numbers stay accurate). */
function blankComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
}

function lineAt(text, index) {
  return text.slice(0, index).split('\n').length
}

/** Every `--name` declared anywhere (any selector scope) across the given files. */
function findDeclaredProps(files) {
  const declared = new Set()
  const re = /(^|[{;\s])(--[a-zA-Z0-9-]+)\s*:/g
  for (const file of files) {
    const stripped = blankComments(fs.readFileSync(file, 'utf8'))
    let m
    while ((m = re.exec(stripped))) declared.add(m[2])
  }
  return declared
}

/** Every bare `var(--name)` reference (no fallback — no comma before the closing paren) across the given files. */
function findBareVarUsages(files) {
  const usages = []
  const re = /var\(\s*(--[a-zA-Z0-9-]+)\s*\)/g
  for (const file of files) {
    const raw = fs.readFileSync(file, 'utf8')
    const stripped = blankComments(raw)
    let m
    while ((m = re.exec(stripped))) {
      usages.push({
        name: m[1],
        file: path.relative(STYLES_DIR, file),
        line: lineAt(stripped, m.index),
      })
    }
  }
  return usages
}

describe('undefined CSS custom properties (ui/src/styles/**)', () => {
  const files = walkCssFiles(STYLES_DIR)

  it('found more than a handful of stylesheets to sweep (sanity — a broken walk would silently pass on zero files)', () => {
    expect(files.length).toBeGreaterThanOrEqual(16)
  })

  it('every bare var(--name) reference resolves to a --name declared somewhere in the tree', () => {
    const declared = findDeclaredProps(files)
    const usages = findBareVarUsages(files)
    expect(usages.length).toBeGreaterThan(0) // sanity — a broken usage regex would silently pass on zero matches too

    const missing = usages.filter((u) => !declared.has(u.name))
    const report = missing
      .map((u) => `  ${u.name}  (${u.file}:${u.line})`)
      .join('\n')

    expect(
      missing,
      `${missing.length} bare var() reference(s) with no matching declaration anywhere under styles/**:\n${report}`
    ).toEqual([])
  })

  it('does not mistake the "var(--x)" doc-comment convention for a real reference', () => {
    // library.css / run-detail.css both use `var(--x)` in prose to describe
    // "this file only consumes tokens, never redefines one" — comment
    // stripping must remove all three sightings, not surface them as a
    // seventh undefined var.
    const usages = findBareVarUsages(files)
    expect(usages.some((u) => u.name === '--x')).toBe(false)
  })
})

describe('undefined-vars regression: the five names found broken 2026-08-31', () => {
  /** Mounts a bare probe under .theme-console so both :root and .theme-console-scoped tokens are in its cascade, exactly like console-contrast.test.jsx's fixtures. */
  function mountProbe({ dark = false } = {}) {
    const shell = document.createElement('div')
    shell.className = 'theme-console'
    if (dark) shell.setAttribute('data-theme', 'dark')
    const probe = document.createElement('div')
    shell.appendChild(probe)
    document.body.appendChild(shell)
    return probe
  }

  const FIXED_VARS = [
    '--c-cortex-teal',
    '--c-bg-elevated',
    '--c-text-primary',
    '--c-bg-base',
    '--c-border',
  ]

  for (const varName of FIXED_VARS) {
    it(`var(${varName}) resolves via cssCascade.js in both themes (used to throw "has no cascaded value")`, () => {
      const light = mountProbe({ dark: false })
      const dark = mountProbe({ dark: true })
      expect(() => resolveValue(light, `var(${varName})`)).not.toThrow()
      expect(() => resolveValue(dark, `var(${varName})`)).not.toThrow()
      expect(resolveValue(light, `var(${varName})`)).toBeTruthy()
      expect(resolveValue(dark, `var(${varName})`)).toBeTruthy()
      document.body.innerHTML = ''
    })
  }

  it('cssCascade.js genuinely still throws on a var with no declaration anywhere (the guard is not a no-op)', () => {
    const probe = mountProbe()
    expect(() => resolveValue(probe, 'var(--definitely-not-a-real-token)')).toThrow(
      /has no cascaded value/
    )
    document.body.innerHTML = ''
  })
})
