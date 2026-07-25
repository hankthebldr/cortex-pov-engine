import React, { useState, useEffect, useCallback } from 'react'
import { getInfraBundles, deleteAgent, agentInstallUrl } from '../../api/client.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'

// Compact relative time for last-seen ("12s" / "5m" / "3h" / "2d").
function relTime(iso) {
  if (!iso) return 'never'
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms)) return '—'
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

// Normalize an agent's reported OS to an installer target.
function installerOS(os) {
  return /win/i.test(os || '') ? 'windows' : 'linux'
}

// Canonical agent id — MUST match EnvironmentContext's resolution
// (`a.id || a.agent_id`) so `setAgent(id)` resolves the same record the header
// AgentSwitcher does. Never diverge from that ordering here.
function agentIdOf(a) {
  return a.id || a.agent_id
}

/**
 * TargetsView — the Agents destination AND the guided-flow target picker.
 *
 * Two responsibilities, cleanly split:
 *
 *   1. MANAGEMENT (always) — enroll/deploy a pull beacon, prune stale agents,
 *      surface the push-bundle path and provisioned IaC lab targets. This is
 *      the "Agents" first-class destination.
 *
 *   2. ACTIVE-AGENT SELECTION (global) — the agent a card names is lifted to
 *      the EnvironmentProvider via `setAgent`, so the always-visible header
 *      AgentSwitcher and this surface share ONE active agent. The active agent
 *      is no longer a launch-only local prop; it is ambient console scope read
 *      back from `useEnvironment().agent`.
 *
 * When embedded in the guided "New POV run" flow, `onSelectTarget` is also
 * provided: selecting an agent/push/iac card lifts it as the launch target
 * (pull vs push mode is derived downstream). Selecting an agent does BOTH —
 * sets the global active agent AND lifts the guided launch target — so the two
 * concepts stay in sync instead of drifting.
 *
 * Props (all optional — the standalone Agents destination passes none):
 *   selectedTarget  — { kind, id } | null   guided-flow launch target
 *   onSelectTarget  — (target) => void       lift the guided launch target
 *   onGoToLab       — () => void             open the Environments/IaC generator
 */

const AGENT_STALE_MS = 60_000 // beacon considered stale after 60s of silence

export default function TargetsView({ selectedTarget = null, onSelectTarget = () => {}, onGoToLab = () => {} }) {
  // ── Global scope from the provider (agents + active agent are ambient) ─────
  const { agents, agent: activeAgent, setAgent, tenant, refreshAgents } = useEnvironment()
  const activeAgentId = activeAgent ? agentIdOf(activeAgent) : null

  // ── Local scope: IaC bundles are not part of the ambient env, fetched here.
  const [bundles, setBundles] = useState([])
  const [loading, setLoading] = useState(true)
  // Deploy-agent flow: pick OS → get install one-liner + downloadable installer.
  const [deployOpen, setDeployOpen] = useState(false)
  const [deployOS, setDeployOS]     = useState('linux')   // 'linux' | 'windows'
  const [deployId, setDeployId]     = useState('jumpbox-01')
  const [copied, setCopied]         = useState(false)
  const [pendingDelete, setPendingDelete] = useState(null) // agent_id awaiting confirm
  const [busyDelete, setBusyDelete] = useState(null)       // agent_id mid-delete

  const removeAgent = useCallback(async (agentId) => {
    setBusyDelete(agentId)
    try {
      await deleteAgent(agentId)
      // The provider owns the agent list; re-pull so the header switcher and
      // this surface both reflect the removal (its stale-pointer guard clears
      // the active pointer + falls back if the deleted agent was active).
      await refreshAgents()
    } catch { /* surfaced via list refresh */ }
    finally { setBusyDelete(null); setPendingDelete(null) }
  }, [refreshAgents])

  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const installUrl = `${origin}/api/agents/install?os=${deployOS}&id=${encodeURIComponent(deployId || 'jumpbox-01')}`
  const oneLiner = deployOS === 'windows'
    ? `iwr -useb "${installUrl}" | iex`
    : `curl -fsSL "${installUrl}" | bash`
  const copyOneLiner = useCallback(() => {
    try {
      navigator.clipboard.writeText(oneLiner)
      setCopied(true); setTimeout(() => setCopied(false), 1800)
    } catch { /* clipboard blocked — user can select manually */ }
  }, [oneLiner])

  // ── Bundles (local) + agent liveness refresh (provider) ────────────────────
  const refreshBundles = useCallback(() => {
    setLoading(true)
    getInfraBundles()
      .then((bv) => setBundles(Array.isArray(bv) ? bv : (bv && bv.bundles) || []))
      .catch(() => setBundles([]))
      .finally(() => setLoading(false))
  }, [])

  const refresh = useCallback(() => {
    refreshBundles()
    refreshAgents()
  }, [refreshBundles, refreshAgents])

  useEffect(() => { refreshBundles() }, [refreshBundles])
  useEffect(() => {
    // Keep beacon liveness + bundle list fresh. Agents flow through the
    // provider so a refresh here re-scopes the header switcher too.
    const t = setInterval(refresh, 10_000)
    return () => clearInterval(t)
  }, [refresh])

  const isSel = (kind, id) => selectedTarget && selectedTarget.kind === kind && selectedTarget.id === id

  const agentStatus = (a) => {
    const seen = a.last_seen || a.last_seen_at || a.updated_at
    if (!seen) return 'unknown'
    const age = Date.now() - new Date(seen).getTime()
    return age < AGENT_STALE_MS ? 'live' : 'stale'
  }

  // Selecting an agent lifts it to global scope (setAgent) AND, in the guided
  // flow, arms it as the launch target. One click, both concepts in sync.
  const chooseAgent = useCallback((a) => {
    const id = agentIdOf(a)
    setAgent(id)
    onSelectTarget({ kind: 'agent', id, label: a.hostname || a.host || id })
  }, [setAgent, onSelectTarget])

  return (
    <div className="targets">
      <header className="view-head">
        <div>
          <h1>Agents &amp; targets</h1>
          <p className="view-head__meta">
            Manage where the simulation runs and pick the <strong>active agent</strong>. The active
            agent is shared with the header switcher — switch here or there, every surface follows.
            {tenant && (
              <> Scoped to tenant <strong className="mono">{tenant.name || tenant.id}</strong>.</>
            )}
          </p>
        </div>
        <div className="targets__head-actions">
          <span className="targets__active" data-testid="active-agent-summary">
            active agent:{' '}
            {activeAgentId
              ? <strong className="mono">{activeAgentId}</strong>
              : <em className="targets__active--none">none</em>}
          </span>
          <button type="button" className="btn" onClick={refresh}>↻ Refresh</button>
        </div>
      </header>

      <div className="targets__grid">
        {/* ── Pull agents ─────────────────────────────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--detected" /> Pull agents
            <span className="target-col__count">{agents.length}</span>
            <button type="button" className="btn btn--xs target-col__action" onClick={() => setDeployOpen(true)}>
              + Deploy agent
            </button>
          </div>
          {loading && agents.length === 0 && <div className="target-card target-card--ghost">polling beacons…</div>}
          {!loading && agents.length === 0 && (
            <div className="target-card target-card--empty">
              <div className="target-card__title">No agents registered</div>
              <p className="target-card__sub">
                Run a <code>cortexsim-agent</code> beacon against your jumpbox, or provision a lab below.
              </p>
              <code className="target-card__cmd">cortexsim-agent --server &lt;url&gt; --id jumpbox-01</code>
            </div>
          )}
          {agents.map((a) => {
            const id = agentIdOf(a)
            const st = agentStatus(a)
            const os = a.os || a.platform || 'linux'
            const confirming = pendingDelete === id
            const isActive = id === activeAgentId
            return (
              <div
                key={id}
                className={
                  'target-card target-card--agent'
                  + (isSel('agent', id) ? ' is-selected' : '')
                  + (isActive ? ' is-active' : '')
                }
              >
                <button
                  type="button"
                  className="target-card__select"
                  aria-pressed={isActive}
                  onClick={() => chooseAgent(a)}
                >
                  <div className="target-card__head">
                    <span className={`status-dot status-dot--${st}`} />
                    <span className="target-card__title mono">{id}</span>
                    {isActive && <span className="target-card__pill target-card__pill--active">active</span>}
                    <span className={`target-card__pill target-card__pill--${st}`}>{st}</span>
                  </div>
                  <p className="target-card__sub">
                    {a.hostname || a.host || 'unknown host'} · {os} · seen {relTime(a.last_seen || a.last_seen_at || a.updated_at)}
                  </p>
                  {isSel('agent', id) && <span className="target-card__selected">✓ selected · pull mode</span>}
                  {!isSel('agent', id) && isActive && (
                    <span className="target-card__selected">✓ active agent</span>
                  )}
                </button>

                {confirming ? (
                  <div className="target-card__confirm">
                    <span>Delete <strong className="mono">{id}</strong>?</span>
                    <button type="button" className="btn btn--xs" onClick={() => setPendingDelete(null)}>Cancel</button>
                    <button
                      type="button"
                      className="btn btn--xs btn--danger"
                      disabled={busyDelete === id}
                      onClick={() => removeAgent(id)}
                    >{busyDelete === id ? '…' : 'Delete'}</button>
                  </div>
                ) : (
                  <div className="target-card__actions">
                    {!isActive && (
                      <button
                        type="button"
                        className="card-action"
                        onClick={() => chooseAgent(a)}
                        title="Make this the active agent"
                      >◉ set active</button>
                    )}
                    <a
                      className="card-action"
                      href={agentInstallUrl({ os: installerOS(os), id })}
                      download
                      title="Re-download this agent's installer"
                    >↓ installer</a>
                    <button
                      type="button"
                      className="card-action card-action--danger"
                      onClick={() => setPendingDelete(id)}
                      title="Delete this agent"
                    >✕ delete</button>
                  </div>
                )}
              </div>
            )
          })}
        </section>

        {/* ── Push bundle ─────────────────────────────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--stitched" /> Push bundle
          </div>
          <button
            type="button"
            className={'target-card target-card--push' + (isSel('push', 'bundle') ? ' is-selected' : '')}
            onClick={() => onSelectTarget({ kind: 'push', id: 'bundle', label: 'Offline push bundle' })}
          >
            <div className="target-card__head">
              <span className="status-dot status-dot--ready" />
              <span className="target-card__title">Offline bundle</span>
              <span className="target-card__pill target-card__pill--ready">always ready</span>
            </div>
            <p className="target-card__sub">
              Generate a self-contained script (bash / k8s) the DC runs on any clean Ubuntu 22.04 host.
              No agent, no inbound connection.
            </p>
            {isSel('push', 'bundle') && <span className="target-card__selected">✓ selected · push mode</span>}
          </button>
        </section>

        {/* ── IaC labs ───────────────────────────────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--pending" /> Lab environments
            <span className="target-col__count">{bundles.length}</span>
          </div>
          {bundles.length === 0 && (
            <div className="target-card target-card--empty">
              <div className="target-card__title">No environments provisioned</div>
              <p className="target-card__sub">
                Generate a Terraform bundle (EDR / CDR / NDR / identity labs) the customer can apply.
              </p>
              <button type="button" className="btn btn--primary" onClick={onGoToLab}>
                Provision environment ▸
              </button>
            </div>
          )}
          {bundles.map((b) => {
            const id = b.bundle_id || b.id
            return (
              <button
                key={id}
                type="button"
                className={'target-card target-card--iac' + (isSel('iac', id) ? ' is-selected' : '')}
                onClick={() => onSelectTarget({ kind: 'iac', id, label: id })}
              >
                <div className="target-card__head">
                  <span className="status-dot status-dot--ready" />
                  <span className="target-card__title mono">{id}</span>
                </div>
                <p className="target-card__sub">
                  {(b.modules || b.selected_modules || []).join(', ') || b.provider || 'aws'} ·{' '}
                  {b.created_at ? new Date(b.created_at).toLocaleDateString() : 'bundle'}
                </p>
              </button>
            )
          })}
          {bundles.length > 0 && (
            <button type="button" className="btn" onClick={onGoToLab}>+ New environment</button>
          )}
        </section>
      </div>

      {deployOpen && (
        <div className="deploy-backdrop" onMouseDown={() => setDeployOpen(false)}>
          <div className="deploy-modal" onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label="Deploy agent">
            <div className="deploy-modal__head">
              <h2>Deploy a pull agent</h2>
              <button type="button" className="deploy-modal__close" onClick={() => setDeployOpen(false)} aria-label="Close">×</button>
            </div>
            <p className="deploy-modal__lede">
              Run the beacon on your target host. It registers with this SimCore and polls for tasks.
              Requires Go 1.21+ on the target (stdlib-only build — no other dependencies).
            </p>

            <div className="deploy-field">
              <span className="launch-field__label">Target OS</span>
              <div className="deploy-os-toggle">
                {['linux', 'windows'].map((o) => (
                  <button
                    key={o}
                    type="button"
                    className={'deploy-os' + (deployOS === o ? ' is-active' : '')}
                    onClick={() => setDeployOS(o)}
                  >{o === 'linux' ? '🐧 Linux' : '🪟 Windows'}</button>
                ))}
              </div>
            </div>

            <label className="deploy-field">
              <span className="launch-field__label">Agent ID</span>
              <input
                className="launch-select deploy-input"
                value={deployId}
                onChange={(e) => setDeployId(e.target.value)}
                placeholder="jumpbox-01"
                spellCheck={false}
              />
            </label>

            <div className="deploy-field">
              <span className="launch-field__label">One-line install ({deployOS === 'windows' ? 'PowerShell' : 'bash'})</span>
              <div className="deploy-snippet">
                <code>{oneLiner}</code>
                <button type="button" className="btn btn--xs" onClick={copyOneLiner}>{copied ? '✓ copied' : 'Copy'}</button>
              </div>
            </div>

            <div className="deploy-actions">
              <a className="btn btn--primary btn--lg" href={installUrl} download>
                ↓ Download installer ({deployOS === 'windows' ? '.ps1' : '.sh'})
              </a>
              <span className="deploy-hint mono">
                or, once built: <code>cortexsim-agent --server {origin} --id {deployId || 'jumpbox-01'} --interval 10</code>
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
