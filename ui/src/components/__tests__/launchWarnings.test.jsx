/**
 * Launch warnings reach the operator (RG-RUNS-06).
 *
 * POST /api/run answers 200 with `warnings[]` when SimCore queued a run it has
 * a reservation about: the target beacon is offline (TARGET_AGENT_OFFLINE), it
 * registered no OS so POSIX was assumed (TARGET_AGENT_OS_UNKNOWN), or a step
 * ran its platform variant. Nothing in the console read the field: the launch
 * notice said "Run <id> started" from run_id alone, and every surface then
 * navigates to the run — so a DC launched at a beacon that had stopped polling,
 * was told nothing, and waited on a run that would not start.
 *
 * Two places now carry it: the launch notice (for the surfaces that stay), and
 * the run itself — from its transcript when SimCore wrote the marker there, and
 * from this browser session's memory of the launch response when it did not
 * (TARGET_AGENT_OFFLINE is not written to Run.output at c852eac). On the run's
 * page it is the one-line notice above the chain; on Results it is one of the
 * run's caveats.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, renderHook, act, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import useLaunchScenario from '../console/useLaunchScenario.js'
import { LaunchSuccessNotice } from '../console/LaunchErrorNotice.jsx'
import RunDetailView from '../console/RunDetailView.jsx'
import RunCaveats, { runDegradations } from '../console/RunCaveats.jsx'
import {
  normalizeLaunchWarnings, rememberLaunchWarnings, launchWarningsFor, forgetLaunchWarnings,
} from '../console/launchWarnings.js'

void React

const OFFLINE = "TARGET_AGENT_OFFLINE: agent 'ag-1' last polled 10800s ago; the task is queued and runs only when its beacon polls again"
const OS_UNKNOWN = '!! TARGET_AGENT_OS_UNKNOWN: the target agent registered no operating system, so POSIX commands were dispatched. If this endpoint is Windows, every step ran the wrong dialect — re-register the beacon.'
const VARIANT = '!! PLATFORM VARIANT: step=step-02 ran its windows variant; its expected_detections were authored against the primary command and are unverified for this OS'

const SCENARIO = {
  scenario_id: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR',
  execution_identity: { default: 'root', options: ['root'] },
}

beforeEach(() => { forgetLaunchWarnings() })

describe('normalizeLaunchWarnings — nothing the server said is dropped', () => {
  it('reads the documented shape: a list of strings, with the "!!" marker removed', () => {
    expect(normalizeLaunchWarnings({ run_id: 'r', warnings: [OFFLINE, OS_UNKNOWN] })).toEqual([
      { code: 'TARGET_AGENT_OFFLINE', text: OFFLINE.replace('TARGET_AGENT_OFFLINE: ', '') },
      { code: 'TARGET_AGENT_OS_UNKNOWN', text: OS_UNKNOWN.replace('!! TARGET_AGENT_OS_UNKNOWN: ', '') },
    ])
  })

  it('an absent or empty field is no warnings', () => {
    expect(normalizeLaunchWarnings({ run_id: 'r' })).toEqual([])
    expect(normalizeLaunchWarnings({ run_id: 'r', warnings: [] })).toEqual([])
    expect(normalizeLaunchWarnings(null)).toEqual([])
  })

  it('an unrecognised shape is shown, not read as empty', () => {
    expect(normalizeLaunchWarnings({ warnings: 'beacon offline' })).toEqual([{ code: null, text: 'beacon offline' }])
    expect(normalizeLaunchWarnings({ warnings: [{ code: 'X', why: 'y' }] }))
      .toEqual([{ code: null, text: '{"code":"X","why":"y"}' }])
    expect(normalizeLaunchWarnings({ warnings: { a: 1 } })).toEqual([{ code: null, text: '{"a":1}' }])
  })
})

describe('session memory of a launch response', () => {
  it('recalls by run id, and only for that run', () => {
    rememberLaunchWarnings('run-1', normalizeLaunchWarnings({ warnings: [OFFLINE] }))
    expect(launchWarningsFor('run-1').map((w) => w.code)).toEqual(['TARGET_AGENT_OFFLINE'])
    expect(launchWarningsFor('run-2')).toEqual([])
    expect(launchWarningsFor(null)).toEqual([])
  })

  it('remembering nothing stores nothing', () => {
    rememberLaunchWarnings('run-3', [])
    expect(launchWarningsFor('run-3')).toEqual([])
  })
})

describe('useLaunchScenario — the launch result keeps the warnings', () => {
  it('a 200 with warnings is a success that carries them, and remembers them for the run', async () => {
    installRoutes({
      'GET /api/agents': { agents: [{ agent_id: 'ag-1', hostname: 'h', status: 'offline', last_seen_age_seconds: 10800 }] },
      'GET /api/runs': { runs: [] },
      'POST /api/run': { run_id: 'run-off-1', mode: 'pull', message: "Task queued for agent 'ag-1'", warnings: [OFFLINE] },
    })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO))
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    await act(async () => { await result.current.launch() })
    expect(result.current.lastRun.status).toBe('success')
    expect(result.current.lastRun.warnings.map((w) => w.code)).toEqual(['TARGET_AGENT_OFFLINE'])
    expect(launchWarningsFor('run-off-1')).toHaveLength(1)
  })

  it('an unremarkable launch carries an empty list', async () => {
    installRoutes({
      'GET /api/agents': { agents: [{ agent_id: 'ag-1', hostname: 'h', status: 'online' }] },
      'GET /api/runs': { runs: [] },
      'POST /api/run': { run_id: 'run-ok-1', mode: 'pull', message: 'Task queued' },
    })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO))
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    await act(async () => { await result.current.launch() })
    expect(result.current.lastRun.warnings).toEqual([])
    expect(launchWarningsFor('run-ok-1')).toEqual([])
  })

  it('a warning object reads as its sentence: its level is not printed', async () => {
    installRoutes({
      'GET /api/agents': { agents: [{ agent_id: 'ag-1', hostname: 'h', status: 'online' }] },
      'GET /api/runs': { runs: [] },
      'POST /api/run': {
        run_id: 'run-old-1', mode: 'pull', message: 'Task queued',
        warnings: [{
          code: 'CLEANUP_NOT_RUN', level: 'info', agent_id: 'ag-1',
          detail: 'this beacon does not run scenario cleanup; update it, or clean the host by hand after the run',
        }],
      },
    })
    const { result } = renderHook(() => useLaunchScenario(SCENARIO))
    await waitFor(() => expect(result.current.agents).toHaveLength(1))
    await act(async () => { await result.current.launch() })
    const [w] = result.current.lastRun.warnings
    expect(w.code).toBe('CLEANUP_NOT_RUN')
    expect(w.text).toBe('agent ag-1 — this beacon does not run scenario cleanup; update it, or clean the host by hand after the run')
    expect(w.text).not.toMatch(/level/)
    // The level still routes the warning (an info one does not hold the launch).
    expect(w.level).toBe('info')
  })
})

describe('LaunchSuccessNotice', () => {
  it('lists each warning with its code, and is not styled as a clean success', () => {
    render(<LaunchSuccessNotice result={{
      status: 'success', message: 'Run run-off-1 started',
      warnings: normalizeLaunchWarnings({ warnings: [OFFLINE, VARIANT] }),
    }} testId="launch-ok" />)
    const n = screen.getByTestId('launch-ok')
    expect(n).toHaveAttribute('role', 'status')
    expect(n.textContent).toContain('Run run-off-1 started')
    expect(n.textContent).toContain('TARGET_AGENT_OFFLINE')
    expect(n.textContent).toMatch(/runs only when its beacon polls again/)
    expect(n.textContent).toContain('PLATFORM VARIANT')
    expect(n.className).not.toContain('launch-result--success')
    expect(n.textContent).toMatch(/SimCore warned/i)
  })

  it('with no warnings it is the plain success line', () => {
    render(<LaunchSuccessNotice result={{ status: 'success', message: 'Run r started', warnings: [] }} testId="launch-ok" />)
    const n = screen.getByTestId('launch-ok')
    expect(n.className).toContain('launch-result--success')
    expect(n.textContent).not.toMatch(/warned/i)
  })

  it('renders nothing for an error or for nothing', () => {
    const { container } = render(<LaunchSuccessNotice result={{ status: 'error', message: 'x' }} />)
    expect(container.textContent).toBe('')
  })
})

describe('the run carries its launch warnings', () => {
  const RUN = { run_id: 'run-off-1', scenario_id: 'SIM-EDR-001', status: 'running', mode: 'pull', target: 'ag-1', output: '' }

  it('runDegradations reads TARGET_AGENT_* markers from the transcript, with or without "!!"', () => {
    const d = runDegradations([OS_UNKNOWN, OFFLINE, 'unrelated line'].join('\n'))
    expect(d.launch.map((w) => w.code)).toEqual(['TARGET_AGENT_OS_UNKNOWN', 'TARGET_AGENT_OFFLINE'])
    expect(runDegradations('nothing here').launch).toEqual([])
  })

  it('a marker SimCore wrote into Run.output is the run page’s one-line notice, not a log line', async () => {
    const run = { ...RUN, output: `${OS_UNKNOWN}\n=== STEP 1/1 · step-01 · T1003 · identity=root ===\nok\n--- exit_code=0 duration=1s ---` }
    installRoutes({ 'GET /api/runs/run-off-1': run })
    render(<RunDetailView runId="run-off-1" run={run} />)
    const notice = await screen.findByTestId('run-notice')
    expect(notice).toHaveTextContent('Agent OS unknown, POSIX assumed')
    // SimCore's own sentence is one hover away.
    expect(screen.getByText('Agent OS unknown, POSIX assumed')).toHaveAttribute('title', expect.stringMatching(/wrong dialect/))
    const items = [...screen.getByRole('region', { name: 'Runtime log' }).querySelectorAll('.rtlog__steps > li')]
    expect(items.map((li) => li.getAttribute('data-testid'))).toEqual(['runtime-step'])
  })

  it('a warning only the launch response carried is in the notice too', async () => {
    rememberLaunchWarnings('run-off-1', normalizeLaunchWarnings({ warnings: [OFFLINE] }))
    installRoutes({ 'GET /api/runs/run-off-1': RUN })
    render(<RunDetailView runId="run-off-1" run={RUN} />)
    const notice = await screen.findByTestId('run-notice')
    expect(notice).toHaveTextContent('Agent was offline at launch')
    expect(screen.getByText('Agent was offline at launch'))
      .toHaveAttribute('title', expect.stringMatching(/runs only when its beacon polls again/))
  })

  it('the same code from the record and the session is said once', async () => {
    rememberLaunchWarnings('run-off-1', normalizeLaunchWarnings({ warnings: [OS_UNKNOWN] }))
    const run = { ...RUN, output: OS_UNKNOWN }
    installRoutes({ 'GET /api/runs/run-off-1': run })
    render(<RunDetailView runId="run-off-1" run={run} />)
    const notice = await screen.findByTestId('run-notice')
    expect(notice.textContent.match(/Agent OS unknown/g)).toHaveLength(1)
    expect(notice.querySelector('details')).toBeNull()
  })

  it('on Results it is a caveat: a banner naming the code and what it means', () => {
    const output = `${OS_UNKNOWN}\n=== STEP 1/1 · step-01 · T1003 · identity=root ===\nok\n--- exit_code=0 duration=1s ---`
    render(<RunCaveats runId="run-off-1" run={RUN} output={output} />)
    const b = screen.getByTestId('run-launch-warnings-banner')
    expect(b).toHaveAttribute('role', 'status')
    expect(b.textContent).toContain('TARGET_AGENT_OS_UNKNOWN')
    expect(b.textContent).toMatch(/wrong dialect/)
  })

  it('a caveat only the launch response carried says it is from this session', () => {
    rememberLaunchWarnings('run-off-1', normalizeLaunchWarnings({ warnings: [OFFLINE] }))
    render(<RunCaveats runId="run-off-1" run={RUN} output="" />)
    const b = screen.getByTestId('run-launch-warnings-banner')
    expect(b.textContent).toContain('TARGET_AGENT_OFFLINE')
    expect(b.textContent).toMatch(/runs only when its beacon polls again/)
    expect(b.textContent).toMatch(/not in the run record/i)
  })

  it('the same code from both sources is listed once; a variant marker keeps its own banner', () => {
    rememberLaunchWarnings('run-off-1', normalizeLaunchWarnings({ warnings: [OS_UNKNOWN, VARIANT] }))
    render(<RunCaveats runId="run-off-1" run={RUN} output={`${OS_UNKNOWN}\n${VARIANT}`} />)
    const b = screen.getByTestId('run-launch-warnings-banner')
    expect(b.textContent.match(/TARGET_AGENT_OS_UNKNOWN/g)).toHaveLength(1)
    expect(b.textContent).not.toContain('PLATFORM VARIANT')
    expect(screen.getByTestId('run-platform-variant-banner')).toBeTruthy()
  })

  it('a run with no launch warnings has no note and no caveat', async () => {
    installRoutes({ 'GET /api/runs/run-off-1': RUN })
    const { container } = render(<RunCaveats runId="run-off-1" run={RUN} output="" />)
    expect(container).toBeEmptyDOMElement()
    render(<RunDetailView runId="run-off-1" run={RUN} />)
    await waitFor(() => expect(screen.getByText(/All runs/)).toBeInTheDocument())
    expect(screen.queryByTestId('runtime-note')).toBeNull()
    expect(screen.queryByTestId('run-notice')).toBeNull()
  })
})
