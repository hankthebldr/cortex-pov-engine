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
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve as resolvePath } from 'node:path'
import { describe, it, expect, afterEach } from 'vitest'
import '../cortex-tokens.css'
import '../cortex-theme.css'
import '../cortex-console.css'
import '../destinations/uctc.css'
import '../destinations/ttps.css'
import '../destinations/readiness.css'
import '../destinations/eal.css'
import '../destinations/adapters.css'
import { resolveProperty, resolveValue } from '../../test/cssCascade.js'
import { contrastRatio, aaFloor, parseColor } from '../../test/contrastRatio.js'

const __dirname = dirname(fileURLToPath(import.meta.url))

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
 * I1 repair: the EAL title fixture used to hardcode `largeText: true`
 * with a comment reasoning from the AUTHORED CSS ("800-weight 22px
 * clears the WCAG bold-large threshold"), not from what actually
 * rendered. T2 found the shared ".theme-console h1,h2,h3" rule was
 * winning over that destination's font-size/font-weight, so the real
 * resolved text was 20px/500 — NOT large text, and this guard was
 * applying the 3:1 floor to something that needed 4.5:1. Worse: because
 * that same shared rule wins `color` on every title fixture (by design —
 * see that rule's comment), every fixture passed for the same one
 * reason, so a 6th destination would have added no real coverage.
 * Deriving largeText from the resolved cascade this file already
 * computes closes both holes at once.
 */
function parsePx(value) {
  const m = String(value ?? '').trim().match(/^(-?[\d.]+)px$/)
  return m ? parseFloat(m[1]) : null
}

/** WCAG 1.4.3: large text is >=24px, or >=18.66px (~14pt) at >=700 weight. */
function isLargeText(el) {
  const px = parsePx(resolveProperty(el, 'font-size'))
  if (px == null) return false
  const rawWeight = resolveProperty(el, 'font-weight')
  const weight = rawWeight === 'bold' ? 700 : parseInt(rawWeight, 10) || 400
  return px >= 24 || (px >= 18.66 && weight >= 700)
}

/**
 * I1 repair: chip variants (`.chip--detected` etc.) paint their color as
 * a TRANSLUCENT tint (e.g. `background: var(--c-detected-soft)` =
 * `rgba(79,209,161,.14)`) over whatever their ancestor's real opaque
 * background is — that's the "composited chips are worse" case C1
 * measured at 1.67:1. `contrastRatio.js::parseColor` deliberately
 * refuses to score a translucent color directly ("cannot score a
 * translucent color without compositing"), so this composites it by
 * hand against a real resolved ancestor background first, then hands an
 * opaque hex to `contrastRatio` — same "resolve the real cascade, then
 * plain arithmetic" split the rest of this file uses; `parseColor` is
 * reused here only for the (already-opaque) ancestor color.
 */
function compositeOverAncestor(foreground, ancestorBackground) {
  const m = String(foreground)
    .trim()
    .match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/)
  if (!m) return foreground // already opaque (hex or rgb()) — nothing to composite
  const alpha = m[4] == null ? 1 : parseFloat(m[4])
  if (alpha >= 0.99) return foreground
  const [ar, ag, ab] = parseColor(ancestorBackground)
  const blend = (fg, bg) => Math.round(fg * alpha + bg * (1 - alpha))
  const toHex = (n) => n.toString(16).padStart(2, '0')
  return `#${toHex(blend(parseFloat(m[1]), ar))}${toHex(blend(parseFloat(m[2]), ag))}${toHex(blend(parseFloat(m[3]), ab))}`.toUpperCase()
}

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
  },
  {
    name: 'TTP Cards — TtpBrowserView.jsx bare h1 inside .ttpb .view-head',
    html: `<div class="ttpb" data-testid="ttp-browser">
      <div class="view-head"><div><h1>TTP Cards</h1></div></div>
    </div>`,
    bgSelector: '.ttpb',
    titleSelector: '.ttpb h1',
  },
  {
    name: 'Readiness — ReadinessView.jsx bare h1 inside .readiness header.view-head',
    html: `<div class="readiness" data-testid="readiness-view">
      <header class="view-head"><div><h1>Readiness</h1></div></header>
    </div>`,
    bgSelector: '.readiness',
    titleSelector: '.readiness h1',
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

/**
 * I1 repair — the C1 blast radius this guard was structurally blind to:
 * 175 `color:` declarations in cortex-console.css alone read the 8
 * `--c-*` foregrounds (action / action-bright / action-deep / signal /
 * detected / missed / pending / stitched) C1 found hardcoded to a
 * single dark-tuned hex with no [data-theme] variant — measuring
 * 1.57-2.50:1 in light theme as plain text and down to 1.67:1 for
 * `.chip--detected` composited into its own soft background. Every
 * fixture above exercises `color: var(--tx)`/`--tx2` or a hardcoded
 * `--ink`-surface pairing — NONE of them touch `--c-signal`,
 * `--c-detected`, `--c-missed`, or `--c-pending`, so this guard could
 * not have caught C1 even at its strictest. These three groups —
 * detection-state chips (translucent soft background, needs
 * `compositeOverAncestor`), links (`.theme-console a`, 69 of the 175),
 * and status values (`.tel-value--*`, opaque, real telemetry markup) —
 * close that hole.
 */
const CHIP_FIXTURES = [
  {
    name: 'Detected chip — .chip.chip--detected on a raised card (TtpBrowserView.jsx .ttpb-stat, bg: --s2)',
    variant: 'detected',
  },
  {
    name: 'Missed chip — .chip.chip--missed on a raised card (TtpBrowserView.jsx .ttpb-stat, bg: --s2)',
    variant: 'missed',
  },
  {
    name: 'Pending chip — .chip.chip--pending on a raised card (TtpBrowserView.jsx .ttpb-stat, bg: --s2)',
    variant: 'pending',
  },
]

const LINK_FIXTURES = [
  {
    name: 'Console link — .theme-console a (color: --c-signal) inside body copy',
    html: `<p>See <a href="#">the run detail</a> for more.</p>`,
    bgSelector: null, // page background (walks up to <body>, same convention as the bare-h1 title fixture)
    textSelector: 'a',
  },
]

const STATUS_VALUE_FIXTURES = [
  {
    name: 'Telemetry status — .tel-value--signal (TelemetryStrip.jsx) on .telemetry (bg: --c-surface)',
    variant: 'signal',
  },
  {
    name: 'Telemetry status — .tel-value--detected (TelemetryStrip.jsx) on .telemetry (bg: --c-surface)',
    variant: 'detected',
  },
  {
    name: 'Telemetry status — .tel-value--pending (TelemetryStrip.jsx) on .telemetry (bg: --c-surface)',
    variant: 'pending',
  },
]

/**
 * AA light-theme repair (2026-08-31) — the five pairs a manual audit
 * measured failing in light theme, each pinned to the real selector
 * that actually carries it in the shipped console (not a synthetic
 * token pairing). Restricted to the destinations vitest.config.js
 * already gives real CSS to (uctc/ttps/readiness/eal/adapters — see
 * that file's `css.include`); a fixture built from any other
 * destination's stylesheet would resolve against STUBBED (empty) CSS
 * and pass for the wrong reason no matter what the real rule says:
 *   - .uctc__link            color: --ac  -> --ac-ink   (was 3.12:1 on --s1)
 *   - .ttpb-badge--pass      color: --ac -> --ac-ink, on its own
 *                            --ac-soft fill (the "accent chip" case —
 *                            same defect family as --ac-str on
 *                            --ac-soft, measured 1.90:1)
 *   - .ttpb-row__count       color: --tx3 (cortex-tokens.css deviation:
 *                            #8B958F -> #656F69)          (was 2.77:1 on --s0)
 *   - .uctc__tone-pending    color: --warn (cortex-tokens.css deviation:
 *                            #C7961B -> #896713)          (was 2.69:1 on --s1)
 *   - .ttpb-btn--accent      color: --ac-tx on a fill that used to be
 *                            --ac, now --ac-ink            (was 3.12:1)
 * `bgSelector: 'self'` reads background off the SAME element as the
 * text (the chip/button paint their own fill) instead of an ancestor.
 * Dark theme is exercised by the same describe.each this file already
 * runs everything through: --ac-ink aliases straight to dark's own
 * --ac (already >=7:1, see cortex-tokens.css), and dark --tx3/--warn
 * are untouched designer values the operator's audit already found
 * passing — so this block proves the fix without needing separate
 * dark fixtures, and a regression in either theme still fails here.
 */
const AA_LIGHT_REPAIR_FIXTURES = [
  {
    name: 'UC/TC link text — .uctc__link (color: --ac-ink, was --ac) on .uctc (bg: --s0)',
    html: `<div class="uctc"><a class="uctc__link" href="#">TC-EDR-05</a></div>`,
    bgSelector: '.uctc',
    textSelector: '.uctc__link',
  },
  {
    name: 'TTP Cards "pass" accent badge — .ttpb-badge--pass (color: --ac-ink, was --ac) on itself (bg: --ac-soft)',
    html: `<span class="ttpb-badge--pass">Pass</span>`,
    bgSelector: 'self',
    textSelector: '.ttpb-badge--pass',
  },
  {
    name: 'TTP row-count eyebrow — .ttpb-row__count (color: --tx3, darkened) on .ttpb (bg: --s0)',
    html: `<div class="ttpb"><span class="ttpb-row__count">12</span></div>`,
    bgSelector: '.ttpb',
    textSelector: '.ttpb-row__count',
  },
  {
    name: 'UC/TC "pending" tone label — .uctc__tone-pending (color: --warn, darkened) on .uctc (bg: --s0)',
    html: `<div class="uctc"><span class="uctc__tone-pending">Pending</span></div>`,
    bgSelector: '.uctc',
    textSelector: '.uctc__tone-pending',
  },
  {
    name: 'TTP Cards accent button label — .ttpb-btn--accent (color: --ac-tx on a fill that is now --ac-ink, was --ac) on itself',
    html: `<button class="ttpb-btn--accent">Launch</button>`,
    bgSelector: 'self',
    textSelector: '.ttpb-btn--accent',
  },
]

/**
 * D-1 repair (2026-08-31) — the seven `color: var(--orange)` sites in
 * destinations/adapters.css, now `color: var(--orange-ink)`. These are
 * not decorative: `.launch-blockers__item` and `.payload-compose__warn`
 * render PAYLOAD_NOT_STAGED / compose-not-airgapped — the exact strings
 * telling a consultant "this tool never reached the target, the step
 * will run and produce nothing" — and an unreadable warning here is how
 * a DC ships a POV report showing an absent detection as "Cortex missed
 * it": a manufactured false negative on the customer's own stack. All
 * seven measured 2.80-3.23:1 against light --s0..--s3 before the fix
 * (below the 4.5:1 AA floor); --orange-ink clears 4.52-5.21:1 — see
 * cortex-tokens.css's --orange-ink block for the full table. Every
 * fixture is wrapped in `.tools-destination` because every rule in
 * adapters.css is anchored `.theme-console .tools-destination ...`
 * (see that file's header comment) — without the wrapper these fixtures
 * would silently resolve against a DIFFERENT, unscoped rule (or none)
 * and prove nothing. Two of the seven (`.chip--pending`,
 * `.launch-blockers__item`/`.payload-compose__warn`) sit on a
 * TRANSPARENT own-background — adapters.css's `.chip--pending` is a
 * plain outline chip (`background: transparent`), not the translucent
 * soft-tint fill `CHIP_FIXTURES` above composites — so these fixtures
 * read `bgSelector` off the real opaque ANCESTOR directly, same
 * convention as `AA_LIGHT_REPAIR_FIXTURES`, rather than compositing.
 * The two `border-color: var(--orange)` sites on the SAME rules
 * (adapters.css:115,382 as of this writing) are deliberately NOT
 * touched here or in the source — borders need the 3:1 non-text floor,
 * which --orange already clears (7.02-7.62:1), and this guard only
 * ever asserts `color:`.
 */
const ADAPTERS_ORANGE_WARN_FIXTURES = [
  {
    name: 'Payload shelf banner tag — .payload-banner__tag on .payload-banner (bg: --s1)',
    html: `<div class="tools-destination"><div class="payload-banner"><span class="payload-banner__tag mono">PAYLOAD SHELF</span></div></div>`,
    bgSelector: '.payload-banner',
    textSelector: '.payload-banner__tag',
  },
  {
    name: 'Pending chip — .chip.chip--pending.adapter-card__unpinned on .adapter-card (bg: --s1, chip bg: transparent)',
    html: `<div class="tools-destination"><div class="adapter-card"><span class="chip chip--pending adapter-card__unpinned">UNPINNED</span></div></div>`,
    bgSelector: '.adapter-card',
    textSelector: '.chip--pending',
  },
  {
    name: 'Adapter schema type annotation — .adapter-schema__type on .adapter-schema (bg: --s2)',
    html: `<div class="tools-destination"><div class="adapter-schema"><div class="adapter-schema__row"><div class="adapter-schema__type mono">string</div></div></div></div>`,
    bgSelector: '.adapter-schema',
    textSelector: '.adapter-schema__type',
  },
  {
    name: 'Provenance unpinned warning — .provenance__unpinned on .tools-destination (bg: --s0)',
    html: `<div class="tools-destination"><div class="provenance"><span class="mono provenance__unpinned">no — upstream can change under you between engagements</span></div></div>`,
    bgSelector: '.tools-destination',
    textSelector: '.provenance__unpinned',
  },
  {
    name: 'Stage dialog unpinned warning — .stage-dialog__warn on .confirm-dialog.stage-dialog (bg: --s1)',
    html: `<div class="tools-destination"><div class="confirm-dialog stage-dialog"><p class="stage-dialog__warn">This pack declares no sha256.</p></div></div>`,
    bgSelector: '.confirm-dialog',
    textSelector: '.stage-dialog__warn',
  },
  {
    name: 'Launch blocker — .launch-blockers__item (renders PAYLOAD_NOT_STAGED / a dead-Launch reason) on .tools-destination (bg: --s0)',
    html: `<div class="tools-destination"><ul class="launch-blockers"><li class="launch-blockers__item">PAYLOAD_NOT_STAGED</li></ul></div>`,
    bgSelector: '.tools-destination',
    textSelector: '.launch-blockers__item',
  },
  {
    name: 'Payload composer warning — .payload-compose__warn (renders compose-not-airgapped) on .tools-destination (bg: --s0)',
    html: `<div class="tools-destination"><div class="payload-compose"><p class="payload-compose__warn">not air-gapped</p></div></div>`,
    bgSelector: '.tools-destination',
    textSelector: '.payload-compose__warn',
  },
]

/**
 * D-2 repair (2026-08-31) — the vacuity guard. `vitest.config.js`'s
 * `css.include` CLAIMS real (non-stubbed) CSS for exactly five
 * destination stylesheets (uctc/ttps/readiness/eal/adapters); this test
 * file is the only thing that can make that claim true, by both
 * importing each one AND actually exercising a rule only that file
 * defines. `adapters.css` was in the config's claim but never imported
 * here — the guard's own scope statement was already wrong, silently,
 * which is the same defect class as everything else this file exists
 * to catch. The two checks below close both directions of that hole:
 *
 *  1. "claims == imports" — parses vitest.config.js's own `css.include`
 *     regex source and this file's own `import` statements and asserts
 *     the destination-name sets are IDENTICAL. A future PR that widens
 *     the config's claim without adding an import (or vice versa) fails
 *     here instead of shipping a silently-vacuous claim again.
 *  2. "imports == real CSS" — for each claimed destination, mounts a
 *     canary selector THAT FILE (and only that file) sets `color:` on,
 *     and asserts the resolved value is EXACTLY the token that rule
 *     names — not merely "truthy". Truthy is not enough: `.theme-console`
 *     itself sets `color: var(--c-text)` (= --tx), so if e.g. adapters.css
 *     were stubbed to empty, `.adapter-schema__type`'s color would still
 *     resolve to something non-null (the inherited --tx) and a bare
 *     `toBeTruthy()` would pass for the wrong reason — silently vacuous,
 *     same failure shape as the missing import above. Every canary
 *     below is deliberately picked on a token that ISN'T --tx/--c-text
 *     (the one thing every element inherits for free) so a stub is
 *     forced to disagree with the expected value, not coincidentally
 *     match it.
 */
const DESTINATION_CANARIES = {
  uctc: {
    html: `<div class="uctc"><div class="uctc__pagehead-eyebrow mono">Analyze</div></div>`,
    selector: '.uctc__pagehead-eyebrow',
    expectedVar: '--tx3',
  },
  ttps: {
    html: `<div class="ttpb"><p class="ttpb-intro">intro copy</p></div>`,
    selector: '.ttpb-intro',
    expectedVar: '--tx2',
  },
  readiness: {
    html: `<div class="readiness"><span class="readiness__down-tag">DOWN</span></div>`,
    selector: '.readiness__down-tag',
    expectedVar: '--crit',
  },
  eal: {
    html: `<section class="eal-console"><div class="eal-console__eyebrow">EAL</div></section>`,
    selector: '.eal-console__eyebrow',
    expectedVar: '--tx2',
  },
  adapters: {
    html: `<div class="tools-destination"><div class="adapter-schema"><div class="adapter-schema__row"><div class="adapter-schema__type mono">string</div></div></div></div>`,
    selector: '.adapter-schema__type',
    expectedVar: '--orange-ink',
  },
}

/** Destination names this test file's own top-level `import "../destinations/<name>.css"` lines actually load. */
function importedDestinations() {
  const src = readFileSync(fileURLToPath(import.meta.url), 'utf8')
  const re = /^import ['"]\.\.\/destinations\/([\w-]+)\.css['"]/gm
  const out = new Set()
  let m
  while ((m = re.exec(src))) out.add(m[1])
  return out
}

/** Destination names `vitest.config.js`'s `css.include` regex CLAIMS to give real CSS. */
function claimedDestinations() {
  const configPath = resolvePath(__dirname, '../../../vitest.config.js')
  const src = readFileSync(configPath, 'utf8')
  const m = src.match(/destinations\\\/\(([^)]+)\)\\\.css\$\//)
  if (!m) {
    throw new Error(
      'console-contrast guard coverage check: could not find the destinations include regex in ' +
        'vitest.config.js — its shape changed, and this check needs updating alongside it rather ' +
        'than being silently skipped.'
    )
  }
  return new Set(m[1].split('|'))
}

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
    const large = isLargeText(titleEl)
    const floor = aaFloor({ largeText: large })
    expect(
      ratio,
      `${fixture.name}: title color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1 for ${large ? 'large' : 'normal'} text ` +
        `(resolved font-size=${resolveProperty(titleEl, 'font-size')}, font-weight=${resolveProperty(titleEl, 'font-weight')})`
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

  it.each(CHIP_FIXTURES.map((f) => [f.name, f]))('detection-state chip contrast clears AA: %s', (_n, fixture) => {
    const { $ } = mountShell(
      `<div class="ttpb-stat"><span class="chip chip--${fixture.variant}">${fixture.variant}</span></div>`,
      { dark }
    )
    const chipEl = $('.chip')
    const ancestorEl = $('.ttpb-stat')
    expect(chipEl, `could not find ".chip--${fixture.variant}" in the fixture`).not.toBeNull()
    expect(ancestorEl, `could not find ".ttpb-stat" in the fixture`).not.toBeNull()

    const color = resolveProperty(chipEl, 'color')
    const chipBackground = resolveProperty(chipEl, 'background') ?? resolveProperty(chipEl, 'background-color')
    const ancestorBackground =
      resolveProperty(ancestorEl, 'background') ?? resolveProperty(ancestorEl, 'background-color')
    expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
    expect(chipBackground, `no resolvable chip background at ${fixture.name}`).toBeTruthy()
    expect(ancestorBackground, `no resolvable ancestor background at ${fixture.name}`).toBeTruthy()

    // The chip's own background is a translucent tint of the SAME variant
    // color as the text — composited against the ancestor's opaque
    // surface before scoring, per this file's compositeOverAncestor doc.
    const opaqueBackground = compositeOverAncestor(chipBackground, ancestorBackground)
    const ratio = contrastRatio(color, opaqueBackground)
    const floor = aaFloor({ largeText: isLargeText(chipEl) })
    expect(
      ratio,
      `${fixture.name}: chip text ${color} on composited background ${opaqueBackground} ` +
        `(chip bg ${chipBackground} over ancestor ${ancestorBackground}) measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1`
    ).toBeGreaterThanOrEqual(floor)
  })

  it.each(LINK_FIXTURES.map((f) => [f.name, f]))('link contrast clears AA: %s', (_n, fixture) => {
    const { shell, $ } = mountShell(fixture.html, { dark })
    const linkEl = $(fixture.textSelector)
    expect(linkEl, `could not find "${fixture.textSelector}" in the fixture`).not.toBeNull()

    const bgEl = fixture.bgSelector ? $(fixture.bgSelector) : shell
    expect(bgEl, `could not find background element "${fixture.bgSelector}"`).not.toBeNull()

    const color = resolveProperty(linkEl, 'color')
    const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
    expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
    expect(background, `no resolvable background at ${fixture.name}`).toBeTruthy()

    const ratio = contrastRatio(color, background)
    const floor = aaFloor({ largeText: isLargeText(linkEl) })
    expect(
      ratio,
      `${fixture.name}: link color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1`
    ).toBeGreaterThanOrEqual(floor)
  })

  it.each(STATUS_VALUE_FIXTURES.map((f) => [f.name, f]))('status value contrast clears AA: %s', (_n, fixture) => {
    const { $ } = mountShell(
      `<div class="telemetry"><span class="tel-value mono tel-value--${fixture.variant}">value</span></div>`,
      { dark }
    )
    const valueEl = $(`.tel-value--${fixture.variant}`)
    const bgEl = $('.telemetry')
    expect(valueEl, `could not find ".tel-value--${fixture.variant}" in the fixture`).not.toBeNull()
    expect(bgEl, `could not find ".telemetry" in the fixture`).not.toBeNull()

    const color = resolveProperty(valueEl, 'color')
    const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
    expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
    expect(background, `no resolvable background at ${fixture.name}`).toBeTruthy()

    const ratio = contrastRatio(color, background)
    const floor = aaFloor({ largeText: isLargeText(valueEl) })
    expect(
      ratio,
      `${fixture.name}: status value ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
        `below the WCAG AA floor of ${floor}:1`
    ).toBeGreaterThanOrEqual(floor)
  })

  it.each(AA_LIGHT_REPAIR_FIXTURES.map((f) => [f.name, f]))(
    'AA light-theme repair contrast clears AA: %s',
    (_n, fixture) => {
      const { shell, $ } = mountShell(fixture.html, { dark })
      const textEl = $(fixture.textSelector)
      expect(textEl, `could not find "${fixture.textSelector}" in the fixture`).not.toBeNull()

      const bgEl = fixture.bgSelector === 'self' ? textEl : $(fixture.bgSelector)
      expect(bgEl, `could not find background element "${fixture.bgSelector}"`).not.toBeNull()

      const color = resolveProperty(textEl, 'color')
      const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
      expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
      expect(background, `no resolvable background at ${fixture.name}`).toBeTruthy()

      const ratio = contrastRatio(color, background)
      const floor = aaFloor({ largeText: isLargeText(textEl) })
      expect(
        ratio,
        `${fixture.name}: color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
          `below the WCAG AA floor of ${floor}:1`
      ).toBeGreaterThanOrEqual(floor)
    }
  )

  it.each(ADAPTERS_ORANGE_WARN_FIXTURES.map((f) => [f.name, f]))(
    'payload-blocker warning contrast clears AA (D-1, --orange-ink): %s',
    (_n, fixture) => {
      const { $ } = mountShell(fixture.html, { dark })
      const textEl = $(fixture.textSelector)
      expect(textEl, `could not find "${fixture.textSelector}" in the fixture`).not.toBeNull()

      const bgEl = fixture.bgSelector === 'self' ? textEl : $(fixture.bgSelector)
      expect(bgEl, `could not find background element "${fixture.bgSelector}"`).not.toBeNull()

      const color = resolveProperty(textEl, 'color')
      const background = resolveProperty(bgEl, 'background') ?? resolveProperty(bgEl, 'background-color')
      expect(color, `no resolvable color at ${fixture.name}`).toBeTruthy()
      expect(background, `no resolvable background at ${fixture.name}`).toBeTruthy()

      const ratio = contrastRatio(color, background)
      const floor = aaFloor({ largeText: isLargeText(textEl) })
      expect(
        ratio,
        `${fixture.name}: color ${color} on background ${background} measures ${ratio.toFixed(2)}:1, ` +
          `below the WCAG AA floor of ${floor}:1`
      ).toBeGreaterThanOrEqual(floor)
    }
  )

  it.each(Object.entries(DESTINATION_CANARIES))(
    'D-2 vacuity guard — %s.css is really loaded, not stubbed to empty CSS',
    (destName, canary) => {
      const { shell, $ } = mountShell(canary.html, { dark })
      const el = $(canary.selector)
      expect(
        el,
        `${destName}.css canary: could not find "${canary.selector}" in the fixture markup`
      ).not.toBeNull()

      const expected = resolveValue(shell, `var(${canary.expectedVar})`)
      const actual = resolveProperty(el, 'color')
      expect(
        actual,
        `${destName}.css canary: "${canary.selector}" resolved color is ${actual}, expected the ` +
          `real ${destName}.css rule to set it to ${canary.expectedVar} (${expected}). A mismatch ` +
          `means ${destName}.css did not actually apply here — it is either not imported or was ` +
          `stubbed to empty CSS, and this destination's contrast fixtures would be passing for the ` +
          `wrong reason (or not running at all).`
      ).toBe(expected)
    }
  )
})

/**
 * D-2 repair — runs once (not per-theme; it is a static source-file
 * check, not a rendering one). Asserts vitest.config.js's `css.include`
 * claim and this test file's own imports name the EXACT same set of
 * destination stylesheets, and that every claimed/imported destination
 * also has a DESTINATION_CANARIES entry — so a sheet can be added to
 * one without the others (silently vacuous) only by failing this test.
 */
describe('console contrast — guard coverage integrity (D-2)', () => {
  it('vitest.config.js css.include and this file\'s imports name the same destinations', () => {
    const claimed = claimedDestinations()
    const imported = importedDestinations()
    const claimedOnly = [...claimed].filter((d) => !imported.has(d)).sort()
    const importedOnly = [...imported].filter((d) => !claimed.has(d)).sort()
    expect(
      claimedOnly,
      `vitest.config.js's css.include claims real CSS for [${claimedOnly.join(', ')}] but this ` +
        `test file does not import ${claimedOnly.length === 1 ? 'it' : 'them'} — that destination ` +
        `would be stubbed to empty CSS and every fixture on it would pass for the wrong reason.`
    ).toEqual([])
    expect(
      importedOnly,
      `this test file imports [${importedOnly.join(', ')}] but vitest.config.js's css.include does ` +
        `not claim ${importedOnly.length === 1 ? 'it' : 'them'} — the import is stubbed to empty CSS ` +
        `by the default css:false config regardless of the import statement being present.`
    ).toEqual([])
  })

  it('every claimed/imported destination has a DESTINATION_CANARIES coverage-integrity entry', () => {
    const claimed = claimedDestinations()
    const covered = new Set(Object.keys(DESTINATION_CANARIES))
    const missingCanary = [...claimed].filter((d) => !covered.has(d)).sort()
    const staleCanary = [...covered].filter((d) => !claimed.has(d)).sort()
    expect(
      missingCanary,
      `destinations [${missingCanary.join(', ')}] are claimed real CSS but have no ` +
        `DESTINATION_CANARIES entry proving it — add one so a future stub is caught rather than ` +
        `passing silently.`
    ).toEqual([])
    expect(
      staleCanary,
      `DESTINATION_CANARIES has an entry for [${staleCanary.join(', ')}], which vitest.config.js no ` +
        `longer claims real CSS for — remove the stale entry (or restore the claim).`
    ).toEqual([])
  })
})
