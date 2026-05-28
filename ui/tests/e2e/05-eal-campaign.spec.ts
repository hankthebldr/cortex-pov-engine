import { test, expect } from './_fixtures'

/**
 * Golden path #5 — the EAL (External Attack Library) plugin surface.
 *
 * EAL adapters power the AI Access / AIRS / Browser / KOI demo flows.
 * Asserts: API reports at least one plugin and the Adapter Registry
 * sub-view in Coverage renders it. Campaign creation is API-tested
 * separately under tests/eal_simulator/ — here we just need the surface
 * to be reachable.
 *
 * Migrated to Mission Ops Console: the EAL list is no longer its own
 * tab — it lives under Coverage → EAL Plugins sub-view (PR #44; renamed
 * from "Adapters" when the Tool Adapters catalog landed).
 */
test('Adapter Registry opens and shows plugin surface', async ({ page, api, baseURL, request }) => {
  await api.health()

  // Sanity: the plugin endpoint reports at least one plugin
  const plugins = await request.get(`${baseURL}/api/eal/plugins`)
  expect(plugins.ok()).toBe(true)
  const pluginBody = await plugins.json()
  const pluginList = pluginBody.plugins ?? pluginBody
  expect(Array.isArray(pluginList)).toBe(true)
  expect(pluginList.length).toBeGreaterThan(0)

  // UI side — navigate Operations → Coverage → EAL Plugins
  await page.goto('/')
  await page.getByRole('tab', { name: /Coverage/ }).first().click()
  await page.waitForLoadState('networkidle')
  await page.getByRole('tab', { name: /^EAL Plugins$/ }).click()
  await page.waitForLoadState('networkidle')

  // At least one plugin name should surface in the Adapter Registry. Grab
  // the first plugin's name from the API and check it renders.
  const firstName: string = pluginList[0].name
  await expect(page.getByText(new RegExp(firstName)).first()).toBeVisible({
    timeout: 10_000,
  })
})
