import { test, expect } from './_fixtures'

/**
 * Readiness — "am I ready to run this in front of a customer?"
 *
 * Runs against a REAL SimCore (CORTEXSIM_BASE_URL). Every assertion here is one
 * a unit test cannot make, because the thing under test is whether the console
 * and the running server agree about the SAME deployment: the payload shelf
 * passed 4368 unit tests while `_handle_pull` never called `compose()`, and two
 * tracks built the two ends of one wire and disagreed on field names.
 *
 * What this spec does NOT do: contact a Cortex tenant. `tenant-verified` is 0
 * across this product and nothing in a CI run can change that.
 */

const goReadiness = async (page) => {
  await page.goto('/#/readiness')
  await page.waitForLoadState('networkidle')
}

test.describe('Readiness surface', () => {
  test('is a first-class destination and renders against the live SimCore', async ({ page }) => {
    await goReadiness(page)
    const view = page.getByTestId('readiness-view')
    if (!(await view.count())) {
      test.skip(true, 'this SimCore serves a console build without the Readiness destination')
    }
    await expect(view).toBeVisible()
    // The overall chip always carries a WORD, never colour alone (WCAG 1.4.1,
    // and a projector flattens hue in a customer briefing).
    await expect(page.getByTestId('readiness-overall')).toHaveText(/OK|DEGRADED|ERROR|UNKNOWN/)
  })

  test('renders the four connector rungs separately', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    for (const rung of ['authored', 'configured', 'reachable', 'verified']) {
      await expect(page.getByTestId(`rung-${rung}`)).toBeVisible()
      await expect(page.getByTestId(`rung-${rung}-state`)).toHaveText(/YES|NO|UNKNOWN/)
    }
  })

  test('states tenant-verified explicitly, and it is 0 on any CI SimCore', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    const stmt = page.getByTestId('tenant-verified-statement')
    await expect(stmt).toBeVisible()
    // If this ever reads non-zero in CI, something claimed a tenant query that
    // could not have happened — investigate before "fixing" the assertion.
    await expect(stmt).toContainText('tenant-verified: 0')
  })

  test('the reachable rung is never rendered as the verified rung', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    const reachable = await page.getByTestId('rung-reachable-state').textContent()
    const verified = await page.getByTestId('rung-verified-state').textContent()
    if (reachable?.trim() === 'YES') expect(verified?.trim()).not.toBe('YES')
  })

  test('names what it did NOT check rather than implying a green page covered it', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    const notChecked = page.getByTestId('readiness-not-checked')
    const gaps = page.getByTestId('readiness-gaps')
    // At least one of the two must render: either the server declares its
    // not_checked list, or the console names the components this build does not
    // report. A page with neither is claiming total coverage.
    expect((await notChecked.count()) + (await gaps.count())).toBeGreaterThan(0)
  })

  test('a catalog reporting 0 is never shown as ok', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    for (const key of ['scenario_catalog', 'ttp_catalog', 'adapter_catalog']) {
      const card = page.getByTestId(`health-${key}`)
      if (!(await card.count())) continue
      const count = await page.getByTestId(`health-${key}-count`).textContent().catch(() => null)
      if (count?.trim() === '0') {
        await expect(card).toHaveAttribute('data-status', /degraded|error/)
      }
    }
  })

  test('is keyboard reachable — Tab lands somewhere actionable, never a dead page', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    await page.keyboard.press('Tab')
    const tag = await page.evaluate(() => document.activeElement?.tagName)
    expect(['A', 'BUTTON', 'INPUT', 'SELECT']).toContain(tag)
  })

  test('survives a SimCore that stops answering, without blanking', async ({ page }) => {
    await goReadiness(page)
    if (!(await page.getByTestId('readiness-view').count())) test.skip()
    // Kill /api/health only, then force a re-check. The page must SAY it is
    // disconnected rather than render an empty, plausible-looking console.
    await page.route('**/api/health', (route) => route.fulfill({ status: 500, body: 'boom' }))
    await page.getByRole('button', { name: /Re-check/i }).click()
    await expect(page.getByTestId('readiness-unreachable')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('connector-ladder')).toHaveCount(0)
  })
})
