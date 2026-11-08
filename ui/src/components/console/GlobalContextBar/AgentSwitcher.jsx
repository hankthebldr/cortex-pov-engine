import React from 'react'
import SwitcherPill from './SwitcherPill.jsx'
import { useEnvironment } from '../../../context/EnvironmentContext.jsx'
import { agentIdOf } from '../../../api/ids.js'
import { agentLiveness, LIVENESS } from '../setupProgress.js'

/**
 * AgentSwitcher — always-visible active-beacon/agent pill in the global bar.
 *
 * Reads the agent list + active agent from the provider; selecting writes
 * through (persisted). The "Manage agents…" footer deep-links to the Agents
 * destination. The dot comes from the same liveness model as the Agents cards
 * and Get started (setupProgress.agentLiveness), so a row the installer only
 * enrolled is a warning here too — never a healthy dot before a beacon polled.
 */
const DOT = {
  [LIVENESS.ONLINE]: 'healthy',
  [LIVENESS.STALE]: 'warn',
  [LIVENESS.ENROLLED]: 'warn',
  [LIVENESS.OFFLINE]: 'bad',
  [LIVENESS.UNKNOWN]: 'bad',
}

function agentStatus(agent) {
  if (!agent) return null
  return DOT[agentLiveness(agent).state] || 'bad'
}

export default function AgentSwitcher({ onManage = () => {} }) {
  const { agent, agents, setAgent } = useEnvironment()

  const options = agents.map((a) => {
    const id = agentIdOf(a)
    const live = agentLiveness(a)
    return {
      id,
      label: a.hostname || id,
      meta: [a.os, id, live.state === LIVENESS.ONLINE ? null : live.label].filter(Boolean).join(' · '),
      status: DOT[live.state] || 'bad',
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
