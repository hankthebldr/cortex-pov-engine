import React, { useState, useEffect, useCallback } from 'react'
import { getAgents, getInfraBundles } from '../../api/client.js'

/**
 * TargetsView — ① Targets: the unified "where does the simulation run?" hub.
 *
 * Surfaces all three execution paths the backend supports (redesign v2):
 *   • pull agents  — registered cortexsim-agent beacons (live / stale)
 *   • push bundle  — self-contained offline bundle, no agent required
 *   • iac labs     — environments provisioned via the infra generator
 *
 * Selecting a target lifts it to AppConsole; the Launch step (③) reads it
 * and auto-sets pull/push mode. This is the concept that makes "Launch"
 * legible — every run is "this scenario against THAT target".
 *
 * Props:
 *   selectedTarget  — { kind, id } | null
 *   onSelectTarget  — (target) => void
 *   onGoToLab       — () => void   (open the Environments/IaC generator)
 */

const AGENT_STALE_MS = 60_000 // beacon considered stale after 60s of silence

export default function TargetsView({ selectedTarget = null, onSelectTarget = () => {}, onGoToLab = () => {} }) {
  const [agents, setAgents]   = useState([])
  const [bundles, setBundles] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    setLoading(true)
    Promise.allSettled([getAgents(), getInfraBundles()])
      .then(([a, b]) => {
        setAgents(a.status === 'fulfilled' && Array.isArray(a.value) ? a.value : [])
        const bv = b.status === 'fulfilled' ? b.value : []
        setBundles(Array.isArray(bv) ? bv : (bv && bv.bundles) || [])
      })
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

  return (
    <div className="targets">
      <header className="view-head">
        <div>
          <h1>Targets</h1>
          <p className="view-head__meta">
            Choose <strong>where</strong> the simulation runs. Pull agents stream live causality into
            Cortex; push bundles run offline on a clean host; lab environments are provisioned for you.
          </p>
        </div>
        <button type="button" className="btn" onClick={refresh}>↻ Refresh</button>
      </header>

      <div className="targets__grid">
        {/* ── Pull agents ─────────────────────────────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--detected" /> Pull agents
            <span className="target-col__count">{agents.length}</span>
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
            const id = a.id || a.agent_id
            const st = agentStatus(a)
            return (
              <button
                key={id}
                type="button"
                className={'target-card target-card--agent' + (isSel('agent', id) ? ' is-selected' : '')}
                onClick={() => onSelectTarget({ kind: 'agent', id, label: id })}
              >
                <div className="target-card__head">
                  <span className={`status-dot status-dot--${st}`} />
                  <span className="target-card__title mono">{id}</span>
                  <span className={`target-card__pill target-card__pill--${st}`}>{st}</span>
                </div>
                <p className="target-card__sub">
                  {a.hostname || a.host || 'unknown host'} · {a.os || a.platform || 'linux'}
                </p>
                {isSel('agent', id) && <span className="target-card__selected">✓ selected · pull mode</span>}
              </button>
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
    </div>
  )
}
