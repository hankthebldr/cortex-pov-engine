/**
 * Runs shows what was attempted; Results shows what Cortex saw.
 *
 *   Runs › run   the lifecycle, the chain and ONE panel: the runtime log, a
 *                block per step with the command that was attempted, how it
 *                went on the host and what it printed. No detection view and
 *                no detection count, on the page or on a row.
 *   Results      the run's verdict, then Detections · Storyline · Techniques ·
 *                Topology · Causality · Tenant evidence. A run that has not
 *                been reviewed says so in every tab, never "missed".
 *   Old links    `#/runs?run=<id>&tab=<a results tab>` open Results on it.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, within, cleanup, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'
import { getDestination } from '../destinations.jsx'

void React

const RunsSurface = getDestination('runs').Component
const ProofSurface = getDestination('proof').Component

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Credential dumping',
  plane: 'EDR',
  tc_ref: 'TC-EDR-01',
  steps: [
    { id: 'step-01', name: 'Read the shadow file', command: 'cat /etc/shadow', identity: 'root', mitre_technique: 'T1003.008' },
    { id: 'step-02', name: 'Dump the process', command: 'gcore -o /tmp/dump 1', identity: 'www-data', mitre_technique: 'T1003.007' },
    { id: 'step-03', name: 'Stage the archive', command: 'tar czf /tmp/loot.tgz /tmp/dump.1', identity: 'www-data', mitre_technique: 'T1560.001' },
  ],
}

const OUTPUT = [
  '=== STEP 1/3 · step-01 · T1003.008 · identity=root ===',
  'root:$6$abc:19000:0:99999:7:::',
  '--- exit_code=0 duration=1.2s ---',
  '=== STEP 2/3 · step-02 · T1003.007 · identity=www-data ===',
  'gcore: failed to attach: Operation not permitted',
  '--- exit_code=1 duration=310ms ---',
].join('\n')

const OUTCOMES = [
  { step_id: 'step-01', termination: 'exited', rc: 0 },
  { step_id: 'step-02', termination: 'exited', rc: 1 },
]

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
  detected_count: 3,
}
const UNREAD = { ...base, status: 'failed', tc_verdict: 'pending', tc_verdict_detail: { source: 'complete' } }
const REVIEWED = {
  ...base,
  status: 'complete',
  tc_verdict: 'fail',
  tc_verdict_detail: {
    source: 'reconcile:xsiam:lab', scored_at: '2026-11-13T17:05:00',
    verdict: 'fail', detail: '2 detection(s) failed verification',
  },
}

const NOT_READ = { run_id: 'run-1', status: 'pending', code: 'TENANT_EVIDENCE_NOT_READ', snapshot: null, history: [], case_ledger: null }

function routes(run, extra = {}) {
  return installRoutes({
    'GET /api/runs/:id': { ...run, output: OUTPUT, step_outcomes: OUTCOMES },
    'GET /api/scenarios/:id': SCENARIO,
    'GET /api/results/:id': { results: [] },
    'GET /api/runs/:id/tenant-evidence': NOT_READ,
    ...extra,
  })
}

function mountRuns(run, params, props = {}) {
  routes(run)
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run], scenarios: [SCENARIO] }}>
      <RunsSurface params={params} setParams={() => {}} onNavigate={() => {}} onAbortRun={() => {}} {...props} />
    </EnvironmentContext.Provider>,
  )
}

function mountResults(run, params = {}, props = {}) {
  routes(run)
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [run], scenarios: [SCENARIO] }}>
      <ProofSurface params={{ run: run.run_id, ...params }} setParams={() => {}} onNavigate={() => {}} {...props} />
    </EnvironmentContext.Provider>,
  )
}

afterEach(cleanup)

describe('Runs › run: the runtime log', () => {
  it('a finished run shows each step: the command attempted, how it went, what it printed', async () => {
    mountRuns(UNREAD, { run: 'run-1' })
    const log = await screen.findByRole('region', { name: 'Runtime log' })
    await waitFor(() => expect(within(log).getAllByTestId('runtime-step')).toHaveLength(3))
    const [one, two, three] = within(log).getAllByTestId('runtime-step')

    await waitFor(() => expect(one).toHaveAttribute('data-state', 'ran'))
    expect(within(one).getByRole('img', { name: 'Ran' })).toBeInTheDocument()
    expect(one).toHaveTextContent('Read the shadow file')
    expect(one).toHaveTextContent('T1003.008')
    expect(one).toHaveTextContent('as root')
    expect(within(one).getByTestId('runtime-step-command')).toHaveTextContent('cat /etc/shadow')
    expect(one).toHaveTextContent('exit 0 · 1.2s')
    expect(within(one).getByTestId('runtime-step-output')).toHaveTextContent('root:$6$abc:19000:0:99999:7:::')

    expect(two).toHaveAttribute('data-state', 'failed')
    expect(within(two).getByRole('img', { name: 'Failed' })).toBeInTheDocument()
    expect(two).toHaveTextContent('exit 1 · 310ms')
    expect(within(two).getByTestId('runtime-step-output')).toHaveTextContent('Operation not permitted')

    // The run stopped before step 3: it did not run, and its command still shows.
    expect(three).toHaveAttribute('data-state', 'did_not_run')
    expect(within(three).getByRole('img', { name: 'Did not run' })).toBeInTheDocument()
    expect(within(three).getByTestId('runtime-step-command')).toHaveTextContent('tar czf /tmp/loot.tgz /tmp/dump.1')
    expect(within(three).queryByTestId('runtime-step-output')).toBeNull()

    expect(screen.queryByText(/waiting for agent/i)).toBeNull()
  })

  it('a live run marks the step on the agent Running and the rest Queued', async () => {
    const live = { ...base, status: 'running', completed_at: null, tc_verdict: null, tc_verdict_detail: null }
    installRoutes({
      'GET /api/runs/:id': {
        ...live,
        output: OUTPUT.split('\n').slice(0, 3).join('\n'),
        step_outcomes: OUTCOMES.slice(0, 1),
      },
      'GET /api/scenarios/:id': SCENARIO,
    })
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [live], scenarios: [SCENARIO] }}>
        <RunsSurface params={{ run: 'run-1' }} setParams={() => {}} onNavigate={() => {}} onAbortRun={() => {}} />
      </EnvironmentContext.Provider>,
    )
    const log = await screen.findByRole('region', { name: 'Runtime log' })
    await waitFor(() => expect(within(log).getAllByTestId('runtime-step')).toHaveLength(3))
    const steps = within(log).getAllByTestId('runtime-step')
    await waitFor(() => expect(steps[0]).toHaveAttribute('data-state', 'ran'))
    expect(steps.map((s) => s.getAttribute('data-state'))).toEqual(['ran', 'running', 'queued'])
    expect(within(steps[1]).getByTestId('runtime-step-command')).toHaveTextContent('gcore -o /tmp/dump 1')
  })

  it('the run page has no detection view: no tabs, no case panel, no detection count', async () => {
    mountRuns(REVIEWED, { run: 'run-1' })
    await screen.findByRole('region', { name: 'Runtime log' })
    expect(screen.queryByRole('tablist')).toBeNull()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByTestId('run-case')).toBeNull()
    expect(screen.queryByTestId('tenant-evidence')).toBeNull()
    expect(document.body.textContent).not.toMatch(/detected|missed/i)
  })

  it('a reviewed run says so and offers Open results as its one primary action', async () => {
    const onNavigate = vi.fn()
    mountRuns(REVIEWED, { run: 'run-1' }, { onNavigate })
    await screen.findByRole('region', { name: 'Runtime log' })
    expect(screen.getByTestId('run-lifecycle-message')).toHaveTextContent('Ran · reviewed via xsiam:lab')
    const open = screen.getByRole('button', { name: 'Open results' })
    expect(open.className).toMatch(/kit-btn--primary/)
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
    fireEvent.click(open)
    expect(onNavigate).toHaveBeenCalledWith('proof', { run: 'run-1' })
  })

  it('a run that has not been reviewed offers Review as its one primary action', async () => {
    mountRuns(UNREAD, { run: 'run-1' })
    await screen.findByRole('region', { name: 'Runtime log' })
    expect(screen.getByTestId('run-lifecycle-message')).toHaveTextContent('Failed · not reviewed')
    expect(screen.getByRole('button', { name: 'Review' }).className).toMatch(/kit-btn--primary/)
    expect(screen.queryByRole('button', { name: 'Open results' })).toBeNull()
  })
})

describe('Runs: a row never carries a detection verdict', () => {
  it('says how the run went and whether it is reviewed', () => {
    mountRuns(REVIEWED, {})
    const row = screen.getByTestId('run-row')
    expect(within(within(row).getByTestId('run-status')).getByRole('img', { name: 'Ran' })).toBeInTheDocument()
    expect(within(within(row).getByTestId('run-review')).getByRole('img', { name: 'Reviewed' })).toBeInTheDocument()
    expect(row.textContent).not.toMatch(/detected|missed|\d+\s*(of|\/)\s*\d+/i)
    expect(within(row).getByRole('button', { name: 'Open results' })).toBeInTheDocument()
  })

  it('an unreviewed failed run reads Failed · Not reviewed, with Review', () => {
    mountRuns(UNREAD, {})
    const row = screen.getByTestId('run-row')
    expect(within(within(row).getByTestId('run-status')).getByRole('img', { name: 'Failed' })).toBeInTheDocument()
    expect(within(within(row).getByTestId('run-review')).getByRole('img', { name: 'Not reviewed' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Review' })).toBeInTheDocument()
  })
})

describe('Runs: old tab links open Results', () => {
  it.each(['evidence', 'storyline', 'techniques', 'topology', 'causality'])(
    '#/runs?run=<id>&tab=%s forwards to #/proof?run=<id>&tab=<same>',
    async (tab) => {
      const onNavigate = vi.fn()
      mountRuns(REVIEWED, { run: 'run-1', tab }, { onNavigate })
      await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('proof', { run: 'run-1', tab }))
      expect(screen.queryByRole('region', { name: 'Runtime log' })).toBeNull()
    },
  )

  it('#/runs?run=<id>&tab=live stays on the run', async () => {
    const onNavigate = vi.fn()
    mountRuns(REVIEWED, { run: 'run-1', tab: 'live' }, { onNavigate })
    await screen.findByRole('region', { name: 'Runtime log' })
    expect(onNavigate).not.toHaveBeenCalled()
  })
})

describe('Results: the verdict, then six tabs', () => {
  it('is headed Results, with the tabs in order and Detections open', async () => {
    mountResults(REVIEWED)
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual([
      'Detections', 'Storyline', 'Techniques', 'Topology', 'Causality', 'Tenant evidence',
    ])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toEqual(['Results'])
    expect(screen.getByRole('region', { name: /result of/i })).toHaveTextContent('SIM-EDR-001')
  })

  it('states the verdict in words, with its reason', async () => {
    mountResults(REVIEWED)
    const line = await screen.findByTestId('result-verdict')
    expect(within(line).getByRole('img', { name: 'Failed' })).toBeInTheDocument()
    expect(line).toHaveTextContent('2 detection(s) failed verification')
  })

  it('the URL names the tab: an old evidence link opens Detections, and a click writes ?tab=', async () => {
    const setParams = vi.fn()
    mountResults(REVIEWED, { tab: 'evidence' }, { setParams })
    expect(await screen.findByRole('tab', { name: 'Detections' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('tab', { name: 'Storyline' }))
    expect(setParams).toHaveBeenCalledWith({ tab: 'storyline' })
  })

  it('Tenant evidence holds the tenant panel and the one case panel', async () => {
    mountResults(REVIEWED, { tab: 'tenant' })
    expect(await screen.findByRole('tab', { name: 'Tenant evidence' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByTestId('tenant-evidence')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('run-case')).toHaveLength(1))
  })

  it('Detections has no tenant panel and no Compare runs', async () => {
    mountResults(REVIEWED)
    await screen.findByRole('tab', { name: 'Detections' })
    await screen.findByRole('button', { name: /validate all/i })
    expect(screen.queryByTestId('tenant-evidence')).toBeNull()
    expect(screen.queryByText(/compare runs/i)).toBeNull()
  })

  it('its actions are kit controls: Validate all and one Download', async () => {
    mountResults(REVIEWED)
    const validate = await screen.findByRole('button', { name: /validate all/i })
    expect(validate.className).toMatch(/kit-btn/)
    const download = screen.getByRole('button', { name: 'Download' })
    expect(download.className).toMatch(/kit-btn--secondary/)
    expect(document.querySelector('.btn')).toBeNull()
    fireEvent.click(download)
    const items = within(screen.getByRole('menu')).getAllByRole('menuitem').map((i) => i.textContent)
    expect(items.join(' ')).toMatch(/Narrative report/)
    expect(items.join(' ')).toMatch(/Detection scoring matrix/)
    expect(items.join(' ')).toMatch(/ATT&CK Navigator layer/)
    expect(items.join(' ')).toMatch(/POV briefing bundle/)
  })
})

describe('Results: a run that has not been reviewed', () => {
  it('says Not reviewed once, with Review, and no verdict', async () => {
    mountResults(UNREAD)
    expect(await screen.findByTestId('readback-notice')).toHaveTextContent('Not reviewed — no results yet')
    expect(screen.getAllByRole('button', { name: 'Review' })).toHaveLength(1)
    expect(screen.queryByTestId('result-verdict')).toBeNull()
  })

  it.each(['detections', 'storyline', 'techniques', 'topology', 'causality'])(
    'the %s tab says Not reviewed, never missed',
    async (tab) => {
      mountResults(UNREAD, { tab })
      const panel = await screen.findByRole('tabpanel')
      expect(within(panel).getByRole('img', { name: 'Not reviewed' })).toBeInTheDocument()
      expect(document.body.textContent).not.toMatch(/missed/i)
      expect(screen.queryByRole('button', { name: /validate all/i })).toBeNull()
    },
  )

  it('Tenant evidence still reads the stored snapshot', async () => {
    mountResults(UNREAD, { tab: 'tenant' })
    expect(await screen.findByTestId('tenant-evidence')).toBeInTheDocument()
  })

  it('says why it is pending while its detectors soak (design C3)', async () => {
    const soaking = {
      ...UNREAD, status: 'complete', tc_verdict: 'pending',
      tc_verdict_detail: { source: 'complete', reason: 'soak_pending' },
    }
    mountResults(soaking)
    const line = await screen.findByTestId('result-verdict')
    expect(within(line).getByRole('img', { name: 'Pending' })).toBeInTheDocument()
    expect(line).toHaveTextContent('detectors soaking')
    expect(line).toHaveTextContent('TC-EDR-01')
    // What to do about it is one hover away, not a paragraph on the page.
    expect(within(line).getByText('detectors soaking')).toHaveAttribute('title', expect.stringMatching(/after the window closes/))
  })

  it('a run where nothing executed says so once, with no Review and no verdict', async () => {
    const dry = {
      ...UNREAD, status: 'complete', tc_verdict: 'pending',
      tc_verdict_detail: { source: 'complete', reason: 'not_executed' },
    }
    mountResults(dry)
    expect(await screen.findByTestId('readback-notice')).toHaveTextContent('No detection step ran — nothing was tested')
    expect(screen.queryByTestId('result-verdict')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
  })
})

describe('Results: what changes how the results read', () => {
  const DEGRADED = [
    '=== STEP 1/3 · step-01 · T1003.008 · identity=Administrator ===',
    '!! IDENTITY NOT HONOURED: windows has no unattended impersonation; ran as direct',
    'dumped',
    '--- exit_code=0 duration=1.2s ---',
  ].join('\n')

  it('is one collapsed line above the verdict, holding the caveats', async () => {
    installRoutes({
      'GET /api/runs/:id': { ...REVIEWED, output: DEGRADED, step_outcomes: [] },
      'GET /api/results/:id': { results: [] },
    })
    render(
      <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, runs: [REVIEWED], scenarios: [SCENARIO] }}>
        <ProofSurface params={{ run: 'run-1' }} setParams={() => {}} onNavigate={() => {}} />
      </EnvironmentContext.Provider>,
    )
    const box = await screen.findByTestId('run-caveats')
    expect(box.open).toBe(false)
    expect(box.querySelector('summary')).toHaveTextContent('1 caveat')
    expect(within(box).getByTestId('run-identity-banner')).toHaveTextContent('1 of 3 steps ran as the beacon account')
    // Before the verdict and the tabs, so it is read first.
    const verdict = screen.getByTestId('result-verdict')
    expect(box.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a run that ran as authored has no caveats line', async () => {
    mountResults(REVIEWED)
    await screen.findByTestId('result-verdict')
    await waitFor(() => expect(screen.queryByTestId('run-caveats')).toBeNull())
  })
})
