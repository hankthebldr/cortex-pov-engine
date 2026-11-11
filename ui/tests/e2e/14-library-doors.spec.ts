import { test, expect } from './_fixtures'
import type { Page } from '@playwright/test'

/**
 * Library door readiness — the chip on every card and the opt-in "Runnable
 * here" filter, in a real browser against the real corpus.
 *
 * WHY AN E2E SPEC
 * ---------------
 * The unit suite proves the chip text and the filter predicate. It cannot prove
 * that the chip actually renders on a library card, that it fits beside the
 * plane and difficulty chips, or that toggling the filter really removes the
 * rows that are not runnable. Phase 1 writes no door events, so a live SimCore
 * reads NOT CHECKED / NEEDS DOORS everywhere; the filter test therefore stubs
 * GET /api/doors/readiness to make one row `ready` and one `partial`, which is
 * exactly what the UI must handle once phase 2 opens doors.
 *
 * Spec: docs/superpowers/specs/2026-11-11-ingestion-doors-design.md §6
 */

const READY = {
  scenario_id: 'SIM-EDR-001', state: 'ready', scorable: 3, total: 3, underived: 0,
  route_blocked: 0, unevaluated: 0, closed: [], agent_scoped_evaluated: false,
}
const PARTIAL = {
  scenario_id: 'SIM-EDR-002', state: 'partial', scorable: 1, total: 3, underived: 0,
  route_blocked: 0, unevaluated: 0, closed: [{ door_id: 'xdr_agent', detections: 2 }],
  agent_scoped_evaluated: false,
}

async function openLibrary(page: Page) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#/library')
  await page.waitForSelector('.scenario-card', { timeout: 20_000 })
}

async function stubReadiness(page: Page) {
  await page.route('**/api/doors/readiness*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      registry_loaded: true, agent_scoped_evaluated: false,
      scenarios: { 'SIM-EDR-001': READY, 'SIM-EDR-002': PARTIAL },
      tenant_verified: 0, outbound_calls: 0,
    }),
  }))
}

test('SIM-EDR-001 carries a door chip, and nothing is hidden by default', async ({ page }) => {
  await openLibrary(page)
  const chip = page.getByTestId('door-chip-SIM-EDR-001')
  await expect(chip).toBeVisible()
  // Phase 1 truth: no door is open on a fresh SimCore, so the chip is never READY.
  await expect(chip).toHaveText(/NOT CHECKED|NEEDS DOORS|PARTIAL \d+\/\d+/)
  const before = await page.locator('.scenario-card').count()
  expect(before).toBeGreaterThan(1)
  await expect(page.getByTestId('filter-runnable')).toHaveAttribute('aria-pressed', 'false')
})

test('Runnable here keeps ready and partial rows and drops the rest; off restores them', async ({ page }) => {
  await stubReadiness(page)
  await openLibrary(page)
  await expect(page.getByTestId('door-chip-SIM-EDR-001')).toHaveText('READY')
  await expect(page.getByTestId('door-chip-SIM-EDR-002')).toHaveText('PARTIAL 1/3')

  const all = await page.locator('.scenario-card').count()
  await page.getByTestId('filter-runnable').click()
  await expect(page.getByTestId('filter-runnable')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.scenario-card')).toHaveCount(2)
  await expect(page.getByTestId('door-chip-SIM-EDR-001')).toBeVisible()
  await expect(page.getByTestId('door-chip-SIM-EDR-002')).toBeVisible()

  await page.getByTestId('filter-runnable').click()
  await expect(page.locator('.scenario-card')).toHaveCount(all)
})

test('a failed readiness fetch leaves every card NOT CHECKED, not hidden and not READY', async ({ page }) => {
  await page.route('**/api/doors/readiness*', (route) => route.fulfill({
    status: 500, contentType: 'application/json',
    body: JSON.stringify({ error: 'boom', code: 'DOOR_DERIVATION_FAILED', detail: 'x' }),
  }))
  await openLibrary(page)
  const chip = page.getByTestId('door-chip-SIM-EDR-001')
  await expect(chip).toHaveText('NOT CHECKED')
  await expect(chip).toHaveAttribute('title', /door readiness unavailable/)
})

test('the toolbar row keeps every button inside the viewport at 800x600', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 600 })
  await page.goto('/#/library')
  await page.waitForSelector('.scenario-card', { timeout: 20_000 })
  await expect(page.getByTestId('filter-runnable')).toBeVisible()
  const rights = await page.evaluate(() =>
    [...document.querySelectorAll('.ops-toolbar button')].map((b) => ({
      text: (b.textContent || '').trim(),
      right: b.getBoundingClientRect().right,
      inner: window.innerWidth,
    })))
  expect(rights.length).toBeGreaterThanOrEqual(3)
  for (const r of rights) expect(r.right, `${r.text} overflows`).toBeLessThanOrEqual(r.inner)
})
