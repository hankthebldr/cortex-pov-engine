/**
 * ds-drift.test.js — the guard on the design↔development seam.
 *
 * `ui/src/styles/ds/` is the PANW Executive Design System's token CSS,
 * vendored verbatim (see docs/design/DESIGN-SYNC.md). `cortex-tokens.css` is
 * this console's own contract — it deliberately does NOT import the DS files,
 * because a console needs dark surfaces, alpha-composited chip fills and
 * measured 4.5:1 text variants a presentation system has no reason to define.
 *
 * What it MUST do is take every brand hue from the DS unchanged. Nothing
 * enforced that before: a DS refresh could move `--panw-orange` and this
 * console would keep painting the old one indefinitely, with the two tracks
 * quietly disagreeing about what the brand color is.
 *
 * So this suite asserts the join, in both directions:
 *   1. every brand hue the console claims to source from the DS resolves to
 *      the DS's own current value, and
 *   2. the console has not started painting chrome in the status hue (the
 *      accent inversion this pass shipped), which is the one drift a value
 *      comparison alone would not catch.
 *
 * Reads raw CSS text rather than rendering: vitest runs with `css: false` and
 * jsdom does not resolve custom-property cascades reliably enough to assert
 * on. Same approach as cortex-tokens.test.js.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const STYLES_DIR = path.resolve(__dirname, '..')
const DS_COLORS = path.join(STYLES_DIR, 'ds', 'colors.css')
const DS_TYPOGRAPHY = path.join(STYLES_DIR, 'ds', 'typography.css')
const TOKENS = path.join(STYLES_DIR, 'cortex-tokens.css')
const CONSOLE_CSS = path.join(STYLES_DIR, 'cortex-console.css')

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** All `--name: value` pairs in a file, last write winning (CSS cascade order). */
function declarations(file) {
  const out = {}
  const re = /--([\w-]+)\s*:\s*([^;]+);/g
  let m
  while ((m = re.exec(stripComments(fs.readFileSync(file, 'utf8'))))) {
    out[m[1]] = m[2].trim()
  }
  return out
}

/** `--name: value` pairs inside the FIRST block matching `selectorRe`. */
function block(file, selectorRe) {
  const css = stripComments(fs.readFileSync(file, 'utf8'))
  const m = selectorRe.exec(css)
  if (!m) return null
  const body = css.slice(m.index + m[0].length, css.indexOf('\n}', m.index))
  const out = {}
  const re = /--([\w-]+)\s*:\s*([^;]+);/g
  let d
  while ((d = re.exec(body))) out[d[1]] = d[2].trim()
  return out
}

const norm = (v) => v.trim().toUpperCase()

describe('design-system drift', () => {
  const ds = declarations(DS_COLORS)
  const light = block(TOKENS, /^:root\s*\{/m)
  const dark = block(TOKENS, /\[data-theme="dark"\]\s*\{/)

  it('vendors the DS token files the sync manifest names', () => {
    for (const f of ['colors.css', 'typography.css', 'spacing.css', 'gradients.css', 'fonts.css']) {
      expect(fs.existsSync(path.join(STYLES_DIR, 'ds', f)), `missing ds/${f}`).toBe(true)
    }
  })

  // The five hues the console actually reproduces. Each pair is
  // [DS token name, the console token that must equal it].
  const SOURCED = [
    ['panw-orange', 'ac', 'chrome accent — the one dominant brand color'],
    ['panw-orange', 'ac-str', 'accent, strong variant'],
    ['panw-green', 'pos-str', 'Cortex green — status only'],
    ['panw-yellow', 'warn-str', 'Strata yellow — warn'],
  ]

  it.each(SOURCED)('--%s from the DS is what --%s paints (%s)', (dsName, ours) => {
    const expected = norm(ds[dsName])
    expect(expected, `ds/colors.css has no --${dsName}`).toBeTruthy()
    // Checked in BOTH themes: a brand hue is a brand hue regardless of surface.
    expect(norm(light[ours]), `light --${ours}`).toBe(expected)
    expect(norm(dark[ours]), `dark --${ours}`).toBe(expected)
  })

  it('keeps the categorical plane tints on DS hues', () => {
    // Plane tints are the DS's own sub-brand hues and tint/shade families.
    // Spot-check the ones that map to a named DS token; the rest are tint
    // families whose values live only in the tint blocks.
    const all = declarations(TOKENS)
    expect(norm(all['plane-edr'])).toBe(norm(ds['panw-cyan']))
    expect(norm(all['plane-itdr'])).toBe(norm(ds['panw-yellow']))
    expect(norm(all['plane-analytics'])).toBe(norm(ds['panw-orange']))
    expect(norm(all['plane-cdr'])).toBe(norm(ds['panw-teal']))
    expect(norm(all['plane-cspm'])).toBe(norm(ds['panw-green-700']))
  })

  it('agrees with the DS that the accent is orange, not the status hue', () => {
    // The DS states it outright; this is the assertion that catches a revert
    // of the inversion, which matching-hex checks alone would not.
    expect(norm(ds['color-accent'])).toBe('VAR(--PANW-ORANGE)')
    expect(norm(light['ac'])).not.toBe(norm(ds['panw-green']))
    expect(norm(dark['ac'])).not.toBe(norm(ds['panw-green']))
  })

  it('uses the DS display family for console display type', () => {
    const dsType = declarations(DS_TYPOGRAPHY)
    const consoleTokens = declarations(CONSOLE_CSS)
    // The DS display stack is Montserrat-first; ours must be too. Exact string
    // equality would fail on our JetBrains Mono addition and on stack ordering
    // below the first family, neither of which is drift.
    const first = (stack) => stack.split(',')[0].replace(/['"]/g, '').trim().toLowerCase()
    expect(first(consoleTokens['font-display'])).toBe(first(dsType['font-display']))
    expect(first(consoleTokens['font-ui'])).toBe(first(dsType['font-body']))
  })
})
