import { test, expect } from './_fixtures'

/**
 * Golden paths #4 + #5 — the two views DCs spend the most secondary time in:
 *   - MITRE/ATT&CK heatmap renders + the coverage endpoint is hit
 *   - Infra topology generator (Lab tab) lists AWS modules and exposes
 *     a Generate action
 *
 * Migrated to Mission Ops Console (PR #44):
 *   - MITRE heatmap now lives under the ATT&CK Coverage tab
 *   - Infra Generator now lives under the Lab tab (the legacy "Deploy"
 *     tab was renamed during the dark-console rework)
 */
test('ATT&CK Coverage tab renders with MITRE coverage data', async ({ page, api }) => {
  await api.health()
  await page.goto('/')

  // Set up the response listener BEFORE any click — the network call fires
  // as soon as CoverageView mounts. Redesign v2: Coverage is behind
  // "More ▾ → ATT&CK Coverage" (role="menuitem"), so two clicks are needed;
  // the listener must precede both to avoid a race.
  const respPromise = page.waitForResponse('**/api/mitre/coverage', { timeout: 10_000 })
  await page.getByRole('button', { name: /More/ }).click()
  await page.getByRole('menuitem', { name: /ATT&CK Coverage/ }).click()
  const resp = await respPromise
  expect(resp.ok()).toBe(true)

  // Either the heatmap grid or the words "MITRE" / "ATT&CK" / "Coverage" show
  await expect(page.locator('body')).toContainText(/MITRE|ATT&CK|Coverage/i)
})

test('Lab tab lists Infra Generator AWS modules and exposes Generate action', async ({
  page,
  api,
}) => {
  await api.health()
  await page.goto('/')
  // Redesign v2: Lab is now "Environments" under the More menu.
  await page.getByRole('button', { name: /More/ }).click()
  await page.getByRole('menuitem', { name: /Environments/ }).click()
  await page.waitForLoadState('networkidle')

  // base module is always shown per CLAUDE.md design rule
  await expect(page.getByText(/\bbase\b/).first()).toBeVisible({ timeout: 10_000 })
  // EDR module is present in feature-complete AWS catalog
  await expect(page.getByText(/\bedr\b/i).first()).toBeVisible()

  // Generate action surface exists (we don't click it — bundle generation
  // takes 5–10s on a fresh box and is covered by API smoke).
  const generateBtn = page.getByRole('button', { name: /Generate|Build|Create/i }).first()
  await expect(generateBtn).toBeVisible()
})
