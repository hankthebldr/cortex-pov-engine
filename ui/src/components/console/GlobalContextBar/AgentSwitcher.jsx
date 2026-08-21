import React from 'react'
import SwitcherPill from './SwitcherPill.jsx'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { agentIdOf } from '../../../api/ids.js'

/**
 * AgentSwitcher — always-visible active-beacon/agent pill in the global bar.
 *
 * Reads the agent list + active agent from the provider; selecting writes
 * through (persisted). The "Manage agents…" footer deep-links to the Agents
 * destination. Liveness dot mirrors the online/stale/offline model.
 */
function agentStatus(agent) {
  const raw = agent?.status || agent?.liveness || null
  if (!raw) return null
  const s = String(raw).toLowerCase()
  if (s.includes('online') || s.includes('active') || s.includes('healthy')) return 'healthy'
  if (s.includes('stale')) return 'warn'
  return 'bad'
}

export default function AgentSwitcher({ onManage = () => {} }) {
  const { agent, agents, setAgent } = useEnvironment()

  const options = agents.map((a) => {
    const id = agentIdOf(a)
    return {
      id,
      label: a.hostname || id,
      meta: [a.os, id].filter(Boolean).join(' · '),
      status: agentStatus(a),
    }
  })

  const label = agent ? (agent.hostname || agentIdOf(agent)) : 'no agent'

  return (
    <SwitcherPill
      kind="agent"
      label={label}
      status={agentStatus(agent)}
      empty={!agent}
      options={options}
      activeId={agentIdOf(agent)}
      onSelect={setAgent}
      manageLabel="Manage agents…"
      onManage={onManage}
    />
  )
}
