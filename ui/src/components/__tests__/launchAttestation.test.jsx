/**
 * Launch-time prevention context: the form posts the attested fields only when
 * set, offers a pair picker over terminal runs of the same scenario and
 * target, and the canary button fills canary_run_id. A 409 renders the
 * server's structured detail inline.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { installRoutes } from '../../test/mockFetch.js'
import useLaunchScenario from '../console/useLaunchScenario.js'
import AttestationFields from '../console/AttestationFields.jsx'

void React

const SCENARIO = {
  scenario_id: 'SIM-EDR-001',
  name: 'Cred dump',
  plane: 'EDR',
  execution_identity: { default: 'www-data', options: ['www-data'] },
}

function Harness() {
  const launch = useLaunchScenario(SCENARIO)
  return (
    <div>
      <AttestationFields launch={launch} />
      <button type="button" onClick={() => launch.launch()}>fire</button>
      <output data-testid="last">{launch.lastRun ? `${launch.lastRun.status}|${launch.lastRun.message}` : ''}</output>
    </div>
  )
}

const RUNS = [
  { run_id: 'r-done', scenario_id: 'SIM-EDR-001', target: 'agent-1', status: 'complete', started_at: '2026-10-26T09:00:00' },
  { run_id: 'r-fail', scenario_id: 'SIM-EDR-001', target: 'agent-1', status: 'failed', started_at: '2026-10-26T08:00:00' },
  { run_id: 'r-live', scenario_id: 'SIM-EDR-001', target: 'agent-1', status: 'running', started_at: '2026-10-26T10:00:00' },
  { run_id: 'r-other-s', scenario_id: 'SIM-EDR-002', target: 'agent-1', status: 'complete', started_at: '2026-10-26T07:00:00' },
  { run_id: 'r-other-t', scenario_id: 'SIM-EDR-001', target: 'agent-9', status: 'complete', started_at: '2026-10-26T06:00:00' },
]

function routes(over = {}) {
  const posted = []
  installRoutes({
    'GET /api/agents': { agents: [{ agent_id: 'agent-1', hostname: 'h1', os: 'linux' }] },
    'GET /api/runs': RUNS,
    'POST /api/run': (_u, init) => { posted.push(JSON.parse(init.body)); return { run_id: 'new', id: 'new' } },
    ...over,
  })
  return posted
}

describe('launch form — prevention context', () => {
  it('default body is unchanged when nothing is set', async () => {
    const posted = routes()
    render(<Harness />)
    await screen.findByLabelText(/Agent profile mode/i)
    await waitFor(() => expect(screen.getByLabelText(/Pair with run/i).options.length).toBeGreaterThan(1))
    await userEvent.click(screen.getByRole('button', { name: 'fire' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(Object.keys(posted[0]).sort()).toEqual(['identity', 'mode', 'scenario_id', 'target_agent_id'])
  })

  it('posts every attested field once set', async () => {
    const posted = routes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByLabelText(/Pair with run/i).options.length).toBeGreaterThan(1))
    await userEvent.selectOptions(screen.getByLabelText(/Agent profile mode/i), 'block')
    await userEvent.selectOptions(screen.getByLabelText(/BAS mode/i), 'enabled')
    await userEvent.selectOptions(screen.getByLabelText(/Soakless/i), 'disabled')
    await userEvent.type(screen.getByLabelText(/Attested by/i), 'henry')
    await userEvent.selectOptions(screen.getByLabelText(/Pair with run/i), 'r-done')
    await userEvent.click(screen.getByRole('button', { name: 'fire' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toMatchObject({
      agent_profile_mode: 'block',
      bas_mode_attested: true,
      soakless_attested: false,
      attested_by: 'henry',
      paired_with_run_id: 'r-done',
    })
  })

  it('pair picker lists only terminal runs of the same scenario and target', async () => {
    routes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByLabelText(/Pair with run/i).options.length).toBeGreaterThan(1))
    const values = Array.from(screen.getByLabelText(/Pair with run/i).options).map((o) => o.value).filter(Boolean)
    expect(values.sort()).toEqual(['r-done', 'r-fail'])
  })

  it('Run canary first calls /canary, shows the run id and sends canary_run_id', async () => {
    let canaryBody = null
    const posted = routes({
      'POST /api/runs/canary': (_u, init) => { canaryBody = JSON.parse(init.body); return { run_id: 'canary-9', scenario_id: 'SIM-CANARY' } },
    })
    render(<Harness />)
    await waitFor(() => expect(screen.getByLabelText(/Pair with run/i).options.length).toBeGreaterThan(1))
    await userEvent.click(screen.getByRole('button', { name: /Run canary first/i }))
    expect(await screen.findByText(/canary-9/)).toBeInTheDocument()
    expect(canaryBody).toEqual({ target_agent_id: 'agent-1' })
    await userEvent.click(screen.getByRole('button', { name: 'fire' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0].canary_run_id).toBe('canary-9')
  })

  it('renders the structured detail of a CANARY_NOT_AVAILABLE 409 inline', async () => {
    routes({
      'POST /api/runs/canary': new Response(JSON.stringify({
        detail: { error: 'Canary not available', code: 'CANARY_NOT_AVAILABLE', detail: 'block mode will be attested-only' },
      }), { status: 409, headers: { 'content-type': 'application/json' } }),
    })
    render(<Harness />)
    await screen.findByLabelText(/Agent profile mode/i)
    await userEvent.click(screen.getByRole('button', { name: /Run canary first/i }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/block mode will be attested-only/)
    expect(alert.textContent).toMatch(/CANARY_NOT_AVAILABLE/)
  })

  it('renders the structured detail of a PAIR_INVALID 409 on launch', async () => {
    routes({
      'POST /api/run': new Response(JSON.stringify({
        detail: { error: 'Pair invalid', code: 'PAIR_INVALID', detail: 'partner run is on a different target' },
      }), { status: 409, headers: { 'content-type': 'application/json' } }),
    })
    render(<Harness />)
    await waitFor(() => expect(screen.getByLabelText(/Pair with run/i).options.length).toBeGreaterThan(1))
    await userEvent.selectOptions(screen.getByLabelText(/Pair with run/i), 'r-done')
    await userEvent.click(screen.getByRole('button', { name: 'fire' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/partner run is on a different target/)
    expect(alert.textContent).toMatch(/PAIR_INVALID/)
  })
})
