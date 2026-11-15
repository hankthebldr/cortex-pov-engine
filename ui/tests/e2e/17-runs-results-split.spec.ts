import { test, expect } from './_fixtures'
import type { APIRequestContext } from '@playwright/test'

/**
 * Runs shows what was attempted; Results shows what Cortex saw.
 *
 * WHY AN E2E SPEC
 * ---------------
 * jsdom proves the words and the wiring against a mocked API. This drives the
 * served console against a real SimCore, where a run's record is the one
 * SimCore stored: the runtime log must be built from the output and the step
 * record the API really returns, and the old tab links must land on the page
 * the router really mounts. It also settles what only a browser can: a long
 * command stays on one line until it is opened, and neither page scrolls
 * sideways at a laptop width.
 *
 * The runs are the spec's own. There is no beacon in CI, so the spec plays one
 * over the agent API (register, take the task, post each step's output, report
 * completion) — the same calls agent/beacon/client.go makes. A review is an
 * empty manual import (POST /observations): no tenant, no outbound call.
 */

const AGENT = 'e2e-runs-results-split'
const SCENARIO = 'SIM-EDR-001'

type Step = { id: string; identity?: string; mitre_technique?: string; command?: string; name?: string }
type Played = { body: string[]; rc: number | null; duration: string; termination: string; reason?: string }

const ok = (line: string, duration = '212ms'): Played => ({ body: [line], rc: 0, duration, termination: 'exited' })

/** Launch `scenarioId` on the spec's agent and take its task, as a beacon would. */
async function launchPull(request: APIRequestContext, baseURL: string, scenarioId: string) {
  const reg = await request.post(`${baseURL}/api/agents/register`, {
    data: { agent_id: AGENT, hostname: 'e2e-split', os: 'linux', capabilities: ['shell', 'identity-harness'] },
  })
  expect(reg.ok(), await reg.text()).toBe(true)
  const run = await request.post(`${baseURL}/api/run`, {
    data: {
      scenario_id: scenarioId, mode: 'pull', target_agent_id: AGENT,
      consent: { simulation_authorized: true, c2_authorized: true },
    },
  })
  expect(run.ok(), await run.text()).toBe(true)
  const runId = (await run.json()).run_id as string
  const poll = await request.get(`${baseURL}/api/agents/${AGENT}/tasks`)
  expect(poll.ok(), await poll.text()).toBe(true)
  const task = (await poll.json()).task as { run_id: string; task_id?: string; steps: Step[] }
  expect(task?.run_id, 'the agent took this run').toBe(runId)
  return { runId, task }
}

/** Post each played step's frame (the beacon's own grammar); `exitCode` completes the run. */
async function play(
  request: APIRequestContext, baseURL: string, runId: string,
  task: { task_id?: string; steps: Step[] }, plan: Array<Played | null>, exitCode: number | null,
) {
  const total = task.steps.length
  const steps: Array<Record<string, unknown>> = []
  for (let i = 0; i < plan.length; i += 1) {
    const p = plan[i]
    if (!p) break
    const s = task.steps[i]
    const who = p.termination === 'not_run' ? '' : ` · identity=${s.identity || 'root'}`
    const frame = [
      `=== STEP ${i + 1}/${total} · ${s.id} · ${s.mitre_technique || ''}${who} ===`,
      ...p.body,
      `--- exit_code=${p.rc === null ? 'none' : p.rc} duration=${p.duration} ---`,
    ].join('\n')
    const out = await request.post(`${baseURL}/api/runs/${runId}/output`, { data: { step_id: s.id, output: `${frame}\n` } })
    expect(out.ok(), await out.text()).toBe(true)
    steps.push({ step_id: s.id, exit_code: p.rc, termination: p.termination, reason: p.reason ?? null })
  }
  if (exitCode === null) return
  const done = await request.post(`${baseURL}/api/runs/${runId}/complete`, {
    data: { exit_code: exitCode, summary: 'played by the spec', steps, task_id: task.task_id, agent_id: AGENT },
  })
  expect(done.ok(), await done.text()).toBe(true)
}

test.describe.configure({ mode: 'serial' })

let finished = ''      // ran · ran · failed · refused · ran — then reviewed
let unreviewed = ''    // every step ran; nobody reviewed it
let steps: Step[] = []

test.beforeAll(async ({ request, baseURL }) => {
  const a = await launchPull(request, baseURL!, SCENARIO)
  finished = a.runId
  steps = a.task.steps
  expect(steps.length, `${SCENARIO} has five steps`).toBe(5)
  await play(request, baseURL!, finished, a.task, [
    ok('[*] 3 accounts with a login shell'),
    ok('[*] shadow not readable as this account', '1.481s'),
    { body: ['bash: line 1: mimipenguin: command not found'], rc: 127, duration: '18ms', termination: 'exited' },
    {
      body: [`!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=${steps[3].id} declared=windows host=linux`],
      rc: null, duration: '0s', termination: 'not_run', reason: 'PLATFORM_UNSATISFIABLE',
    },
    ok('[*] cleanup complete', '96ms'),
  ], 0)
  const review = await request.post(`${baseURL}/api/runs/${finished}/observations`, {
    data: { observations: [], host: 'e2e-split' },
  })
  expect(review.ok(), await review.text()).toBe(true)

  const b = await launchPull(request, baseURL!, SCENARIO)
  unreviewed = b.runId
  await play(request, baseURL!, unreviewed, b.task, b.task.steps.map((_, i) => ok(`[*] step ${i + 1} done`)), 0)
})

test('Runs › run: the runtime log shows each step’s command and how it went', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/#/runs?run=${finished}`)
  const log = page.getByTestId('runtime-log')
  await expect(log).toBeVisible({ timeout: 15_000 })
  const blocks = log.getByTestId('runtime-step')
  await expect(blocks).toHaveCount(5)
  // How each step went, from the host's record — a StatusIcon and a word.
  const want = [['ran', 'Ran'], ['ran', 'Ran'], ['failed', 'Failed'], ['did_not_run', 'Did not run'], ['ran', 'Ran']]
  for (let i = 0; i < 5; i += 1) {
    await expect(blocks.nth(i)).toHaveAttribute('data-state', want[i][0])
    await expect(blocks.nth(i).getByRole('img', { name: want[i][1], exact: true })).toBeVisible()
  }
  await expect(blocks.nth(2)).toContainText('exit 127 · 18ms')
  await expect(blocks.nth(2).getByTestId('runtime-step-output')).toContainText('mimipenguin: command not found')
  await expect(blocks.nth(3)).toContainText('PLATFORM_UNSATISFIABLE')
  // The command that was attempted, on every block — the scenario's own text.
  for (let i = 0; i < 5; i += 1) {
    const cmd = blocks.nth(i).getByTestId('runtime-step-command')
    await expect(cmd).toContainText(String(steps[i].command).split('\n')[0].slice(0, 40))
    await expect(cmd).toContainText('as authored')
  }
  await expect(log).not.toContainText(/waiting for agent/i)

  // One panel: no detection view, no case panel, no detection word anywhere.
  await expect(page.getByRole('tablist')).toHaveCount(0)
  await expect(page.getByTestId('run-case')).toHaveCount(0)
  await expect(page.getByTestId('tenant-evidence')).toHaveCount(0)
  await expect(page.locator('.run-detail')).not.toContainText(/detected|missed/i)
  // The chain says the same thing as the log, card by card.
  for (let i = 0; i < 5; i += 1) {
    await expect(page.getByTestId(`stage-card-${steps[i].id}`)).toHaveAttribute('data-state', want[i][0])
  }
  // It is reviewed, so its one primary action opens Results.
  await expect(page.getByTestId('run-lifecycle-message')).toContainText('Ran · reviewed')
  const open = page.locator('.run-detail__actions').getByRole('button', { name: 'Open results' })
  await expect(open).toHaveClass(/kit-btn--primary/)
  await open.click()
  await expect(page).toHaveURL(new RegExp(`#/proof\\?run=${finished}$`))
})

test('Runs › run: a long command stays on one line until it is opened, and nothing scrolls sideways', async ({ page }) => {
  // The longest command of the scenario: longer than any column here.
  const long = steps.reduce((best, s, i) => (String(s.command).length > String(steps[best].command).length ? i : best), 0)
  expect(String(steps[long].command).length, `${SCENARIO} has a long command`).toBeGreaterThan(200)
  for (const width of [1280, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`/#/runs?run=${finished}`)
    const block = page.getByTestId('runtime-step').nth(long)
    await expect(block).toBeVisible({ timeout: 15_000 })
    const more = block.getByTestId('runtime-step-command').locator('details')
    await expect(more).toHaveCount(1)
    const line = more.locator('summary code')
    // One line: as tall as one line of text, and cut rather than wrapped.
    const box = (await line.boundingBox())!
    expect(box.height, `the command line is ${box.height}px tall at ${width}px`).toBeLessThan(30)
    const cut = await line.evaluate((e) => e.scrollWidth > e.clientWidth)
    expect(cut, 'the one line is cut, not wrapped').toBe(true)
    await expect(more.locator('pre')).toBeHidden()
    await more.locator('summary').click()
    await expect(more.locator('pre')).toBeVisible()
    await expect(more.locator('pre')).toHaveText(String(steps[long].command))
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow, `the run page overflows by ${overflow}px at ${width}px`).toBeLessThanOrEqual(0)
    // And it closes again, back to its one line.
    await more.locator('summary').click()
    await expect(more.locator('pre')).toBeHidden()
  }
})

test('Runs: a row says how the run went and whether it is reviewed, never what was detected', async ({ page }) => {
  await page.goto('/#/runs')
  await expect(page.getByTestId('run-list')).toBeVisible({ timeout: 15_000 })
  // Consecutive runs of one scenario fold into a group; open it to see both.
  const fold = page.getByTestId('run-group-toggle').first()
  if (await fold.count()) await fold.click()
  const reviewedRow = page.locator(`[data-testid=run-row][data-run-id="${finished}"]`)
  await expect(reviewedRow).toBeVisible({ timeout: 15_000 })
  // A state is a kit StatusIcon: a glyph, and its word beside it.
  await expect(reviewedRow.getByTestId('run-status').locator('.kit-status__word')).toHaveText('Ran')
  await expect(reviewedRow.getByTestId('run-review').locator('.kit-status__word')).toHaveText('Reviewed')
  await expect(reviewedRow.getByRole('button', { name: 'Open results' })).toBeVisible()
  const freshRow = page.locator(`[data-testid=run-row][data-run-id="${unreviewed}"]`)
  await expect(freshRow.getByTestId('run-review').locator('.kit-status__word')).toHaveText('Not reviewed')
  await expect(freshRow.getByRole('button', { name: 'Review', exact: true })).toHaveClass(/kit-btn--primary/)
  for (const row of [reviewedRow, freshRow]) await expect(row).not.toContainText(/detected|missed/i)
  // Compare runs stays here, as a secondary action.
  await expect(page.getByRole('button', { name: 'Compare runs' })).toHaveClass(/kit-btn--secondary/)
})

test('old run tab links open Results on the same tab', async ({ page }) => {
  for (const tab of ['evidence', 'storyline', 'techniques', 'topology', 'causality']) {
    await page.goto(`/#/runs?run=${finished}&tab=${tab}`)
    await expect(page).toHaveURL(new RegExp(`#/proof\\?run=${finished}&tab=${tab}$`), { timeout: 15_000 })
    const label = tab === 'evidence' ? 'Detections' : tab[0].toUpperCase() + tab.slice(1)
    await expect(page.getByRole('tab', { name: label, exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('runtime-log')).toHaveCount(0)
  }
  // A link to the run itself stays on the run.
  await page.goto(`/#/runs?run=${finished}&tab=live`)
  await expect(page.getByTestId('runtime-log')).toBeVisible({ timeout: 15_000 })
  expect(page.url()).toMatch(/#\/runs\?/)
})

test('Results: the verdict, six tabs in order, and kit actions', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`/#/proof?run=${finished}`)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Results')
  await expect(page.getByTestId('result-of')).toContainText(SCENARIO)
  // One verdict line: the verdict as a StatusIcon and a word, then its reason.
  const verdict = page.getByTestId('result-verdict')
  await expect(verdict).toBeVisible({ timeout: 15_000 })
  await expect(verdict.locator('.kit-status')).toHaveCount(1)
  await expect(verdict.locator('.result-verdict__why')).not.toHaveText('')

  const tabs = page.getByRole('tablist', { name: 'Results views' }).getByRole('tab')
  await expect(tabs).toHaveText(['Detections', 'Storyline', 'Techniques', 'Topology', 'Causality', 'Tenant evidence'])
  await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.kpi').first()).toBeVisible()
  // Each tab opens its view and writes ?tab=, so the address is a deep link.
  const views: Array<[string, string, string]> = [
    ['Storyline', 'storyline', '.storyline, [data-testid^=storyline]'],
    ['Techniques', 'techniques', '[data-testid=run-techniques]'],
    ['Topology', 'topology', '[data-testid=run-topology]'],
    ['Tenant evidence', 'tenant', '[data-testid=tenant-evidence]'],
  ]
  for (const [name, id, landmark] of views) {
    await page.getByRole('tab', { name, exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`tab=${id}$`))
    await expect(page.locator(landmark).first()).toBeVisible({ timeout: 15_000 })
  }
  await page.getByRole('tab', { name: 'Causality', exact: true }).click()
  await expect(page).toHaveURL(/tab=causality$/)
  await expect(page.getByRole('tabpanel')).not.toBeEmpty()

  // Actions are kit controls: Validate all on Detections, one Download for the report.
  await page.getByRole('tab', { name: 'Detections', exact: true }).click()
  await expect(page.getByRole('button', { name: /Validate all/ })).toHaveClass(/kit-btn/)
  const download = page.getByRole('button', { name: 'Download', exact: true })
  await expect(download).toHaveClass(/kit-btn--secondary/)
  expect(await page.getByTestId('results').locator('.btn').count(), 'no legacy button on Results').toBe(0)
  await download.click()
  const menu = page.getByRole('menu')
  for (const item of ['POV briefing bundle', 'Narrative report', 'Detection scoring matrix', 'ATT&CK Navigator layer']) {
    await expect(menu).toContainText(item)
  }
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  // Compare runs is not here.
  await expect(page.getByTestId('results')).not.toContainText(/compare runs/i)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `Results overflows by ${overflow}px at 1440px`).toBeLessThanOrEqual(0)
})

test('Results: a run that has not been reviewed says so in every tab, never missed', async ({ page }) => {
  await page.goto(`/#/proof?run=${unreviewed}`)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Results')
  const notice = page.getByTestId('readback-notice')
  await expect(notice).toContainText('Not reviewed', { timeout: 15_000 })
  await expect(notice.getByRole('button', { name: 'Review', exact: true })).toHaveClass(/kit-btn--primary/)
  await expect(page.getByTestId('result-verdict')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Download', exact: true })).toHaveCount(0)
  for (const name of ['Detections', 'Storyline', 'Techniques', 'Topology', 'Causality']) {
    await page.getByRole('tab', { name, exact: true }).click()
    const panel = page.getByRole('tabpanel')
    await expect(panel.getByTestId('results-not-reviewed').locator('.kit-status__word')).toHaveText('Not reviewed')
    await expect(panel).not.toContainText(/missed/i)
    await expect(panel.locator('.kpi, .scorecard')).toHaveCount(0)
  }
  // Tenant evidence still shows what is stored, and reads nothing by itself.
  await page.getByRole('tab', { name: 'Tenant evidence', exact: true }).click()
  await expect(page.getByTestId('tenant-evidence')).toBeVisible({ timeout: 15_000 })
  // One Review on the page, on every tab.
  await expect(page.getByRole('button', { name: 'Review', exact: true })).toHaveCount(1)
})
