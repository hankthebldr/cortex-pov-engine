import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  getInfraBundles,
  deleteAgent,
  agentInstallUrl,
  mintEnrollmentToken,
} from '../../api/client.js'
import { agentIdOf } from '../../api/ids.js'
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
  // Deploy-agent flow: mint an enrollment token → copy ONE line → SimCore
  // assigns the id when the jumpbox redeems it. The legacy self-asserted-id
  // installer is kept behind a disclosure for hosts that cannot reach back.
  const [deployOpen, setDeployOpen] = useState(false)
  const [deployOS, setDeployOS]     = useState('linux')   // 'linux' | 'windows'
  const [deployId, setDeployId]     = useState('jumpbox-01')
  const [tokenLabel, setTokenLabel] = useState('')
  const [tokenTtl, setTokenTtl]     = useState(3600)      // seconds
  const [tokenUses, setTokenUses]   = useState(1)
  const [token, setToken]           = useState(null)      // minted token (shown once)
  const [minting, setMinting]       = useState(false)
  const [mintError, setMintError]   = useState(null)
  const [showLegacy, setShowLegacy] = useState(false)
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
  // The one-liner is only as good as the address it carries. A DC on the
  // control plane usually has the console open at localhost:8888 (dev-up.sh),
  // and `localhost` pasted on a jumpbox resolves to the JUMPBOX — the beacon
  // installs, calls itself, and the roster stays empty with nothing to read.
  // The install endpoint refuses to bake a derived loopback, but say so here
  // too: catching it before the copy beats catching it after the paste.
  const originIsLoopback = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:|$)/i.test(origin)

  // Token-bearing installer (preferred) vs legacy explicit-id installer.
  // The token rides in an env var rather than the URL so it stays out of the
  // jumpbox's shell history and any proxy access log the customer keeps.
  const installUrl = `${origin}${agentInstallUrl({ os: deployOS })}`
  const legacyInstallUrl = `${origin}${agentInstallUrl({ os: deployOS, id: deployId || 'jumpbox-01' })}`
  const oneLiner = token
    ? (deployOS === 'windows'
        ? `$env:CORTEXSIM_TOKEN='${token.token}'; iwr -useb "${installUrl}" | iex`
        : `curl -fsSL "${installUrl}" | CORTEXSIM_TOKEN='${token.token}' bash`)
    : null
  const legacyOneLiner = deployOS === 'windows'
    ? `iwr -useb "${legacyInstallUrl}" | iex`
    : `curl -fsSL "${legacyInstallUrl}" | bash`

  const copyText = useCallback((text) => {
    if (!text) return
    try {
      navigator.clipboard.writeText(text)
      setCopied(true); setTimeout(() => setCopied(false), 1800)
    } catch { /* clipboard blocked — user can select manually */ }
  }, [])

  const mint = useCallback(async () => {
    setMinting(true); setMintError(null)
    try {
      const t = await mintEnrollmentToken({
        label: tokenLabel.trim() || null,
        ttl_seconds: Number(tokenTtl),
        max_uses: Number(tokenUses),
      })
      setToken(t)
    } catch (err) {
      setMintError(err.message || 'Could not mint an enrollment token')
    } finally {
      setMinting(false)
    }
  }, [tokenLabel, tokenTtl, tokenUses])

  const closeDeploy = useCallback(() => {
    setDeployOpen(false)
    // The full token value is unrecoverable once dismissed — drop it from state
    // rather than leaving a live credential sitting in a closed dialog.
    setToken(null); setMintError(null); setShowLegacy(false)
  }, [])

  // ── Dialog focus + Escape ────────────────────────────────────────────────
  // A keydown handler on the dialog element only fires once focus is already
  // inside it, and opening the modal leaves focus on the opener behind the
  // backdrop — so Escape did nothing for the operator who just clicked Deploy.
  // Same contract as ConfirmDialog: move focus in, listen on the document,
  // hand focus back on close.
  const deployRef = useRef(null)
  const deployPrevFocusRef = useRef(null)
  useEffect(() => {
    if (!deployOpen) return undefined
    deployPrevFocusRef.current = document.activeElement
    const t = setTimeout(() => {
      const el = deployRef.current
      if (!el) return
      const first = el.querySelector('input, select, button:not(.deploy-modal__close)')
      ;(first || el.querySelector('.deploy-modal__close'))?.focus()
    }, 20)
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); closeDeploy() } }
    document.addEventListener('keydown', onKey)
    return () => {
      clearTimeout(t)
      document.removeEventListener('keydown', onKey)
      const prev = deployPrevFocusRef.current
      if (prev && prev.focus) prev.focus()
    }
  }, [deployOpen, closeDeploy])

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
                Pull mode needs one beacon on the target host. Mint an enrollment token and
                run the one-liner it gives you — SimCore assigns the agent id. No agent?
                Use the offline push bundle instead, or provision a lab below.
              </p>
              <button type="button" className="btn btn--primary" onClick={() => setDeployOpen(true)}>
                + Deploy agent ▸
              </button>
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
          {loading && bundles.length === 0 && (
            <div className="target-card target-card--ghost">loading environments…</div>
          )}
          {!loading && bundles.length === 0 && (
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
        <div className="deploy-backdrop" onMouseDown={closeDeploy}>
          <div
            className="deploy-modal"
            ref={deployRef}
            onMouseDown={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="deploy-modal-title"
          >
            <div className="deploy-modal__head">
              <h2 id="deploy-modal-title">Deploy a pull agent</h2>
              <button type="button" className="deploy-modal__close" onClick={closeDeploy} aria-label="Close">×</button>
            </div>
            <p className="deploy-modal__lede">
              Mint an enrollment token, run one line on the jumpbox, and SimCore assigns the
              agent id when the token is redeemed — nobody has to invent one. Tokens expire
              and can be revoked. The target needs <strong>no toolchain</strong> — SimCore
              serves a prebuilt, checksummed beacon — only <code>curl</code> and outbound
              reach to this SimCore.
            </p>
            {originIsLoopback && (
              <p className="deploy-warn" role="status" data-testid="deploy-loopback-warn">
                ⚠ This console is open at <code className="mono">{origin}</code>, so the
                one-liner below carries a loopback address. On the jumpbox that resolves to
                the <strong>jumpbox itself</strong> — the beacon would install and call
                nothing. Reopen the console on the control plane's reachable address (e.g.
                <code className="mono">http://&lt;control-plane-ip&gt;:8888</code>) before
                copying. The install endpoint refuses this too
                (<code className="mono">INSTALLER_SERVER_LOOPBACK</code>).
              </p>
            )}
            {deployOS === 'windows' && (
              <p className="deploy-warn" role="status">
                ⚠ No Windows beacon ships yet — this script refuses before it enrolls
                (<code className="mono">WINDOWS_AGENT_UNAVAILABLE</code>) rather than
                registering a host it cannot drive. Use the offline <strong>push bundle</strong>
                for Windows targets.
              </p>
            )}

            <div className="deploy-field">
              <span className="launch-field__label">Target OS</span>
              <div className="deploy-os-toggle">
                {/* macOS is a real target: /api/agents/install accepts it and
                    the darwin beacons ship in the dist matrix. One POSIX script
                    covers Linux and macOS — it branches on `uname -s`. */}
                {[['linux', '🐧 Linux'], ['macos', '🍎 macOS'], ['windows', '🪟 Windows']].map(([o, label]) => (
                  <button
                    key={o}
                    type="button"
                    className={'deploy-os' + (deployOS === o ? ' is-active' : '')}
                    aria-pressed={deployOS === o}
                    onClick={() => setDeployOS(o)}
                  >{label}</button>
                ))}
              </div>
            </div>

            {!token ? (
              <>
                <div className="deploy-token-form">
                  <label className="deploy-field">
                    <span className="launch-field__label">Label (optional)</span>
                    <input
                      className="launch-select deploy-input"
                      value={tokenLabel}
                      onChange={(e) => setTokenLabel(e.target.value)}
                      placeholder="acme-pov jumpbox"
                      maxLength={80}
                      spellCheck={false}
                    />
                  </label>
                  <label className="deploy-field">
                    <span className="launch-field__label">Valid for</span>
                    <select
                      className="launch-select"
                      value={tokenTtl}
                      onChange={(e) => setTokenTtl(Number(e.target.value))}
                    >
                      <option value={900}>15 minutes</option>
                      <option value={3600}>1 hour</option>
                      <option value={86400}>1 day</option>
                      <option value={604800}>7 days</option>
                    </select>
                  </label>
                  <label className="deploy-field">
                    <span className="launch-field__label">Max uses</span>
                    <select
                      className="launch-select"
                      value={tokenUses}
                      onChange={(e) => setTokenUses(Number(e.target.value))}
                    >
                      <option value={1}>1 host</option>
                      <option value={5}>5 hosts</option>
                      <option value={25}>25 hosts</option>
                    </select>
                  </label>
                </div>

                {mintError && (
                  <p className="deploy-error" role="alert">{mintError}</p>
                )}

                <div className="deploy-actions">
                  <button
                    type="button"
                    className="btn btn--primary btn--lg"
                    onClick={mint}
                    disabled={minting}
                  >
                    {minting ? 'Minting…' : 'Mint enrollment token ▸'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="deploy-field">
                  <span className="launch-field__label">
                    Enrollment token — shown once, copy it now
                  </span>
                  <div className="deploy-snippet">
                    <code data-testid="deploy-token">{token.token}</code>
                    <button type="button" className="btn btn--xs" onClick={() => copyText(token.token)}>
                      Copy
                    </button>
                  </div>
                  <span className="deploy-hint mono">
                    {token.remaining_uses ?? token.max_uses} use(s)
                    {token.expires_at && ` · expires ${new Date(token.expires_at).toLocaleString()}`}
                  </span>
                </div>

                <div className="deploy-field">
                  <span className="launch-field__label">
                    Run this on the target ({deployOS === 'windows' ? 'PowerShell' : 'bash'})
                  </span>
                  <div className="deploy-snippet">
                    <code data-testid="deploy-one-liner">{oneLiner}</code>
                    <button type="button" className="btn btn--xs" onClick={() => copyText(oneLiner)}>
                      {copied ? '✓ copied' : 'Copy'}
                    </button>
                  </div>
                </div>

                <div className="deploy-actions">
                  <a className="btn btn--primary btn--lg" href={installUrl} download>
                    ↓ Download installer ({deployOS === 'windows' ? '.ps1' : '.sh'})
                  </a>
                  <button type="button" className="btn" onClick={() => { setToken(null); setCopied(false) }}>
                    Mint another
                  </button>
                </div>
                <p className="deploy-hint">
                  The beacon appears in this list within one poll interval (~10s) once the
                  token is redeemed.
                </p>
              </>
            )}

            {/* Legacy path — kept because a host that cannot redeem a token
                (no outbound POST, pre-seeded image) still needs an installer. */}
            <div className="deploy-legacy">
              <button
                type="button"
                className="linklike deploy-legacy__toggle"
                aria-expanded={showLegacy}
                onClick={() => setShowLegacy((v) => !v)}
              >
                {showLegacy ? '▾' : '▸'} Legacy: self-asserted agent id
              </button>
              {showLegacy && (
                <div className="deploy-legacy__body">
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
                  <div className="deploy-snippet">
                    <code>{legacyOneLiner}</code>
                    <button type="button" className="btn btn--xs" onClick={() => copyText(legacyOneLiner)}>
                      Copy
                    </button>
                  </div>
                  <span className="deploy-hint mono">
                    or, once built: <code>cortexsim-agent --server {origin} --id {deployId || 'jumpbox-01'} --interval 10</code>
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
