import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Catalog › TTP cards — group and sort by use case, test case and FY27
 * subdomain, driven against the served console and the real corpus.
 *
 * WHY AN E2E SPEC
 * ---------------
 * The unit suite proves the join and the render against three mocked lists.
 * Three things it cannot reach:
 *
 *   1. THE SERVED API. Grouping needs `expected_detections[].ttp_ref` in the
 *      scenario LIST projection. A SimCore image older than that field serves
 *      a list the console correctly reads as degraded — so on a stale bake
 *      this whole page silently loses the feature. The first assertion below
 *      is that guard, and its message says what to rebuild.
 *   2. THE ROUTER. `?group=` has to survive a real navigation into the UC / TC
 *      index and a real browser Back. jsdom has no history stack worth the
 *      name; the first draft of this view kept a stale grouping when the hash
 *      changed under it, and only a browser showed that.
 *   3. LAYOUT. A group row is id + title + subdomain + count + link on one
 *      line. At 375px that was 245px of content in a 146px button, with the
 *      count painted over "Open in index". Every rect in jsdom is zero.
 *
 * Read-only: it launches nothing and writes nothing. Counts are asserted as
 * relations (a group has cards; placements >= cards), never as literals, so a
 * content pass that grows the corpus does not break it.
 */

const groupSections = (page: Page) => page.locator('[data-testid^="ttp-group-section-"]')

async function openCatalog(page: Page, query = '') {
  // Enter from another destination first: a hash-only change on the same
  // destination does not remount the surface.
  await page.goto('/#/start')
  await page.goto(`/#/ttps${query}`)
  await expect(page.getByTestId('ttp-browser')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('[data-testid^="ttp-card-TTP"], [data-testid^="ttp-group-section-"]').first())
    .toBeVisible({ timeout: 20_000 })
}

async function expectIndexJoin(page: Page) {
  const chip = page.getByTestId('ttp-group-by-subdomain')
  await expect(
    chip,
    'Group by is disabled: this SimCore does not serve the card → scenario link '
    + '(expected_detections[].ttp_ref in GET /api/scenarios) or the UC / TC index is not loaded. '
    + 'Rebuild the image from this tree; the reason is printed in the organize bar.',
  ).toBeEnabled({ timeout: 20_000 })
}

test('group by subdomain is an outline of the real corpus, and says what it is not', async ({ page, api }) => {
  await api.health()
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openCatalog(page)
  await expectIndexJoin(page)

  const cardTotal = Number((await page.locator('.ttpb-stat__value').first().innerText()).trim())
  expect(cardTotal).toBeGreaterThan(0)

  await page.getByTestId('ttp-group-by-subdomain').click()
  await expect(page).toHaveURL(/#\/ttps\?group=subdomain/)
  await expect(page.getByTestId('ttp-group-by-subdomain')).toHaveAttribute('aria-pressed', 'true')

  const sections = groupSections(page)
  await expect(sections.first()).toBeVisible()
  const counts = await sections.locator('[data-testid^="ttp-group-count-"]').allInnerTexts()
  const perGroup = counts.map((t) => Number(t.match(/^(\d+) cards?$/)?.[1] ?? NaN))
  expect(perGroup.length).toBeGreaterThan(1)
  for (const n of perGroup) expect(n, 'no empty group is ever rendered').toBeGreaterThan(0)
  // Under "every test case evidenced" a card can sit in several groups, so the
  // group counts add up to AT LEAST the card total — and the bar must say so
  // rather than let the sum pass for a count.
  const placements = perGroup.reduce((a, b) => a + b, 0)
  expect(placements).toBeGreaterThanOrEqual(cardTotal)
  const note = page.getByTestId('ttp-organize-note')
  await expect(note).toContainText(`${cardTotal} cards in ${perGroup.length} groups`)
  if (placements > cardTotal) await expect(note).toContainText('listed more than once')
  await expect(note, 'authored is not proven').toContainText('not proof a detection fired')

  // A long outline starts collapsed; opening a group shows exactly its count.
  const first = sections.first()
  const toggle = first.locator('.ttpb-group__toggle')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(first.locator('[data-testid^="ttp-card-TTP"]')).toHaveCount(perGroup[0])
  // Every card in an index group says where it sits.
  await expect(first.locator('[data-testid^="ttp-card-uctc-"]')).toHaveCount(perGroup[0])

  // "Primary test case only" can only remove placements, never add them.
  await page.getByTestId('ttp-include-primary').click()
  await expect(page).toHaveURL(/include=primary/)
  const primaryCounts = await groupSections(page).locator('[data-testid^="ttp-group-count-"]').allInnerTexts()
  const primaryPlacements = primaryCounts.reduce((a, t) => a + Number(t.match(/^(\d+)/)?.[1] ?? 0), 0)
  expect(primaryPlacements).toBeLessThanOrEqual(placements)
  expect(primaryPlacements).toBeGreaterThanOrEqual(cardTotal)
})

test('the grouping survives a trip into the UC / TC index and a browser Back', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openCatalog(page, '?group=uc')
  await expectIndexJoin(page)
  await expect(page.getByTestId('ttp-group-by-uc')).toHaveAttribute('aria-pressed', 'true')

  const sections = groupSections(page)
  await expect(sections.first()).toBeVisible()
  const before = await sections.count()
  const firstKey = (await sections.first().getAttribute('data-testid'))!.replace('ttp-group-section-', '')
  expect(firstKey, 'use-case groups are keyed by the index id').toMatch(/^UC-/)

  await page.getByTestId(`ttp-group-open-${firstKey}`).click()
  await expect(page).toHaveURL(new RegExp(`#/uctc\\?uc=${firstKey}$`))
  await expect(page.getByTestId('uctc-index')).toBeVisible({ timeout: 15_000 })

  await page.goBack()
  await expect(page).toHaveURL(/#\/ttps\?group=uc/)
  await expect(page.getByTestId('ttp-group-by-uc')).toHaveAttribute('aria-pressed', 'true')
  await expect(groupSections(page)).toHaveCount(before)

  // Editing the hash under the mounted view regroups it (no remount happens).
  await page.evaluate(() => { window.location.hash = '#/ttps?group=tc' })
  await expect(page.getByTestId('ttp-group-by-tc')).toHaveAttribute('aria-pressed', 'true')
  await expect(groupSections(page).first()).toHaveAttribute('data-testid', /^ttp-group-section-TC-/)
})

test('sort by test case orders the flat grid by the card\'s own test case', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await openCatalog(page)
  await expectIndexJoin(page)
  await page.getByTestId('ttp-sort-by-tc').click()
  await expect(page).toHaveURL(/sort=tc/)
  await expect(page.getByTestId('ttp-pager'), 'flat mode keeps its pager').toBeVisible()

  const faces = await page.locator('[data-testid^="ttp-card-uctc-"]').allInnerTexts()
  const tcs = faces.map((t) => t.match(/TC-[A-Z0-9]+-\d+/)?.[0]).filter(Boolean) as string[]
  expect(tcs.length).toBeGreaterThan(5)
  const sorted = [...tcs].sort((a, b) => a.localeCompare(b, 'en', { numeric: true, sensitivity: 'base' }))
  expect(tcs, 'page 1 is in natural test-case order').toEqual(sorted)
})

for (const vp of [{ w: 1440, h: 900 }, { w: 900, h: 700 }, { w: 375, h: 812 }]) {
  test(`group rows hold together at ${vp.w}x${vp.h}`, async ({ page }) => {
    await page.setViewportSize({ width: vp.w, height: vp.h })
    await openCatalog(page, '?group=tc')
    await expectIndexJoin(page)
    await expect(groupSections(page).first()).toBeVisible()

    const report = await page.evaluate(() => {
      const d = document.documentElement
      const heads = [...document.querySelectorAll('.ttpb-group__head')].slice(0, 12)
      const hit = (a: DOMRect, b: DOMRect) =>
        !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom)
      return {
        pageScrollsX: d.scrollWidth > d.clientWidth + 1,
        rows: heads.length,
        countOverLink: heads.filter((h) => {
          const count = h.querySelector('.ttpb-group__count')
          const link = h.querySelector('.ttpb-group__open')
          return count && link && hit(count.getBoundingClientRect(), link.getBoundingClientRect())
        }).length,
        clipped: heads.filter((h) => {
          const t = h.querySelector('.ttpb-group__toggle') as HTMLElement
          const count = h.querySelector('.ttpb-group__count') as HTMLElement
          // The count must end inside its own button, not spill past it.
          return count.getBoundingClientRect().right > t.getBoundingClientRect().right + 1
        }).length,
      }
    })
    expect(report.rows).toBeGreaterThan(0)
    expect(report.pageScrollsX, 'the page never scrolls sideways').toBe(false)
    expect(report.countOverLink, 'a card count drawn over "Open in index"').toBe(0)
    expect(report.clipped, 'a card count spilling out of its row').toBe(0)
  })
}
