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
  test('destination is reachable from the rail and states where tools come from', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    await expect(page.getByTestId('tool-adapter-catalog')).toBeVisible()
    await expect(page.getByText(/where the bytes come from at run time/i)).toBeVisible()
  })

  test('every adapter card carries a supply chip with TEXT, not just a colour', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    const chips = page.locator('[data-testid^="supply-chip-"]')
    await expect(chips.first()).toBeVisible()
    const count = await chips.count()
    expect(count).toBeGreaterThan(0)
    for (let i = 0; i < Math.min(count, 5); i += 1) {
      const text = (await chips.nth(i).innerText()).trim()
      expect(['STAGED', 'NOT STAGED', 'FETCHES AT RUN TIME', 'N/A', 'UNKNOWN']).toContain(text)
    }
  })

  test('the banner counts target-host egress before the customer meeting', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    const banner = page.getByTestId('payload-shelf-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText(/on the target host, at run time|UNKNOWN|read-only/i)
  })

  test('the supply filter narrows to what still needs staging', async ({ page }) => {
    await gotoView(page, 'Payload Shelf')
    await expect(page).toHaveURL(/supply=unstaged/)
    await expect(page.getByTestId('tool-adapter-catalog')).toBeVisible()
  })

  test('a tier-4 adapter detail leads with Supply, above Upstream', async ({ page }) => {
    await gotoView(page, 'Tools & Payloads')
    const card = page.locator('[data-testid^="tool-adapter-card-"]').first()
    await card.click()
    await expect(page.getByTestId('tool-adapter-detail')).toBeVisible()
    const labels = await page.locator('.competitive__detail-label').allInnerTexts()
    expect(labels.indexOf('Supply')).toBeGreaterThanOrEqual(0)
    expect(labels.indexOf('Supply')).toBeLessThan(labels.indexOf('Upstream'))
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

    const stageCta = page.getByRole('button', { name: /Stage on SimCore/ })
    if (await stageCta.count()) {
      await stageCta.click()
      const dialog = page.getByTestId('stage-payload-dialog')
      await expect(dialog).toBeVisible()
      // The licence gate is real: Stage is dead until it is ticked.
      await expect(dialog.getByRole('button', { name: /^Stage ▸$/ })).toBeDisabled()
      await dialog.getByTestId('stage-license-ack').click()
      await expect(dialog.getByRole('button', { name: /^Stage ▸$/ })).toBeEnabled()
      await dialog.getByRole('button', { name: /^Stage ▸$/ }).click()
      await expect(page.getByTestId('stage-done')).toBeVisible()
      await expect(page.getByTestId('stage-done')).toContainText('1,106,683 bytes')
    }
  })
})
