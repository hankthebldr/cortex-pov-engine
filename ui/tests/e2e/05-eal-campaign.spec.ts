import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #5 — the EAL (External Attack Library) plugin surface that
 * powers the AI Access / AIRS / Browser / KOI flows.
 *
 * Console v2: the EAL list lives under More ▾ → ATT&CK Coverage → the
 * "EAL Plugins" sub-tab. Asserts the API reports ≥1 plugin and it renders.
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

  // UI: More ▾ → ATT&CK Coverage → EAL Plugins sub-tab
  await page.goto('/')
  await gotoView(page, 'ATT&CK Coverage')
  await page.getByRole('tab', { name: /^EAL Plugins$/ }).click()
  await page.waitForLoadState('networkidle')

  // At least one plugin name from the API should surface
  const firstName: string = pluginList[0].name
  await expect(page.getByText(new RegExp(firstName)).first()).toBeVisible({ timeout: 10_000 })
})
