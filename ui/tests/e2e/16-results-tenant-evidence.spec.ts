import { test, expect } from './_fixtures'

/**
 * Results › Tenant evidence, the one case panel, and Review behind a confirm.
 *
 * WHY AN E2E SPEC
 * ---------------
 * jsdom proves the copy, the single POST and the single case panel. What a DC
 * deploys is the baked image, and the claims only a browser settles are where
 * things land: the scorecard on Detections, the tenant panel on its own tab,
 * and nothing overflowing a 375 px phone.
 *
 * The run is a push launch that is then read back by importing an empty batch
 * of observations (POST /observations: no tenant credential, no outbound
 * call), which stamps the run `reconcile:manual-import`, so SimCore treats it
 * as read back and the console shows its Results. Opening the page reads the
 * stored tenant evidence once (a GET); nothing here reads a tenant.
 */

const BODY = "Review reads the tenant and may change this run's verdict, marking detections "
  + 'detected or missed. Refreshing tenant evidence never does.'

test.describe.configure({ mode: 'serial' })

let runId = ''

test.beforeAll(async ({ request, baseURL }) => {
  const r = await request.post(`${baseURL}/api/run`, {
    data: {
      scenario_id: 'SIM-EDR-001',
      mode: 'push',
      consent: { simulation_authorized: true, c2_authorized: true },
    },
  })
  expect(r.ok(), 'launch').toBeTruthy()
  runId = (await r.json()).run_id as string
  const o = await request.post(`${baseURL}/api/runs/${runId}/observations`, {
    data: { observations: [], host: 'e2e-read-back-host' },
  })
  expect(o.ok(), 'manual import').toBeTruthy()
})

test('Results: Detections holds the scorecard; the tenant evidence panel is its own tab', async ({ page }) => {
  await page.goto(`/#/proof?run=${runId}`)
  const kpi = page.locator('.kpi').first()
  await expect(kpi).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.kpi__label', { hasText: 'Analytics plane' })).toBeVisible()
  await expect(page.locator('.kpi__label', { hasText: 'XSIAM Stitch' })).toHaveCount(0)
  await expect(page.getByTestId('tenant-evidence')).toHaveCount(0)

  await page.getByRole('tab', { name: 'Tenant evidence' }).click()
  await expect(page).toHaveURL(/tab=tenant/)
  await expect(page.getByTestId('tenant-evidence')).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.kpi')).toHaveCount(0)
})

test('the case panel is on Results › Tenant evidence, once, and not on the run’s page', async ({ page }) => {
  await page.goto(`/#/proof?run=${runId}&tab=tenant`)
  await expect(page.getByTestId('run-case').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('run-case')).toHaveCount(1)

  await page.goto(`/#/runs?run=${runId}`)
  await expect(page.getByTestId('runtime-log')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('run-case')).toHaveCount(0)
  await expect(page.getByTestId('tenant-evidence')).toHaveCount(0)
})

test('Review on a reviewed run asks first, and Cancel posts nothing', async ({ page }) => {
  const posts: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/reconcile')) posts.push(req.url())
  })
  await page.goto(`/#/proof?run=${runId}&tab=tenant`)
  await expect(page.getByTestId('tenant-evidence')).toBeVisible({ timeout: 15_000 })
  // exact: the safety banner's "Review scope" also contains the word.
  await page.getByRole('button', { name: 'Review', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Review this run?')
  await expect(dialog).toContainText(BODY)
  await page.getByRole('button', { name: /^Cancel/ }).click()
  await expect(dialog).toHaveCount(0)
  expect(posts, 'cancel sends no reconcile').toEqual([])
})

test('nothing overflows horizontally at 375 px', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const pages: Array<[string, string]> = [
    [`/#/proof?run=${runId}`, 'results'],
    [`/#/proof?run=${runId}&tab=tenant`, 'tenant-evidence'],
    [`/#/runs?run=${runId}`, 'runtime-log'],
  ]
  for (const [url, landmark] of pages) {
    await page.goto(url)
    await expect(page.getByTestId(landmark)).toBeVisible({ timeout: 15_000 })
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow, `${url} overflows by ${overflow}px`).toBeLessThanOrEqual(0)
  }
})
