/**
 * AgentsView — the beacon fleet tab split out of TargetsView.
 *
 * Guards the behaviour that moved: the roster renders with derived liveness,
 * selecting a beacon still arms it as the launch target in pull mode, and the
 * deploy flow hands over a per-OS one-line installer. Also pins the split
 * itself — TargetsView must no longer render the agent roster, or the two
 * views would drift back into duplicating each other.
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../../api/client.js', () => ({
  getAgents: vi.fn(),
  deleteAgent: vi.fn(),
  agentInstallUrl: ({ os, id }) => `/api/agents/install?os=${os}&id=${id}`,
  getInfraBundles: vi.fn(),
}))

import { getAgents, deleteAgent, getInfraBundles } from '../../api/client.js'
import AgentsView from '../console/AgentsView.jsx'
import TargetsView from '../console/TargetsView.jsx'

void React

const LIVE_AGENT = {
  agent_id: 'jumpbox-01',
  hostname: 'lab-jump',
  os: 'linux',
  last_seen: new Date().toISOString(),
}

// 10 minutes stale — well past the 60s AGENT_STALE_MS window.
const STALE_AGENT = {
  agent_id: 'victim-02',
  hostname: 'k3s-node',
  os: 'linux',
  last_seen: new Date(Date.now() - 600_000).toISOString(),
}

describe('<AgentsView />', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getAgents.mockResolvedValue([LIVE_AGENT, STALE_AGENT])
    getInfraBundles.mockResolvedValue([])
  })

  it('renders the roster with liveness derived from last_seen', async () => {
    render(<AgentsView />)
    expect(await screen.findByText('jumpbox-01')).toBeInTheDocument()
    expect(screen.getByText('victim-02')).toBeInTheDocument()
    // One live, one stale — the count reflects only the live one.
    expect(screen.getByText('1/2 live')).toBeInTheDocument()
    expect(screen.getByText('live')).toBeInTheDocument()
    expect(screen.getByText('stale')).toBeInTheDocument()
  })

  it('arms a beacon as the launch target in pull mode', async () => {
    const onSelectTarget = vi.fn()
    render(<AgentsView onSelectTarget={onSelectTarget} />)
    fireEvent.click(await screen.findByText('jumpbox-01'))
    expect(onSelectTarget).toHaveBeenCalledWith({
      kind: 'agent',
      id: 'jumpbox-01',
      label: 'jumpbox-01',
    })
  })

  it('marks the currently selected beacon', async () => {
    render(<AgentsView selectedTarget={{ kind: 'agent', id: 'jumpbox-01' }} />)
    expect(await screen.findByText(/selected · pull mode/)).toBeInTheDocument()
  })

  it('offers a per-OS one-line installer from the deploy flow', async () => {
    render(<AgentsView />)
    fireEvent.click(await screen.findByRole('button', { name: /deploy agent/i }))
    expect(screen.getByRole('dialog', { name: /deploy agent/i })).toBeInTheDocument()
    expect(screen.getByText(/curl -fsSL/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /windows/i }))
    expect(screen.getByText(/iwr -useb/)).toBeInTheDocument()
  })

  it('confirms before deleting a beacon', async () => {
    deleteAgent.mockResolvedValue({})
    render(<AgentsView />)
    await screen.findByText('jumpbox-01')

    fireEvent.click(screen.getAllByTitle('Delete this agent')[0])
    // Confirm state: a Cancel button appears alongside the destructive one.
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    expect(deleteAgent).not.toHaveBeenCalled() // still just confirming

    fireEvent.click(screen.getByRole('button', { name: /^delete$/i }))
    await waitFor(() => expect(deleteAgent).toHaveBeenCalledWith('jumpbox-01'))
  })

  it('shows an empty state when no beacons are registered', async () => {
    getAgents.mockResolvedValue([])
    render(<AgentsView />)
    expect(await screen.findByText('No agents registered')).toBeInTheDocument()
  })
})

describe('<TargetsView /> after the Agents split', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getAgents.mockResolvedValue([LIVE_AGENT])
    getInfraBundles.mockResolvedValue([])
  })

  it('no longer renders the agent roster, and never fetches agents', async () => {
    render(<TargetsView />)
    await screen.findByText('Managed in the Agents tab')
    expect(screen.queryByText('jumpbox-01')).toBeNull()
    expect(getAgents).not.toHaveBeenCalled()
  })

  it('routes the operator to the Agents tab', async () => {
    const onGoToAgents = vi.fn()
    render(<TargetsView onGoToAgents={onGoToAgents} />)
    fireEvent.click(await screen.findByRole('button', { name: /open agents/i }))
    expect(onGoToAgents).toHaveBeenCalled()
  })

  it('still offers the agentless push-bundle path', async () => {
    const onSelectTarget = vi.fn()
    render(<TargetsView onSelectTarget={onSelectTarget} />)
    fireEvent.click(await screen.findByText('Offline bundle'))
    expect(onSelectTarget).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'push', id: 'bundle' })
    )
  })
})
