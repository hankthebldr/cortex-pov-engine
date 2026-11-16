/**
 * Launch gate — consent for a scenario's EAL steps.
 *
 * A launch that does not name who authorised it runs its EAL steps dry
 * (EAL_DRY_RUN_NO_CONSENT). The gate asks only when the armed scenario has an
 * EAL step: "Send live EAL traffic", who authorised it (remembered in this
 * browser), and "Includes C2-shaped traffic". Unticked is today's launch; the
 * fields are sent only when ticked.
 */
import React from 'react'
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react'
import '@testing-library/jest-dom'
import { installRoutes } from '../../test/mockFetch.js'
import LaunchView from '../console/LaunchView.jsx'

void React

const BASE = {
  scenario_id: 'SIM-CDR-019', name: 'Cloud audit log burst', plane: 'CDR',
  push_supported: true, pull_supported: true, bundle_targets: ['posix'],
  execution_identity: { default: 'root', options: ['root'] },
}
const AGENT_STEP = { id: 'step-01', identity: 'root', platforms: ['linux'], command: 'id', expected_detections: [{ type: 'BIOC' }] }
const EAL_STEP = {
  id: 'step-02', channel: 'eal', eal: { plugin: 'cloud_audit_emitter', params: {} },
  expected_detections: [{ type: 'Analytics' }],
}
const CLI_STEP = {
  id: 'step-02', identity: 'root', platforms: ['linux'],
  command: 'python3 -m scripts.eal_simulator.cli run "$C/campaign.yml" --live',
  expected_detections: [{ type: 'XQL' }],
}
const withSteps = (...steps) => ({ ...BASE, steps })

const AGENT = {
  agent_id: 'jumpbox-01', hostname: 'jumpbox-01', os: 'linux', status: 'online', last_seen_age_seconds: 2,
  capabilities: ['shell', 'identity-harness', 'artifact-fetch', 'cleanup', 'live-output'],
}
const TARGET = { kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }

let posted
function routes() {
  posted = []
  return installRoutes({
    'GET /api/tools/adapters': { adapters: [], total: 0 },
    'GET /api/agents': { agents: [AGENT], total: 1 },
    'GET /api/runs': { runs: [] },
    'GET /api/credentials/integrations': { integrations: [] },
    'GET /api/shelf/payloads': { payloads: [], total: 0, declared: [] },
    'GET /api/shelf/artifacts': { artifacts: null },
    'POST /api/run': (_url, init) => {
      posted.push(JSON.parse(init.body))
      return { run_id: 'run-eal-1', mode: 'pull', message: 'Task queued' }
    },
  })
}

const row = () => screen.queryByTestId('launch-eal-consent')
const live = () => within(row()).getByRole('checkbox', { name: 'Send live EAL traffic' })
const c2 = () => within(row()).getByRole('checkbox', { name: 'Includes C2-shaped traffic' })
const who = () => within(row()).getByRole('textbox', { name: 'Authorized by' })

async function mount(scenario) {
  routes()
  render(<LaunchView scenario={scenario} selectedTarget={TARGET} />)
  await screen.findByTestId('new-run')
  fireEvent.click(screen.getByTestId('new-run-authorised'))
}
async function run() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Run' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Run' }))
  await waitFor(() => expect(posted).toHaveLength(1))
  return posted[0]
}

beforeEach(() => { window.localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Launch gate — EAL consent', () => {
  it('asks only when the armed scenario has an EAL step', async () => {
    await mount(withSteps(AGENT_STEP))
    expect(row()).toBeNull()
    cleanup()

    await mount(withSteps(AGENT_STEP, EAL_STEP))
    expect(row()).not.toBeNull()
    expect(live()).not.toBeChecked()
    expect(c2()).not.toBeChecked()
  })

  it('counts a step whose command calls the EAL CLI', async () => {
    await mount(withSteps(AGENT_STEP, CLI_STEP))
    expect(row()).not.toBeNull()
  })

  it('unticked, the launch is the one sent today', async () => {
    await mount(withSteps(AGENT_STEP, EAL_STEP))
    const body = await run()
    expect(body).not.toHaveProperty('authorized_by')
    expect(body).not.toHaveProperty('consent')
  })

  it('ticked, it sends the consent and who gave it', async () => {
    await mount(withSteps(AGENT_STEP, EAL_STEP))
    fireEvent.click(live())
    fireEvent.change(who(), { target: { value: '  Dana Reyes ' } })
    const body = await run()
    expect(body.authorized_by).toBe('Dana Reyes')
    expect(body.consent).toEqual({ simulation_authorized: true })
  })

  it('sends the C2 consent only when its box is ticked too', async () => {
    await mount(withSteps(AGENT_STEP, EAL_STEP))
    fireEvent.click(live())
    fireEvent.change(who(), { target: { value: 'Dana Reyes' } })
    fireEvent.click(c2())
    const body = await run()
    expect(body.consent).toEqual({ simulation_authorized: true, c2_authorized: true })
  })

  it('live traffic needs a name: Run waits, and says so in one line', async () => {
    await mount(withSteps(AGENT_STEP, EAL_STEP))
    fireEvent.click(live())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled())
    expect(screen.getByTestId('launch-blockers')).toHaveTextContent('Name who authorised the live EAL traffic')
    fireEvent.change(who(), { target: { value: 'Dana Reyes' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run' })).toBeEnabled())
  })

  it('remembers who authorised it in this browser', async () => {
    await mount(withSteps(AGENT_STEP, EAL_STEP))
    fireEvent.click(live())
    fireEvent.change(who(), { target: { value: 'Dana Reyes' } })
    await run()
    cleanup()

    await mount(withSteps(AGENT_STEP, EAL_STEP))
    expect(who()).toHaveValue('Dana Reyes')
    // Remembering the name does not tick the box.
    expect(live()).not.toBeChecked()
  })
})
