/**
 * Run detail — where the run is, and the one thing to do next
 * (design v26 §4.6).
 *
 *   - The five-stage lifecycle strip, the current stage marked as the step.
 *   - One primary action by phase: Read back on a finished run nobody has read
 *     back; Open results once it has been.
 *   - Abort run is an inline confirm on the run's own page: "Stop this run on
 *     the agent?" · Keep running · Abort now.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, within, cleanup, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import RunDetailView from '../console/RunDetailView.jsx'

void React

const base = {
  run_id: 'run-1',
  scenario_id: 'SIM-EDR-001',
  mode: 'pull',
  target: 'agent-1',
  started_at: '2026-11-13T17:00:00',
  completed_at: '2026-11-13T17:01:00',
  delivered_at: '2026-11-13T17:00:03',
  output_bytes: 900,
  expected_detections: 10,
  detected_count: 0,
  status: 'complete',
  tc_verdict_detail: { source: 'complete' },
}

function mount(run, props = {}, env = {}) {
  installRoutes({ 'GET /api/runs/:id': { ...run, output: '' } })
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run], ...env }}>
      <RunDetailView runId={run.run_id} run={run} subTab="live" {...props} />
    </EnvironmentContext.Provider>,
  )
}

afterEach(cleanup)

describe('Run detail lifecycle', () => {
  it('draws the five stages and marks where the run is', () => {
    mount(base)
    const strip = screen.getByRole('list', { name: /run lifecycle/i })
    const items = within(strip).getAllByRole('listitem')
    expect(items.map((li) => li.querySelector('.lc-stage__label').textContent))
      .toEqual(['Launched', 'Running', 'Finished', 'Read back', 'Results'])
    expect(items[2]).toHaveAttribute('aria-current', 'step')
    expect(items[3]).toHaveTextContent('not yet')
  })

  it('a finished, unread run says it has no results and offers Read back as the primary action', () => {
    mount(base)
    expect(screen.getByTestId('run-lifecycle-message')).toHaveTextContent(/nothing has been read back/i)
    const btn = screen.getByRole('button', { name: 'Read back' })
    expect(btn.className).toMatch(/kit-btn--primary/)
    expect(screen.queryByRole('button', { name: 'Open results' })).toBeNull()
  })

  it('a read-back run offers Open results, landing on Results for that run', () => {
    const onNavigate = vi.fn()
    mount({ ...base, detected_count: 3, tc_verdict_detail: { source: 'reconcile:xsiam:lab' } }, { onNavigate })
    fireEvent.click(screen.getByRole('button', { name: 'Open results' }))
    expect(onNavigate).toHaveBeenCalledWith('proof', { run: 'run-1' })
    expect(screen.getByTestId('run-lifecycle-message')).toHaveTextContent(/3 of 10 expected detections were seen/i)
  })

  it('the Evidence tab of an unread run shows no scorecard — it offers Read back instead', () => {
    mount(base, { subTab: 'evidence' })
    expect(screen.queryByRole('button', { name: /validate all/i })).toBeNull()
    expect(screen.getAllByRole('button', { name: 'Read back' }).length).toBeGreaterThan(0)
  })
})

describe('Run detail — Abort run is an inline confirm', () => {
  const live = { ...base, status: 'running', completed_at: null, delivered_at: null }
  const activeRun = { runId: 'run-1', scenarioId: 'SIM-EDR-001', status: 'running', step: 2, totalSteps: 7 }

  it('asks "Stop this run on the agent?" with Keep running and Abort now', () => {
    const onAbort = vi.fn()
    mount(live, { activeRun, onAbort })
    fireEvent.click(screen.getByRole('button', { name: 'Abort run' }))
    expect(screen.getByText('Stop this run on the agent?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Keep running' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Abort now' }).className).toMatch(/kit-btn--destructive/)
    expect(onAbort).not.toHaveBeenCalled()
    // The safe answer has focus, and Tab stays between the two answers.
    const keep = screen.getByRole('button', { name: 'Keep running' })
    expect(document.activeElement).toBe(keep)
    const now = screen.getByRole('button', { name: 'Abort now' })
    now.focus()
    fireEvent.keyDown(now, { key: 'Tab' })
    expect(document.activeElement).toBe(keep)
  })

  it('Keep running backs out without aborting', () => {
    const onAbort = vi.fn()
    mount(live, { activeRun, onAbort })
    fireEvent.click(screen.getByRole('button', { name: 'Abort run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep running' }))
    expect(onAbort).not.toHaveBeenCalled()
    expect(screen.queryByText('Stop this run on the agent?')).toBeNull()
    expect(screen.getByRole('button', { name: 'Abort run' })).toBeInTheDocument()
  })

  it('Abort now sends this run to the abort path once', () => {
    const onAbort = vi.fn()
    mount(live, { activeRun, onAbort })
    fireEvent.click(screen.getByRole('button', { name: 'Abort run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Abort now' }))
    expect(onAbort).toHaveBeenCalledTimes(1)
    expect(onAbort).toHaveBeenCalledWith({ runId: 'run-1', confirmed: true })
  })

  it('Escape backs out of the confirm', () => {
    const onAbort = vi.fn()
    mount(live, { activeRun, onAbort })
    fireEvent.click(screen.getByRole('button', { name: 'Abort run' }))
    fireEvent.keyDown(screen.getByRole('button', { name: 'Keep running' }), { key: 'Escape' })
    expect(screen.queryByText('Stop this run on the agent?')).toBeNull()
    expect(onAbort).not.toHaveBeenCalled()
  })
})

describe('Run detail — the chain, live (ChainStage)', () => {
  const SCENARIO = {
    scenario_id: 'SIM-EDR-001',
    name: 'Credential dumping',
    plane: 'EDR',
    steps: [
      { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', mitre_technique: 'T1003' },
      { id: 'step-02', name: 'Dump the process', command: 'gcore 1', mitre_technique: 'T1003.007' },
    ],
  }
  const OUTCOMES = [
    { step_id: 'step-01', termination: 'exited', rc: 0 },
    { step_id: 'step-02', termination: 'exited', rc: 0 },
  ]

  function mountChain(run, props = {}, results = []) {
    installRoutes({
      'GET /api/runs/:id': { ...run, output: '', step_outcomes: OUTCOMES },
      'GET /api/scenarios/:id': SCENARIO,
      'GET /api/results/:id': { results },
    })
    return render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run] }}>
        <RunDetailView runId={run.run_id} run={run} subTab="live" {...props} />
      </EnvironmentContext.Provider>,
    )
  }

  it('leads with the run’s chain in live mode; before a read back each step says only Ran', async () => {
    mountChain(base)
    const stage = await screen.findByTestId('chain-stage')
    expect(stage).toHaveAttribute('data-mode', 'live')
    const card = await screen.findByTestId('stage-card-step-01')
    await waitFor(() => expect(card).toHaveAttribute('data-state', 'ran'))
    expect(card).toHaveTextContent('Ran — not read back')
    expect(stage).not.toHaveTextContent(/Detected|Not seen/)
  })

  it('after the read back a step whose detection was seen says Detected', async () => {
    const read = { ...base, detected_count: 1, tc_verdict_detail: { source: 'reconcile:xsiam:lab' } }
    mountChain(read, {}, [
      { id: 1, step_id: 'step-01', outcome: 'detected' },
      { id: 2, step_id: 'step-02', outcome: 'missed' },
    ])
    await waitFor(() => expect(screen.getByTestId('stage-card-step-01')).toHaveAttribute('data-state', 'detected'))
    await waitFor(() => expect(screen.getByTestId('stage-card-step-02')).toHaveAttribute('data-state', 'not_seen'))
  })

  it('a lane header opens the component behind it', async () => {
    const onNavigate = vi.fn()
    mountChain(base, { onNavigate })
    fireEvent.click(await screen.findByRole('button', { name: /Open the component behind ENDPOINT RUNTIME/ }))
    expect(onNavigate).toHaveBeenCalledWith('component-agents', undefined)
  })
})

describe('Run detail — a Broken step opens its troubleshooting sheet', () => {
  const SCENARIO = {
    scenario_id: 'SIM-EDR-001',
    name: 'Credential dumping',
    plane: 'EDR',
    steps: [
      { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', mitre_technique: 'T1003' },
      { id: 'step-02', name: 'Dump the process', command: 'gcore 1', mitre_technique: 'T1003.007' },
    ],
  }
  // step-02 was killed by a signal POVengine did not send, and the read back
  // found nothing for it: Broken.
  const OUTCOMES = [
    { step_id: 'step-01', termination: 'exited', rc: 0 },
    { step_id: 'step-02', termination: 'signaled', rc: 137, signal: 9 },
  ]
  const read = { ...base, detected_count: 1, tc_verdict_detail: { source: 'reconcile:xsiam:lab' } }

  function mountBroken(run, props = {}) {
    installRoutes({
      'GET /api/runs/:id': { ...run, output: '', step_outcomes: OUTCOMES },
      'GET /api/scenarios/:id': SCENARIO,
      'GET /api/results/:id': { results: [
        { id: 1, step_id: 'step-01', outcome: 'detected' },
        { id: 2, step_id: 'step-02', outcome: 'missed' },
      ] },
    })
    return render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run] }}>
        <RunDetailView runId={run.run_id} run={run} subTab="live" {...props} />
      </EnvironmentContext.Provider>,
    )
  }

  it('only the Broken card is a button; it opens the causes in the order to check', async () => {
    mountBroken(read)
    await waitFor(() => expect(screen.getByTestId('stage-card-step-02')).toHaveAttribute('data-state', 'broken'))
    // A card with nowhere to go is not a button.
    expect(screen.getByTestId('stage-card-step-01').tagName).toBe('DIV')
    fireEvent.click(screen.getByRole('button', { name: /step-02 · Dump the process · Broken/ }))

    const sheet = screen.getByRole('dialog', { name: /step-02/ })
    // The host's record the causes were read from.
    expect(sheet).toHaveTextContent('exit code137')
    expect(sheet).toHaveTextContent('signal9')
    const causes = within(sheet).getAllByRole('button').filter((b) => b.dataset.cause)
    expect(causes.map((b) => b.dataset.cause)).toEqual(['killed', 'output'])
    expect(causes[0]).toHaveTextContent('Something other than POVengine killed the step (signal 9)')
    expect(causes[0]).toHaveTextContent('Results ›')
    // Focus lands on the first cause.
    expect(causes[0]).toHaveFocus()
  })

  it('a cause opens the place that fixes it and closes the sheet', async () => {
    const onNavigate = vi.fn()
    mountBroken(read, { onNavigate })
    fireEvent.click(await screen.findByRole('button', { name: /step-02 · Dump the process · Broken/ }))
    const sheet = screen.getByRole('dialog', { name: /step-02/ })
    fireEvent.click(within(sheet).getByRole('button', { name: /Read what the step printed/ }))
    expect(onNavigate).toHaveBeenCalledWith('runs', { run: 'run-1', tab: 'live' })
    expect(screen.queryByRole('dialog', { name: /step-02/ })).toBeNull()
  })

  it('Escape closes the sheet and returns focus to the card', async () => {
    mountBroken(read)
    const card = await screen.findByRole('button', { name: /step-02 · Dump the process · Broken/ })
    card.focus()
    fireEvent.click(card)
    expect(screen.getByRole('dialog', { name: /step-02/ })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: /step-02/ })).toBeNull()
    expect(card).toHaveFocus()
  })

  it('before the read back no card is a button: nothing can be Broken yet', async () => {
    mountBroken(base)
    await waitFor(() => expect(screen.getByTestId('stage-card-step-02')).toHaveAttribute('data-state', 'ran'))
    expect(screen.getByTestId('stage-card-step-02').tagName).toBe('DIV')
  })
})

describe('Run detail — a pending test case says why (design C3)', () => {
  const nothingRan = {
    ...base,
    tc_verdict: 'pending',
    tc_verdict_detail: { source: 'complete', reason: 'not_executed' },
  }
  const scenarios = [{ scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', tc_ref: 'TC-EDR-01' }]

  it('the header names the test case, PENDING, the reason chip and what to do', () => {
    mount(nothingRan, {}, { scenarios })
    const line = screen.getByTestId('run-verdict-header')
    expect(within(line).getByText('Test case')).toBeInTheDocument()
    expect(within(line).getByTestId('run-verdict-header-verdict')).toHaveTextContent(/pending/i)
    expect(within(line).getByTestId('run-verdict-header-reason')).toHaveTextContent('nothing executed')
    expect(line).toHaveTextContent('TC-EDR-01')
    // The action is visible text, not a tooltip.
    expect(line).toHaveTextContent(/Re-run the scenario/)
  })

  it('the Cortex Engine terminal under the chain states the same verdict and reason', () => {
    mount({ ...nothingRan, tc_verdict_detail: { source: 'complete', reason: 'soak_pending' } }, {}, { scenarios })
    const engine = screen.getByTestId('run-verdict-engine')
    expect(engine).toHaveTextContent('CORTEX ENGINE')
    expect(within(engine).getByTestId('run-verdict-engine-reason')).toHaveTextContent('detectors soaking')
  })

  it('a verdict with no reason draws neither line', () => {
    mount({ ...base, tc_verdict: 'pass' }, {}, { scenarios })
    expect(screen.queryByTestId('run-verdict-header')).toBeNull()
    expect(screen.queryByTestId('run-verdict-engine')).toBeNull()
  })
})
