import { test, expect } from './_fixtures'

/**
 * Golden path #1 — application shell loads, health is green, header chrome
 * renders, plane selector pulls scenario counts.
 *
 * If this fails, every other E2E test is meaningless — keep it first.
 */
test.describe('app shell', () => {
  test('SimCore /api/health responds', async ({ api }) => {
    const body = await api.health()
    expect(body.status).toBe('ok')
  })

  test('UI loads and shows the Cortex header + plane selector', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText(/Cortex/).first()).toBeVisible()
    await expect(page.getByText(/Detection Simulation Engine/i)).toBeVisible()

    // Plane selector renders all six core planes. The Mission Ops Console
    // (PR #35) packs 49 scenario cards into a single ScenarioGrid and each
    // card surfaces its plane name in the accessible label, so a
    // name-regex selector matched 50+ buttons and tripped Playwright's
    // strict-mode guard. Plane buttons now carry a stable data-testid for
    // any test that needs the *plane button specifically*, not any button
    // whose accessible name happens to contain a plane id.
    for (const id of ['EDR', 'CDR', 'NDR', 'ITDR', 'CLOUD_APP', 'ANALYTICS']) {
      await expect(page.getByTestId(`plane-button-${id}`)).toBeVisible()
    }
  })

  test('view toggles flip the main panel without errors', async ({ page }) => {
    await page.goto('/')
    for (const btn of ['MITRE', 'Deploy', 'EAL', 'Runs']) {
      await page.getByRole('button', { name: new RegExp(btn) }).click()
      // Wait for any view-content to appear or just a network idle tick
      await page.waitForLoadState('networkidle')
    }
  })
})
