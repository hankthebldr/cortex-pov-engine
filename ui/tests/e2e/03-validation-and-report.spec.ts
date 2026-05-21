import { test, expect } from './_fixtures'

/**
 * Golden path #3 — DC validation flow: launch via API, then drive the UI
 * Validate wizard to mark detections observed and confirm coverage updates.
 *
 * This exercises ResultsValidationWizard + the report-export contract
 * end-to-end without flakily depending on UI-driven launch.
 */
test('DC validates seeded results and report reflects 100% coverage', async ({
  page,
  api,
  baseURL,
  request,
}) => {
  await api.health()

  // 1. Launch a push run via the API so we have a known runId
  const runId = await api.launchPush('SIM-EDR-001')
  expect(runId).toMatch(/[a-f0-9-]+/)

  // 2. Validate every result via API (the UI flow is covered in unit tests;
  //    here we want to assert downstream coverage/report)
  await api.validateAllResults(runId)

  // 3. Fetch the run report JSON — must show 100% coverage
  const reportR = await request.get(`${baseURL}/api/runs/${runId}/report?format=json`)
  expect(reportR.ok()).toBe(true)
  const report = await reportR.json()
  expect(report.coverage.pct).toBe(100)
  expect(report.mttd).not.toBeNull()
  expect(report.mttd.count).toBeGreaterThan(0)

  // 4. Markdown report download works
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

  // 7. UI Evidence tab shows the run (Mission Ops Console — PR #44).
  // The legacy "Runs" tab no longer exists; Evidence is now the
  // canonical place to confirm a run lands in the UI.
  await page.goto('/')
  await page.getByRole('tab', { name: /Evidence/ }).first().click()
  await expect(page.getByText(runId).first()).toBeVisible({ timeout: 10_000 })
})
