import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #1 — application shell loads, health is green, header chrome
 * renders, the detection-plane rail pulls scenario counts, and every
 * primary + secondary view is reachable through the guided stepper.
 *
 * If this fails, every other E2E test is meaningless — keep it first.
 */
test.describe('app shell', () => {
  test('SimCore /api/health responds', async ({ api }) => {
    const body = await api.health()
    expect(body.status).toBe('ok')
  })

  test('UI loads and shows the Cortex header + detection-plane rail', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText(/cortex/i).first()).toBeVisible()
    await expect(page.getByText(/Detection Simulation Engine/i)).toBeVisible()

    // The rail lists all 11 detection planes; each plane button carries a
    // stable data-testid (the scenario grid also surfaces plane names, so a
    // name-regex would trip strict-mode). Check the core planes render.
    for (const id of ['EDR', 'CDR', 'NDR', 'ITDR', 'CLOUD_APP', 'ANALYTICS']) {
      await expect(page.getByTestId(`plane-button-${id}`)).toBeVisible()
    }
  })

  test('stepper + More menu flip the workspace without errors', async ({ page }) => {
    await page.goto('/')
    // Redesign v2: primary workflow is a numbered stepper (Targets /
    // Library / Launch / Live / Evidence); ATT&CK Coverage + Environments
    // live under the "More ▾" menu. gotoView handles both.
    for (const name of ['Targets', 'Library', 'Live', 'Evidence', 'ATT&CK Coverage', 'Environments', 'Launch']) {
      await gotoView(page, name)
    }
    // App is still alive after the full tour
    await expect(page.getByText(/Detection Simulation Engine/i)).toBeVisible()
  })
})
