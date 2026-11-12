/**
 * Kubernetes tab of the deploy-agent dialog + agentManifestUrl.
 */
import React from 'react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { installRoutes } from '../../test/mockFetch.js'
import { agentManifestUrl } from '../../api/client.js'
import TargetsView from '../console/TargetsView.jsx'
import { EnvironmentContext, DEFAULT_ENV } from '../../context/EnvironmentContext.jsx'

void React

const AGENTS = [
  { agent_id: 'k8s-1', status: 'online', runtime_facts: {
    container: true, posture_profile: 'cluster-privilege', node_name: 'node-a',
    prepare_status: 'PREPARE_FAILED: apt' } },
  { agent_id: 'plain-1', status: 'online', runtime_facts: null },
  { agent_id: 'empty-1', status: 'online', runtime_facts: {} },
  { agent_id: 'nulls-1', status: 'online', runtime_facts: { container: true, node_name: null, posture_profile: null } },
]

function mountTargets(agents = []) {
  return render(
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, agents, refreshAgents: vi.fn() }}>
      <TargetsView />
    </EnvironmentContext.Provider>,
  )
}

const MINTED = {
  id: 9, token: 'cxs_K8sTokenValue0123456789abcdefABCD', max_uses: 50,
  remaining_uses: 50, expires_at: null,
}

describe('agentManifestUrl', () => {
  it('is the bare path with no arguments', () => {
    expect(agentManifestUrl()).toBe('/api/agents/manifest')
  })
  it('carries both consent keys and authorized_by for lab-privileged, never a token', () => {
    const u = agentManifestUrl({
      profile: 'lab-privileged',
      consent: { cluster_privilege_authorized: true, node_access_authorized: true },
      authorizedBy: 'henry',
    })
    expect(u).toContain('profile=lab-privileged')
    expect(u).toContain('cluster_privilege_authorized=true')
    expect(u).toContain('node_access_authorized=true')
    expect(u).toContain('authorized_by=henry')
    expect(u).not.toContain('token')
  })
  it('omits a consent key that is not true', () => {
    const u = agentManifestUrl({
      profile: 'lab-privileged',
      consent: { cluster_privilege_authorized: true, node_access_authorized: false },
      authorizedBy: 'henry',
    })
    expect(u).toContain('cluster_privilege_authorized=true')
    expect(u).not.toContain('node_access_authorized')
  })
  it('omits parameters equal to their default', () => {
    expect(agentManifestUrl({
      profile: 'baseline', image: 'debian:12-slim', arch: 'amd64', prepare: 'packages', ttlSeconds: 43200,
    })).toBe('/api/agents/manifest')
    expect(agentManifestUrl({ arch: 'arm64', ttlSeconds: 600 })).toBe('/api/agents/manifest?arch=arm64&ttl_seconds=600')
  })
})

describe('TargetsView — Kubernetes tab', () => {
  let fetchSpy
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fetchSpy = installRoutes({
      'GET /api/agents': { agents: [] },
      'GET /api/infra/bundles': { bundles: [] },
      'POST /api/agents/enroll/tokens': MINTED,
    })
  })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  async function openK8s() {
    mountTargets()
    const openers = await screen.findAllByRole('button', { name: /Deploy agent/i })
    fireEvent.click(openers[0])
    await screen.findByRole('dialog')
    fireEvent.click(screen.getByRole('button', { name: /Kubernetes/i }))
    return screen.findByTestId('deploy-kubernetes')
  }

  it('shows the baseline manifest command first and the Secret command only after minting', async () => {
    await openK8s()
    const manifest = screen.getByTestId('deploy-k8s-manifest').textContent
    expect(manifest).toMatch(/^kubectl apply -f '.*\/api\/agents\/manifest'$/)
    expect(screen.queryByTestId('deploy-k8s-secret')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Mint enrollment token/i }))
    const secret = (await screen.findByTestId('deploy-k8s-secret')).textContent
    expect(secret).toBe(
      `kubectl -n cortexsim-agent create secret generic cortexsim-enroll --from-literal=server=${window.location.origin} --from-literal=token=${MINTED.token}`)
    expect(screen.getByTestId('deploy-k8s-manifest').textContent).not.toContain(MINTED.token)
    const post = fetchSpy.mock.calls.find(([u, o]) => String(u).includes('/enroll/tokens') && o?.method === 'POST')
    expect(JSON.parse(post[1].body)).toMatchObject({ max_uses: 50, ttl_seconds: 43200 })
    expect(screen.getByText(/leaves the pod in Init:Error/)).toBeTruthy()
    expect(screen.getByText(/It removes itself after 12 hours\./)).toBeTruthy()
  })

  it('withholds the lab-privileged command until both boxes are ticked and a name is given', async () => {
    await openK8s()
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'lab-privileged' } })
    expect(screen.queryByTestId('deploy-k8s-manifest')).toBeNull()
    expect(screen.getByText(/Tick each capability and name who authorised it/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText(/Bind a wildcard ClusterRole/))
    fireEvent.change(screen.getByLabelText('Authorized by'), { target: { value: 'henry' } })
    expect(screen.queryByTestId('deploy-k8s-manifest')).toBeNull()
    fireEvent.click(screen.getByLabelText(/host PID and the node's root/))
    const cmd = screen.getByTestId('deploy-k8s-manifest').textContent
    expect(cmd).toContain('profile=lab-privileged')
    expect(cmd).toContain('node_access_authorized=true')
    expect(cmd).toContain('authorized_by=henry')
  })

  it('drops stale consent when the profile changes back to baseline', async () => {
    await openK8s()
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'cluster-privilege' } })
    fireEvent.click(screen.getByLabelText(/Bind a wildcard ClusterRole/))
    fireEvent.change(screen.getByLabelText('Authorized by'), { target: { value: 'henry' } })
    expect(screen.getByTestId('deploy-k8s-manifest').textContent).toContain('cluster_privilege_authorized=true')
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'baseline' } })
    const cmd = screen.getByTestId('deploy-k8s-manifest').textContent
    expect(cmd).not.toContain('authorized')
  })

  it('unticks every box on a profile change and never asserts consent the profile does not need', async () => {
    await openK8s()
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'lab-privileged' } })
    fireEvent.click(screen.getByLabelText(/Bind a wildcard ClusterRole/))
    fireEvent.click(screen.getByLabelText(/host PID and the node's root/))
    fireEvent.change(screen.getByLabelText('Authorized by'), { target: { value: 'henry' } })
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'cluster-privilege' } })
    expect(screen.getByLabelText(/Bind a wildcard ClusterRole/).checked).toBe(false)
    expect(screen.queryByTestId('deploy-k8s-manifest')).toBeNull()
    fireEvent.click(screen.getByLabelText(/Bind a wildcard ClusterRole/))
    const cmd = screen.getByTestId('deploy-k8s-manifest').textContent
    expect(cmd).toContain('cluster_privilege_authorized=true')
    expect(cmd).not.toContain('node_access_authorized')
  })

  it('shows an inline message and no command for an authorized_by the server would reject', async () => {
    await openK8s()
    fireEvent.change(screen.getByLabelText('Profile'), { target: { value: 'cluster-privilege' } })
    fireEvent.click(screen.getByLabelText(/Bind a wildcard ClusterRole/))
    fireEvent.change(screen.getByLabelText('Authorized by'), { target: { value: 'bad&name=1' } })
    expect(screen.getByRole('alert').textContent).toMatch(/Authorized by may contain/)
    expect(screen.queryByTestId('deploy-k8s-manifest')).toBeNull()
  })
})

describe('TargetsView — container facts on agent rows', () => {
  beforeEach(() => {
    installRoutes({
      'GET /api/agents': { agents: [] },
      'GET /api/infra/bundles': { bundles: [] },
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('renders profile, node and a failed prepare; nothing for missing members', async () => {
    mountTargets(AGENTS)
    await screen.findByText('k8s-1')
    await waitFor(() => expect(screen.getAllByTestId('agent-container-facts').length).toBe(1))
    const [first] = screen.getAllByTestId('agent-container-facts')
    expect(first.textContent).toBe('cluster-privilege · node node-a · PREPARE_FAILED: apt')
    // every member missing -> no paragraph at all
    expect(screen.getAllByTestId('agent-container-facts').length).toBe(1)
  })
})
