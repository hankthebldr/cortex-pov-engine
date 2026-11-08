import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'
import { installRoutes } from '../../test/mockFetch.js'
import LaunchView from '../console/LaunchView.jsx'

/**
 * The preflight has to live AT the launch button.
 *
 * A readiness page is visited once, on a good day. These assertions pin the
 * check to the surface the DC is already on when the customer is watching.
 */

const SCENARIO = {
  scenario_id: 'SIM-EDR-022',
  name: 'Shelf-staged privesc enumeration',
  plane: 'EDR',
  push_supported: true,
  pull_supported: true,
  execution_identity: { default: 'www-data', options: ['www-data', 'root'] },
  external_tools: [{ adapter_ref: 'TOOL-LINPEAS' }],
  steps: [{ id: 'step-01', identity: 'www-data', platforms: ['linux'] }],
  primary_kpi: 'MTTD',
  threshold: { kpi: 'MTTD', op: '<=', value: 300 },
}

const AGENT = {
  agent_id: 'jumpbox-01', hostname: 'ip-10-0-1-24', os: 'linux',
  capabilities: ['shell', 'identity-harness', 'artifact-fetch'],
  status: 'online', last_seen_age_seconds: 2,
}

const ADAPTERS = {
  adapters: [{ adapter_id: 'TOOL-LINPEAS', tier: 4, name: 'LinPEAS', license: 'MIT' }],
  total: 1,
}

const ROUTES = (over = {}) => ({
  'GET /api/tools/adapters': ADAPTERS,
  'GET /api/agents': { agents: [AGENT], total: 1 },
  'GET /api/credentials/integrations': { integrations: [] },
  'GET /api/shelf/payloads': {
    payloads: [], dist_dir: '/app/payloads', total: 0,
    declared: [{
      name: 'linpeas.sh', adapter_id: 'TOOL-LINPEAS',
      url: 'https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh',
    }],
  },
  'GET /api/shelf/artifacts': { artifacts: null },
  ...over,
})

const TARGET = { kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }

beforeEach(() => { installRoutes(ROUTES()) })
afterEach(() => { vi.restoreAllMocks() })

describe('LaunchView preflight', () => {
  it('renders the preflight card on the launch surface', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByTestId('scenario-preflight')).toBeInTheDocument())
    for (const id of ['tools', 'target', 'platform', 'identity', 'measurement']) {
      expect(screen.getByTestId(`preflight-${id}`)).toBeInTheDocument()
    }
  })

  it('warns about a tool the TARGET must fetch, before the run rather than after', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('preflight-tools')).toHaveAttribute('data-status', 'warn')
    })
    expect(screen.getByTestId('preflight-tools')).toHaveTextContent(/default-deny/)
  })

  it('never claims READY while a precondition is unchecked', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByTestId('preflight-verdict')).toBeInTheDocument())
    // The identity account has not been probed on this target, so the run is
    // NOT ready — it is degraded-or-unknown, and the card says which.
    expect(screen.getByTestId('preflight-verdict')).not.toHaveTextContent('READY')
    expect(screen.getByTestId('preflight-identity')).toHaveAttribute('data-status', 'unknown')
  })

  it('folds a BLOCKING preflight row into the launch blockers list', async () => {
    installRoutes(ROUTES({
      'GET /api/agents': { agents: [{ ...AGENT, status: 'offline', last_seen_age_seconds: 90000 }] },
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/beacon offline/i)
    })
    // …and the button the DC would have clicked is dead, with the reason next to it.
    expect(screen.getByRole('button', { name: /Launch run/i })).toBeDisabled()
  })

  it('does NOT disable launch for a mere warning — ceremony is not safety', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => expect(screen.getByTestId('preflight-tools')).toHaveAttribute('data-status', 'warn'))
    expect(screen.getByRole('button', { name: /Launch run/i })).not.toBeDisabled()
  })

  it('says a scoreable scenario with no credential will resolve pending, not fail', async () => {
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    await waitFor(() => {
      expect(screen.getByTestId('preflight-measurement')).toHaveTextContent(/still owed, NOT a failed detection/)
    })
  })
})

/**
 * UI-08 / UI-04 — a pull launch the SERVER refuses must read as clearly as a
 * refused download: code AND structured detail, as an alert, next to the button.
 * The run-integrity refusals carry an OBJECT detail (unresolved steps, missing
 * agents); the old path kept only string details, so the step ids the DC has to
 * fix were dropped on the floor.
 */
describe('LaunchView — refused pull launch renders code and detail', () => {
  const refuse = (body) => () => new Response(JSON.stringify(body), {
    status: 409, headers: { 'content-type': 'application/json' },
  })

  it('PULL_TARGET_UNSATISFIABLE names every unresolved step and its reason', async () => {
    installRoutes(ROUTES({
      'POST /api/run': refuse({
        detail: {
          error: "PULL_TARGET_UNSATISFIABLE: agent 'jumpbox-01' is posix and cannot run step(s) "
            + '[step-01 (POSIX_COMMAND_UNAVAILABLE)] — declare platform_variants.linux on those steps',
          code: 'PULL_TARGET_UNSATISFIABLE',
          detail: {
            target: 'posix', agent_id: 'jumpbox-01', agent_os: 'linux',
            unresolved: [
              { step_id: 'step-01', reason: 'POSIX_COMMAND_UNAVAILABLE' },
              { step_id: 'step-04', reason: 'POSIX_COMMAND_UNAVAILABLE' },
            ],
            hint: 'declare platform_variants.linux on those steps, or launch against a Windows agent',
          },
        },
      }),
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    const btn = await screen.findByRole('button', { name: /Launch run/i })
    await waitFor(() => expect(btn).not.toBeDisabled())
    fireEvent.click(btn)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('PULL_TARGET_UNSATISFIABLE')
    // step-04 appears ONLY in the structured detail, never in the message.
    expect(alert).toHaveTextContent('step-04')
    expect(alert).toHaveTextContent(/launch against a Windows agent/)
  })

  it('TARGET_AGENT_NOT_ENROLLED names the steps that reference the missing agent', async () => {
    installRoutes(ROUTES({
      'POST /api/run': refuse({
        detail: {
          error: "TARGET_AGENT_NOT_ENROLLED: composed step target(s) ['ghost-02'] are not enrolled",
          code: 'TARGET_AGENT_NOT_ENROLLED',
          detail: { missing_agents: ['ghost-02'], referencing_steps: { 'ghost-02': ['step-03'] } },
        },
      }),
    }))
    render(<LaunchView scenario={SCENARIO} selectedTarget={TARGET} />)
    const btn = await screen.findByRole('button', { name: /Launch run/i })
    await waitFor(() => expect(btn).not.toBeDisabled())
    fireEvent.click(btn)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('TARGET_AGENT_NOT_ENROLLED')
    expect(alert).toHaveTextContent('step-03')
  })
})
