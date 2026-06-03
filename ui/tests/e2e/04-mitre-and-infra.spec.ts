import { test, expect, gotoView } from './_fixtures'

/**
 * Golden paths #4 + #5 — the two secondary views, now under the "More ▾"
 * menu of the redesigned stepper:
 *   - ATT&CK Coverage (MITRE heatmap + /api/mitre/coverage)
 *   - Environments (the IaC topology generator, formerly the "Lab" tab)
 */
test('ATT&CK Coverage renders with MITRE coverage data', async ({ page, api }) => {
  await api.health()
  await page.goto('/')

  // Arm the response listener BEFORE navigating — the fetch fires as soon
  // as CoverageView mounts.
  const respPromise = page.waitForResponse('**/api/mitre/coverage', { timeout: 10_000 })
  await gotoView(page, 'ATT&CK Coverage')
  const resp = await respPromise
  expect(resp.ok()).toBe(true)

  await expect(page.locator('body')).toContainText(/MITRE|ATT&CK|Coverage/i)
})

test('Environments lists Infra Generator AWS modules and exposes Generate', async ({
  page,
  api,
}) => {
  await api.health()
  await page.goto('/')
  await gotoView(page, 'Environments')

  // base module is always shown per CLAUDE.md design rule; EDR is in the
  // feature-complete AWS catalog.
  await expect(page.getByText(/\bbase\b/).first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText(/\bedr\b/i).first()).toBeVisible()

  // Generate action exists (not clicked — bundle gen is covered by API smoke).
  const generateBtn = page.getByRole('button', { name: /Generate|Build|Create/i }).first()
  await expect(generateBtn).toBeVisible()
})
