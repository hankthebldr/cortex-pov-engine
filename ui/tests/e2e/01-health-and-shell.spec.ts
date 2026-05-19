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

    // Plane selector renders all six labels
    for (const label of ['EDR', 'CDR', 'NDR', 'ITDR', 'Cloud App', 'Analytics']) {
      await expect(page.getByRole('button', { name: new RegExp(label) })).toBeVisible()
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
