import { describe, it, expect, beforeAll } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

// This suite reads the raw CSS text rather than rendering it, because the
// vitest config runs with `css: false` (imported stylesheets are stripped
// before a component test ever sees them) and jsdom's CSS engine does not
// compute custom-property cascades reliably enough to assert on. Parsing
// the source text is deterministic and matches how the rest of this repo
// tests CSS-adjacent contracts.

const STYLES_DIR = path.resolve(__dirname, '..')
const TOKENS_PATH = path.join(STYLES_DIR, 'cortex-tokens.css')
const THEME_PATH = path.join(STYLES_DIR, 'cortex-theme.css')
const MAIN_JSX_PATH = path.resolve(STYLES_DIR, '..', 'main.jsx')

/** Strip /* ... *\/ CSS comments so they can't be mistaken for declarations. */
function stripComments(css) {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** Parse `--name: value;` custom-property declarations out of a CSS block body. */
function parseDeclarations(blockBody) {
  const out = {}
  const re = /--([\w-]+)\s*:\s*([^;]+);/g
  let m
  while ((m = re.exec(stripComments(blockBody)))) {
    out[m[1]] = m[2].trim()
  }
  return out
}

/** Extract the body of the first `selector { ... }` block matching `selectorRe`. */
function extractBlock(css, selectorRe) {
  const m = selectorRe.exec(css)
  if (!m) return null
  const start = m.index + m[0].length
  const end = css.indexOf('\n}', start)
  if (end === -1) return null
  return css.slice(start, end)
}

// The 12 legacy `--cortex-*` token names this repo's components reference
// (~117+ call sites across cortex-theme.css and cortex-console.css). This
// list is the contract: every name here must alias onto a real token
// defined in both the light and dark blocks of cortex-tokens.css.
const EXPECTED_ALIASES = [
  'navy',
  'teal',
  'teal-deep',
  'steel',
  'ink',
  'ink-muted',
  'light-bg',
  'white',
  'border',
  'success',
  'warning',
  'danger',
]

describe('design token contract (cortex-tokens.css)', () => {
  let tokensCss
  let themeCss
  let lightTokens
  let darkTokens

  beforeAll(() => {
    tokensCss = fs.readFileSync(TOKENS_PATH, 'utf8')
    themeCss = fs.readFileSync(THEME_PATH, 'utf8')

    const lightBlock = extractBlock(tokensCss, /:root\s*\{/)
    const darkBlock = extractBlock(tokensCss, /\[data-theme="dark"\]\s*\{/)
    expect(lightBlock, 'cortex-tokens.css must declare a :root block').not.toBeNull()
    expect(darkBlock, 'cortex-tokens.css must declare a [data-theme="dark"] block').not.toBeNull()

    lightTokens = parseDeclarations(lightBlock)
    darkTokens = parseDeclarations(darkBlock)
  })

  it('defines a complete LIGHT (:root) token set matching the design reference', () => {
    // Spot-check one token from each family named in the task brief.
    expect(lightTokens.s1).toBe('#FFFFFF')
    expect(lightTokens.ac).toBe('#00A855') // primary accent, light theme
    expect(lightTokens.tx).toBe('#0A0F0D')
    expect(lightTokens.bd).toBe('#E3E9E6')
    // --warn is a recorded WCAG AA deviation (2026-08-31), not the designer's
    // verbatim #C7961B — that value measured 2.69:1 against --s1, short of
    // the 4.5:1 text floor at the 9-11px this console renders warn text at
    // (and even short of the 3:1 non-text floor for its own decorative
    // fills). Same hue/saturation, darkened only as far as 4.5:1 requires —
    // see the deviation note atop cortex-tokens.css's :root block.
    expect(lightTokens.warn).toBe('#896713')
    expect(lightTokens.crit).toBe('#A51B00')
    expect(lightTokens.orange).toBe('#FA582D')
    expect(lightTokens.info).toBe('#0090AA')
    expect(lightTokens.ink).toBe('#06120C')
  })

  it('defines a complete DARK ([data-theme="dark"]) token set matching the design reference', () => {
    expect(darkTokens.s1).toBe('#101815')
    expect(darkTokens.ac).toBe('#00CC66') // primary accent, dark theme
    expect(darkTokens.tx).toBe('#F2F6F4')
    expect(darkTokens.bd).toBe('#26312A')
    expect(darkTokens.warn).toBe('#FFCB06')
    expect(darkTokens.crit).toBe('#FF6A4D')
    expect(darkTokens.orange).toBe('#FF7A54')
    expect(darkTokens.info).toBe('#35D3F0')
    expect(darkTokens.ink).toBe('#04100A')
  })

  it('actually gives light and dark two different palettes (not a copy-paste no-op)', () => {
    // If dark ever regressed to being byte-identical to light, the whole
    // point of shipping two token sets would be silently defeated.
    for (const key of ['s0', 's1', 'ac', 'tx', 'tx2', 'bd', 'ink']) {
      expect(darkTokens[key], `--${key} should differ between themes`).not.toBe(lightTokens[key])
    }
  })

  it('imports cortex-tokens.css ahead of cortex-theme.css so aliases resolve', () => {
    const mainJsx = fs.readFileSync(MAIN_JSX_PATH, 'utf8')
    const tokensIdx = mainJsx.indexOf("styles/cortex-tokens.css'")
    const themeIdx = mainJsx.indexOf("styles/cortex-theme.css'")
    expect(tokensIdx, 'main.jsx must import cortex-tokens.css').toBeGreaterThan(-1)
    expect(themeIdx, 'main.jsx must import cortex-theme.css').toBeGreaterThan(-1)
    expect(tokensIdx).toBeLessThan(themeIdx)
  })

  describe('--cortex-* alias table', () => {
    let aliases

    beforeAll(() => {
      const themeRootBlock = extractBlock(themeCss, /:root\s*\{/)
      expect(themeRootBlock, 'cortex-theme.css must declare a :root block').not.toBeNull()

      aliases = {}
      const re = /--cortex-([\w-]+)\s*:\s*([^;]+);/g
      let m
      while ((m = re.exec(stripComments(themeRootBlock)))) {
        aliases[m[1]] = m[2].trim()
      }
    })

    it('declares exactly the 12 expected --cortex-* names', () => {
      expect(Object.keys(aliases).sort()).toEqual([...EXPECTED_ALIASES].sort())
    })

    it.each(EXPECTED_ALIASES)('--cortex-%s resolves to a var() reference', (name) => {
      const value = aliases[name]
      expect(value, `--cortex-${name} must be declared`).toBeDefined()
      expect(value, `--cortex-${name} should alias a token via var(), not a hardcoded value`).toMatch(
        /^var\(--[\w-]+\)$/
      )
    })

    it.each(EXPECTED_ALIASES)(
      '--cortex-%s aliases a token defined in BOTH the light and dark sets',
      (name) => {
        const value = aliases[name]
        const target = value.match(/^var\(--([\w-]+)\)$/)?.[1]
        expect(target, `could not parse alias target out of "${value}"`).toBeDefined()
        expect(lightTokens, `--${target} (light) must be defined in cortex-tokens.css`).toHaveProperty(target)
        expect(darkTokens, `--${target} (dark) must be defined in cortex-tokens.css`).toHaveProperty(target)
      }
    )

    it('routes the primary accent alias (--cortex-teal) onto Cortex green (--ac), not the old teal', () => {
      expect(aliases['teal']).toBe('var(--ac)')
    })

    it('routes --cortex-warning onto the warn signal token, never onto --orange', () => {
      expect(aliases['warning']).toBe('var(--warn)')
      expect(aliases['warning']).not.toMatch(/--orange/)
    })
  })
})
