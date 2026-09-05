import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #5 — the EAL (External Attack Library) plugin surface that
 * powers the AI Access / AIRS / Browser / KOI flows.
 *
 * Console v2: the EAL list lives under More ▾ → ATT&CK Coverage → the
 * Traffic / EAL surface. Asserts the API reports ≥1 plugin and it renders.
 */
test('EAL Plugins sub-view opens and shows the plugin surface', async ({ page, api, baseURL, request }) => {
  await api.health()

  // Sanity: the plugin endpoint reports at least one plugin
  const plugins = await request.get(`${baseURL}/api/eal/plugins`)
  expect(plugins.ok()).toBe(true)
  const pluginBody = await plugins.json()
  const pluginList = pluginBody.plugins ?? pluginBody
  expect(Array.isArray(pluginList)).toBe(true)
  expect(pluginList.length).toBeGreaterThan(0)

  // EAL was a sub-tab hanging off ATT&CK Coverage; the destination redesign
  // promoted it to a first-class surface (Traffic / EAL), so there is no
  // sub-tab left to click.
  await page.goto('/')
  await gotoView(page, 'EAL Plugins')
  // The surface opens on Campaigns; the plugin catalogue is the builder's.
  // "+ New Campaign" is a TAB in the surface's tablist, not a button — the
  // destination redesign made the builder a sibling tab of Campaigns rather
  // than a control that opens one. getByRole('button') can never match a
  // role=tab, so this timed out for a reason unrelated to the plugin surface.
  await page.getByRole('tab', { name: /New Campaign/i }).click()
  await page.waitForLoadState('networkidle')

  // At least one plugin name from the API should surface. The builder renders
  // the catalogue as <option> elements, which Playwright never reports as
  // visible — assert on the select's option set instead of on visibility, or
  // this fails for a reason that has nothing to do with the plugin surface.
  const firstName: string = pluginList[0].name
  const pluginSelect = page.locator('select').filter({ hasText: /pick a plugin/i }).first()
  await expect(pluginSelect).toBeVisible({ timeout: 10_000 })
  await expect(
    pluginSelect.locator('option', { hasText: new RegExp(firstName) }).first(),
  ).toHaveCount(1)
})
