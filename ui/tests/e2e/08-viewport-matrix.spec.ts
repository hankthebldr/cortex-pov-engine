import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Viewport-matrix layout guard — the console must survive a narrow window.
 *
 * WHY THIS EXISTS AS AN E2E SPEC AND NOT A UNIT TEST
 * --------------------------------------------------
 * The unit suite is vitest + jsdom, and **jsdom has no layout engine**:
 * `getBoundingClientRect()` returns zeros for everything. Every layout defect
 * below shipped past a fully green 1128-test unit suite, because that suite is
 * structurally incapable of failing on geometry. Four separate instances were
 * fixed by hand in one session (2026-09-06) — a rail that clipped below its
 * ~760px floor, two sidebars 159px out of vertical alignment, a header infocard
 * that overflowed instead of condensing, and chrome eating 70% of the viewport
 * height. Each was invisible to CI. This spec is the thing that makes the next
 * one a red build instead of a screenshot in chat.
 *
 * WHAT COUNTS AS A DEFECT (read before adding assertions)
 * ------------------------------------------------------
 * Overflow is NOT the bug. Content wider than the viewport is legitimate when
 * it is reachable — a wide table in an `overflow-x: auto` container is fine and
 * deliberate. It becomes a defect when the nearest clipping ancestor is
 * `overflow: hidden`, because then the content is neither visible NOR
 * scrollable: it is silently gone.
 *
 * That distinction is the whole reason these bugs survived review. `.shell`,
 * `.main` and `.view` all clip horizontally, so `scrollWidth === clientWidth`
 * and `document.scrollLeft` stays 0 — every ordinary overflow probe returns a
 * clean zero while content is missing. Assertions here therefore check
 * LANDMARK GEOMETRY against the viewport, not scroll extents.
 *
 * Landmarks rather than a whole-DOM sweep is also deliberate: a sweep flags
 * intentionally-parked elements (the closed ScenarioInspector sits 420px off
 * the right edge behind `translateX(100%)`, which `.view`'s `overflow-x:
 * hidden` exists to clip) and would be quietly weakened by an allowlist until
 * it proved nothing.
 */

/** Widths that matter, including the one actually reported broken (845). */
const MATRIX = [
  { w: 1920, h: 1080, note: 'wide desktop, above every breakpoint' },
  { w: 1600, h: 900,  note: 'desktop' },
  { w: 1440, h: 900,  note: 'common laptop' },
  { w: 1280, h: 800,  note: 'small laptop' },
  { w: 1024, h: 768,  note: 'below the reflow breakpoint (1000px rail collapse)' },
  { w: 900,  h: 700,  note: 'below the filter-rail stack breakpoint (860px)' },
  { w: 845,  h: 620,  note: 'the window this was actually reported broken in' },
]

/**
 * Landmarks that must never be clipped. Each earned its place by breaking:
 * `.header__right` is the tenant/agent/run infocard that overflowed behind
 * `flex-shrink: 0`; `.rail--nav` is the sidebar that scrolled out of view;
 * `.main` is the column that could not shrink without `min-width: 0`.
 */
const LANDMARKS = [
  '.header',
  '.header__right',
  '.phase-bar',
  '.workspace',
  '.rail--nav',
  '.main',
  '.view',
  '.command-strip',
]

type Box = { sel: string; left: number; right: number; width: number; clippedBy: string | null }

/**
 * Measure landmark geometry and, for anything exceeding the viewport, report
 * whether the nearest horizontal-overflow ancestor makes it reachable.
 */
async function measure(
  page: Page,
  selectors: string[] = LANDMARKS,
): Promise<{ vw: number; boxes: Box[]; docScrollable: boolean }> {
  return page.evaluate((sels: string[]) => {
    const vw = document.documentElement.clientWidth
    const boxes = sels.map((sel) => {
      const el = document.querySelector(sel) as HTMLElement | null
      if (!el) return { sel, left: 0, right: 0, width: 0, clippedBy: null }
      const r = el.getBoundingClientRect()
      let clippedBy: string | null = null
      if (r.right > vw + 1 || r.left < -1) {
        // Walk up to the nearest ancestor that either scrolls (reachable) or
        // clips (unreachable). The first one found decides.
        let n: HTMLElement | null = el.parentElement
        clippedBy = 'viewport'
        while (n) {
          const ox = getComputedStyle(n).overflowX
          if (ox === 'auto' || ox === 'scroll') { clippedBy = null; break }
          if (ox === 'hidden') { clippedBy = n.className || n.tagName; break }
          n = n.parentElement
        }
      }
      return {
        sel,
        left: Math.round(r.left),
        right: Math.round(r.right),
        width: Math.round(r.width),
        clippedBy,
      }
    })
    const d = document.documentElement
    return { vw, boxes, docScrollable: d.scrollWidth > d.clientWidth + 1 }
  }, selectors)
}

for (const { w, h, note } of MATRIX) {
  test(`layout holds at ${w}x${h} — ${note}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h })
    await page.goto('/#/library')
    await page.waitForSelector('.rail--nav', { timeout: 15_000 })
    // Let the rail's width transition settle; measuring mid-transition reports
    // an intermediate width and produces a confusing failure.
    await page.waitForTimeout(500)

    const { vw, boxes } = await measure(page)

    const clipped = boxes.filter((b) => b.clippedBy)
    expect(
      clipped,
      `landmarks are clipped and unreachable at ${w}px (not scrollable — silently gone):\n` +
        clipped.map((b) => `  ${b.sel} spans ${b.left}..${b.right} in a ${vw}px viewport, clipped by ${b.clippedBy}`).join('\n'),
    ).toEqual([])

    // The nav rail is the specific regression that kept recurring: when the
    // page could be scrolled or shifted, the rail left the screen and its
    // labels appeared "cut off", which reads as a spacing bug.
    const rail = boxes.find((b) => b.sel === '.rail--nav')!
    expect(rail.left, `nav rail must stay pinned at x=0 (was ${rail.left})`).toBe(0)
    expect(rail.width, 'nav rail must have width').toBeGreaterThan(0)
  })
}

test('the document never scrolls horizontally — chrome must reflow, not shift', async ({ page }) => {
  // If the document itself scrolls, the nav rail and header scroll away with
  // it, which is what produced "the sidebar labels are cut off" reports.
  for (const { w, h } of MATRIX) {
    await page.setViewportSize({ width: w, height: h })
    await page.goto('/#/library')
    await page.waitForSelector('.rail--nav')
    await page.waitForTimeout(300)
    const scrolled = await page.evaluate(() => {
      const d = document.documentElement
      d.scrollLeft = 500                    // try to force it
      const moved = d.scrollLeft
      d.scrollLeft = 0
      return { moved, scrollW: d.scrollWidth, clientW: d.clientWidth }
    })
    expect(scrolled.moved, `document scrolled horizontally at ${w}px`).toBe(0)
    expect(
      scrolled.scrollW,
      `document is wider than its viewport at ${w}px`,
    ).toBeLessThanOrEqual(scrolled.clientW + 1)
  }
})

test('the two rails share a top — a global notice must not push only one of them', async ({ page }) => {
  // The console shows two vertical rails side by side: the shell's nav rail,
  // pinned to the top of `.workspace`, and a per-view filter rail that lives
  // INSIDE `.view`. Anything rendered inside `.view` above the view's own
  // content therefore pushes the filter rail down while the nav rail stays put.
  //
  // That is exactly what a global notice did: the SimCore health banner was
  // passed as an AppShell CHILD (so it landed inside `.view`) rather than into
  // the shell's `banner` slot, leaving the two rails 159px out of alignment at
  // 1460px. It reads as "the nav bar bleeds into the page above" — a spacing
  // fault — which is why it was repeatedly chased in CSS instead of in the tree.
  //
  // The tolerance is `.view`'s own padding, not zero: the filter rail is inside
  // a padded scroll container and legitimately starts slightly lower.
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await page.waitForSelector('.rail--filter')
  await page.waitForTimeout(400)

  const tops = await page.evaluate(() => {
    const t = (s: string) => {
      const e = document.querySelector(s) as HTMLElement | null
      return e ? Math.round(e.getBoundingClientRect().top) : null
    }
    const banner = document.querySelector('.readiness-banner')
    const view = document.querySelector('.view')
    return {
      nav: t('.rail--nav'),
      filter: t('.rail--filter'),
      // A global notice inside the scrolling view is the mechanism, so name it.
      bannerInsideView: banner ? !!view?.contains(banner) : false,
    }
  })

  expect(tops.nav, '.rail--nav not found').not.toBeNull()
  expect(tops.filter, '.rail--filter not found').not.toBeNull()
  expect(
    tops.bannerInsideView,
    'the SimCore health banner is rendering inside .view — it will push the ' +
      "filter rail down while the nav rail stays pinned. Pass it as AppShell's " +
      '`banner` slot, not as a child.',
  ).toBe(false)

  const delta = (tops.filter as number) - (tops.nav as number)
  expect(
    delta,
    `the two rails are ${delta}px out of alignment (nav ${tops.nav}, filter ` +
      `${tops.filter}) — something is being rendered inside .view above the view's content`,
  ).toBeLessThanOrEqual(48)
})

test('chrome leaves the content most of the viewport height', async ({ page }) => {
  // The reported console spent 437px of 620 (70%) on stacked chrome rows,
  // leaving 183px for the Library. Every row was individually defensible;
  // nothing owned the total. This is the budget nobody was keeping.
  await page.setViewportSize({ width: 845, height: 620 })
  await page.goto('/#/library')
  await page.waitForSelector('.rail--nav')
  await page.waitForTimeout(400)

  const budget = await page.evaluate(() => {
    const h = (s: string) => {
      const e = document.querySelector(s) as HTMLElement | null
      return e ? Math.round(e.getBoundingClientRect().height) : 0
    }
    // The safety banner is EXCLUDED on purpose: it is a blast-radius consent
    // gate that leaves the grid permanently once acknowledged, so counting it
    // would make this budget depend on consent state rather than on layout.
    const chrome = h('.header') + h('.phase-bar') + h('.readiness-banner') + h('.command-strip')
    return { chrome, viewport: window.innerHeight, view: h('.view') }
  })

  const pct = budget.chrome / budget.viewport
  expect(
    pct,
    `persistent chrome is ${Math.round(pct * 100)}% of a 620px viewport ` +
      `(${budget.chrome}px), leaving ${budget.view}px for content`,
  ).toBeLessThan(0.45)
})

test('a collapsed rail still names its destinations to the keyboard', async ({ page }) => {
  // Below 1000px the rail collapses to a 62px icon strip. Unlabelled glyphs are
  // not navigation, and hover-only labels are not reachable without a mouse —
  // so the reveal must fire on :focus-within, not just :hover.
  await page.setViewportSize({ width: 845, height: 620 })
  await page.goto('/#/library')
  await page.waitForSelector('.rail--nav .plane-item')
  await page.waitForTimeout(400)

  const collapsedLabel = await page.evaluate(
    () => getComputedStyle(document.querySelector('.rail--nav .plane-item__name')!).display,
  )
  expect(collapsedLabel, 'labels should be hidden while collapsed').toBe('none')

  await page.focus('.rail--nav .plane-item')
  await page.waitForTimeout(400)

  const focused = await page.evaluate(() => {
    const name = document.querySelector('.rail--nav .plane-item__name') as HTMLElement
    const main = document.querySelector('.main') as HTMLElement
    return {
      labelDisplay: getComputedStyle(name).display,
      labelText: (name.textContent || '').trim(),
      mainLeft: Math.round(main.getBoundingClientRect().left),
    }
  })
  expect(focused.labelDisplay, 'keyboard focus must reveal the label').not.toBe('none')
  expect(focused.labelText.length, 'the revealed label must have text').toBeGreaterThan(0)
  // Revealing a label must not reflow what the operator is reading mid-demo.
  expect(focused.mainLeft, 'the rail must overlay, not push the content').toBe(62)
})

/**
 * NEGATIVE CONTROL — proves the detector can actually go red, and that it is
 * red for the RIGHT reason.
 *
 * The repo already demands this of assertion artifacts (`A-18`: an assertion
 * that cannot fail proves nothing, and the authored negative control must
 * really evaluate `fail`). A layout guard that has never been observed failing
 * is an assumption with good syntax.
 *
 * The first attempt at this control re-injected the pre-fix CSS and asserted
 * the matrix went red. It did not — some declarations lost the cascade to the
 * live stylesheet, so the control silently tested nothing while reporting
 * green. That is the same class of bug this whole spec exists to catch, so the
 * control now exercises the DETECTOR'S RULE directly with two probes it fully
 * controls, rather than trying to re-create an old stylesheet:
 *
 *   (a) oversized content inside a clipping ancestor  -> MUST be flagged
 *   (b) the same content inside a scrolling ancestor  -> MUST NOT be flagged
 *
 * (b) matters as much as (a). A detector that flags every wide element would be
 * "safe" and useless: the corpus has legitimately wide, deliberately scrollable
 * surfaces (the MITRE heatmap, the phase bar), and a guard that cried wolf on
 * those would be relaxed within a week until it caught nothing.
 */
test('NEGATIVE CONTROL: hidden overflow is flagged, scrollable overflow is not', async ({ page }) => {
  await page.setViewportSize({ width: 845, height: 620 })
  await page.goto('/#/library')
  await page.waitForSelector('.view')

  await page.evaluate(() => {
    const view = document.querySelector('.view') as HTMLElement
    // (a) `.view` is overflow-x: hidden — this is unreachable once it overflows.
    const hidden = document.createElement('div')
    hidden.id = 'nc-hidden'
    hidden.style.cssText = 'width:3000px;height:8px;background:transparent'
    view.appendChild(hidden)

    // (b) identical width, but inside an explicitly scrollable box: reachable.
    const scroller = document.createElement('div')
    scroller.style.cssText = 'overflow-x:auto;width:200px'
    const wide = document.createElement('div')
    wide.id = 'nc-scrollable'
    wide.style.cssText = 'width:3000px;height:8px;background:transparent'
    scroller.appendChild(wide)
    view.appendChild(scroller)
  })
  await page.waitForTimeout(200)

  const { boxes } = await measure(page, ['#nc-hidden', '#nc-scrollable'])
  const hidden = boxes.find((b) => b.sel === '#nc-hidden')!
  const scrollable = boxes.find((b) => b.sel === '#nc-scrollable')!

  expect(
    hidden.clippedBy,
    'a 3000px element inside overflow-x:hidden was NOT flagged — the detector ' +
      'cannot fail, so every passing assertion above proves nothing. Fix the ' +
      'detector, not this test.',
  ).not.toBeNull()

  expect(
    scrollable.clippedBy,
    'a 3000px element inside overflow-x:auto WAS flagged — the detector cannot ' +
      'tell reachable from lost content, and will be disabled the first time a ' +
      'wide-but-scrollable surface trips it.',
  ).toBeNull()
})

/**
 * The two guards below cover a DIFFERENT geometry failure from the clipping
 * ones above: content that is neither clipped by an ancestor nor overflowing
 * the viewport, but crushed inside a box its own stylesheet sized wrong. The
 * landmark sweep cannot see these — the elements sit well inside `.view`, so
 * every ancestor probe returns clean.
 *
 * Both shipped past the same green 1128-test unit suite, for the same reason
 * recorded at the top of this file: jsdom returns zeros for
 * `getBoundingClientRect()`, `scrollHeight` and `clientHeight` alike, so the
 * assertions here are vacuously true there and meaningful only in a real
 * browser.
 */

test('quick-launch selects are tall enough for their own text', async ({ page }) => {
  // `cortex-console.css` pins `.insp-select { height: 26px; padding: 0 8px }`.
  // The Library override restyled the padding to `8px 10px` and did NOT
  // re-declare the height, forcing 16px of vertical padding inside a fixed
  // 26px box — the selected option's text was clipped. Its sibling `.btn--xs`
  // in the same override block re-declares its height and was unaffected,
  // which is why this went unnoticed.
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await page.waitForSelector('.scenario-card')
  await page.locator('.scenario-card').first().click()
  await page.waitForSelector('.insp-select')

  // NOT `scrollHeight > clientHeight`. That is the natural probe and it does
  // not work here: a <select> clips its selected option internally without
  // ever reporting overflow, so scrollHeight === clientHeight even when the
  // text is visibly cut. Written that way this guard PASSED against the
  // reverted, still-broken build — an assertion with good syntax proving
  // nothing. Measure the content box against the line it has to hold instead.
  const selects = await page.evaluate(() =>
    [...document.querySelectorAll('.insp-select')].map((el) => {
      const s = el as HTMLSelectElement
      const cs = getComputedStyle(s)
      const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
      const lineH = parseFloat(cs.lineHeight) // NaN when 'normal'
      return {
        label:
          s.closest('.insp-config__row')?.querySelector('.insp-config__label')?.textContent?.trim() ??
          '(unlabelled)',
        contentH: s.clientHeight - padY,
        needs: Number.isFinite(lineH) ? lineH : parseFloat(cs.fontSize),
        cssHeight: cs.height,
        padding: cs.padding,
      }
    }),
  )

  // A zero here is degraded, not ok: if the selects stop rendering entirely
  // this test must fail rather than pass over an empty list.
  expect(
    selects.length,
    'no .insp-select rendered — the quick-launch config did not mount, so this ' +
      'guard proved nothing. Fix the fixture, do not relax the assertion.',
  ).toBeGreaterThan(0)

  for (const s of selects) {
    expect(
      s.contentH,
      `the ${s.label} select leaves ${s.contentH}px of content box for a ` +
        `${s.needs}px line (height: ${s.cssHeight}, padding: ${s.padding}) — ` +
        `its own text is clipped`,
    ).toBeGreaterThanOrEqual(s.needs)
  }
})

test('the plane filter rail aligns its names and wraps none of them', async ({ page }) => {
  // `.plane-item` is shared by both rails, but `.rail--nav` puts a ~7px glyph
  // in `__code` while `.rail--filter` puts a plane code spanning 21.1px (EDR)
  // to 63.4px (CLOUD_APP / AI_ACCESS / ANALYTICS). Under the inherited
  // `display: flex` the codes set the layout, so the name column started at six
  // different offsets spanning 42.3px. Declaring a fixed column fixes that but
  // introduces the opposite failure if sized too generously: inside a 202px
  // rail, a 64px code column left 70px for names and wrapped 'Cloud Posture'
  // and 'Attack Surface' onto two lines. Both directions are asserted here.
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await page.waitForSelector('.rail--filter .plane-item')

  const rows = await page.evaluate(() =>
    [...document.querySelectorAll('.rail--filter .plane-item')].map((pi) => {
      const code = pi.querySelector('.plane-item__code') as HTMLElement
      const name = pi.querySelector('.plane-item__name') as HTMLElement
      const nameBox = name.getBoundingClientRect()
      const lineH = parseFloat(getComputedStyle(name).lineHeight) || 16
      return {
        code: (code.textContent || '').trim(),
        name: (name.textContent || '').trim(),
        nameX: Math.round(nameBox.x),
        lines: Math.round(nameBox.height / lineH),
        codeOverflow: code.scrollWidth - code.clientWidth,
      }
    }),
  )

  expect(rows.length, 'the filter rail rendered no planes').toBeGreaterThan(0)

  const origins = [...new Set(rows.map((r) => r.nameX))]
  expect(
    origins.length,
    `plane names start at ${origins.length} different offsets ` +
      `(${origins.sort((a, b) => a - b).join(', ')}) — the code column is being ` +
      `sized by its content instead of being declared`,
  ).toBe(1)

  const wrapped = rows.filter((r) => r.lines > 1).map((r) => r.name)
  expect(
    wrapped,
    `these plane names wrapped onto a second line: ${wrapped.join(', ')} — the ` +
      `code column is reserving width the name column needs`,
  ).toEqual([])

  const clipped = rows.filter((r) => r.codeOverflow > 0).map((r) => r.code)
  expect(
    clipped,
    `these plane codes are cut off by the column: ${clipped.join(', ')} — the ` +
      `column is now too narrow for the widest code`,
  ).toEqual([])
})
