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

/*
 * ─── The overlays: drawers, dock, run band, React Flow chrome ───────────────
 *
 * Composer mode floats the palette, inspector and workstream OVER the graph
 * instead of laying them out beside it, so every defect below is one overlay
 * painting over another, or a windowed rule leaking into the mode. None of it
 * is visible to jsdom; each test measures the real boxes.
 *
 * The consent banner stays open in these runs (it is per-session), so
 * 1024x768 leaves a ~390px stage and 845x620 a ~240px one — the SHORT-stage
 * container query is live at 845x620 and not at 1024x768. Both matter.
 */

type Box = { left: number; right: number; top: number; bottom: number; width: number; height: number }

async function rect(page: Page, sel: string): Promise<Box | null> {
  return page.evaluate((s) => {
    const el = document.querySelector(s)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }
  }, sel)
}

const intersects = (a: Box, b: Box) =>
  a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom

/** A console token resolved to the same `rgb()` form getComputedStyle returns. */
async function tokenColor(page: Page, name: string) {
  return page.evaluate((n) => {
    const host = document.querySelector('.composer--mode') || document.body
    const probe = document.createElement('span')
    probe.style.color = `var(${n})`
    host.appendChild(probe)
    const c = getComputedStyle(probe).color
    probe.remove()
    return c
  }, name)
}

/** Alpha of a computed colour: rgba(), rgb() and color(srgb … / a) forms. */
function alphaOf(c: string) {
  const slash = c.match(/\/\s*([\d.]+)\s*\)\s*$/)
  if (slash) return Number(slash[1])
  const rgba = c.match(/^rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\s*\)$/)
  if (rgba) return Number(rgba[1])
  return 1
}

async function openPalette(page: Page) {
  await page.getByRole('button', { name: '＋ Add' }).click()
  await page.waitForSelector('.composer-mode__drawer--left')
}
async function closePalette(page: Page) {
  await page.getByRole('button', { name: 'Collapse palette' }).click()
}
/** Selecting a step body opens the inspector on that step. Waits for the
 *  drawer FRAME, not its content, so a hidden-content bug fails on its own
 *  assertion instead of timing out every test that opens the inspector. */
async function selectStep(page: Page, id = 'step-01') {
  await page.getByTestId(`chain-step-${id}`).click()
  await page.waitForSelector('.composer-mode__drawer--right')
}

// The windowed layout hides the bench and inspector below 1150px; that rule
// must not leak into the mode, where both are overlays, not grid tracks.
for (const { w, h } of [{ w: 1024, h: 768 }, { w: 845, h: 620 }]) {
  test(`the palette drawer shows its content at ${w}x${h}`, async ({ page }) => {
    await openComposer(page, w, h)
    await openPalette(page)
    const bench = await rect(page, '.composer-mode__drawer--left .composer-bench')
    expect(bench!.height, 'the palette drawer opened EMPTY — its content is display:none').toBeGreaterThan(40)
    await expect(page.getByTestId('composer-palette')).toBeVisible()
  })

  test(`the inspector drawer shows its content at ${w}x${h}`, async ({ page }) => {
    await openComposer(page, w, h)
    await selectStep(page)
    const insp = await rect(page, '.composer-mode__drawer--right .composer-inspector')
    expect(insp!.height, 'the inspector drawer opened EMPTY — its content is display:none').toBeGreaterThan(40)
    const cmd = page.locator('.composer-mode__drawer--right [aria-label^="Command for"]').first()
    await cmd.scrollIntoViewIfNeeded()
    await expect(cmd).toBeVisible()
    const c = (await cmd.boundingBox())!
    expect(intersects({ left: c.x, top: c.y, right: c.x + c.width, bottom: c.y + c.height, width: c.width, height: c.height }, insp!),
      'the command editor is inside the inspector drawer, not off in a hidden subtree').toBe(true)
  })
}

for (const { w, h } of [{ w: 1280, h: 800 }, { w: 845, h: 620 }]) {
  test(`Run lens: the run band's text starts below the floating tabs and caption at ${w}x${h}`, async ({ page }) => {
    await openComposer(page, w, h)
    await page.getByTestId('composer-lens-run').click()
    const band = page.getByTestId('composer-run-graph')
    await expect(band).toBeVisible()

    // The text box: every line the band renders (the completeness / broken-
    // stitch line, or the "No run yet — EXPECTED only." disclaimer). Gate A5:
    // that first line is the honesty statement, so it may not sit under chrome.
    const text = await page.evaluate(() => {
      const el = document.querySelector('[data-testid=composer-run-graph]')!
      const range = document.createRange()
      range.selectNodeContents(el)
      const first = [...range.getClientRects()].find((r) => r.width > 0 && r.height > 0)!
      return { left: first.left, right: first.right, top: first.top, bottom: first.bottom, width: first.width, height: first.height }
    })
    const overlays = await page.evaluate(() =>
      [...document.querySelectorAll('.composer-mode__reopen, .composer-canvas__meta')]
        .filter((el) => getComputedStyle(el).display !== 'none')
        .map((el) => {
          const r = el.getBoundingClientRect()
          return { cls: el.className, box: { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height } }
        }))
    // Negative control: the tabs this measures against really are on screen.
    expect(overlays.filter((o) => /composer-mode__reopen/.test(o.cls)).length).toBe(2)
    for (const o of overlays) {
      expect(intersects(text, o.box), `run band text at y=${Math.round(text.top)} sits under ${o.cls} (y=${Math.round(o.box.top)}..${Math.round(o.box.bottom)})`).toBe(false)
    }
  })
}

test('an open workstream dock stops the drawers above it, last inspector field included (1024x768)', async ({ page }) => {
  await openComposer(page, 1024, 768)
  await selectStep(page)
  await openPalette(page)
  await page.getByTestId('ws-tab-preflight').click()
  await expect(page.locator('.composer-ws--open')).toBeVisible()

  const dock = (await rect(page, '.composer-mode__dock'))!
  for (const side of ['left', 'right']) {
    const d = (await rect(page, `.composer-mode__drawer--${side}`))!
    expect(d.height, `${side} drawer still has room`).toBeGreaterThan(80)
    expect(d.bottom, `${side} drawer bottom ${d.bottom} runs under the open dock (top ${dock.top})`).toBeLessThanOrEqual(dock.top)
  }
  // The inspector's LAST field can be scrolled above the dock, not only its frame.
  const last = await page.evaluate(() => {
    const scroller = document.querySelector('.composer-mode__drawer--right .composer-inspector') as HTMLElement
    scroller.scrollTop = scroller.scrollHeight
    const fields = scroller.querySelectorAll('input, textarea, select, button')
    const r = fields[fields.length - 1].getBoundingClientRect()
    return { bottom: r.bottom, height: r.height }
  })
  expect(last.height, 'the last inspector field is rendered').toBeGreaterThan(0)
  expect(last.bottom).toBeLessThanOrEqual(dock.top)
})

test('an open workstream dock never buries the drawer tabs on a short stage (845x620)', async ({ page }) => {
  // ~240px stage with the consent banner open: a 220px dock body made the open
  // dock taller than the whole stage.
  await openComposer(page, 845, 620)
  await page.getByTestId('ws-tab-preflight').click()
  await expect(page.locator('.composer-ws--open')).toBeVisible()
  const dock = (await rect(page, '.composer-mode__dock'))!
  const stage = (await rect(page, '.composer-mode__stage'))!
  expect(dock.top, 'the open dock covers the whole stage').toBeGreaterThan(stage.top)
  for (const side of ['left', 'right']) {
    const tab = (await rect(page, `.composer-mode__reopen--${side}`))!
    expect(tab.bottom, `${side} reopen tab is buried under the open dock`).toBeLessThanOrEqual(dock.top)
  }
})

test('the overview map mask is translucent, so off-screen nodes still read', async ({ page }) => {
  await openComposer(page, 1440, 900)
  await page.getByRole('button', { name: 'Pin the overview map' }).click()
  await page.waitForSelector('.react-flow__minimap-mask')
  const fill = await page.evaluate(() => getComputedStyle(document.querySelector('.react-flow__minimap-mask')!).fill)
  const a = alphaOf(fill)
  expect(a, `mask fill ${fill} is opaque`).toBeLessThan(1)
  expect(a, `mask fill ${fill} vanished entirely`).toBeGreaterThan(0)
})

test('short stage: "+ Add step" moves clear of the open inspector (845x620)', async ({ page }) => {
  await openComposer(page, 845, 620)
  // Precondition: the short-stage container query is the rule under test.
  expect(await page.evaluate(() => getComputedStyle(document.querySelector('.composer-canvas__meta')!).display)).toBe('none')
  await selectStep(page)
  const add = (await rect(page, '[data-testid=composer-add-step]'))!
  const drawer = (await rect(page, '.composer-mode__drawer--right'))!
  expect(add.width).toBeGreaterThan(0)
  expect(intersects(add, drawer), '"+ Add step" is under the inspector drawer').toBe(false)
})

test('<=1100px: Controls, overview map and caption sit just beside the narrower drawers (1024x768)', async ({ page }) => {
  await openComposer(page, 1024, 768)
  // Soft: one run reports every panel that drifted, not just the first.
  const stage = (await rect(page, '.composer-mode__stage'))!
  const centred = async (free: { l: number; r: number }) => {
    const m = (await rect(page, '.composer-canvas__meta'))!
    return Math.abs((m.left + m.right) / 2 - (free.l + free.r) / 2)
  }

  await openPalette(page)
  const left = (await rect(page, '.composer-mode__drawer--left'))!
  const controls = (await rect(page, '.react-flow__panel.react-flow__controls'))!
  expect.soft(controls.left - left.right, 'Controls float far from the narrow palette').toBeLessThanOrEqual(16)
  expect.soft(controls.left - left.right, 'Controls sit under the palette').toBeGreaterThanOrEqual(0)
  expect.soft(await centred({ l: left.right, r: stage.right }), 'caption not centred in the free stage').toBeLessThanOrEqual(2)

  await closePalette(page)
  await selectStep(page)
  await page.getByRole('button', { name: 'Pin the overview map' }).click()
  await page.waitForSelector('.react-flow__minimap')
  const right = (await rect(page, '.composer-mode__drawer--right'))!
  const map = (await rect(page, '.react-flow__minimap'))!
  expect.soft(right.left - map.right, 'overview map floats far from the narrow inspector').toBeLessThanOrEqual(16)
  expect.soft(right.left - map.right, 'overview map sits under the inspector').toBeGreaterThanOrEqual(0)
  expect.soft(await centred({ l: stage.left, r: right.left }), 'caption not centred in the free stage').toBeLessThanOrEqual(2)

  await openPalette(page)
  const left2 = (await rect(page, '.composer-mode__drawer--left'))!
  expect.soft(await centred({ l: left2.right, r: right.left }), 'caption not centred between both drawers').toBeLessThanOrEqual(2)
})

test('the canvas dot grid is painted in the console border token, not React Flow\'s default grey', async ({ page }) => {
  await openComposer(page, 1440, 900)
  const bd2 = await tokenColor(page, '--bd2')
  const dots = () => page.evaluate(() => getComputedStyle(document.querySelector('.react-flow__background-pattern.dots')!).fill)
  expect(await dots()).toBe(bd2)
  // The stylesheet's own theme hook, without the component's `color` prop:
  // React Flow 12 reads `--xy-background-pattern-color` (the `-props` twin is
  // what the prop sets inline). A dead variable name here leaves the grid on
  // the library's #91919a the moment the prop is dropped.
  await page.evaluate(() => (document.querySelector('.react-flow__background') as SVGElement)
    .style.removeProperty('--xy-background-pattern-color-props'))
  expect(await dots(), 'composer.css sets a dot-colour variable React Flow never reads').toBe(bd2)
})

test('keyboard focus is visible on step cards and causality edges; the tool row reads at rest', async ({ page }) => {
  await openComposer(page, 1440, 900)
  await page.mouse.move(0, 0)
  const ring = await tokenColor(page, '--ac-str')
  // Soft: each a11y affordance is reported on its own; `fv` stays hard
  // because without keyboard modality the rest measures nothing.

  // Keyboard modality, then focus: :focus-visible is what a Tab user gets.
  await page.keyboard.press('Shift')
  await page.getByTestId('chain-step-step-02').focus()
  const body = await page.getByTestId('chain-step-step-02').evaluate((el) => {
    const cs = getComputedStyle(el)
    return { fv: el.matches(':focus-visible'), style: cs.outlineStyle, width: parseFloat(cs.outlineWidth), color: cs.outlineColor }
  })
  expect(body.fv).toBe(true)
  expect.soft(body.style).toBe('solid')
  expect.soft(body.width).toBeGreaterThanOrEqual(2)
  expect.soft(body.color).toBe(ring)

  // EVERY tab stop in the card rings — the React Flow wrapper included when it
  // is one (React Flow strips its outline to none; whether it is a tab stop is
  // the canvas's `nodesFocusable` call, so this asserts on what is focusable
  // rather than assuming).
  const stops = await page.getByTestId('chain-step-step-02').evaluate((el) => {
    const n = el.closest('.react-flow__node') as HTMLElement
    const all = [n, ...n.querySelectorAll<HTMLElement>('button, [tabindex]')]
      .filter((x) => x.tabIndex >= 0 && x.getAttribute('tabindex') !== '-1' && !(x as HTMLButtonElement).disabled)
    return all.map((x) => {
      x.focus()
      const cs = getComputedStyle(x)
      return {
        who: x === n ? 'react-flow__node wrapper' : (x.getAttribute('aria-label') || x.className),
        fv: x.matches(':focus-visible'), style: cs.outlineStyle, width: parseFloat(cs.outlineWidth),
      }
    })
  })
  expect(stops.length).toBeGreaterThan(1)
  for (const s of stops) {
    expect(s.fv, `${s.who} took keyboard focus`).toBe(true)
    expect.soft(s.style, `${s.who} has no visible focus ring`).not.toBe('none')
    expect.soft(s.width, `${s.who} focus ring is thinner than 2px`).toBeGreaterThanOrEqual(2)
  }

  // A focused causality edge: the path's INLINE stroke must not win.
  const focused = await page.evaluate(() => {
    const e = document.querySelector('.react-flow__edge-causality') as unknown as HTMLElement
    e.focus()
    const p = getComputedStyle(e.querySelector('.react-flow__edge-path')!)
    return { fv: e.matches(':focus-visible'), stroke: p.stroke, width: parseFloat(p.strokeWidth) }
  })
  expect(focused.fv).toBe(true)
  expect.soft(focused.stroke).toBe(ring)
  expect.soft(focused.width).toBeGreaterThanOrEqual(2.5)

  // A SELECTED edge (mouse), with focus moved away so only .selected applies.
  await page.locator('.react-flow__edge-causality').first().locator('.react-flow__edge-interaction').click({ force: true })
  await expect(page.locator('.react-flow__edge-causality.selected')).toHaveCount(1)
  const selected = await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur?.()
    const e = document.querySelector('.react-flow__edge-causality.selected')!
    const p = getComputedStyle(e.querySelector('.react-flow__edge-path')!)
    return { stroke: p.stroke, width: parseFloat(p.strokeWidth) }
  })
  expect.soft(selected.stroke).toBe(ring)
  expect.soft(selected.width).toBeGreaterThanOrEqual(2.5)

  // Tool row: >= .85 at rest (it sat at .55 — ~2.7:1), fully shown on focus.
  await page.mouse.move(0, 0)
  const rest = await page.locator('[data-testid=chain-step-step-03]').evaluate((el) =>
    Number(getComputedStyle(el.closest('.chain-node')!.querySelector('.chain-node__tools')!).opacity))
  expect.soft(rest).toBeGreaterThanOrEqual(0.85)
  await page.keyboard.press('Shift')
  await page.getByRole('button', { name: 'Duplicate step-03' }).focus()
  // Polled: the opacity eases over --dur-fast.
  await expect.poll(() => page.getByRole('button', { name: 'Duplicate step-03' }).evaluate((el) =>
    Number(getComputedStyle(el.closest('.chain-node__tools')!).opacity))).toBe(1)
})
