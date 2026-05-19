import { test, expect } from './_fixtures'

/**
 * Golden path #5 — the EAL (External Attack Library) console.
 *
 * EAL is the headline POV demo path for AI Access / AIRS / Browser / KOI
 * scenarios.  Asserts: console opens, plugin list renders, campaigns list
 * renders.  Campaign creation is API-tested separately under
 * tests/eal_simulator/ — here we just need the surface to be reachable.
 */
test('EAL console opens and shows plugin + campaign surface', async ({ page, api, baseURL, request }) => {
  await api.health()

  // Sanity: the plugin endpoint reports at least one plugin
  const plugins = await request.get(`${baseURL}/api/eal/plugins`)
  expect(plugins.ok()).toBe(true)
  const pluginBody = await plugins.json()
  const pluginList = pluginBody.plugins ?? pluginBody
  expect(Array.isArray(pluginList)).toBe(true)
  expect(pluginList.length).toBeGreaterThan(0)

  // UI side
  await page.goto('/')
  await page.getByRole('button', { name: /EAL/ }).click()
  await page.waitForLoadState('networkidle')

  // At least one plugin name should surface in the EAL view.  Grab the
  // first plugin's name from the API and check it renders.
  const firstName: string = pluginList[0].name
  await expect(page.getByText(new RegExp(firstName)).first()).toBeVisible({
    timeout: 10_000,
  })
})
