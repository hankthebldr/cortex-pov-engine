import { test, expect } from './_fixtures'

/**
 * Runs surface rebuild — the Runs list on live run records, the run detail's
 * Techniques / Topology tabs, and the Composer dock following a canvas launch.
 *
 * WHY AN E2E SPEC
 * ---------------
 * jsdom proves the row fields, the tab strip and the dock auto-switch against
 * mocked fetch. What a DC deploys is the IMAGE: the bundle SimCore bakes into
 * core/static and serves on :8888. A stale bake, a dropped chunk, a route the
 * real API answers differently from the mock — none of that reaches the unit
 * suite. This spec drives the served console against the real SimCore, and
 * measures the two layout claims only a browser can settle (the 14-stage strip
 * on one row, the Engine terminal pinned beside the lanes).
 *
 * The run records it reads are the ones it creates: 27 push launches over
 * three scenarios (never three of one scenario in a row, so the consecutive-
 * repeat grouping leaves them as 27 rows and the pager has a second page), and
 * one pull launch from the Composer canvas onto an agent nothing polls, so the
 * step rows stay `queued` — the honest state for a beacon that has not called
 * in. tenant-verified stays 0 throughout; every surface says so.
 */

const CYCLE = ['SIM-EDR-001', 'SIM-EDR-002', 'SIM-EDR-003']
const AGENT = 'e2e-runs-rebuild-agent'
const ABSOLUTE_UTC = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$/

test.describe.configure({ mode: 'serial' })

test('Runs list: rows carry live fields, the pager turns at 25, the scenario filter narrows', async ({ page, api }) => {
  await api.health()
  const ids: string[] = []
  for (let i = 0; i < 27; i += 1) ids.push(await api.launchPush(CYCLE[i % 3]))
  const newest = ids[ids.length - 1]
  const newestScenario = CYCLE[26 % 3]

  await page.goto('/#/runs')
  await expect(page.getByTestId('run-list')).toBeVisible({ timeout: 15_000 })

  // The newest run is on page 1, as a plain row, with every field off the record.
  const row = page.locator(`[data-testid=run-row][data-run-id="${newest}"]`)
  await expect(row).toBeVisible({ timeout: 15_000 })
  await expect(row.locator('.run-list__scenario-id')).toHaveText(newestScenario)
  const started = row.getByTestId('run-started')
  await expect(started).not.toHaveText('')
  await expect(started, 'hover carries the absolute UTC timestamp').toHaveAttribute('title', ABSOLUTE_UTC)
  await expect(row.getByTestId('run-duration')).not.toHaveText('')
  await expect(row.getByTestId('run-detections'), 'detected / expected from Result rows').toHaveText(/^\d+ \/ \d+$/)
  await expect(row.getByTestId('run-status')).toHaveText('staged')

  // Pager: 25 per page, and the label counts the whole list.
  const label = page.getByTestId('run-pager-label')
  await expect(label).toHaveText(/^Page 1 of \d+ · \d+ runs/)
  const total = Number((await label.textContent())!.match(/· (\d+) runs/)![1])
  expect(total).toBeGreaterThanOrEqual(27)
  expect(await page.getByTestId('run-row').count()).toBe(25)
  await page.getByTestId('run-pager-next').click()
  await expect(label).toHaveText(/^Page 2 of \d+/)
  expect(page.url()).toMatch(/page=2/)
  await page.getByTestId('run-pager-prev').click()
  await expect(label).toHaveText(/^Page 1 of \d+/)

  // Scenario filter: the label says "of total" and the filter lives in the URL.
  // Narrowing to one scenario makes every one of its runs consecutive, so the
  // list collapses them into ONE group — the newest as a row, the rest behind
  // a toggle that says how many and what they were. Expanding it yields them all.
  await page.getByTestId('run-filter-scenario').selectOption(CYCLE[1])
  await expect(label).toHaveText(new RegExp(`· \\d+ runs of ${total}$`))
  expect(page.url()).toMatch(/scenario=SIM-EDR-002/)
  const group = page.locator(`[data-testid=run-group][data-scenario-id="${CYCLE[1]}"]`)
  await expect(group).toHaveCount(1)
  const toggle = group.getByTestId('run-group-toggle')
  await expect(toggle).toContainText(new RegExp(`\\d+ consecutive runs of ${CYCLE[1]}`))
  await expect(toggle).toContainText('staged')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  expect(await page.getByTestId('run-row').count()).toBe(1)
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  const scenarios = await group.locator('[data-testid=run-row] .run-list__scenario-id').allTextContents()
  expect(scenarios.length).toBeGreaterThanOrEqual(9)
  expect(scenarios.every((s) => s === CYCLE[1])).toBe(true)
  await page.getByTestId('run-filter-clear').click()
  await expect(label).toHaveText(/^Page 1 of \d+ · \d+ runs$/)
  expect(page.url()).not.toMatch(/scenario=/)

  // The footer never lets a count read as tenant proof.
  await expect(page.getByTestId('run-list')).toContainText('tenant-verified: 0')
})

test('Run detail: Techniques draws 14 stages on one row, Topology pins the Engine beside the lanes', async ({ page, api }) => {
  await api.health()
  const runId = await api.launchPush(CYCLE[0])
  await page.goto(`/#/runs?run=${runId}&tab=techniques`)

  const tech = page.getByTestId('run-techniques')
  await expect(tech).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('tab', { name: 'Techniques' })).toHaveAttribute('aria-selected', 'true')
  const stages = page.getByTestId('technique-stage')
  await expect(stages).toHaveCount(14)
  await expect(stages.first()).toHaveAttribute('data-tactic', 'TA0043')
  await expect(stages.last()).toHaveAttribute('data-tactic', 'TA0040')
  // Layout claims: the strip is one row (every stage shares a top edge) AND
  // the whole row fits its column — a kill chain whose last two stages sit
  // behind a scrollbar is not a kill chain the reader can read.
  const tops = await stages.evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(tops).size, `stages wrapped onto ${new Set(tops).size} rows`).toBe(1)
  const strip = page.locator('.run-tech__strip')
  const fit = await strip.evaluate((e) => ({ scrollWidth: e.scrollWidth, clientWidth: e.clientWidth, right: e.getBoundingClientRect().right }))
  expect(fit.scrollWidth, `strip overflows its column by ${fit.scrollWidth - fit.clientWidth}px`).toBeLessThanOrEqual(fit.clientWidth)
  const lastBox = (await stages.last().boundingBox())!
  expect(lastBox.x + lastBox.width, 'the Impact stage is inside the strip').toBeLessThanOrEqual(fit.right + 1)
  // Empty stages are dimmed, never green; something is placed.
  const empties = page.locator('[data-testid=technique-stage][data-tone=empty]')
  expect(await empties.count()).toBeGreaterThan(0)
  expect(await empties.count()).toBeLessThan(14)
  const opacity = await empties.first().evaluate((e) => Number(getComputedStyle(e).opacity))
  expect(opacity).toBeLessThan(0.6)
  await expect(page.getByTestId('technique-totals')).toContainText('tenant-verified: 0')
  await expect(tech).toContainText('Authored is not proven.')
  // A staged push run is not proven: nothing may read missed.
  expect(await page.locator('[data-testid=technique-row][data-tone=miss]').count()).toBe(0)

  await page.getByRole('tab', { name: 'Topology' }).click()
  const topo = page.getByTestId('run-topology')
  await expect(topo).toBeVisible({ timeout: 15_000 })
  expect(page.url()).toMatch(/tab=topology/)
  const lanes = page.getByTestId('topo-lane')
  await expect(lanes).toHaveCount(5)
  await expect(lanes.nth(0)).toHaveAttribute('data-lane', 'AGT')
  await expect(lanes.nth(4)).toHaveAttribute('data-lane', 'ENG')
  // SIM-EDR-001 is an EDR scenario: every step is behind the ENDPOINT door.
  await expect(lanes.nth(0)).toHaveAttribute('data-empty', 'false')
  const cells = page.locator('[data-testid=topo-cell][data-lane=AGT]')
  expect(await cells.count()).toBe(5)
  // A push bundle executes off this SimCore: no beacon queues its steps and no
  // host records them. Every cell reads staged — never queued (a beacon will
  // come), never not reached (the chain ran and stopped short).
  for (let i = 0; i < 5; i += 1) await expect(cells.nth(i)).toHaveAttribute('data-status', 'staged')
  await expect(topo).toHaveAttribute('data-proven', 'false')
  for (const i of [1, 2, 3, 4]) {
    await expect(lanes.nth(i)).toHaveAttribute('data-empty', 'true')
    await expect(lanes.nth(i)).toContainText('no step launched through this door')
  }
  // Layout claim: the Engine terminal sits beside the lanes, not under them.
  const engine = page.getByTestId('topo-engine')
  await expect(engine).toBeVisible()
  const lanesBox = (await page.locator('.run-topo__lanes').boundingBox())!
  const engineBox = (await engine.boundingBox())!
  expect(engineBox.x, 'Engine terminal is left of the lane block').toBeGreaterThanOrEqual(lanesBox.x + lanesBox.width - 1)
  expect(engineBox.y, 'Engine terminal does not sit below the lanes').toBeLessThan(lanesBox.y + lanesBox.height)
  await expect(page.getByTestId('topo-totals')).toContainText('tenant-verified: 0')
  expect(await page.locator('[data-testid=topo-cell][data-tone=miss]').count()).toBe(0)
})

test('Composer: a canvas launch on a seeded agent switches the dock to Active with queued step rows', async ({ page, api, request, baseURL }) => {
  await api.health()
  // A beacon that registers and never polls: the run it is handed stays
  // pending and its step rows stay queued — the state under test.
  const reg = await request.post(`${baseURL}/api/agents/register`, {
    data: { agent_id: AGENT, hostname: 'e2e-runs-rebuild', os: 'linux', capabilities: ['shell', 'identity-harness'] },
  })
  expect(reg.ok(), await reg.text()).toBe(true)
  await page.addInitScript((id) => {
    try { window.localStorage.setItem('cortexsim.activeAgent', id) } catch { /* handled by the app */ }
  }, AGENT)
  await page.setViewportSize({ width: 1440, height: 900 })

  // SIM-EDR-008 wires no gated tool, so the canvas launch needs no consent.
  await page.goto('/#/composer?from=SIM-EDR-008')
  await page.waitForSelector('.react-flow__node-step', { timeout: 20_000 })
  await page.getByTestId('composer-preflight').click()
  const launch = page.getByTestId('composer-launch')
  await expect(launch).toBeEnabled({ timeout: 10_000 })
  await expect(launch).toHaveText(/^Launch on /)
  await launch.click()

  // The dock switches itself to Active on the new run; nothing navigates away.
  await expect(page.getByTestId('ws-tab-active')).toHaveAttribute('aria-selected', 'true', { timeout: 15_000 })
  const active = page.getByTestId('ws-active')
  await expect(active).toBeVisible()
  const runId = await active.getAttribute('data-run-id')
  expect(runId).toBeTruthy()
  expect(page.url()).toMatch(/#\/composer/)
  await expect(page.getByTestId('composer-chain')).toBeVisible()

  const rows = page.getByTestId('ws-step-row')
  await expect(rows).toHaveCount(3)
  for (let i = 0; i < 3; i += 1) await expect(rows.nth(i)).toHaveAttribute('data-status', 'queued')
  await expect(active).toContainText('tenant-verified: 0')

  // And the record agrees: the run exists, targets the seeded agent, and is not complete.
  const r = await request.get(`${baseURL}/api/runs/${runId}`)
  expect(r.ok()).toBe(true)
  const body = await r.json()
  expect(body.target).toBe(AGENT)
  expect(['pending', 'running']).toContain(body.status)
})
