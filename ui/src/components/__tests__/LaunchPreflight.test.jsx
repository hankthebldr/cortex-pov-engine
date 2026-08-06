import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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
