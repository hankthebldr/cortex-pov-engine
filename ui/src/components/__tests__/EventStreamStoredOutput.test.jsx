/**
 * #340 — a finished run's STDOUT panel shows the output SimCore stored.
 *
 * The panel used to show only the live SSE stream, so a run that had already
 * ended read "waiting for agent…" while `GET /api/runs/{id}` held its whole
 * transcript. A settled run (complete · failed · aborted · staged) now renders
 * that stored output, split on the beacon's per-step grammar
 * (agent/beacon/client.go::formatStepOutput, and refuseStep for a step the
 * host would not run). A live run keeps the stream.
 */
import React from 'react'
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import EventStream from '../console/EventStream.jsx'
import { splitStepTranscript } from '../console/useRunStepTail.js'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import RunDetailView from '../console/RunDetailView.jsx'

void React

// SIM-CDR-006 as the beacon stores it: three steps, all exit 0. Step 2 prints
// nothing (its systemctl call sends stderr to /dev/null).
const CDR_006_OUTPUT = [
  '=== STEP 1/3 · step-01 · T1543.002 · identity=root ===',
  '[*] systemd unit written: /etc/systemd/system/cortexsim-node.service',
  '--- exit_code=0 duration=212ms ---',
  '=== STEP 2/3 · step-02 · T1543.002 · identity=root ===',
  '--- exit_code=0 duration=1.481s ---',
  '=== STEP 3/3 · step-03 · T1053.003 · identity=root ===',
  '--- exit_code=0 duration=37ms ---',
].join('\n')

// One of each ending: exit 0, a non-zero exit, a timeout (exit 124 plus the
// beacon's STEP TIMEOUT line) and a step the host refused (no identity in the
// header, exit_code=none). A launch marker sits before the first step.
const MIXED_OUTPUT = [
  '!! PLATFORM VARIANT: step=step-01 linux',
  '=== STEP 1/4 · step-01 · T1059.004 · identity=www-data ===',
  'uid=33(www-data) gid=33(www-data) groups=33(www-data)',
  '--- exit_code=0 duration=15ms ---',
  '=== STEP 2/4 · step-02 · T1003.008 · identity=root ===',
  'cat: /etc/shadow-: No such file or directory',
  '--- exit_code=1 duration=9ms ---',
  '=== STEP 3/4 · step-03 · T1046 · identity=root ===',
  '!! STEP TIMEOUT: command exceeded 2m0s timeout and was terminated',
  '--- exit_code=124 duration=2m0s ---',
  '=== STEP 4/4 · step-04 · T1547.001 ===',
  '!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-04 needs windows',
  '   THIS STEP DID NOT RUN on this host. Its detections are not_executed, not missed.',
  '--- exit_code=none duration=0s ---',
].join('\n')

const liveRun = {
  id: 'r-live',
  scenario_id: 'SIM-CDR-006',
  mode: 'pull',
  status: 'running',
  started_at: '2026-11-14T22:00:00Z',
  current_step: 1,
  steps: [{ id: 'step-01', mitre_technique: 'T1543.002', identity: 'root', status: 'pending' }],
  results: [],
}

beforeEach(() => {
  if (typeof window !== 'undefined' && 'EventSource' in window) delete window.EventSource
  installRoutes({
    'GET /api/runs/r-done': { id: 'r-done', status: 'complete', output: CDR_006_OUTPUT, steps: [], results: [] },
    'GET /api/runs/r-live': liveRun,
  })
})

const steps = () => screen.queryAllByTestId('stdout-step')

describe('splitStepTranscript', () => {
  it('keeps each step with its own lines, exit code and duration', () => {
    const segs = splitStepTranscript(CDR_006_OUTPUT)
    expect(segs.map((s) => s.kind)).toEqual(['step', 'step', 'step'])
    expect(segs.map((s) => s.id)).toEqual(['step-01', 'step-02', 'step-03'])
    expect(segs[0]).toMatchObject({ index: 1, total: 3, technique: 'T1543.002', identity: 'root', exitCode: 0, duration: '212ms' })
    expect(segs[0].lines).toEqual(['[*] systemd unit written: /etc/systemd/system/cortexsim-node.service'])
    expect(segs[1].lines).toEqual([])
  })

  it('reads a timeout, a refused step and the text before the first step', () => {
    const segs = splitStepTranscript(MIXED_OUTPUT)
    expect(segs[0]).toMatchObject({ kind: 'text', lines: ['!! PLATFORM VARIANT: step=step-01 linux'] })
    const byId = Object.fromEntries(segs.filter((s) => s.kind === 'step').map((s) => [s.id, s]))
    expect(byId['step-02']).toMatchObject({ exitCode: 1, timedOut: false, notRun: false })
    expect(byId['step-03']).toMatchObject({ exitCode: 124, timedOut: true })
    expect(byId['step-04']).toMatchObject({ exitCode: null, notRun: true, identity: null })
  })

  it('is empty for no output', () => {
    expect(splitStepTranscript('')).toEqual([])
    expect(splitStepTranscript(null)).toEqual([])
  })
})

describe('<EventStream /> — a finished run', () => {
  it('shows the stored output split by step, with each exit code', () => {
    render(<EventStream runId="r-done" status="complete" output={CDR_006_OUTPUT} />)
    expect(screen.queryByText(/waiting for agent/i)).toBeNull()
    expect(steps()).toHaveLength(3)
    for (const step of steps()) {
      expect(within(step).getByRole('img', { name: 'Ran' })).toBeInTheDocument()
      expect(step).toHaveTextContent('exit 0')
    }
    expect(steps()[0]).toHaveTextContent('step-01')
    expect(steps()[0]).toHaveTextContent('[*] systemd unit written: /etc/systemd/system/cortexsim-node.service')
  })

  it('says how each step ended: Ran, Failed (exit N), Timed out, Did not run', () => {
    render(<EventStream runId="r-done" status="failed" output={MIXED_OUTPUT} />)
    const words = steps().map((s) => within(s).getByRole('img').getAttribute('aria-label'))
    expect(words).toEqual(['Ran', 'Failed (exit 1)', 'Timed out', 'Did not run'])
    expect(screen.getByTestId('stdout-note')).toHaveTextContent('!! PLATFORM VARIANT: step=step-01 linux')
  })

  it('reads stored output for every settled status, not only complete', () => {
    for (const status of ['complete', 'failed', 'aborted', 'staged']) {
      const { unmount } = render(<EventStream runId="r-done" status={status} output={CDR_006_OUTPUT} />)
      expect(steps(), status).toHaveLength(3)
      unmount()
    }
  })

  it('an empty stored output reads "No output recorded", never "waiting for agent…"', () => {
    render(<EventStream runId="r-done" status="complete" output="" />)
    expect(screen.getByText('No output recorded')).toBeInTheDocument()
    expect(screen.queryByText(/waiting for agent/i)).toBeNull()
  })

  it('before the record arrives it says the output is not loaded, not that none was recorded', () => {
    render(<EventStream runId="r-done" status="complete" output={null} />)
    expect(screen.getByText('Output not loaded')).toBeInTheDocument()
    expect(screen.queryByText('No output recorded')).toBeNull()
    expect(screen.queryByText(/waiting for agent/i)).toBeNull()
  })
})

describe('Run detail › Live — the panel gets the record the page already fetched', () => {
  it('a finished run shows its stored steps on the Live tab', async () => {
    const run = {
      run_id: 'run-cdr', scenario_id: 'SIM-CDR-006', mode: 'pull', target: 'agent-1',
      started_at: '2026-11-14T17:00:00', completed_at: '2026-11-14T17:00:05', status: 'complete',
      output_bytes: CDR_006_OUTPUT.length,
    }
    installRoutes({ 'GET /api/runs/:id': { ...run, output: CDR_006_OUTPUT } })
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run] }}>
        <RunDetailView runId="run-cdr" run={run} subTab="live" />
      </EnvironmentContext.Provider>,
    )
    await waitFor(() => expect(steps()).toHaveLength(3))
    expect(screen.queryByText(/waiting for agent/i)).toBeNull()
  })
})

describe('<EventStream /> — a live run', () => {
  it('keeps the stream and does not render stored output', async () => {
    render(<EventStream runId="r-live" status="running" output={CDR_006_OUTPUT} />)
    expect(steps()).toHaveLength(0)
    await waitFor(() => expect(screen.getByText(/run started/i)).toBeInTheDocument(), { timeout: 4000 })
    expect(steps()).toHaveLength(0)
  })
})
