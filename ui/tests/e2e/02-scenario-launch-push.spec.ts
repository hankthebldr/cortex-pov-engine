import { test, expect } from './_fixtures'

/**
 * Golden path #2 — the most common DC workflow:
 *   pick plane → pick scenario → switch to Push mode → trigger Launch.
 *
 * This is the smoke test for the *user-visible* launch pipeline; the API
 * smoke harness covers the backend slice separately.
 */
test('DC can launch a push run from the UI', async ({ page, api }) => {
  await api.health() // bail early if SimCore is down

  await page.goto('/')

  // Filter to EDR plane (most scenarios)
  await page.getByRole('button', { name: /^EDR\b/ }).click()

  // Wait for the scenario library to populate
  const libraryHeader = page.getByRole('heading', { name: /Scenario Library/ })
  await expect(libraryHeader).toBeVisible()

  // Click the first scenario row.  Use text "SIM-EDR" which always appears
  // in the id column for an EDR row.
  const firstScenario = page.getByText(/SIM-EDR-\d+/).first()
  await expect(firstScenario).toBeVisible({ timeout: 10_000 })
  await firstScenario.click()

  // LaunchPanel should appear
  await expect(page.getByRole('heading', { name: /Launch Panel/ })).toBeVisible()

  // Switch to Push mode (avoids needing a connected agent)
  await page.getByRole('button', { name: /^▲ Push|Push$/ }).click()

  // Launch
  await page.getByRole('button', { name: /Launch Run/ }).click()

  // Either a success toast OR the LaunchPanel's inline status string
  await expect(page.getByText(/started|success|run /i).first()).toBeVisible({
    timeout: 15_000,
  })
})
