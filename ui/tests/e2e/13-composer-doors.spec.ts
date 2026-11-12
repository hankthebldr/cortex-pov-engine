import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Composer doors — a step's lane and badge follow its SIGNAL PATH, in a real
 * browser, against real corpus scenarios.
 *
 * WHY AN E2E SPEC
 * ---------------
 * The unit suite proves the derivation and the badge text. What jsdom cannot
 * prove is that the Lanes lens actually draws each card inside the band its
 * path names, that a dropped card visibly snaps back, and that a badge as long
 * as "AGT → COLLECTOR" fits on the card without covering its id. Those are
 * measured here.
 *
 * The scenarios were picked from the corpus because the old plane-derived
 * badge got each of them wrong:
 *   SIM-AIACC-003  step-02 is the Gemini burst, a traffic generator → WIRE
 *                  (it used to read AGT)
 *   SIM-NDR-003    step-01 runs a plain command on the host → ENDPOINT
 *                  (it used to read "Broker VM relay")
 *   SIM-CDR-023    both steps POST records to a collector → COLLECTOR
 *                  (they used to read "Cloud")
 *   SIM-AISPM-003  calls AWS, then waits, then acts → CLOUD, NONE, ENDPOINT
 *
 * Spec: docs/superpowers/specs/2026-11-10-composer-doors-design.md
 */

async function openComposer(page: Page, scenario: string, w = 1440, h = 900) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto(`/#/composer?from=${scenario}`)
  await page.waitForSelector('.react-flow__node-step', { timeout: 20_000 })
  await page.waitForFunction(() => {
    const nodes = [...document.querySelectorAll('.react-flow__node')]
    const vp = (document.querySelector('.react-flow__viewport') as HTMLElement | null)?.style.transform || ''
    return nodes.length > 0
      && nodes.every((n) => getComputedStyle(n).visibility !== 'hidden')
      && !/scale\(1\)$/.test(vp)
  }, undefined, { timeout: 15_000 })
}

const badge = (page: Page, step: string) => page.getByTestId(`chain-door-${step}`)

/** Switch to the Lanes lens and wait for its fit to finish: the viewport moves
 *  when the lens changes, and a drag read from a box mid-fit misses its card. */
async function openLanes(page: Page) {
  await page.getByTestId('composer-lens-lanes').click()
  // The band container has no box of its own (its bands are absolute): wait
  // for a band.
  await page.waitForSelector('.composer-lane[data-lane="ENDPOINT"]')
  let last = ''
  await expect.poll(async () => {
    const now = await page.evaluate(() => (document.querySelector('.react-flow__viewport') as HTMLElement).style.transform)
    const steady = now === last
    last = now
    return steady
  }, { intervals: [150, 150, 150, 250, 250, 500], timeout: 8_000 }).toBe(true)
}

test('SIM-AIACC-003: the Gemini burst reads as live traffic, its preflight as the endpoint', async ({ page }) => {
  await openComposer(page, 'SIM-AIACC-003')
  await expect(badge(page, 'step-01')).toHaveText('AGT')
  await expect(badge(page, 'step-02')).toHaveText('AGT → WIRE')
  await expect(badge(page, 'step-02')).toHaveAttribute('title', /LIVE TRAFFIC.*llm_provider_egress.*runs on the beacon/)
})

test('SIM-NDR-003: an NDR step that runs a plain command no longer claims the Broker VM', async ({ page }) => {
  await openComposer(page, 'SIM-NDR-003')
  await expect(badge(page, 'step-01')).toHaveText('AGT')
  await expect(badge(page, 'step-01')).not.toHaveAttribute('title', /Broker VM/)
  await expect(badge(page, 'step-02')).toHaveText('AGT → WIRE')
})

test('SIM-CDR-023: collector steps read as log injection, and the badge fits the card', async ({ page }) => {
  await openComposer(page, 'SIM-CDR-023')
  for (const id of ['step-01', 'step-02']) {
    await expect(badge(page, id)).toHaveText('AGT → COLLECTOR')
    await expect(badge(page, id)).toHaveAttribute('title', /LOG INJECTION · records POSTed to a collector · Broker VM or HTTP collector/)
  }
  // The longest badge in the corpus has its own line: below the header row
  // (it used to cover the step id), inside the card, and not clipped.
  const geo = await page.evaluate(() => {
    const card = document.querySelector('.react-flow__node[data-id="step-01"] .chain-node--step')!
    const box = (el: Element) => el.getBoundingClientRect()
    const b = card.querySelector('.chain-node__doorbadge')!
    return {
      card: box(card), badge: box(b), id: box(card.querySelector('.chain-node__id')!),
      order: box(card.querySelector('.chain-node__order')!), name: box(card.querySelector('.chain-node__name')!),
      clipped: (b as HTMLElement).scrollWidth > (b as HTMLElement).clientWidth + 1,
    }
  })
  expect(geo.badge.top, 'badge sits below the id').toBeGreaterThanOrEqual(geo.id.bottom - 1)
  expect(geo.badge.top, 'badge sits below the order number').toBeGreaterThanOrEqual(geo.order.bottom - 1)
  expect(geo.badge.bottom, 'badge sits above the step name').toBeLessThanOrEqual(geo.name.top + 1)
  expect(geo.badge.right).toBeLessThanOrEqual(geo.card.right)
  expect(geo.clipped, 'badge text is not clipped').toBe(false)
})

test('SIM-AISPM-003: Lanes lens draws each card inside the band its path names', async ({ page }) => {
  await openComposer(page, 'SIM-AISPM-003')
  await openLanes(page)
  await expect(badge(page, 'step-01')).toHaveText('AGT → CLOUD')
  await expect(badge(page, 'step-02')).toHaveText('AGT · no action')
  await expect(badge(page, 'step-03')).toHaveText('AGT')

  const placed = await page.evaluate(() => {
    const bands = Object.fromEntries([...document.querySelectorAll('.composer-lane')]
      .map((b) => [(b as HTMLElement).dataset.lane, b.getBoundingClientRect()]))
    const centre = (id: string) => {
      const r = document.querySelector(`.react-flow__node[data-id="${id}"]`)!.getBoundingClientRect()
      return r.top + Math.min(r.height, 40) / 2
    }
    const inBand = (id: string) => Object.entries(bands)
      .find(([, b]) => centre(id) >= b.top && centre(id) < b.bottom)?.[0] || null
    return { 'step-01': inBand('step-01'), 'step-02': inBand('step-02'), 'step-03': inBand('step-03'), keys: Object.keys(bands) }
  })
  expect(placed.keys).toEqual(['LAUNCH', 'ENDPOINT', 'COLLECTOR', 'WIRE', 'CLOUD', 'NONE', 'ENG', 'PROOF'])
  expect(placed['step-01']).toBe('CLOUD')
  expect(placed['step-02']).toBe('NONE')
  expect(placed['step-03']).toBe('ENDPOINT')
  await expect(page.getByTestId('composer-lane-bands')).toContainText('CLOUD CONTROL PLANE')
})

test('a card dragged into another lane snaps back and says why', async ({ page }) => {
  await openComposer(page, 'SIM-AISPM-003')
  await openLanes(page)
  const node = page.locator('.react-flow__node[data-id="step-03"]')
  const before = (await node.boundingBox())!
  const target = (await page.locator('.composer-lane[data-lane="WIRE"]').boundingBox())!
  const grip = page.getByTestId('chain-node-grip-step-03')
  const g = (await grip.boundingBox())!
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2)
  await page.mouse.down()
  await page.mouse.move(g.x + g.width / 2, target.y + target.height / 2, { steps: 12 })
  await page.mouse.up()

  await expect(page.getByTestId('canvas-refusal')).toContainText(/^Lane not changed/)
  await expect(page.getByTestId('canvas-refusal')).toContainText(/lane follows what its command does/i)
  await expect.poll(async () => Math.round((await node.boundingBox())!.y)).toBe(Math.round(before.y))
  await expect(badge(page, 'step-03')).toHaveText('AGT')
  // A lane is not an edit: the chain does not read as changed.
  await expect(page.locator('.composer__edited')).toHaveCount(0)
})

test('the Engine band holds tenant content, never a step', async ({ page }) => {
  await openComposer(page, 'SIM-AIACC-003')
  // The rule arrives with its card; wait for it before switching lens.
  await page.waitForSelector('[data-testid="chain-rule-CR-AIACC-0003"]', { timeout: 20_000 })
  await openLanes(page)
  const where = await page.evaluate(() => {
    const eng = document.querySelector('.composer-lane[data-lane="ENG"]')!.getBoundingClientRect()
    const inEng = (sel: string) => [...document.querySelectorAll(sel)].filter((n) => {
      const r = n.getBoundingClientRect()
      const c = r.top + Math.min(r.height, 40) / 2
      return c >= eng.top && c < eng.bottom
    }).length
    return { steps: inEng('.react-flow__node-step'), rules: inEng('.react-flow__node-rule') }
  })
  expect(where.steps).toBe(0)
  expect(where.rules).toBe(1)
})
