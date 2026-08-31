/**
 * title-typography.test.jsx — T2 repair guard.
 *
 * `.theme-console h1, h2, h3` (cortex-console.css) is (0,1,1) for
 * font-family / font-weight / letter-spacing / line-height / font-size.
 * The earlier repair fixed `color` (by making the WINNING value correct
 * for every theme instead of lowering specificity — see that rule's own
 * comment), but color is the one property where "the shared value is
 * always right" holds: every heading legitimately wants the theme's text
 * color. Typography does not have that property — a destination's own
 * `font: 800 22px/1.15 'Montserrat'` is a deliberate departure from the
 * shared Funnel-Display/500/tight-tracking default, not a bug to
 * overwrite. Four destination title rules style an h1/h2/h3 at bare
 * single-class (0,1,0) and lost every overlapping property to the
 * shared (0,1,1) rule:
 *   - destinations/eal.css:74     .eal-console__title  on <h2> (EalConsole.jsx)
 *   - destinations/uctc.css:66    .uctc__pagehead-title on <h1> (UcTcIndexView.jsx)
 *   - destinations/ttps.css:291   .ttpb-detail__title  on <h3> (TtpBrowserView.jsx)
 *   - destinations/ttps.css:499   .ttpb-modal__title   on <h3> (TtpBrowserView.jsx)
 *
 * Root fix: each destination rule is re-anchored under `.theme-console`
 * (`.theme-console .eal-console__title` etc.), raising it to (0,2,0) —
 * unconditionally ahead of the shared rule's (0,1,1), the same technique
 * already used successfully elsewhere in this codebase (adapters.css,
 * agents.css, ttp-detail.css, uctc.css's other 58 rules) — rather than
 * lowering the shared rule's specificity, which would strip typography
 * from every plain heading that has no destination override at all.
 */
import { describe, it, expect, afterEach } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import '../destinations/uctc.css'
import '../destinations/ttps.css'
import '../destinations/eal.css'
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

const FIXTURES = [
  {
    name: 'EAL Traffic Simulator — h2.eal-console__title (destinations/eal.css)',
    html: `<section class="eal-console">
      <h2 class="eal-console__title"><span class="eal-console__title-accent">EAL</span> Traffic Simulator</h2>
    </section>`,
    selector: '.eal-console__title',
    expect: { fontSize: '22px', fontWeight: '800' },
  },
  {
    name: 'UC / TC Index — h1.uctc__pagehead-title (destinations/uctc.css)',
    html: `<div class="adapter-registry uctc">
      <h1 class="uctc__pagehead-title">UC / TC Index</h1>
    </div>`,
    selector: '.uctc__pagehead-title',
    expect: { fontSize: '25px', fontWeight: '800' },
  },
  {
    name: 'TTP detail rail — h3.ttpb-detail__title (destinations/ttps.css)',
    html: `<div class="ttpb"><h3 class="ttpb-detail__title">Some TTP</h3></div>`,
    selector: '.ttpb-detail__title',
    expect: { fontSize: '17px', fontWeight: '800' },
  },
  {
    name: 'TTP launch-all modal — h3.ttpb-modal__title (destinations/ttps.css)',
    html: `<div class="ttpb"><h3 class="ttpb-modal__title">Launch all citing scenarios</h3></div>`,
    selector: '.ttpb-modal__title',
    expect: { fontSize: '16px', fontWeight: '700' },
  },
]

describe('destination title rules win their own typography, not just color', () => {
  it.each(FIXTURES.map((f) => [f.name, f]))('%s', (_n, fixture) => {
    const shell = mountShell(fixture.html)
    const el = shell.querySelector(fixture.selector)
    expect(el, `could not find "${fixture.selector}" in the fixture`).not.toBeNull()

    const fontSize = resolveProperty(el, 'font-size')
    const fontWeight = resolveProperty(el, 'font-weight')
    expect(
      fontSize,
      `${fixture.name}: resolved font-size="${fontSize}" — the shared ` +
        `".theme-console h1,h2,h3" rule (0,1,1) is still overriding the destination's own (0,1,0) rule`
    ).toBe(fixture.expect.fontSize)
    expect(
      fontWeight,
      `${fixture.name}: resolved font-weight="${fontWeight}" — the shared rule is still winning`
    ).toBe(fixture.expect.fontWeight)
  })
})
