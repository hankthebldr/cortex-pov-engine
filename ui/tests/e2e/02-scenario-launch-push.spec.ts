import { test, expect } from './_fixtures'

/**
 * Golden path #2 — the most common DC workflow:
 *   pick plane → pick scenario → switch to Push mode → trigger Launch.
 *
 * Migrated to Mission Ops Console (PR #44):
 *   - Plane button selector uses the stable data-testid
 *   - "Scenario Library" heading no longer exists; we wait for a card to
 *     render instead
 *   - Launch lives in the right-side ScenarioInspector drawer, not a
 *     separate Launch Panel heading
 */
test('DC can launch a push run from the UI', async ({ page, api }) => {
  await api.health() // bail early if SimCore is down

  await page.goto('/')

  // Filter to EDR plane via the stable testid
  await page.getByTestId('plane-button-EDR').click()

  // Wait for an EDR scenario card to render — confirms the grid populated
  const firstScenario = page.getByText(/SIM-EDR-\d+/).first()
  await expect(firstScenario).toBeVisible({ timeout: 10_000 })
  await firstScenario.click()

  // Inspector drawer opens with the pinned launch CTA at top — look for
  // the "ready to launch" label which is part of every drawer head
  await expect(page.getByText(/ready to launch/i).first()).toBeVisible({
    timeout: 5_000,
  })

  // Switch to Push mode (avoids needing a connected agent). The segmented
  // control has two buttons labeled Pull/Push.
  await page.getByRole('button', { name: /^Push$/ }).click()

  // Launch — the inspector's primary CTA. The accessible name is
  // "Launch ⌘L" (button bundles the label + kbd hint), so we anchor
  // on the prefix.
  await page.getByRole('button', { name: /^Launch\b/ }).click()

  // Either a success toast OR the inspector's inline status string
  await expect(page.getByText(/started|success|launched|run /i).first()).toBeVisible({
    timeout: 15_000,
  })
})
