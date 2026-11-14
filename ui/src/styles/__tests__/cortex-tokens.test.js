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

  it('defines a complete LIGHT (:root) token set on the v26 contract', () => {
    // Design v26 §9.1 (2026-11-13, the owner's direction): Cortex green is the
    // primary action, the selected state and the brand; yellow is the only
    // warning; orange is no longer an accent. Before v26 the contract read
    // "orange = the one primary action, selected = brightness" (minimal pass)
    // and before that "orange = chrome, green = status only". These
    // assertions are v26 written down: if a pass reverts it, this says so.
    expect(lightTokens.s1).toBe('#FFFFFF')
    // Light fills the primary with the DEEP green so white text clears 7.5:1.
    expect(lightTokens.ac).toBe('#0A6231')
    expect(lightTokens['ac-tx']).toBe('#FFFFFF')
    expect(lightTokens.br).toBe('#00CC66')      // the brand hue itself
    expect(lightTokens.pos).toBe('#0A6231')     // status — Cortex green, deep
    expect(lightTokens.tx).toBe('#000000')
    expect(lightTokens.bd).toBe('#E9E9E9')
    // --warn is Strata yellow darkened for AA: #FFCB06 measures 2.69:1 against
    // --s1, short of the 4.5:1 text floor at the 9-11px this console renders
    // warn text at. --warn-str keeps the brand value for fills.
    expect(lightTokens.warn).toBe('#7D5E11')
    // Muted to an accent (Henry, 2026-11-13): the DS gold-600, not Strata yellow.
    expect(lightTokens['warn-str']).toBe('#C7AA47')
    expect(lightTokens.crit).toBe('#A51B00')
    expect(lightTokens.info).toBe('#00667B')
    // No orange anywhere in the accent family, and no orange alias left.
    for (const key of ['ac', 'ac-str', 'ac-ink', 'ac-soft', 'ac-line', 'br']) {
      expect(lightTokens[key], `light --${key} must not be orange`).not.toBe('#FA582D')
    }
    expect(lightTokens.orange).toBeUndefined()
    expect(lightTokens['ac-brand']).toBeUndefined()
  })

  it('defines a complete DARK ([data-theme="dark"]) token set on the same contract', () => {
    // Dark surfaces are the DS neutrals verbatim — PANW theme dk1/dk2 plus the
    // template's working greys. The accent and status values are v26 §3's.
    expect(darkTokens.s0).toBe('#000000')
    expect(darkTokens.s1).toBe('#141414')
    expect(darkTokens.ac).toBe('#00CC66')
    expect(darkTokens['ac-tx']).toBe('#0B1F14')
    expect(darkTokens.br).toBe('#00CC66')
    expect(darkTokens.pos).toBe('#00CC66')
    expect(darkTokens.tx).toBe('#FFFFFF')
    expect(darkTokens.tx2).toBe('#C7C7C7')
    expect(darkTokens.bd).toBe('#333333')
    // --crit is coral again: the minimal pass moved it to red because coral read
    // as the ORANGE primary button; with a green primary that collision is gone.
    expect(darkTokens.warn).toBe('#C7AA47')
    // One primary button in both themes: the deep matte green, white text.
    for (const t of [lightTokens, darkTokens]) {
      expect(t.btn).toBe('#0A6231')
      expect(t['btn-tx']).toBe('#FFFFFF')
      expect(t['btn-hover']).toBe('#1E8247')
    }
    expect(darkTokens.crit).toBe('#FDAC96')
    expect(darkTokens.info).toBe('#00C0E8')
    for (const key of ['ac', 'ac-str', 'ac-ink', 'ac-soft', 'ac-line', 'br']) {
      expect(darkTokens[key], `dark --${key} must not be orange`).not.toBe('#FA582D')
    }
    expect(darkTokens.orange).toBeUndefined()
    expect(darkTokens['ac-brand']).toBeUndefined()
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

  it('gives every accent and status ink a text-safe value on cards in BOTH themes', () => {
    // A fill hue is not automatically a text colour: dark --ac (#00CC66) is a
    // fill under --ac-tx, and anything painting accent TEXT uses --ac-ink (same
    // split for --pos / --pos-ink). Light --ac happens to be text-safe already,
    // so the guard is the ratio itself, not "ink differs from fill".
    const lum = (hex) => {
      const c = hex.replace('#', '').match(/../g).map((h) => parseInt(h, 16) / 255)
        .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    }
    const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05) }
    for (const [name, t] of [['light', lightTokens], ['dark', darkTokens]]) {
      for (const key of ['ac-ink', 'pos-ink', 'warn', 'crit', 'info', 'tx2']) {
        expect(ratio(t[key], t.s1), `${name} --${key} on --s1`).toBeGreaterThanOrEqual(4.5)
      }
      expect(ratio(t['ac-tx'], t.ac), `${name} --ac-tx on the primary fill`).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('actually gives light and dark two different palettes (not a copy-paste no-op)', () => {
    // If dark ever regressed to being byte-identical to light, the whole point
    // of shipping two token sets would be silently defeated.
    for (const key of ['s0', 's1', 'tx', 'tx2', 'bd', 'ink', 'warn', 'crit']) {
      expect(darkTokens[key], `--${key} should differ between themes`).not.toBe(lightTokens[key])
    }
    // The BRAND hue does not change with the theme (a brand colour that did
    // would stop being the brand colour). --ac may: light fills with the deep
    // green so its label stays legible.
    expect(darkTokens.br, '--br is the brand hue and must NOT differ by theme').toBe(lightTokens.br)
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
      // `:root, [data-theme] {` — the aliases are re-declared on the themed
      // element so they compute against that element's tokens.
      const themeRootBlock = extractBlock(themeCss, /:root(?:\s*,\s*\[data-theme\])?\s*\{/)
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
