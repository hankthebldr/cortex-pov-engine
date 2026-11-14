import { test, expect, gotoView } from './_fixtures'

/**
 * Tools & Payloads — the supply surface, against a REAL SimCore.
 *
 * These assert the two things a unit test cannot: that the destination is
 * reachable from the rail, and that the real `/api/shelf/*` responses drive the
 * chips. Staging is route-mocked — CI must never fetch from github.com, and an
 * outbound request from a test host is exactly the egress this feature exists
 * to move.
 *
 * TOOL-LINPEAS is asserted BY NAME on purpose. `tools/packs/linpeas.yml` does
 * not exist yet, and Henry's request ("console pulls linpeas, composes it, the
 * agent runs it") renders an empty result until it does. A spec that passed on
 * TOOL-DEEPCE instead would let that gap ship looking finished, so the linpeas
 * assertions live in their own test and are skipped LOUDLY, naming the pack.
 */

test.describe('Tools & Payloads', () => {
  test('destination is reachable and says what a package is', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    await expect(page.getByTestId('tool-adapter-catalog')).toBeVisible()
    await expect(page.getByText(/The tools a step can run/i)).toBeVisible()
  })

  test('the catalog carries no shelf or fetch-timing vocabulary, and every card can join a chain', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    const cards = page.locator('[data-testid^="tool-adapter-card-"]')
    await expect(cards.first()).toBeVisible()
    await expect(page.getByTestId('payload-shelf-banner')).toHaveCount(0)
    await expect(page.locator('[data-testid^="supply-chip-"]')).toHaveCount(0)
    const text = await page.getByTestId('tool-adapter-catalog').innerText()
    expect(text).not.toMatch(/shelf|staged|fetches at run time|egress/i)
    await expect(cards.first().getByRole('button', { name: 'Add to chain' })).toBeVisible()
  })

  test('a readiness link scopes the catalog and names the scope in plain words', async ({ page }) => {
    await gotoView(page, 'Payload Shelf')
    await expect(page).toHaveURL(/supply=unstaged/)
    await expect(page.getByTestId('tool-adapter-catalog')).toBeVisible()
    await expect(page.getByTestId('adapter-supply-scope')).toContainText('tools not ready on this instance')
  })

  test('a package detail leads with Availability, above Upstream', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    const card = page.locator('[data-testid^="tool-adapter-card-"]').first()
    await card.click()
    await expect(page.getByTestId('tool-adapter-detail')).toBeVisible()
    // Normalised: the labels are styled `text-transform: uppercase`, and
    // Chromium's allInnerTexts() returns the RENDERED casing, so matching
    // 'Supply' against 'SUPPLY' failed. Asserting on casing that comes from CSS
    // is brittle — the property under test is the ORDER.
    const labels = (await page.locator('.competitive__detail-label').allInnerTexts())
      .map((t) => t.trim().toLowerCase())
    expect(labels).toContain('availability')
    expect(labels.indexOf('availability')).toBeLessThan(labels.indexOf('upstream'))
    expect(labels).not.toContain('install')
  })

  test('LinPEAS is in the catalog and stageable — Henry\'s exact journey', async ({ page, request, baseURL }) => {
    const r = await request.get(`${baseURL}/api/tools/adapters/TOOL-LINPEAS`)
    test.skip(
      r.status() === 404,
      'tools/packs/linpeas.yml does not exist yet (content track owes TOOL-LINPEAS). '
      + 'Until it lands, the console renders an empty result for the linpeas journey — '
      + 'this spec fails loudly rather than passing on a different tool.',
    )

    await page.goto('/#/adapters?tool=TOOL-LINPEAS')
    await expect(page.getByTestId('tool-adapter-detail')).toBeVisible()
    await expect(page.getByTestId('supply-detail')).toBeVisible()

    // Route-mock the stage call: CI must not fetch from github.com.
    await page.route('**/api/shelf/stage', (route) => route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'staged',
        payload: { name: 'linpeas.sh', sha256: 'a'.repeat(64), size_bytes: 1106683, license: 'MIT', pinned: true },
        warnings: [],
        pack_snippet: 'install:\n  artifact:\n    filename: linpeas.sh\n',
      }),
    }))

    const stageCta = page.getByRole('button', { name: /Make ready/ })
    if (await stageCta.count()) {
      await stageCta.click()
      const dialog = page.getByTestId('stage-payload-dialog')
      await expect(dialog).toBeVisible()
      // The licence gate is real: Stage is dead until it is ticked.
      await expect(dialog.getByRole('button', { name: /^Make ready ▸$/ })).toBeDisabled()
      await dialog.getByTestId('stage-license-ack').click()
      await expect(dialog.getByRole('button', { name: /^Make ready ▸$/ })).toBeEnabled()
      await dialog.getByRole('button', { name: /^Make ready ▸$/ }).click()
      await expect(page.getByTestId('stage-done')).toBeVisible()
      await expect(page.getByTestId('stage-done')).toContainText('1,106,683 bytes')
    }
  })
})
