import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Composer — correlation rules in the chain story, in a real browser.
 *
 * WHY AN E2E SPEC
 * ---------------
 * `SIM-AIACC-003` step-02 used to read `XQL XQL XQL`: three chips that looked
 * like tags on the TTP. The rule that would raise the alert, `CR-AIACC-0003`,
 * was on the card and nowhere on the canvas. The unit suite proves the status
 * logic and the wording; what jsdom cannot prove is that the rule node is
 * actually DRAWN, joined to its step, placed below the chain without cutting
 * through a card, and readable at a small window. This spec measures those
 * against the real corpus scenario and the real card.
 *
 * What it does NOT prove: that the rule exists in any tenant, or that anything
 * here is importable. `CR-AIACC-0003` is intent-only, and the page says so.
 */

const SCENARIO = 'SIM-AIACC-003'
const RULE = 'CR-AIACC-0003'
const RULE_NODE = `rule:TTP-2026-0009:${RULE}`

async function openComposer(page: Page, w: number, h: number) {
  await page.setViewportSize({ width: w, height: h })
  await page.goto(`/#/composer?from=${SCENARIO}`)
  await page.waitForSelector('.react-flow__node-step', { timeout: 20_000 })
  // The rule arrives after its card is fetched; wait for it, measured and fitted.
  await page.waitForSelector(`[data-testid="chain-rule-${RULE}"]`, { timeout: 20_000 })
  await page.waitForFunction(() => {
    const nodes = [...document.querySelectorAll('.react-flow__node')]
    const vp = (document.querySelector('.react-flow__viewport') as HTMLElement | null)?.style.transform || ''
    return nodes.length > 0
      && nodes.every((n) => getComputedStyle(n).visibility !== 'hidden')
      && !/scale\(1\)$/.test(vp)
  }, undefined, { timeout: 15_000 })
}

async function boxes(page: Page) {
  return page.evaluate((ruleNode) => {
    const box = (el: Element | null) => {
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }
    }
    const d = document.documentElement
    return {
      steps: [...document.querySelectorAll('.react-flow__node-step')].map(box),
      rule: box(document.querySelector(`.react-flow__node[data-id="${ruleNode}"]`)),
      rail: box(document.querySelector('[data-testid="composer-content-rail"]')),
      stage: box(document.querySelector('.composer-mode__stage')),
      addStep: box(document.querySelector('[data-testid="composer-add-step"]')),
      docScrollsX: d.scrollWidth > d.clientWidth + 1,
      docScrollsY: d.scrollHeight > window.innerHeight + 1,
    }
  }, RULE_NODE)
}

test('step-02 reads as a proof contract, not three tags', async ({ page }) => {
  await openComposer(page, 1440, 900)
  const card = page.locator('.react-flow__node[data-id="step-02"]')
  await expect(card.locator('.chain-node__expects')).toHaveText('expects')
  await expect(card.locator('.chip--own-run')).toHaveText('run · 3 queries')
  // The bare type tag is gone from the card.
  await expect(card.locator('.chip').filter({ hasText: /^XQL$/ })).toHaveCount(0)
  // Words, not uppercase tags: the chip is not transformed by the base .chip rule.
  expect(await card.locator('.chip--own-run').evaluate((el) => getComputedStyle(el).textTransform)).toBe('none')
})

test(`${RULE} is drawn as an intent-only rule the scenario does not declare`, async ({ page }) => {
  await openComposer(page, 1440, 900)
  const rule = page.getByTestId(`chain-rule-${RULE}`)
  await expect(rule).toBeVisible()
  await expect(rule).toContainText(RULE)
  await expect(rule).toContainText('Anomalous Gemini Usage Volume Single User')
  await expect(rule).toContainText('INTENT ONLY · nothing to import')
  await expect(rule).toContainText('not declared by this scenario · not scored')
  await expect(rule).toContainText('3 of 3 inputs on steps')
  // The status is also carried by the card's own class, never by colour alone.
  await expect(page.locator(`.react-flow__node[data-id="${RULE_NODE}"] .chain-node--rule-intent-only`)).toHaveCount(1)
  // The chrome names the count and the worst status.
  await expect(page.getByTestId('composer-content-toggle')).toContainText('1 rule · intent only')

  // The proof image. Written to the test's output dir (kept on failure, or
  // always with `preserveOutput: 'always'`) and attached to the report.
  const shot = test.info().outputPath('composer-correlation-story.png')
  await page.screenshot({ path: shot })
  await test.info().attach('composer-correlation-story', { path: shot, contentType: 'image/png' })
})

test('the rule is joined to step-02 by a labelled, inert edge', async ({ page }) => {
  await openComposer(page, 1440, 900)
  // The step's content port is added AFTER React Flow first measures the card
  // (the cited card loads later). The edge only draws if the node's internals
  // were refreshed; this is the assertion that caught it missing.
  const edge = page.locator(`.react-flow__edge[data-id="step-02~>${RULE_NODE}"]`)
  await expect(edge).toHaveCount(1)
  const d = await edge.locator('path').first().getAttribute('d')
  expect(d && d.length, 'the edge must have a drawn path').toBeGreaterThan(10)
  await expect(page.locator('.composer-content-label')).toHaveText('feeds · 3 inputs')
  // Only step-02 feeds it: step-01 and step-03 declare other queries.
  await expect(page.locator(`.react-flow__edge[data-id$="~>${RULE_NODE}"]`)).toHaveCount(1)
})

for (const { w, h } of [{ w: 1440, h: 900 }, { w: 1024, h: 768 }]) {
  test(`the rail sits below every step card and nothing scrolls at ${w}x${h}`, async ({ page }) => {
    await openComposer(page, w, h)
    // The rule arrives with its card, AFTER the first fit. The canvas refits
    // once for it (unless the DC has already panned); wait for that to settle
    // rather than measuring the frame before it.
    await expect.poll(async () => {
      const now = await boxes(page)
      return !!now.rule && !!now.stage && now.rule.bottom <= now.stage.bottom
    }, { message: 'the rule node must be brought into view when it arrives', timeout: 8_000 }).toBe(true)
    const b = await boxes(page)
    expect(b.rail, 'the rail must be drawn').not.toBeNull()
    expect(b.rule, 'the rule node must be drawn').not.toBeNull()
    expect(b.steps.length).toBe(3)

    // The rail clears the real bottom of every step card (it used to be placed
    // by the row pitch and was drawn THROUGH the taller cards).
    for (const s of b.steps) expect(b.rail!.top).toBeGreaterThanOrEqual(s!.bottom - 1)
    // The rule sits below the rail line, inside the stage.
    expect(b.rule!.top).toBeGreaterThan(b.rail!.top)
    expect(b.rule!.bottom).toBeLessThanOrEqual(b.stage!.bottom)
    expect(b.rule!.left).toBeGreaterThanOrEqual(b.stage!.left)
    expect(b.rule!.right).toBeLessThanOrEqual(b.stage!.right)
    // It does not sit under the floating "+ Add step" button.
    const overlapsAdd = b.addStep
      && b.rule!.bottom > b.addStep.top && b.rule!.top < b.addStep.bottom
      && b.rule!.right > b.addStep.left && b.rule!.left < b.addStep.right
    expect(overlapsAdd, 'the rule card must clear the + Add step button').toBeFalsy()

    expect(b.docScrollsX, 'document must not scroll horizontally').toBe(false)
    expect(b.docScrollsY, 'document must not scroll vertically').toBe(false)
  })
}

test('selecting the rule opens a read-only Inspector and edits nothing', async ({ page }) => {
  await openComposer(page, 1440, 900)
  await page.getByTestId(`chain-rule-${RULE}`).click()
  const panel = page.getByTestId('composer-rule-inspector')
  await expect(panel).toBeVisible()
  await expect(panel).toContainText(/authoring note, not XQL/i)
  await expect(panel).toContainText('XQL(AIACC-003 Gemini Request Rate Exceeds Per User Baseline)')
  await expect(panel).toContainText("No correlation rule on this chain's cards is deployable")
  await expect(panel.getByTestId('rule-input-0')).toContainText('expected on step-02')
  await expect(panel.locator('input, textarea, select')).toHaveCount(0)
  // A rule is view state: selecting it never makes the chain read as edited.
  await expect(page.locator('.composer__edited')).toHaveCount(0)

  // Selecting a step afterwards gives the step config back.
  await page.getByTestId('chain-step-step-02').click()
  await expect(panel).toHaveCount(0)
})

test('the rail toggles off and the step chips stay', async ({ page }) => {
  await openComposer(page, 1440, 900)
  const toggle = page.getByTestId('composer-content-toggle')
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  await expect(page.getByTestId(`chain-rule-${RULE}`)).toHaveCount(0)
  await expect(page.getByTestId('composer-content-rail')).toHaveCount(0)
  // NEGATIVE CONTROL for the toggle: the chips are a property of the step.
  await expect(page.locator('.react-flow__node[data-id="step-02"] .chip--own-run')).toHaveText('run · 3 queries')
  // The headline still says what the chain needs while the rail is hidden.
  await expect(toggle).toContainText('1 rule · intent only')
})

test('the Run lens draws no rule: a rule is never lent a run verdict', async ({ page }) => {
  await openComposer(page, 1440, 900)
  await page.getByTestId('composer-lens-run').click()
  await expect(page.getByTestId(`chain-rule-${RULE}`)).toHaveCount(0)
  await expect(page.getByTestId('composer-content-rail')).toHaveCount(0)
})

test('the export route serves what the card holds and invents nothing', async ({ request }) => {
  const resp = await request.get(`/api/scenarios/${SCENARIO}/tenant-content`)
  expect(resp.status()).toBe(200)
  const m = await resp.json()
  expect(m.tenant_verified).toBe(0)
  expect(m.write_path).toBe(false)
  expect(m.catalog_loaded).toBe(true)
  expect(m.rules).toHaveLength(1)
  const rule = m.rules[0]
  expect(rule.rule_id).toBe(RULE)
  expect(rule.content_status).toBe('intent-only')
  expect(rule.binding).toBe('card-only')
  expect(rule.deploy).toBeNull()
  expect(rule).not.toHaveProperty('severity')
  expect(m.summary.correlation.deployable).toBe(0)
  expect(m.queries).toHaveLength(5)
})

test('the Export control downloads that manifest', async ({ page }) => {
  await openComposer(page, 1440, 900)
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByTestId('composer-content-export').click(),
  ])
  expect(download.suggestedFilename()).toBe(`${SCENARIO}-tenant-content.json`)
  await expect(page.getByTestId('composer-notice')).toContainText(/1 rule, 0 deployable/i)
  await expect(page.getByTestId('composer-notice')).toContainText(/nothing was sent to a tenant/i)
})
