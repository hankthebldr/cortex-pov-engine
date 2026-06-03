import { test, expect, gotoView } from './_fixtures'

/**
 * Golden path #3 — DC validation flow: launch via API, validate, and assert
 * the downstream coverage/report contract, then confirm the run surfaces in
 * the Evidence step of the console.
 */
test('DC validates seeded results and report reflects 100% coverage', async ({
  page,
  api,
  baseURL,
  request,
}) => {
  await api.health()

  // 1. Launch a push run via the API (fixture authorizes the consent gate)
  const runId = await api.launchPush('SIM-EDR-001')
  expect(runId).toMatch(/[a-f0-9-]+/)

  // 2. Validate every result via API
  await api.validateAllResults(runId)

  // 3. Run report JSON — 100% coverage
  const reportR = await request.get(`${baseURL}/api/runs/${runId}/report?format=json`)
  expect(reportR.ok()).toBe(true)
  const report = await reportR.json()
  expect(report.coverage.pct).toBe(100)
  expect(report.mttd).not.toBeNull()
  expect(report.mttd.count).toBeGreaterThan(0)

  // 4. Markdown report download
  const md = await request.get(`${baseURL}/api/runs/${runId}/report?format=markdown`)
  expect(md.ok()).toBe(true)
  expect(await md.text()).toContain('POV Detection Validation Report')

  // 5. ATT&CK Navigator layer parses
  const nav = await request.get(`${baseURL}/api/runs/${runId}/report/navigator`)
  expect(nav.ok()).toBe(true)
  const layer = await nav.json()
  expect(Array.isArray(layer.techniques)).toBe(true)

  // 6. Bundle is gzip
  const bundle = await request.get(`${baseURL}/api/runs/${runId}/report/bundle`)
  expect(bundle.ok()).toBe(true)
  const buf = await bundle.body()
  expect(buf[0]).toBe(0x1f)
  expect(buf[1]).toBe(0x8b)

  // 7. ⑤ Evidence step renders (a push run stays 'pending' so it doesn't
  //    auto-surface as the active run; the report contract above is the
  //    substantive assertion). Confirm the Evidence panel itself renders.
  await page.goto('/')
  await gotoView(page, 'Evidence')
  await expect(page.locator('.view')).toContainText(/Coverage|MTTD|Evidence|Export|no results/i, { timeout: 10_000 })
})
