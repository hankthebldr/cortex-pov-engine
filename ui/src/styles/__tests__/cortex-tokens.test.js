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

  it('defines a complete LIGHT (:root) token set on the inverted contract', () => {
    // THE CONTRACT INVERTED. It used to read "Cortex green is the primary
    // accent, PANW orange is reserved for warn/gap signal". It now reads the
    // other way round, per the brand's own one-dominant-color rule and the DS's
    // `--color-accent: var(--panw-orange)`. These assertions are the inversion
    // written down: if a future pass reverts it, this is what says so.
    expect(lightTokens.s1).toBe('#FFFFFF')
    expect(lightTokens.ac).toBe('#FA582D')      // chrome accent — PANW orange
    expect(lightTokens.pos).toBe('#0A6231')     // status — Cortex green, deep
    expect(lightTokens.tx).toBe('#000000')
    expect(lightTokens.bd).toBe('#E9E9E9')
    // --warn is Strata yellow darkened for AA: #FFCB06 measures 2.69:1 against
    // --s1, short of the 4.5:1 text floor at the 9-11px this console renders
    // warn text at. Same hue/saturation, darkened only as far as 4.5:1
    // requires. --warn-str keeps the brand value for fills.
    expect(lightTokens.warn).toBe('#7D5E11')
    expect(lightTokens['warn-str']).toBe('#FFCB06')
    expect(lightTokens.crit).toBe('#A51B00')
    expect(lightTokens.info).toBe('#00667B')
    // --orange is now an ALIAS of the accent rather than a separate signal
    // hue. Every legacy `var(--orange)` call site keeps resolving; what it
    // resolves to is the chrome accent.
    expect(lightTokens.orange).toBe('var(--ac)')
  })

  it('defines a complete DARK ([data-theme="dark"]) token set on the same contract', () => {
    // Dark surfaces are the DS neutrals verbatim — PANW theme dk1/dk2 plus the
    // template's working greys. Deliberately NOT green-tinted any more: the
    // tint competed with the status hue it sat behind.
    expect(darkTokens.s0).toBe('#000000')
    expect(darkTokens.s1).toBe('#141414')
    expect(darkTokens.ac).toBe('#FA582D')
    expect(darkTokens.pos).toBe('#00CC66')
    expect(darkTokens.tx).toBe('#FFFFFF')
    expect(darkTokens.tx2).toBe('#C7C7C7')
    expect(darkTokens.bd).toBe('#333333')
    expect(darkTokens.warn).toBe('#FFCB06')
    expect(darkTokens.crit).toBe('#FDAC96')
    expect(darkTokens.info).toBe('#00C0E8')
    expect(darkTokens.orange).toBe('var(--ac)')
  })

  it('keeps the soft chip fills OPAQUE so their contrast is well defined', () => {
    // The design authors these as rgba() over black. Left translucent, a chip
    // fill is a different colour on --s0 than on --s3, so "what is the contrast
    // of this label on its chip" has no single answer — and the contrast
    // harness cannot score one at all without being told what is underneath.
    for (const key of ['ac-soft', 'ac-line', 'pos-soft', 'pos-line']) {
      expect(lightTokens[key], `light --${key}`).toMatch(/^#[0-9A-Fa-f]{6}$/)
      expect(darkTokens[key], `dark --${key}`).toMatch(/^#[0-9A-Fa-f]{6}$/)
    }
  })

  it('separates the fill hue from the text-safe ink for BOTH brand colours', () => {
    // --ac measures 4.38:1 on dark --s3 and 2.66:1 on light --s3 — below the
    // text floor in both themes. It is a fill. Anything painting accent TEXT
    // uses --ac-ink, and the same split exists for green. Collapsing either
    // pair is how the console shipped unreadable 9px labels last time.
    expect(lightTokens['ac-ink']).not.toBe(lightTokens.ac)
    expect(lightTokens['pos-ink']).toBeDefined()
    expect(darkTokens['ac-ink']).not.toBe(darkTokens.ac)
    expect(darkTokens['pos-ink']).toBeDefined()
  })

  it('actually gives light and dark two different palettes (not a copy-paste no-op)', () => {
    // If dark ever regressed to being byte-identical to light, the whole point
    // of shipping two token sets would be silently defeated. --ac is NOT in
    // this list any more, and that is correct: a brand colour that changed
    // between themes would stop being the brand colour.
    for (const key of ['s0', 's1', 'tx', 'tx2', 'bd', 'ink', 'warn', 'crit']) {
      expect(darkTokens[key], `--${key} should differ between themes`).not.toBe(lightTokens[key])
    }
    expect(darkTokens.ac, '--ac is a brand hue and must NOT differ by theme').toBe(lightTokens.ac)
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

    it('routes the primary accent alias (--cortex-teal) onto --ac, whatever --ac now is', () => {
      // The legacy name is cyan, the value has been green, and it is now PANW
      // orange. What this guards is the indirection, not the hue: 130+ call
      // sites read --cortex-teal and every one of them must follow the accent.
      expect(aliases['teal']).toBe('var(--ac)')
    })

    it('routes --cortex-success onto the STATUS hue, never onto the accent', () => {
      // The alias that would have failed loudest and most silently. --ac-str
      // used to BE Cortex green, so `--cortex-success: var(--ac-str)` read
      // correctly. After the inversion --ac-str is PANW orange, and leaving
      // the alias alone would have painted every success badge in the console
      // a warning colour without one call site changing.
      expect(aliases['success']).toBe('var(--pos)')
      expect(aliases['success']).not.toMatch(/--ac/)
    })

    it('routes --cortex-warning onto the warn signal token, never onto --orange', () => {
      expect(aliases['warning']).toBe('var(--warn)')
      expect(aliases['warning']).not.toMatch(/--orange/)
    })
  })
})
