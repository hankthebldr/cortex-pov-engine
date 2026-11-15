/**
 * Launch gate MVP (owner's v33 handoff, item 1): one Run, for pull and push
 * alike, and Run stays disabled until the authorisation is ticked.
 */
import React from 'react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'
import { installRoutes } from '../../test/mockFetch.js'
import LaunchView from '../console/LaunchView.jsx'

void React

const SCENARIO = {
  scenario_id: 'SIM-EDR-001', name: 'Credential dumping', plane: 'EDR',
  push_supported: true, pull_supported: true, bundle_targets: ['posix'],
  execution_identity: { default: 'www-data', options: ['www-data'] },
  steps: [{ id: 'step-01', identity: 'www-data', platforms: ['linux'], expected_detections: [{ type: 'BIOC' }] }],
}
const AGENT = { agent_id: 'jumpbox-01', hostname: 'jumpbox-01', os: 'linux', status: 'online', last_seen_age_seconds: 2,
  capabilities: ['shell', 'identity-harness', 'artifact-fetch'] }
const routes = () => ({
  'GET /api/tools/adapters': { adapters: [], total: 0 },
  'GET /api/agents': { agents: [AGENT], total: 1 },
  'GET /api/credentials/integrations': { integrations: [] },
  'GET /api/shelf/payloads': { payloads: [], total: 0, declared: [] },
  'GET /api/shelf/artifacts': { artifacts: null },
})

afterEach(() => { vi.restoreAllMocks() })

const primaries = () => Array.from(document.querySelectorAll('button.kit-btn--primary'))

describe('Launch gate MVP', () => {
  it('push mode renders exactly one primary action, Run, and no separate download button', async () => {
    installRoutes(routes())
    render(<LaunchView scenario={SCENARIO} selectedTarget={{ kind: 'push', id: 'bundle', label: 'Offline bundle' }} />)
    await screen.findByTestId('new-run')
    await waitFor(() => expect(primaries().map((b) => b.textContent)).toEqual(['Run']))
    expect(screen.queryByRole('button', { name: /download bundle/i })).toBeNull()
  })

  it('pull mode renders the same single Run', async () => {
    installRoutes(routes())
    render(<LaunchView scenario={SCENARIO} selectedTarget={{ kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }} />)
    await screen.findByTestId('new-run')
    await waitFor(() => expect(primaries().map((b) => b.textContent)).toEqual(['Run']))
  })

  it('Run stays disabled until the authorisation is ticked, and the reason is written under it', async () => {
    installRoutes(routes())
    render(<LaunchView scenario={SCENARIO} selectedTarget={{ kind: 'agent', id: 'jumpbox-01', label: 'jumpbox-01' }} />)
    const tick = await screen.findByTestId('new-run-authorised')
    expect(tick).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(screen.getByTestId('launch-blockers')).toHaveTextContent(/authorised/)
    fireEvent.click(tick)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run' })).toBeEnabled())
  })
})
