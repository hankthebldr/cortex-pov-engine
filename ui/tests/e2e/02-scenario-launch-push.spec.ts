import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #2 — the redesigned guided launch flow (console v2):
 *   ① Targets (pick push bundle) → ② Library (arm a scenario) →
 *   ③ Launch (authorize the dual-use tool consent gate) → fire.
 *
 * SIM-EDR-001 is wired to TOOL-ATOMIC-RED-TEAM (dual-use-lab-only), so the
 * Launch step must surface the consent prompt and block the button until
 * it's checked — this exercises that gate end-to-end.
 */
test('DC arms a scenario against a target and launches through the consent gate', async ({ page, api }) => {
  await api.health()
  await page.goto('/')

  // ① Targets — pick the always-ready offline push bundle
  await gotoView(page, 'Targets')
  await page.locator('.target-card--push').first().click()

  // ② Library — filter to EDR and arm a scenario (clicking the card arms it)
  await gotoView(page, 'Library')
  await page.getByTestId('plane-button-EDR').click()
  const card = page.getByText(/SIM-EDR-001/).first()
  await expect(card).toBeVisible({ timeout: 10_000 })
  await card.click()

  // ③ Launch — armed scenario + selected target compose here
  await gotoView(page, 'Launch')
  await expect(page.locator('.launch-card__title').first()).toBeVisible({ timeout: 10_000 })

  // dual-use consent gate: button is disabled until consent is granted
  const launchBtn = page.getByRole('button', { name: /Launch run/i })
  const consent = page.locator('.launch-consent input[type="checkbox"]').first()
  await expect(consent).toBeVisible({ timeout: 5_000 })
  await expect(launchBtn).toBeDisabled()
  await consent.check()
  await expect(launchBtn).toBeEnabled()

  // fire — expect the success result banner / toast
  await launchBtn.click()
  await expect(
    page.getByText(/started|success|launched|run /i).first(),
  ).toBeVisible({ timeout: 15_000 })
})
