import { test, expect } from './_fixtures'
import type { APIRequestContext, Page } from '@playwright/test'

/**
 * Results on kit controls, a live runtime log, and EAL consent at launch.
 *
 * WHY AN E2E SPEC
 * ---------------
 * The unit suite proves the words against a mocked API. This drives the served
 * console against a real SimCore, where the things under test only exist once
 * SimCore has done its part: a step's live output is a tail on the run record
 * and chunks on the run's event stream; the cleanup outcome is what SimCore
 * stored from the beacon's report; a launch warning is a line SimCore wrote
 * onto the run.
 *
 * There is no beacon in CI, so the spec plays one over the agent API, the same
 * calls agent/beacon/client.go makes: register, take the task, announce a step
 * (`kind: start`), stream its output (`kind: chunk`), post its final frame,
 * write the cleanup section and report completion. A review is an empty manual
 * import (POST /observations): no tenant, no outbound call. The launch the
 * consent test makes is answered by the spec, so no EAL step is ever sent.
 */

const NEW_BEACON = 'e2e-live-log-beacon'
const OLD_BEACON = 'e2e-live-log-old-beacon'
const NEW_CAPS = ['shell', 'identity-harness', 'artifact-fetch', 'cleanup', 'live-output']
const OLD_CAPS = ['shell', 'identity-harness', 'artifact-fetch']

type Step = { id: string; identity?: string; mitre_technique?: string; command?: string }
type Task = { run_id: string; task_id?: string; steps: Step[]; cleanup?: { commands: Array<{ command: string }> } }

async function post(request: APIRequestContext, url: string, data: unknown) {
  const r = await request.post(url, { data })
  expect(r.ok(), `${url}: ${await r.text()}`).toBe(true)
  return r.json()
}

/** Launch `scenarioId` on `agent` and take its task, as a beacon would. */
async function launchPull(request: APIRequestContext, baseURL: string, scenarioId: string, agent: string, caps: string[]) {
  await post(request, `${baseURL}/api/agents/register`, { agent_id: agent, hostname: agent, os: 'linux', capabilities: caps })
  const run = await post(request, `${baseURL}/api/run`, {
    scenario_id: scenarioId, mode: 'pull', target_agent_id: agent,
    consent: { simulation_authorized: true, c2_authorized: true },
  })
  const poll = await request.get(`${baseURL}/api/agents/${agent}/tasks`)
  expect(poll.ok(), await poll.text()).toBe(true)
  const task = (await poll.json()).task as Task
  expect(task?.run_id, 'the agent took this run').toBe(run.run_id)
  return { runId: run.run_id as string, task }
}

const frameOf = (task: Task, i: number, body: string[], rc = 0, duration = '212ms') => {
  const s = task.steps[i]
  return [
    `=== STEP ${i + 1}/${task.steps.length} · ${s.id} · ${s.mitre_technique || ''} · identity=${s.identity || 'root'} ===`,
    ...body,
    `--- exit_code=${rc} duration=${duration} ---`,
  ].join('\n') + '\n'
}

/** The beacon announces a step: its command, before it runs. */
const startStep = (request: APIRequestContext, baseURL: string, runId: string, task: Task, i: number) =>
  post(request, `${baseURL}/api/runs/${runId}/output`, {
    step_id: task.steps[i].id, kind: 'start', output: '', command: task.steps[i].command || '',
    identity: task.steps[i].identity || 'root', index: i + 1, total: task.steps.length,
  })

/** Output while the step runs. Never stored. */
const chunk = (request: APIRequestContext, baseURL: string, runId: string, stepId: string, seq: number, output: string) =>
  post(request, `${baseURL}/api/runs/${runId}/output`, { step_id: stepId, kind: 'chunk', seq, output })

/** The step's own frame: the only kind SimCore stores. */
const endStep = (request: APIRequestContext, baseURL: string, runId: string, task: Task, i: number, body: string[]) =>
  post(request, `${baseURL}/api/runs/${runId}/output`, { step_id: task.steps[i].id, output: frameOf(task, i, body) })

const stepBlock = (page: Page, stepId: string) => page.locator(`[data-testid=runtime-step][data-step="${stepId}"]`)

test.describe.configure({ mode: 'serial' })

test('Runs › run: live output appears as it is streamed, then the stored frame replaces it', async ({ page, request, baseURL }) => {
  const { runId, task } = await launchPull(request, baseURL!, 'SIM-EDR-001', NEW_BEACON, NEW_CAPS)
  const first = task.steps[0]
  await startStep(request, baseURL!, runId, task, 0)
  await chunk(request, baseURL!, runId, first.id, 1, 'live-line-one\n')
  await chunk(request, baseURL!, runId, first.id, 2, 'live-line-two\n')

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/#/runs?run=${runId}`)
  const block = stepBlock(page, first.id)
  await expect(block).toBeVisible({ timeout: 15_000 })
  // What the step printed before the page opened is there: the record's tail.
  const out = block.getByTestId('runtime-step-output')
  await expect(out).toHaveText('live-line-one\nlive-line-two', { timeout: 15_000 })
  await expect(block).toHaveAttribute('data-state', 'running')
  await expect(block.getByRole('img', { name: 'Running', exact: true })).toBeVisible()
  // The command is the one the agent said it is running.
  await expect(block.getByTestId('runtime-step-command')).toContainText('from the agent')
  await expect(page.getByTestId('runtime-log').locator('.rtlog__bar')).toContainText(/Live|Polling/)

  // A chunk streamed while the page is open is appended, once.
  await chunk(request, baseURL!, runId, first.id, 3, 'live-line-three\n')
  await expect(out).toHaveText('live-line-one\nlive-line-two\nlive-line-three', { timeout: 15_000 })

  // The step ends: its stored frame is what the block shows, and only that.
  await endStep(request, baseURL!, runId, task, 0, ['stored-line-only'])
  await expect(out).toHaveText('stored-line-only', { timeout: 15_000 })
  await expect(block).not.toContainText('live-line')
  await expect(block).toHaveAttribute('data-state', 'ran')
  await expect(block).toContainText('exit 0 · 212ms')
  // The next step has not been announced, so it shows the scenario's command.
  await expect(stepBlock(page, task.steps[1].id).getByTestId('runtime-step-command')).toContainText('as authored')

  // A late chunk for the step that ended changes nothing.
  await chunk(request, baseURL!, runId, first.id, 4, 'live-line-late\n')
  await startStep(request, baseURL!, runId, task, 1)
  await expect(stepBlock(page, task.steps[1].id)).toHaveAttribute('data-state', 'running', { timeout: 15_000 })
  await expect(stepBlock(page, task.steps[1].id).getByTestId('runtime-step-command')).toContainText('from the agent')
  await expect(block).not.toContainText('live-line-late')
  await expect(out).toHaveText('stored-line-only')

  // Leave nothing running behind the spec.
  await post(request, `${baseURL}/api/runs/${runId}/complete`, {
    exit_code: 0, summary: 'played by the spec', task_id: task.task_id, agent_id: NEW_BEACON,
    steps: [{ step_id: first.id, exit_code: 0, termination: 'exited', reason: null }],
  })
})

test('Runs › run: cleanup is its own block, and what the launch said is one line', async ({ page, request, baseURL }) => {
  // An older beacon: SimCore says at launch that it will not run the cleanup.
  const old = await launchPull(request, baseURL!, 'SIM-EDR-003', OLD_BEACON, OLD_CAPS)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/#/runs?run=${old.runId}`)
  const notice = page.getByTestId('run-notice')
  await expect(notice).toBeVisible({ timeout: 15_000 })
  // While the run is live it also says its output cannot be streamed.
  await expect(notice).toContainText('This beacon does not run cleanup')
  await notice.locator('summary').click()
  await expect(notice.locator('li')).toContainText(['Live output needs the new beacon'])
  for (const line of await notice.locator('li').allTextContents()) {
    expect(line.split(/\s+/).length, `"${line}" is one short line`).toBeLessThanOrEqual(8)
  }
  // The raw launch lines are not also in the log.
  await expect(page.getByTestId('runtime-log')).not.toContainText('CLEANUP_NOT_RUN')
  // The cleanup it will not run reads Pending while the run is live.
  await expect(page.getByTestId('runtime-cleanup').getByRole('img', { name: 'Pending', exact: true })).toBeVisible()

  // The current beacon runs the cleanup when the task ends; one command fails.
  const { runId, task } = await launchPull(request, baseURL!, 'SIM-EDR-003', NEW_BEACON, NEW_CAPS)
  const commands = (task.cleanup?.commands || []).map((c) => c.command)
  expect(commands.length, 'the task carries the scenario cleanup').toBeGreaterThan(1)
  for (let i = 0; i < task.steps.length; i += 1) {
    await startStep(request, baseURL!, runId, task, i)
    await endStep(request, baseURL!, runId, task, i, [`[*] step ${i + 1} done`])
  }
  const section = [
    `=== CLEANUP · ${commands.length} commands · identity=direct ===`,
    ...commands.flatMap((c, i) => [
      `$ ${c}`,
      ...(i === 1 ? ['cleanup-failure-output'] : []),
      `--- cleanup ${i + 1}/${commands.length} exit_code=${i === 1 ? 1 : 0} duration=${4 + i}ms ---`,
    ]),
  ].join('\n') + '\n'
  await post(request, `${baseURL}/api/runs/${runId}/output`, { output: section })
  await post(request, `${baseURL}/api/runs/${runId}/complete`, {
    exit_code: 0, summary: 'played by the spec', task_id: task.task_id, agent_id: NEW_BEACON,
    steps: task.steps.map((s) => ({ step_id: s.id, exit_code: 0, termination: 'exited', reason: null })),
    cleanup: { status: 'failed', ran: commands.length, failed: 1, reason: 'COMMAND_FAILED' },
  })

  await page.goto(`/#/runs?run=${runId}`)
  const cleanup = page.getByTestId('runtime-cleanup')
  await expect(cleanup).toBeVisible({ timeout: 15_000 })
  await expect(cleanup).toContainText('Cleanup')
  await expect(cleanup.getByRole('img', { name: `1 of ${commands.length} failed`, exact: true })).toBeVisible()
  // After the steps, never inside the last one.
  const lastStep = stepBlock(page, task.steps[task.steps.length - 1].id)
  await expect(lastStep).not.toContainText('cleanup-failure-output')
  await expect(page.getByTestId('runtime-log')).not.toContainText('=== CLEANUP')
  const lastBox = (await lastStep.boundingBox())!
  const cleanupBox = (await cleanup.boundingBox())!
  expect(cleanupBox.y, 'the cleanup block sits below the last step').toBeGreaterThan(lastBox.y + lastBox.height - 1)
  // One line until it is opened; then every command, its output and its exit.
  const list = cleanup.locator('.rtlog__cleanup-cmds')
  await expect(list).toBeHidden()
  await cleanup.locator('summary').click()
  await expect(list.locator('li')).toHaveCount(commands.length)
  await expect(list).toContainText('cleanup-failure-output')
  await expect(list.locator('li').nth(1)).toContainText('exit 1')
  // This beacon streams and cleans up: the page has nothing to add.
  await expect(page.getByTestId('run-notice')).not.toContainText(/cleanup|new beacon/)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `the run page overflows by ${overflow}px at 1280px`).toBeLessThanOrEqual(0)

  // The older beacon's run: once it is no longer live, the streaming line goes.
  await post(request, `${baseURL}/api/runs/${old.runId}/complete`, {
    exit_code: 1, summary: 'played by the spec', task_id: old.task.task_id, agent_id: OLD_BEACON, steps: [],
  })
  await page.goto(`/#/runs?run=${old.runId}`)
  await expect(page.getByTestId('run-notice')).toContainText('This beacon does not run cleanup', { timeout: 15_000 })
  await expect(page.getByTestId('run-notice')).not.toContainText('new beacon')
})

test('Results › Detections: states are a StatusIcon and one word; tiles and actions are terse kit controls', async ({ page, request, baseURL }) => {
  const { runId, task } = await launchPull(request, baseURL!, 'SIM-EDR-001', NEW_BEACON, NEW_CAPS)
  // Every step but the last ran; the last one the host never ran.
  for (let i = 0; i < task.steps.length - 1; i += 1) await endStep(request, baseURL!, runId, task, i, [`[*] step ${i + 1} done`])
  await post(request, `${baseURL}/api/runs/${runId}/complete`, {
    exit_code: 0, summary: 'played by the spec', task_id: task.task_id, agent_id: NEW_BEACON,
    // So its detections are a host fact, review or no review: Not executed.
    steps: task.steps.map((s, i) => (i === task.steps.length - 1
      ? { step_id: s.id, exit_code: null, termination: 'not_run', reason: 'PLATFORM_UNSATISFIABLE' }
      : { step_id: s.id, exit_code: 0, termination: 'exited', reason: null })),
  })
  await post(request, `${baseURL}/api/runs/${runId}/observations`, { observations: [], host: 'e2e-live-log' })

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`/#/proof?run=${runId}&tab=detections`)
  const rows = page.locator('.scorecard__row')
  await expect(rows.first()).toBeVisible({ timeout: 15_000 })
  // One vocabulary, as a StatusIcon and its word. No pill, no "awaiting reconcile".
  const VOCABULARY = ['Detected', 'Prevented', 'Missed', 'Pending', 'Not reached', 'Not executed', 'Not reviewed', 'Not prevented']
  const words = await rows.locator('.scorecard__state .kit-status__word').allTextContents()
  expect(words.length).toBe(await rows.count())
  for (const w of words) expect(VOCABULARY, `"${w}" is one of the detection words`).toContain(w)
  // Reviewed, and nothing judged these rows: Pending. Never "Not reviewed" here.
  expect(words).toContain('Pending')
  expect(words).toContain('Not executed')
  expect(words).not.toContain('Not reviewed')
  await expect(page.locator('.scorecard__status')).toHaveCount(0)
  await expect(page.getByTestId('results')).not.toContainText(/awaiting reconcile/i)

  // Tiles: a sentence-case label, one number, at most one short line.
  await expect(page.locator('.kpi__label')).toHaveText(['Coverage', 'Median MTTD', 'Analytics plane', 'Pending'])
  for (const label of await page.locator('.kpi__label').all()) {
    expect(await label.evaluate((e) => getComputedStyle(e).textTransform), 'tile labels are not uppercased').toBe('none')
  }
  await expect(page.locator('.kpi').first().locator('.kpi__meta')).toHaveText(/^\d+ of \d+ detected( · \d+ not counted)?$|^\d+ not counted$/)
  for (const meta of await page.locator('.kpi__meta').allTextContents()) {
    expect(meta.split(/\s+/).length, `"${meta}" is one short line`).toBeLessThanOrEqual(8)
  }
  // No MTTD yet: the panel is one line.
  await expect(page.getByTestId('mttd-panel')).toHaveText('No MTTD yet')

  // Row actions are kit icon buttons that name themselves in a tooltip.
  const pending = rows.nth(words.indexOf('Pending'))
  await pending.hover()
  const mark = pending.getByRole('button', { name: 'Mark detected', exact: true })
  await expect(mark).toHaveClass(/kit-btn--icon/)
  await expect(mark).toHaveAttribute('title', 'Mark detected')
  expect((await mark.textContent())!.trim(), 'the action draws its icon only').toBe('')
  await mark.click()
  await expect(pending.locator('.scorecard__state .kit-status__word')).toHaveText('Detected', { timeout: 15_000 })
  await expect(page.getByTestId('mttd-panel')).toContainText('MTTD distribution', { timeout: 15_000 })
  // The detail drawer it opens is kit too.
  await pending.click()
  const drawer = page.getByRole('complementary', { name: 'Detection detail' })
  await expect(drawer.locator('.kit-status__word').first()).toHaveText('Detected')
  expect(await drawer.locator('.btn').count(), 'no legacy button in the drawer').toBe(0)
  await drawer.getByRole('button', { name: 'Close detection detail' }).click()

  // Techniques and Topology say the verdict once (Results' own line) and carry no status line.
  for (const [tab, view, totals] of [['Techniques', 'run-techniques', 'technique-totals'], ['Topology', 'run-topology', 'topo-totals']]) {
    await page.getByRole('tab', { name: tab, exact: true }).click()
    const v = page.getByTestId(view)
    await expect(v).toBeVisible({ timeout: 15_000 })
    await expect(v).not.toContainText(/run verdict/i)
    await expect(v).not.toContainText('tenant-verified')
    await expect(page.getByTestId(totals)).toContainText(/\d+ of \d+ detected/)
  }
  await expect(page.getByTestId('result-verdict')).toHaveCount(1)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `Results overflows by ${overflow}px at 1280px`).toBeLessThanOrEqual(0)
})

test('Launch gate: the EAL consent row sends its fields only when ticked', async ({ page, request, baseURL }) => {
  // SIM-MP-038 sends EAL steps itself (channel: eal); SIM-EDR-001 has none.
  await post(request, `${baseURL}/api/agents/register`, { agent_id: NEW_BEACON, hostname: NEW_BEACON, os: 'linux', capabilities: NEW_CAPS })
  // The spec answers the launch, so nothing is created and no EAL step is sent.
  const launches: Array<Record<string, unknown>> = []
  await page.route('**/api/run', async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    launches.push(route.request().postDataJSON())
    await route.fulfill({ json: { run_id: `e2e-eal-consent-${launches.length}`, mode: 'pull', message: 'answered by the spec' } })
  })
  await page.setViewportSize({ width: 1280, height: 900 })

  await page.goto('/#/preflight?arm=SIM-EDR-001')
  await expect(page.getByTestId('new-run')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('launch-eal-consent')).toHaveCount(0)

  const arm = async () => {
    await page.goto('/#/preflight?arm=SIM-MP-038')
    await page.reload()   // a fresh gate each time: nothing ticked
    await expect(page.getByTestId('new-run')).toBeVisible({ timeout: 15_000 })
    await page.getByTestId(`new-run-target-agent-${NEW_BEACON}`).click()
    await page.getByTestId('new-run-authorised').check()
  }
  await arm()
  const row = page.getByTestId('launch-eal-consent')
  await expect(row).toBeVisible()
  const live = row.getByRole('checkbox', { name: 'Send live EAL traffic' })
  const who = row.getByRole('textbox', { name: 'Authorized by' })
  const c2 = row.getByRole('checkbox', { name: 'Includes C2-shaped traffic' })
  await expect(live).not.toBeChecked()
  // One compact row: three controls, a few words each, on one line at this width.
  const tops = await row.locator('label').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().top)))
  expect(Math.max(...tops) - Math.min(...tops), 'the consent row is one line').toBeLessThan(12)

  // Unticked: the launch is the one sent before.
  const run = page.getByRole('button', { name: 'Run', exact: true })
  await expect(run).toBeEnabled({ timeout: 15_000 })
  await run.click()
  await expect.poll(() => launches.length).toBe(1)
  await expect(page).toHaveURL(/#\/runs\?run=e2e-eal-consent-1/)   // the launch opens its run
  expect(launches[0]).not.toHaveProperty('authorized_by')
  expect(launches[0].consent ?? {}).not.toHaveProperty('simulation_authorized')

  // Ticked, with a name and the C2 box: all three are sent.
  await arm()
  await live.check()
  await expect(run).toBeDisabled()
  await expect(page.getByTestId('launch-blockers')).toContainText('Name who authorised the live EAL traffic')
  await who.fill('E2E Operator')
  await c2.check()
  await expect(run).toBeEnabled()
  await run.click()
  await expect.poll(() => launches.length).toBe(2)
  await expect(page).toHaveURL(/#\/runs\?run=e2e-eal-consent-2/)
  expect(launches[1].authorized_by).toBe('E2E Operator')
  expect(launches[1].consent).toMatchObject({ simulation_authorized: true, c2_authorized: true })

  // The name is remembered in this browser; the tick is not.
  await arm()
  await expect(who).toHaveValue('E2E Operator')
  await expect(live).not.toBeChecked()
})
