/**
 * console-contrast.test.jsx — the guard the redesign shipped without.
 *
 * `vitest.config.js` runs with `css: false` (CSS imports stubbed to empty
 * strings) everywhere EXCEPT the handful of stylesheets this file imports
 * below (see `test.css.include` in vitest.config.js) — so no OTHER test in
 * this repo has ever rendered a real stylesheet. That is exactly how a
 * 36-agent redesign shipped page titles at ~1.05:1 contrast (near-white
 * on near-white) alongside 701 green tests: nothing ever checked. This
 * file asserts REAL, computed WCAG contrast — using the actual shipped
 * CSS files, actual class names lifted from the actual component JSX —
 * on page titles and primary body text, in BOTH themes.
 *
 * jsdom's own `getComputedStyle` neither resolves `var()` nor respects
 * CSS specificity (verified empirically; see the module doc on
 * src/test/cssCascade.js), so this uses that hand-rolled — but
 * real-stylesheet-driven, real-selector-matching-driven — cascade
 * resolver instead of trusting `getComputedStyle` directly. Contrast
 * itself is plain sRGB relative-luminance arithmetic (src/test/contrastRatio.js),
 * no new dependency.
 */
import { describe, it, expect, afterEach } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import '../destinations/uctc.css'
import '../destinations/ttps.css'
import '../destinations/readiness.css'
import '../destinations/eal.css'
import { resolveProperty } from '../../test/cssCascade.js'
import { contrastRatio, aaFloor } from '../../test/contrastRatio.js'

/** Builds `.theme-console[data-theme?]` > innerHTML, appended to <body>. Returns the shell + a $ query helper. */
function mountShell(innerHTML, { dark = false } = {}) {
  const shell = document.createElement('div')
  shell.className = 'theme-console'
  if (dark) shell.setAttribute('data-theme', 'dark')
  shell.innerHTML = innerHTML
  document.body.appendChild(shell)
  return { shell, $: (sel) => shell.querySelector(sel) }
}

function cleanupAll() {
  document.body.innerHTML = ''
}
afterEach(cleanupAll)

/**
 * Fixtures mirror real markup pulled directly from the mounting component's
 * JSX, not simplified/renamed — so a class-name or structure drift between
 * the component and this guard would itself surface as "element not found"
 * rather than silently testing a stale shape.
 */
const TITLE_FIXTURES = [
  {
    name: 'bare .theme-console h1 (no destination wrapper — the shared cascade rule itself)',
    html: `<h1>Bare Heading</h1>`,
    bgSelector: null, // background comes from .theme-console itself
    titleSelector: 'h1',
    largeText: true, // --fs-display-lg (32px)
  },
  {
    name: 'UC / TC Index — UcTcIndexView.jsx h1.uctc__pagehead-title',
    html: `<div class="adapter-registry uctc" data-testid="uctc-index">
      <div class="uctc__pagehead">
        <div class="uctc__pagehead-bar" aria-hidden="true"></div>
        <div class="uctc__pagehead-eyebrow mono">Analyze · UC / TC Index</div>
        <h1 class="uctc__pagehead-title">UC / TC Index</h1>
      </div>
    </div>`,
    bgSelector: '.uctc',
    titleSelector: '.uctc__pagehead-title',
    largeText: true, // 25px
  },
  {
    name: 'TTP Cards — TtpBrowserView.jsx bare h1 inside .ttpb .view-head',
    html: `<div class="ttpb" data-testid="ttp-browser">
      <div class="view-head"><div><h1>TTP Cards</h1></div></div>
    </div>`,
    bgSelector: '.ttpb',
    titleSelector: '.ttpb h1',
    largeText: true, // .theme-console h1 → --fs-display-lg (32px)
  },
  {
    name: 'Readiness — ReadinessView.jsx bare h1 inside .readiness header.view-head',
    html: `<div class="readiness" data-testid="readiness-view">
      <header class="view-head"><div><h1>Readiness</h1></div></header>
    </div>`,
    bgSelector: '.readiness',
    titleSelector: '.readiness h1',
    largeText: true, // .theme-console h1 → --fs-display-lg (32px)
  },
  {
    name: 'EAL Traffic Simulator — EalConsole.jsx h2.eal-console__title',
    html: `<section class="eal-console">
      <header class="eal-console__head">
        <div class="eal-console__hero">
          <div class="eal-console__heading">
            <h2 class="eal-console__title"><span class="eal-console__title-accent">EAL</span> Traffic Simulator</h2>
          </div>
        </div>
      </header>
    </section>`,
    bgSelector: '.eal-console',
    titleSelector: '.eal-console__title',
    largeText: true, // 800-weight 22px clears the WCAG bold-large threshold (14pt/~18.7px bold)
  },
]

/**
 * Primary body text — deliberately picked from rules that use --tx/--tx2
 * (the design's own readiness.css comment: ">6:1 in both themes"), NOT
 * --tx3/--ac/--ac-str, which are Defect 3 (the designer's light-palette
 * values at small sizes) and are explicitly out of scope for this repair.
 */
const BODY_TEXT_FIXTURES = [
  {
    name: 'UC/TC Index intro prose — .adapter-registry__intro-prose (color: --tx2) on .adapter-registry__intro (bg: --s1)',
    html: `<div class="adapter-registry uctc">
      <div class="adapter-registry__intro">
        <p class="adapter-registry__intro-prose">intro copy</p>
      </div>
    </div>`,
    bgSelector: '.adapter-registry__intro',
    textSelector: '.adapter-registry__intro-prose',
  },
  {
    name: 'TTP Cards intro — .ttpb-intro (color: --tx2) on .ttpb (bg: --s0)',
    html: `<div class="ttpb"><p class="ttpb-intro">intro copy</p></div>`,
    bgSelector: '.ttpb',
    textSelector: '.ttpb-intro',
  },
]

/**
 * Onboarding overlay — TourSpotlight.jsx's bubble, Term.jsx's hover tooltip,
 * and FirstUseHint.jsx (M7). None of `.tour__*`, `.term__*` or
 * `.first-use-hint` appeared anywhere in this guard before — a brand-new
 * full-screen overlay shipping with zero coverage from the very guard this
 * repo built after invisible page titles hid behind 701 green tests.
 *
 * `.tour__bubble` and `.term__tip` both sit on `--cortex-navy` → `--ink`,
 * the "always-dark chrome" surface (dark in BOTH themes, see the CSS
 * comment) — markup mirrors TourSpotlight.jsx / Term.jsx exactly, with the
 * bubble/tip element supplying its own (opaque) background.
 *
 * `.first-use-hint` sits on the ordinary THEMED page background instead —
 * its own `background` is a translucent teal tint (`rgba(0,192,232,.12)`),
 * which `contrastRatio.js::parseColor` deliberately REFUSES to score
 * ("cannot score a translucent color without compositing"), so — same as
 * the bare-`.theme-console` title fixture above — `bgSelector: null` reads
 * the opaque `--c-void` page background the hint actually composites onto.
 */
const TOUR_OVERLAY_FIXTURES = [
  {
    name: 'Tour bubble progress — .tour__progress (color: --ink-tx2) on .tour__bubble (bg: --ink)',
    html: `<div class="tour__bubble">
      <h2 class="tour__title">Deploy one now</h2>
      <p class="tour__body">Mint an enrollment token and run the one-liner.</p>
      <div class="tour__foot"><span class="tour__progress">4 of 5</span></div>
    </div>`,
    bgSelector: '.tour__bubble',
    textSelector: '.tour__progress',
  },
  {
    name: 'Tour bubble body — .tour__body (color: hardcoded #d7e3ec) on .tour__bubble (bg: --ink)',
    html: `<div class="tour__bubble">
      <h2 class="tour__title">Deploy one now</h2>
      <p class="tour__body">Mint an enrollment token and run the one-liner.</p>
    </div>`,
    bgSelector: '.tour__bubble',
    textSelector: '.tour__body',
  },
  {
    name: 'Vocabulary tooltip — .term__tip (color: hardcoded #d7e3ec) on itself (bg: --ink)',
    html: `<span class="term-wrap">
      <span class="term" tabindex="0">MTTD</span>
      <span role="tooltip" class="term__tip">Mean time to detect.</span>
    </span>`,
    bgSelector: '.term__tip',
    textSelector: '.term__tip',
  },
  {
    name: 'First-use hint — .first-use-hint (color: --tx2) on page background (bg: --c-void, bgSelector: null)',
    html: `<span class="first-use-hint" role="note">
      Launch fires the armed scenario.
      <button type="button" class="first-use-hint__x" aria-label="Dismiss hint">×</button>
    </span>`,
    bgSelector: null,
    textSelector: '.first-use-hint',
  },
]

describe.each([
  ['light (default — no [data-theme])', false],
  ['dark ([data-theme="dark"])', true],
])('console contrast — %s', (_label, dark) => {
  it.each(TITLE_FIXTURES.map((f) => [f.name, f]))('title contrast clears AA: %s', (_n, fixture) => {
    const { shell, $ } = mountShell(fixture.html, { dark })
    const titleEl = $(fixture.titleSelector)
    expect(titleEl, `could not find "${fixture.titleSelector}" in the fixture`).not.toBeNull()

    const bgEl = fixture.bgSelector ? $(fixture.bgSelector) : shell
    expect(bgEl, `could not find background element "${fixture.bgSelector}"`).not.toBeNull()

    const color = resolveProperty(titleEl, 'color')
    const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
    expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
    expect(background, `no resolvable background at ${fixture.name}`).toBeTruthy()

    const ratio = contrastRatio(color, background)
    const floor = aaFloor({ largeText: fixture.largeText })
    expect(
      ratio,
      `${fixture.name}: title color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1 for ${fixture.largeText ? 'large' : 'normal'} text`
    ).toBeGreaterThanOrEqual(floor)
  })

  it.each(BODY_TEXT_FIXTURES.map((f) => [f.name, f]))('primary text contrast clears AA: %s', (_n, fixture) => {
    const { $ } = mountShell(fixture.html, { dark })
    const textEl = $(fixture.textSelector)
    const bgEl = $(fixture.bgSelector)
    expect(textEl, `could not find "${fixture.textSelector}" in the fixture`).not.toBeNull()
    expect(bgEl, `could not find background element "${fixture.bgSelector}"`).not.toBeNull()

    const color = resolveProperty(textEl, 'color')
    const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
    const ratio = contrastRatio(color, background)
    const floor = aaFloor({ largeText: false })
    expect(
      ratio,
      `${fixture.name}: text color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1`
    ).toBeGreaterThanOrEqual(floor)
  })

  it.each(TOUR_OVERLAY_FIXTURES.map((f) => [f.name, f]))('onboarding overlay contrast clears AA: %s', (_n, fixture) => {
    const { shell, $ } = mountShell(fixture.html, { dark })
    const textEl = $(fixture.textSelector)
    expect(textEl, `could not find "${fixture.textSelector}" in the fixture`).not.toBeNull()

    // Same null-means-shell convention as the title fixtures above — needed
    // here for `.first-use-hint`, whose own background is translucent and
    // therefore unscoreable directly (see the fixture array's doc comment).
    const bgEl = fixture.bgSelector ? $(fixture.bgSelector) : shell
    expect(bgEl, `could not find background element "${fixture.bgSelector}"`).not.toBeNull()

    const color = resolveProperty(textEl, 'color')
    const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
    expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
    expect(background, `no resolvable background at ${fixture.name}`).toBeTruthy()

    const ratio = contrastRatio(color, background)
    const floor = aaFloor({ largeText: false })
    expect(
      ratio,
      `${fixture.name}: text color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1`
    ).toBeGreaterThanOrEqual(floor)
  })
})
