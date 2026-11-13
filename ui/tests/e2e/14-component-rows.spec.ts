import { test, expect } from './_fixtures'

/**
 * Component page rows after visiting the Components hub.
 *
 * The hub and the component pages each style a class named `.cmp-row` (a grid
 * card on the hub, a flex row on a page). Stylesheets stay loaded once their
 * page has been visited, so opening the hub and then a component page drew
 * the page's rows with the hub's grid: the status word sat on top of the
 * title. jsdom loads no layout, so only a browser can see it.
 */
test('a component page row keeps its own layout after the Components hub was open', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/components')
  await page.waitForSelector('.cmp-hub .cmp-row')
  // In-app navigation: both stylesheets are now loaded.
  await page.evaluate(() => { window.location.hash = '#/component-pov' })
  await page.waitForSelector('.cmp-page .cmp-row')

  const rows = await page.evaluate(() => [...document.querySelectorAll('.cmp-page .cmp-row')].map((row) => {
    const word = row.querySelector('.kit-status__word')?.getBoundingClientRect()
    const title = row.querySelector('.cmp-row__title')?.getBoundingClientRect()
    return {
      display: getComputedStyle(row).display,
      overlaps: !!word && !!title && word.right > title.left + 1,
    }
  }))
  expect(rows.length).toBeGreaterThan(0)
  for (const r of rows) {
    expect(r.display).toBe('flex')
    expect(r.overlaps, 'the status word must not sit on the row title').toBe(false)
  }
})
