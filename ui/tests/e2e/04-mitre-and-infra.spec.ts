import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #4 — a secondary view, now under the "More ▾" menu of the
 * redesigned stepper: ATT&CK Coverage (MITRE heatmap + /api/mitre/coverage).
 * The Environments (IaC generator) path moved to feature/scenarios-cloud-posture.
 */
test('ATT&CK Coverage renders with MITRE coverage data', async ({ page, api }) => {
  await api.health()
  await page.goto('/')

  // Arm the response listener BEFORE navigating — the fetch fires as soon
  // as the Reports MITRE ATT&CK tab mounts.
  const respPromise = page.waitForResponse('**/api/mitre/coverage', { timeout: 10_000 })
  await gotoView(page, 'ATT&CK Coverage')
  const resp = await respPromise
  expect(resp.ok()).toBe(true)

  await expect(page.locator('body')).toContainText(/MITRE|ATT&CK|Coverage/i)
})
