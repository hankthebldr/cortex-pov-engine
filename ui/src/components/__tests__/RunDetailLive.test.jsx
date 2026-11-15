/**
 * A run's page while the run is live.
 *
 *   - What a running step has printed is on the page when it opens (the
 *     record's `live_tail`), and grows as the run's stream delivers chunks. A
 *     chunk already shown is skipped; the step's stored frame replaces the
 *     live text.
 *   - What SimCore said about the launch is one short line above the chain,
 *     not raw lines in the log.
 *   - "Live output needs the new beacon" shows only while a run is live on a
 *     beacon that does not stream.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, within, cleanup, waitFor, act } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { rememberLaunchWarnings, forgetLaunchWarnings } from '../console/launchWarnings.js'
import RunDetailView from '../console/RunDetailView.jsx'

void React

class FakeEventSource {
  static instances = []
  constructor(url) {
    this.url = url
    this.readyState = 0
    FakeEventSource.instances.push(this)
  }
  open() { this.readyState = 1; this.onopen && this.onopen({}) }
  emit(evt) { this.onmessage && this.onmessage({ data: JSON.stringify(evt) }) }
  close() { this.readyState = 2 }
}

const SCENARIO = {
  scenario_id: 'SIM-EDR-001', name: 'Credential dumping',
  steps: [
    { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', identity: 'root', mitre_technique: 'T1003.008' },
    { id: 'step-02', name: 'Dump the process', command: 'gcore -o /tmp/dump 1', identity: 'www-data', mitre_technique: 'T1003.007' },
  ],
}
const RUN = {
  run_id: 'run-live-1', scenario_id: 'SIM-EDR-001', mode: 'pull', target: 'ag-1', status: 'running',
  started_at: '2026-11-15T10:00:00', delivered_at: '2026-11-15T10:00:03', output: '',
}
const NEW_BEACON = { agent_id: 'ag-1', hostname: 'ag-1', status: 'online', capabilities: ['shell', 'cleanup', 'live-output'] }
const OLD_BEACON = { agent_id: 'ag-1', hostname: 'ag-1', status: 'online', capabilities: ['shell', 'cleanup'] }
const FRAME_1 = '=== STEP 1/2 · step-01 · T1003.008 · identity=root ===\nroot:$6$stored:19000:0:99999:7:::\n--- exit_code=0 duration=1.2s ---\n'

const chunk = (seq, message) => ({
  id: `chunk-${seq}`, timestamp: '2026-11-15T10:00:05', level: 'info', stepIndex: 0,
  stepId: 'step-01', phase: 'output', live: true, seq, message, synthetic: false, command: null,
})

function mount(record, { agents = [NEW_BEACON], run = RUN } = {}) {
  installRoutes({
    'GET /api/runs/run-live-1': record,
    'GET /api/scenarios/SIM-EDR-001': SCENARIO,
  })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run], agents, scenarios: [SCENARIO] }}>
      <RunDetailView runId={run.run_id} run={run} />
    </EnvironmentContext.Provider>,
  )
}
const step = async (id) => {
  await waitFor(() => expect(document.querySelector(`[data-testid="runtime-step"][data-step="${id}"]`)).not.toBeNull())
  return document.querySelector(`[data-testid="runtime-step"][data-step="${id}"]`)
}
const outputOf = (el) => within(el).getByTestId('runtime-step-output').textContent

let savedES
beforeEach(() => {
  savedES = globalThis.EventSource
  FakeEventSource.instances = []
  globalThis.EventSource = FakeEventSource
  forgetLaunchWarnings()
})
afterEach(() => {
  cleanup()
  if (savedES === undefined) delete globalThis.EventSource
  else globalThis.EventSource = savedES
})

describe('a live run — output while a step runs', () => {
  const record = {
    ...RUN,
    step_starts: [{ step_id: 'step-01', command: 'cat /etc/shadow', identity: 'root', started_at: '2026-11-15T10:00:04' }],
    live_tail: [{ step_id: 'step-01', text: 'root:$6$live-one\n', seq: 1, truncated: false }],
  }

  it('shows what the step has printed so far when the page opens, under its command', async () => {
    mount(record)
    const one = await step('step-01')
    await waitFor(() => expect(within(one).queryByTestId('runtime-step-output')).not.toBeNull())
    expect(outputOf(one)).toBe('root:$6$live-one')
    expect(within(one).getByRole('img', { name: 'Running' })).toBeInTheDocument()
    expect(within(one).getByTestId('runtime-step-command')).toHaveTextContent('cat /etc/shadow')
    expect(within(one).getByTestId('runtime-step-command')).toHaveTextContent('from the agent')
  })

  it('appends chunks from the stream and skips one it already shows', async () => {
    mount(record)
    const one = await step('step-01')
    await waitFor(() => expect(within(one).queryByTestId('runtime-step-output')).not.toBeNull())
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    act(() => {
      es.emit(chunk(1, 'root:$6$live-one\n'))   // already in the tail
      es.emit(chunk(2, 'daemon:*:live-two\n'))
    })
    await waitFor(() => expect(outputOf(one)).toBe('root:$6$live-one\ndaemon:*:live-two'))
  })

  it("replaces the live text with the step's stored frame", async () => {
    mount(record)
    const one = await step('step-01')
    await waitFor(() => expect(within(one).queryByTestId('runtime-step-output')).not.toBeNull())
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    act(() => { es.emit(chunk(2, 'daemon:*:live-two\n')) })
    await waitFor(() => expect(outputOf(one)).toContain('live-two'))
    act(() => {
      es.emit({
        id: 'stored-1', timestamp: '2026-11-15T10:00:06', level: 'info', stepIndex: 0,
        stepId: 'step-01', phase: 'output', live: false, seq: null, message: FRAME_1, synthetic: false, command: null,
      })
    })
    await waitFor(() => expect(outputOf(one)).toBe('root:$6$stored:19000:0:99999:7:::'))
    expect(one).not.toHaveTextContent('live-one')
    expect(one).not.toHaveTextContent('live-two')
    expect(within(one).getByRole('img', { name: 'Ran' })).toBeInTheDocument()
    expect(one).toHaveTextContent('exit 0 · 1.2s')
  })

  it('a step the stream says has started reads Running with its command', async () => {
    mount({ ...record, step_starts: [], live_tail: [] })
    const two = await step('step-02')
    const es = FakeEventSource.instances[0]
    act(() => { es.open() })
    act(() => {
      es.emit({
        id: 'start-2', timestamp: '2026-11-15T10:00:07', level: 'step', stepIndex: 1, stepId: 'step-02',
        phase: 'start', live: false, seq: null, command: 'gcore -o /tmp/dump 1 --variant', message: 'Running: gcore', synthetic: false,
      })
    })
    await waitFor(() => expect(within(two).getByRole('img', { name: 'Running' })).toBeInTheDocument())
    expect(within(two).getByTestId('runtime-step-command')).toHaveTextContent('gcore -o /tmp/dump 1 --variant')
  })
})

describe('a run page — what SimCore said about the launch is one line', () => {
  const GAP = "!! RUNTIME_DEPENDENCY_GAP: agent 'ag-1'; steps step-02; The agent did not report python3, and the beacon will not run these steps without it."
  const CLEANUP = "!! CLEANUP_NOT_RUN: agent 'ag-1'; this beacon does not run scenario cleanup; update it, or clean the host by hand after the run"

  it('lifts a launch line out of the log into the notice, with the sentence in its tooltip', async () => {
    mount({ ...RUN, output: `${GAP}\n` })
    const notice = await screen.findByTestId('run-notice')
    expect(notice).toHaveTextContent('Agent is missing python3')
    expect(notice).not.toHaveTextContent('RUNTIME_DEPENDENCY_GAP')
    expect(within(notice).getByText('Agent is missing python3')).toHaveAttribute('title', expect.stringMatching(/did not report python3/))
    expect(screen.queryByTestId('runtime-note')).toBeNull()
  })

  it('several notices stay one line, and open to one line each', async () => {
    rememberLaunchWarnings('run-live-1', [{ code: 'EAL_RELAY_UNAVAILABLE', agent_id: 'ag-1', steps: ['step-02'], text: 'this beacon cannot relay EAL steps' }])
    mount({ ...RUN, output: `${GAP}\n${CLEANUP}\n` })
    await within(await screen.findByTestId('run-notice')).findByRole('img', { name: '3 notices' })
    const notice = await screen.findByTestId('run-notice')
    expect(within(notice).getByRole('img', { name: '3 notices' })).toBeInTheDocument()
    const more = notice.querySelector('details')
    expect(more.open).toBe(false)
    more.open = true
    const lines = [...notice.querySelectorAll('li')].map((li) => li.textContent)
    expect(lines).toEqual([
      'Agent is missing python3',
      'This beacon does not run cleanup',
      'This beacon cannot relay EAL steps',
    ])
  })

  it('says live output needs the new beacon only while the run is live on an older one', async () => {
    mount({ ...RUN }, { agents: [OLD_BEACON] })
    expect(await screen.findByTestId('run-notice')).toHaveTextContent('Live output needs the new beacon')
    cleanup()

    const done = { ...RUN, status: 'complete', completed_at: '2026-11-15T10:01:00', output: FRAME_1 }
    mount(done, { agents: [OLD_BEACON], run: done })
    await step('step-01')
    expect(screen.queryByTestId('run-notice')).toBeNull()
  })

  it('a run with nothing to say has no notice', async () => {
    mount({ ...RUN })
    await step('step-01')
    expect(screen.queryByTestId('run-notice')).toBeNull()
  })
})
