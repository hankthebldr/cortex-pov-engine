/**
 * RunTechniquesView — the Techniques tab rendered from live Result rows.
 *
 * Pins: the strip is 14 stages in kill-chain order with empties dimmed;
 * placement comes from the TTP card; a failed run never shows a miss; an
 * unplaceable technique is listed with its reason; no count renders before
 * the fetch resolves and a failed fetch says so.
 */
import React from 'react'
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import { installRoutes } from '../../../test/mockFetch.js'
import RunTechniquesView from '../RunTechniquesView.jsx'

afterEach(cleanup)

const CARD = {
  ttp_id: 'TTP-2026-0032',
  mitre_attack: {
    techniques: [
      { technique_id: 'T1003', subtechnique_id: 'T1003.008', name: '/etc/passwd and /etc/shadow', tactic_ids: ['TA0006'] },
      { technique_id: 'T1087', subtechnique_id: 'T1087.001', name: 'Local Account', tactic_ids: ['TA0007'] },
    ],
  },
}

const RESULTS = {
  results: [
    { id: 1, run_id: 'r1', step_id: 'step-01', ttp_ref: 'TTP-2026-0032', mitre_technique: 'T1087.001', outcome: 'detected', observed: true, kpi_verdict: 'pass' },
    { id: 2, run_id: 'r1', step_id: 'step-02', ttp_ref: 'TTP-2026-0032', mitre_technique: 'T1003.008', outcome: 'missed', observed: false },
    { id: 3, run_id: 'r1', step_id: 'step-03', ttp_ref: 'TTP-2026-0404', mitre_technique: 'T1566', outcome: null, observed: false },
  ],
  total: 3,
}

function routes(runStatus, extra = {}) {
  return installRoutes({
    'GET /api/results/r1': RESULTS,
    'GET /api/ttps/TTP-2026-0032': CARD,
    'GET /api/ttps/TTP-2026-0404': () => new Response(JSON.stringify({ error: 'not found', code: 'NOT_FOUND' }), { status: 404 }),
    ...extra,
  })
}

describe('RunTechniquesView', () => {
  it('draws 14 stages in order, dims the empty ones, and places techniques through the card', async () => {
    routes()
    render(<RunTechniquesView runId="r1" run={{ run_id: 'r1', status: 'complete', tc_verdict: 'fail' }} />)
    expect(screen.getByRole('status')).toHaveTextContent('loading results')
    await waitFor(() => expect(screen.getByTestId('technique-strip')).toBeInTheDocument())
    const stages = screen.getAllByTestId('technique-stage')
    expect(stages).toHaveLength(14)
    expect(stages[0]).toHaveAttribute('data-tactic', 'TA0043')
    expect(stages[13]).toHaveAttribute('data-tactic', 'TA0040')
    const cred = stages.find((s) => s.dataset.tactic === 'TA0006')
    expect(cred).toHaveTextContent('T1003.008')
    expect(cred).toHaveAttribute('data-tone', 'miss')
    const disc = stages.find((s) => s.dataset.tactic === 'TA0007')
    expect(disc).toHaveAttribute('data-tone', 'hit')
    const empties = stages.filter((s) => s.dataset.tone === 'empty')
    expect(empties).toHaveLength(12)
    expect(empties.every((s) => s.className.includes('run-tech__stage--empty'))).toBe(true)
    // the verdict + the sentence
    expect(screen.getByTestId('technique-verdict')).toHaveTextContent('fail')
    expect(screen.getByTestId('run-techniques')).toHaveTextContent('Authored is not proven.')
    expect(screen.getByTestId('technique-totals')).toHaveTextContent('tenant-verified: 0')
  })

  it('a pending run where nothing executed says so beside the verdict, with what to do', async () => {
    const neverRan = RESULTS.results.map((r) => ({ ...r, outcome: 'not_executed', outcome_reason: 'TOOL_ABSENT', observed: false }))
    routes('complete', { 'GET /api/results/r1': { results: neverRan, total: neverRan.length } })
    const run = { run_id: 'r1', status: 'complete', tc_verdict: 'pending',
      tc_verdict_detail: { reason: 'not_executed', detail: 'nothing scored' } }
    render(<RunTechniquesView runId="r1" run={run} />)
    await waitFor(() => expect(screen.getByTestId('technique-verdict')).toHaveTextContent('pending'))
    const reason = screen.getByTestId('technique-verdict-reason')
    expect(reason).toHaveAttribute('data-reason', 'not_executed')
    expect(reason).toHaveTextContent('nothing executed')
    expect(reason.className).toContain('verdict-reason--warn')
    expect(screen.getByTestId('technique-verdict-reason-sentence')).toHaveTextContent(/re-run/i)
    expect(reason.parentElement).not.toHaveTextContent('not_executed')
    // `not_executed` rows are not "not reached" (that is an upstream sever):
    // the count names what both excluded outcomes share.
    expect(screen.getByTestId('technique-totals')).toHaveTextContent('3 never ran')
    expect(screen.getByTestId('run-techniques')).not.toHaveTextContent('not reached')
  })

  it('on a FAILED run no technique and no stage reads miss, and the page says why', async () => {
    routes()
    render(<RunTechniquesView runId="r1" run={{ run_id: 'r1', status: 'failed' }} />)
    await waitFor(() => expect(screen.getByTestId('technique-strip')).toBeInTheDocument())
    expect(screen.getByTestId('run-techniques')).toHaveAttribute('data-proven', 'false')
    expect(screen.getAllByTestId('technique-stage').some((s) => s.dataset.tone === 'miss')).toBe(false)
    expect(screen.getAllByTestId('technique-row').some((r) => r.dataset.tone === 'miss')).toBe(false)
    expect(screen.getByTestId('technique-unproven')).toHaveTextContent(/pending/)
    expect(screen.getByTestId('technique-verdict')).toHaveTextContent('not scored')
  })

  it('lists an unplaceable technique with its reason instead of guessing a stage', async () => {
    routes()
    render(<RunTechniquesView runId="r1" run={{ run_id: 'r1', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByTestId('technique-unplaced')).toBeInTheDocument())
    const rows = screen.getAllByTestId('technique-unplaced-row')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('T1566')
    expect(rows[0]).toHaveTextContent(/could not be loaded/)
    // and it is on NO stage
    expect(screen.getAllByTestId('technique-stage').some((s) => s.textContent.includes('T1566'))).toBe(false)
  })

  it('says "could not load" on a failed results fetch — never 0', async () => {
    installRoutes({
      'GET /api/results/r1': () => new Response(JSON.stringify({ error: 'boom', code: 'INTERNAL' }), { status: 500 }),
    })
    render(<RunTechniquesView runId="r1" run={{ run_id: 'r1', status: 'complete' }} />)
    await waitFor(() => expect(screen.getByTestId('technique-error')).toBeInTheDocument())
    expect(screen.queryByTestId('technique-strip')).toBeNull()
    expect(screen.getByTestId('technique-error')).toHaveTextContent(/could not load results/)
  })

  it('fetches the run itself when no run record is passed', async () => {
    routes('complete', { 'GET /api/runs/r1': { run_id: 'r1', status: 'complete', tc_verdict: 'pass', step_outcomes: [] } })
    render(<RunTechniquesView runId="r1" />)
    await waitFor(() => expect(screen.getByTestId('technique-verdict')).toHaveTextContent('pass'))
    expect(within(screen.getByTestId('technique-table')).getAllByTestId('technique-row')).toHaveLength(2)
  })
})
