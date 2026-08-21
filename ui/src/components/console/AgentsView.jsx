import React, { useState, useEffect, useCallback } from 'react'
import { getAgents, deleteAgent, agentInstallUrl } from '../../api/client.js'

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

/**
 * AgentsView — the beacon fleet: deploy, monitor, retire.
 *
 * Split out of TargetsView so agent lifecycle is a first-class surface rather
 * than one column of the "where does this run?" hub. Targets still owns the
 * push-bundle and IaC-lab paths; this view owns everything agent-shaped:
 *   • roster    — registered cortexsim-agent beacons with live / stale liveness
 *   • deploy    — OS picker → one-line install + downloadable installer
 *   • retire    — delete a beacon (confirm-gated), re-download its installer
 *
 * Selecting an agent still lifts it to AppConsole as the launch target
 * (kind 'agent'), exactly as it did from Targets — the Launch step reads that
 * and auto-sets pull mode. Selection is shared state, so it survives the tab
 * switch.
 *
 * Props:
 *   selectedTarget  — { kind, id } | null
 *   onSelectTarget  — (target) => void
 *   onGoToTargets   — () => void   (back to push bundle / lab environments)
 */

const AGENT_STALE_MS = 60_000 // beacon considered stale after 60s of silence

export default function AgentsView({
  selectedTarget = null,
  onSelectTarget = () => {},
  onGoToTargets = () => {},
}) {
  const [agents, setAgents]   = useState([])
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
      setAgents((prev) => prev.filter((a) => (a.agent_id || a.id) !== agentId))
    } catch { /* surfaced via list refresh */ }
    finally { setBusyDelete(null); setPendingDelete(null) }
  }, [])

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

  const refresh = useCallback(() => {
    setLoading(true)
    getAgents()
      .then((a) => setAgents(Array.isArray(a) ? a : []))
      .catch(() => setAgents([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => {
    const t = setInterval(refresh, 10_000) // keep beacon liveness fresh
    return () => clearInterval(t)
  }, [refresh])

  const isSel = (kind, id) => selectedTarget && selectedTarget.kind === kind && selectedTarget.id === id

  const agentStatus = (a) => {
    const seen = a.last_seen || a.last_seen_at || a.updated_at
    if (!seen) return 'unknown'
    const age = Date.now() - new Date(seen).getTime()
    return age < AGENT_STALE_MS ? 'live' : 'stale'
  }

  const liveCount = agents.filter((a) => agentStatus(a) === 'live').length

  return (
    <div className="targets agents-view">
      <header className="view-head">
        <div>
          <h1>Agents</h1>
          <p className="view-head__meta">
            The beacon fleet. Each <code>cortexsim-agent</code> polls this SimCore for tasks and
            executes them through the identity harness, streaming live process causality into Cortex.
            Selecting an agent arms it as the launch target (pull mode).
          </p>
        </div>
        <button type="button" className="btn" onClick={refresh}>↻ Refresh</button>
      </header>

      <div className="targets__grid">
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--detected" /> Registered beacons
            <span className="target-col__count">{liveCount}/{agents.length} live</span>
            <button type="button" className="btn btn--xs target-col__action" onClick={() => setDeployOpen(true)}>
              + Deploy agent
            </button>
          </div>
          {loading && agents.length === 0 && <div className="target-card target-card--ghost">polling beacons…</div>}
          {!loading && agents.length === 0 && (
            <div className="target-card target-card--empty">
              <div className="target-card__title">No agents registered</div>
              <p className="target-card__sub">
                Deploy a beacon to a target host, or use an offline push bundle instead —
                push mode needs no agent at all.
              </p>
              <code className="target-card__cmd">cortexsim-agent --server &lt;url&gt; --id jumpbox-01</code>
              <button type="button" className="btn" onClick={onGoToTargets}>
                Push bundle &amp; labs ▸
              </button>
            </div>
          )}
          {agents.map((a) => {
            const id = a.agent_id || a.id
            const st = agentStatus(a)
            const os = a.os || a.platform || 'linux'
            const confirming = pendingDelete === id
            return (
              <div
                key={id}
                className={'target-card target-card--agent' + (isSel('agent', id) ? ' is-selected' : '')}
              >
                <button
                  type="button"
                  className="target-card__select"
                  onClick={() => onSelectTarget({ kind: 'agent', id, label: id })}
                >
                  <div className="target-card__head">
                    <span className={`status-dot status-dot--${st}`} />
                    <span className="target-card__title mono">{id}</span>
                    <span className={`target-card__pill target-card__pill--${st}`}>{st}</span>
                  </div>
                  <p className="target-card__sub">
                    {a.hostname || a.host || 'unknown host'} · {os} · seen {relTime(a.last_seen || a.last_seen_at || a.updated_at)}
                  </p>
                  {isSel('agent', id) && <span className="target-card__selected">✓ selected · pull mode</span>}
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
