import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Composer mode — the full-screen node graph, measured in a real browser.
 *
 * WHY AN E2E SPEC
 * ---------------
 * Composer mode is a layout claim: "everything below the console header is the
 * graph". jsdom has no layout engine (every rect is zero), so the unit suite
 * can prove the classes are applied and the rail is not rendered, but not that
 * the stage actually reaches the bottom of the window, that nothing scrolls,
 * or that React Flow measured and fitted the nodes. This spec measures those.
 *
 * It also guards the ONE thing a full-screen mode can break for every other
 * page: leaving it must give the rail back (08-viewport-matrix only ever looks
 * at the Library, which is exactly where a leaked immersive class would show).
 */

const MATRIX = [
  { w: 1920, h: 1080 },
  { w: 1440, h: 900 },
  { w: 1280, h: 800 },
  { w: 1024, h: 768 },
  { w: 900, h: 700 },
  { w: 845, h: 620 },
]

const SCENARIO = 'SIM-EDR-001'

async function openComposer(page: Page, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto(`/#/composer?from=${SCENARIO}`)
  await page.waitForSelector('.react-flow__node-step', { timeout: 20_000 })
  // React Flow hides a node until it has measured it, then FitOnReady fits on
  // the next frame. Wait for both rather than sleeping.
  await page.waitForFunction(() => {
    const nodes = [...document.querySelectorAll('.react-flow__node')]
    const vp = (document.querySelector('.react-flow__viewport') as HTMLElement | null)?.style.transform || ''
    return nodes.length > 0
      && nodes.every((n) => getComputedStyle(n).visibility !== 'hidden')
      && !/scale\(1\)$/.test(vp)
  }, undefined, { timeout: 15_000 })
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const box = (sel: string) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) }
    }
    const d = document.documentElement
    const vpTransform = (document.querySelector('.react-flow__viewport') as HTMLElement | null)?.style.transform || ''
    const scale = Number((vpTransform.match(/scale\(([\d.]+)\)/) || [])[1] || 'NaN')
    return {
      vw: d.clientWidth,
      vh: window.innerHeight,
      docScrollsX: d.scrollWidth > d.clientWidth + 1,
      docScrollsY: d.scrollHeight > window.innerHeight + 1,
      rail: !!document.querySelector('.rail--nav'),
      tabs: !!document.querySelector('[data-testid=section-tabs]'),
      mode: (document.querySelector('.pov-shell') as HTMLElement | null)?.dataset.mode ?? null,
      header: box('.pov-header'),
      stage: box('.composer-mode__stage'),
      flow: box('.composer-canvas__flow'),
      scale,
    }
  })
}

for (const { w, h } of MATRIX) {
  test(`composer mode fills the window below the header at ${w}x${h}`, async ({ page }) => {
    await openComposer(page, w, h)
    const g = await geometry(page)

    expect(g.mode, 'shell must be in immersive mode').toBe('immersive')
    expect(g.rail, 'the nav rail must not render in composer mode').toBe(false)
    expect(g.tabs, 'the Library tab strip must not render in composer mode').toBe(false)
    expect(g.header && g.header.top, 'the console header stays on screen').toBeGreaterThanOrEqual(0)

    // The stage spans the full window width and reaches its bottom edge.
    expect(g.stage, 'stage must exist').not.toBeNull()
    expect(g.stage!.left).toBeLessThanOrEqual(1)
    expect(g.stage!.right).toBeGreaterThanOrEqual(g.vw - 1)
    expect(Math.abs(g.stage!.bottom - g.vh), `stage bottom ${g.stage!.bottom} vs window ${g.vh}`).toBeLessThanOrEqual(2)

    // The React Flow pane fills the stage width and has real height.
    expect(g.flow!.width).toBeGreaterThanOrEqual(g.stage!.width - 2)
    expect(g.flow!.height).toBeGreaterThan(120)

    // Nothing scrolls: the graph pans inside its pane instead.
    expect(g.docScrollsX, 'document must not scroll horizontally').toBe(false)
    expect(g.docScrollsY, 'document must not scroll vertically').toBe(false)

    // The graph was actually fitted (not left at the default scale(1) corner).
    expect(g.scale).toBeGreaterThan(0.15)
    expect(g.scale).toBeLessThanOrEqual(1.2)
  })
}

test('leaving composer mode gives the rail back (and entering through the tab takes it away)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await page.waitForSelector('.rail--nav', { timeout: 15_000 })

  // NEGATIVE CONTROL: on an ordinary page the rail IS there — so the
  // "rail absent" assertions above are measuring something, not nothing.
  const before = await geometry(page)
  expect(before.rail).toBe(true)
  expect(before.mode).toBeNull()

  await page.getByTestId('section-tab-composer').click()
  await page.waitForSelector('[data-testid=composer-view]')
  const inMode = await geometry(page)
  expect(inMode.mode).toBe('immersive')
  expect(inMode.rail).toBe(false)

  await page.getByTestId('composer-exit').click()
  await page.waitForSelector('.rail--nav', { timeout: 10_000 })
  const after = await geometry(page)
  expect(page.url()).toMatch(/#\/library/)
  expect(after.mode).toBeNull()
  expect(after.tabs).toBe(true)
  const railLeft = await page.locator('.rail--nav').evaluate((el) => Math.round(el.getBoundingClientRect().left))
  expect(railLeft).toBe(0)
})
