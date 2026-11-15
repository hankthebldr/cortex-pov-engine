import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #2 — the Launch gate (design v33 MVP):
 *   Scenarios (browse) → Launch gate armed with SIM-EDR-001:
 *   What runs › Readiness › Where it runs › What it will do →
 *   the authorisation tick → ONE Run.
 *
 * Push is the offline bundle in "Where it runs". Run saves the bundle and
 * records the run, which reads Staged: the DC runs the bundle on the target
 * and POVengine takes their word until it reports in. There is no separate
 * "Download bundle" button.
 *
 * SIM-EDR-001 is wired to TOOL-ATOMIC-RED-TEAM (dual-use-lab-only), so the
 * tool consent must block Run on its own: the authorisation tick alone does
 * not unblock it.
 */
test('DC arms a scenario at the Launch gate and runs it as an offline bundle through the consent gate', async ({ page, api, request }) => {
  await api.health()
  await page.goto('/')

  // Scenarios — filter to EDR and confirm the scenario is browsable.
  await gotoView(page, 'Library')
  await page.getByTestId('plane-button-EDR').click()
  await expect(page.getByText(/SIM-EDR-001/).first()).toBeVisible({ timeout: 10_000 })

  // Launch gate, armed by route.
  await gotoView(page, 'Launch gate', { arm: 'SIM-EDR-001' })
  const panel = page.getByTestId('new-run')
  await expect(panel).toBeVisible({ timeout: 10_000 })
  await expect(panel.locator('.new-run__what-meta')).toContainText('SIM-EDR-001')

  // One primary action, and it is Run; nothing else downloads the bundle.
  const run = panel.getByRole('button', { name: 'Run', exact: true })
  await expect(panel.locator('.kit-btn--primary')).toHaveCount(1)
  await expect(run).toBeDisabled()
  await expect(page.getByRole('button', { name: /download/i })).toHaveCount(0)

  // Where it runs — the offline bundle. It needs no enrolled agent.
  const bundle = page.getByTestId('new-run-target-push-bundle')
  await bundle.click()
  await expect(bundle).toHaveAttribute('aria-checked', 'true')
  const blockers = page.getByTestId('launch-blockers')
  await expect(blockers).not.toContainText('Choose where it runs')
  await expect(blockers).not.toContainText('No agent enrolled')

  // The authorisation tick starts unticked and blocks Run.
  const authorised = page.getByTestId('new-run-authorised')
  await expect(authorised).not.toBeChecked()
  await expect(blockers).toContainText('Confirm you are authorised')
  await authorised.check()

  // The dual-use tool consent still blocks Run on its own.
  await expect(page.locator('.launch-consent')).toContainText('Atomic Red Team')
  await expect(run).toBeDisabled()
  await expect(blockers).toContainText('lab-only consent')
  await page.locator('.launch-consent input[type="checkbox"]').first().check()
  await expect(run).toBeEnabled()
  await expect(blockers).toHaveCount(0)

  // Run — saves the bundle, records the run and opens it.
  const download = page.waitForEvent('download')
  await run.click()
  expect((await download).suggestedFilename()).toMatch(/SIM-EDR-001/i)
  await expect(page).toHaveURL(/#\/runs\?(.*&)?run=/, { timeout: 15_000 })
  const runId = new URLSearchParams(page.url().split('?')[1]).get('run')
  expect(runId).toBeTruthy()

  // The run is Staged: recorded, and waiting for the DC to run the bundle.
  const r = await request.get(`/api/runs/${runId}`)
  expect(r.ok()).toBeTruthy()
  const body = await r.json()
  expect(body.status ?? body.run?.status).toBe('staged')
  await expect(
    page.getByTestId('run-lifecycle').getByText('Staged', { exact: true }).first(),
  ).toBeVisible({ timeout: 10_000 })
})
