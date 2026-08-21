import React, { useState, useEffect, useCallback } from 'react'
import { getInfraBundles } from '../../api/client.js'

/**
 * TargetsView — ① Targets: the agentless "where does the simulation run?" paths.
 *
 * Two of the three execution paths the backend supports (redesign v2):
 *   • push bundle  — self-contained offline bundle, no agent required
 *   • iac labs     — environments provisioned via the infra generator
 *
 * The third — pull agents — lives in its own Agents tab (AgentsView), so beacon
 * lifecycle is a first-class surface instead of one column here. Target
 * selection is shared state either way, so picking an agent there and a
 * scenario in Library still meets in ③ Launch.
 *
 * Selecting a target lifts it to AppConsole; the Launch step (③) reads it
 * and auto-sets pull/push mode. This is the concept that makes "Launch"
 * legible — every run is "this scenario against THAT target".
 *
 * Props:
 *   selectedTarget  — { kind, id } | null
 *   onSelectTarget  — (target) => void
 *   onGoToLab       — () => void   (open the Environments/IaC generator)
 *   onGoToAgents    — () => void   (open the Agents tab)
 */

export default function TargetsView({
  selectedTarget = null,
  onSelectTarget = () => {},
  onGoToLab = () => {},
  onGoToAgents = () => {},
}) {
  const [bundles, setBundles] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    setLoading(true)
    getInfraBundles()
      .then((b) => {
        const bv = Array.isArray(b) ? b : (b && b.bundles) || []
        setBundles(bv)
      })
      .catch(() => setBundles([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const isSel = (kind, id) => selectedTarget && selectedTarget.kind === kind && selectedTarget.id === id

  return (
    <div className="targets">
      <header className="view-head">
        <div>
          <h1>Targets</h1>
          <p className="view-head__meta">
            Choose <strong>where</strong> the simulation runs. Push bundles run offline on a clean
            host; lab environments are provisioned for you. For live pull-agent beacons, see the
            Agents tab.
          </p>
        </div>
        <button type="button" className="btn" onClick={refresh}>↻ Refresh</button>
      </header>

      <div className="targets__grid">
        {/* ── Pull agents live in the Agents tab ─────────────────────── */}
        <section className="target-col">
          <div className="target-col__title">
            <span className="plane-dot plane-dot--detected" /> Pull agents
          </div>
          <div className="target-card target-card--empty">
            <div className="target-card__title">Managed in the Agents tab</div>
            <p className="target-card__sub">
              Beacon roster, deploy one-liners and retirement moved to their own view.
              Selecting an agent there arms it as the launch target, same as before.
            </p>
            <button type="button" className="btn btn--primary" onClick={onGoToAgents}>
              Open Agents ▸
            </button>
          </div>
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
    </div>
  )
}
