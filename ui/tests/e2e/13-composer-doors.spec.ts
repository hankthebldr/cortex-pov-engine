import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Composer doors — a step's lane and badge follow its SIGNAL PATH, in a real
 * browser, against real corpus scenarios.
 *
 * WHY AN E2E SPEC
 * ---------------
 * The unit suite proves the derivation and the badge text. What jsdom cannot
 * prove is that the Lanes lens (the ChainStage, design v26 §4.1) actually
 * draws each card inside the band its path names, that pressing a card opens
 * its step, and that a badge as long as "AGT → COLLECTOR" fits on the card
 * without covering its id. Those are measured here.
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

/** Switch to the Lanes lens: the ChainStage in its editable mode (design v26
 *  §4.1). It is not a React Flow pane, so there is no fit to wait out — the
 *  bands and cards are placed on first paint. */
async function openLanes(page: Page) {
  await page.getByTestId('composer-lens-lanes').click()
  await page.waitForSelector('[data-testid="chain-stage"][data-mode="editable"] .chain-stage__lane[data-lane="ENDPOINT"]')
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
    const bands = Object.fromEntries([...document.querySelectorAll('.chain-stage__lane')]
      .map((b) => [(b as HTMLElement).dataset.lane, b.getBoundingClientRect()]))
    const card = (id: string) => document.querySelector(`[data-testid="stage-card-${id}"]`)!.getBoundingClientRect()
    // The WHOLE card sits inside its band, not just its centre.
    const inBand = (id: string) => Object.entries(bands)
      .find(([, b]) => card(id).top >= b.top - 0.5 && card(id).bottom <= b.bottom + 0.5)?.[0] || null
    return { 'step-01': inBand('step-01'), 'step-02': inBand('step-02'), 'step-03': inBand('step-03'), keys: Object.keys(bands) }
  })
  // Launch and Proof are the run's Start and Stop points, not lanes.
  expect(placed.keys).toEqual(['ENDPOINT', 'COLLECTOR', 'WIRE', 'CLOUD', 'NONE', 'ENG'])
  expect(placed['step-01']).toBe('CLOUD')
  expect(placed['step-02']).toBe('NONE')
  expect(placed['step-03']).toBe('ENDPOINT')
  await expect(page.getByTestId('chain-stage')).toContainText('CLOUD CONTROL PLANE')
})

test('a Lanes card is a button: pressing it opens that step in the inspector', async ({ page }) => {
  await openComposer(page, 'SIM-AISPM-003')
  await openLanes(page)
  await page.getByTestId('stage-card-step-03').click()
  await expect(page.getByTestId('stage-card-step-03')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.composer-mode__drawer--right .composer-mode__drawer-title')).toHaveText('step-03')
  await expect(badge(page, 'step-03')).toHaveText('AGT')
  // Selecting is not an edit: the chain does not read as changed.
  await expect(page.locator('.composer__edited')).toHaveCount(0)
})

test('the Engine band holds tenant content, never a step', async ({ page }) => {
  await openComposer(page, 'SIM-AIACC-003')
  // The rule arrives with its card; wait for it before switching lens.
  await page.waitForSelector('[data-testid="chain-rule-CR-AIACC-0003"]', { timeout: 20_000 })
  await openLanes(page)
  const where = await page.evaluate(() => {
    const eng = document.querySelector('.chain-stage__lane[data-lane="ENG"]')!.getBoundingClientRect()
    const inEng = (sel: string) => [...document.querySelectorAll(sel)].filter((n) => {
      const r = n.getBoundingClientRect()
      const c = r.top + r.height / 2
      return c >= eng.top && c < eng.bottom
    }).length
    return { steps: inEng('[data-testid^="stage-card-step-"]'), rules: inEng('[data-testid^="stage-rule-"]') }
  })
  expect(where.steps).toBe(0)
  expect(where.rules).toBe(1)
})
