import React, { useState, useEffect, useCallback } from 'react'
import EalCampaignBuilder from './EalCampaignBuilder.jsx'
import EalRunProgress from './EalRunProgress.jsx'
import {
  getEalCampaigns,
  getEalRuns,
  launchEalCampaign,
} from '../api/client.js'

/**
 * EalConsole — orchestrator for the EAL Traffic Simulator UI.
 *
 * Tabs:
 *   Campaigns   list persisted campaigns + launch
 *   New         declarative campaign builder (plugin picker + dynamic form)
 *   Runs        executed run history with progress drill-in
 *
 * The console is mounted as one of the App's mutually-exclusive views
 * (alongside MITRE / Deploy / Runs / Validate).
 */
export default function EalConsole({ onMessage, onClose }) {
  const [tab, setTab] = useState('campaigns')   // 'campaigns' | 'new' | 'runs'

  // ── Lists ──────────────────────────────────────────────────────────────
  const [campaigns, setCampaigns] = useState([])
  const [runs, setRuns] = useState([])
  const [loadingCampaigns, setLoadingCampaigns] = useState(false)
  const [loadingRuns, setLoadingRuns] = useState(false)

  // ── Drill-in state ─────────────────────────────────────────────────────
  const [openRunId, setOpenRunId] = useState(null)

  // ── Refresh helpers ────────────────────────────────────────────────────
  const refreshCampaigns = useCallback(() => {
    setLoadingCampaigns(true)
    getEalCampaigns()
      .then(data => setCampaigns(Array.isArray(data?.campaigns) ? data.campaigns : []))
      .catch(err => onMessage?.(`Failed to load campaigns: ${err.message}`, 'error'))
      .finally(() => setLoadingCampaigns(false))
  }, [onMessage])

  const refreshRuns = useCallback(() => {
    setLoadingRuns(true)
    getEalRuns()
      .then(data => setRuns(Array.isArray(data?.runs) ? data.runs : []))
      .catch(err => onMessage?.(`Failed to load runs: ${err.message}`, 'error'))
      .finally(() => setLoadingRuns(false))
  }, [onMessage])

  useEffect(() => {
    refreshCampaigns()
    refreshRuns()
  }, [refreshCampaigns, refreshRuns])

  // ── Launch handler ─────────────────────────────────────────────────────
  const handleLaunch = useCallback(async (campaign, opts) => {
    try {
      const resp = await launchEalCampaign(campaign.campaign_id, opts)
      onMessage?.(`Campaign ${campaign.campaign_id} launched (run ${resp.run_id})`, 'success')
      // Auto-switch to the runs tab and open the new run.
      setTab('runs')
      setOpenRunId(resp.run_id)
      refreshRuns()
      return resp
    } catch (err) {
      onMessage?.(`Launch failed: ${err.message}`, 'error')
      throw err
    }
  }, [onMessage, refreshRuns])

  // ── Persist-and-launch (from the builder tab) ──────────────────────────
  const handleCampaignCreated = useCallback((campaign) => {
    onMessage?.(`Campaign ${campaign.campaign_id} saved`, 'success')
    setTab('campaigns')
    refreshCampaigns()
  }, [onMessage, refreshCampaigns])

  return (
    <section className="eal-console">
      <header className="eal-console__head">
        <h2 style={{ margin: 0, fontSize: '18px' }}>
          <span style={{ color: 'var(--cortex-teal)' }}>EAL</span> Traffic Simulator
        </h2>
        <nav className="eal-console__tabs">
          <button
            className={`btn btn-sm ${tab === 'campaigns' ? 'btn-navy' : 'btn-secondary'}`}
            onClick={() => setTab('campaigns')}
          >
            Campaigns {campaigns.length > 0 && <span className="badge">{campaigns.length}</span>}
          </button>
          <button
            className={`btn btn-sm ${tab === 'new' ? 'btn-navy' : 'btn-secondary'}`}
            onClick={() => setTab('new')}
          >
            + New Campaign
          </button>
          <button
            className={`btn btn-sm ${tab === 'runs' ? 'btn-navy' : 'btn-secondary'}`}
            onClick={() => setTab('runs')}
          >
            Runs {runs.length > 0 && <span className="badge">{runs.length}</span>}
          </button>
        </nav>
        {onClose && (
          <button
            className="btn btn-sm btn-secondary"
            onClick={onClose}
            style={{ marginLeft: 'auto' }}
          >
            Close
          </button>
        )}
      </header>

      <div className="eal-console__body">
        {tab === 'campaigns' && (
          <EalCampaignsList
            campaigns={campaigns}
            loading={loadingCampaigns}
            onLaunch={handleLaunch}
            onRefresh={refreshCampaigns}
          />
        )}
        {tab === 'new' && (
          <EalCampaignBuilder
            onCreated={handleCampaignCreated}
            onError={(msg) => onMessage?.(msg, 'error')}
          />
        )}
        {tab === 'runs' && (
          <EalRunsList
            runs={runs}
            loading={loadingRuns}
            openRunId={openRunId}
            onOpenRun={setOpenRunId}
            onRefresh={refreshRuns}
            onMessage={onMessage}
          />
        )}
      </div>
    </section>
  )
}

// ─── Campaigns tab ───────────────────────────────────────────────────────────

function EalCampaignsList({ campaigns, loading, onLaunch, onRefresh }) {
  const [busyId, setBusyId] = useState(null)
  const [confirmLive, setConfirmLive] = useState(null) // campaign object or null

  if (loading) return <p className="muted">Loading campaigns…</p>
  if (campaigns.length === 0) {
    return (
      <div className="empty-state">
        <p>No campaigns persisted yet.</p>
        <p className="muted small">
          Use <strong>+ New Campaign</strong> above, or POST a YAML / JSON spec to
          <code> /api/eal/campaigns</code>.
        </p>
      </div>
    )
  }

  const doLaunch = async (c, dryRun) => {
    setBusyId(c.campaign_id)
    try {
      await onLaunch(c, { dry_run: dryRun, operator: 'cortexsim-ui' })
    } catch { /* parent already toasted */ }
    finally { setBusyId(null); setConfirmLive(null) }
  }

  return (
    <div className="eal-campaigns">
      <div className="flex-row" style={{ justifyContent: 'space-between', marginBottom: '8px' }}>
        <p className="muted small" style={{ margin: 0 }}>
          {campaigns.length} campaign(s)
        </p>
        <button className="btn btn-sm btn-secondary" onClick={onRefresh}>Refresh</button>
      </div>
      <table className="cs-table">
        <thead>
          <tr>
            <th>Campaign ID</th>
            <th>Name</th>
            <th>Steps</th>
            <th>Authorized</th>
            <th>Allowlist</th>
            <th>Created</th>
            <th style={{ width: '180px' }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map(c => {
            const steps = c.spec?.steps?.length ?? '–'
            return (
              <tr key={c.campaign_id}>
                <td><code className="mono small">{c.campaign_id}</code></td>
                <td>{c.name}</td>
                <td>{steps}</td>
                <td>
                  {c.simulation_authorized ? (
                    <span className="pill pill-success">✓ {c.authorized_by}</span>
                  ) : (
                    <span className="pill pill-warn">dry-run only</span>
                  )}
                </td>
                <td>
                  {(c.target_allowlist || []).length === 0 ? (
                    <span className="muted small">–</span>
                  ) : (
                    <span className="mono small" title={c.target_allowlist.join(', ')}>
                      {c.target_allowlist.slice(0, 2).join(', ')}
                      {c.target_allowlist.length > 2 && ` +${c.target_allowlist.length - 2}`}
                    </span>
                  )}
                </td>
                <td className="muted small">{c.created_at?.slice(0, 10) || '–'}</td>
                <td>
                  <button
                    className="btn btn-sm btn-secondary"
                    disabled={busyId === c.campaign_id}
                    onClick={() => doLaunch(c, true)}
                    title="Run without emitting real traffic"
                  >
                    Dry-run
                  </button>
                  <button
                    className="btn btn-sm btn-navy"
                    disabled={busyId === c.campaign_id || !c.simulation_authorized}
                    onClick={() => setConfirmLive(c)}
                    style={{ marginLeft: '6px' }}
                    title={c.simulation_authorized
                      ? 'Run live against the campaign target_allowlist'
                      : 'Live execution requires simulation_authorized=true'}
                  >
                    Run live
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {confirmLive && (
        <div className="modal-backdrop" onClick={() => setConfirmLive(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Confirm live campaign launch</h3>
            <p>
              <strong>{confirmLive.campaign_id}</strong> — {confirmLive.name}
            </p>
            <p>
              Authorized by:{' '}
              <code>{confirmLive.authorized_by || '<unset>'}</code>
            </p>
            <p>
              Targets:{' '}
              <code className="mono small">
                {(confirmLive.target_allowlist || []).join(', ') || '<empty>'}
              </code>
            </p>
            <p className="muted small">
              Real network traffic will be emitted to the hosts above.
              Every request carries an <code>X-Simulation-Run-ID</code>{' '}
              header for SOC filtering.
            </p>
            <div className="flex-row" style={{ gap: '8px', justifyContent: 'flex-end' }}>
              <button className="btn btn-sm btn-secondary" onClick={() => setConfirmLive(null)}>
                Cancel
              </button>
              <button
                className="btn btn-sm btn-navy"
                disabled={busyId === confirmLive.campaign_id}
                onClick={() => doLaunch(confirmLive, false)}
              >
                {busyId === confirmLive.campaign_id ? 'Launching…' : 'Launch live'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Runs tab ────────────────────────────────────────────────────────────────

function EalRunsList({ runs, loading, openRunId, onOpenRun, onRefresh, onMessage }) {
  if (loading) return <p className="muted">Loading runs…</p>
  if (runs.length === 0) {
    return (
      <div className="empty-state">
        <p>No runs yet.</p>
        <p className="muted small">Launch a campaign from the <strong>Campaigns</strong> tab.</p>
      </div>
    )
  }

  return (
    <div className="eal-runs">
      <div className="flex-row" style={{ justifyContent: 'space-between', marginBottom: '8px' }}>
        <p className="muted small" style={{ margin: 0 }}>{runs.length} run(s)</p>
        <button className="btn btn-sm btn-secondary" onClick={onRefresh}>Refresh</button>
      </div>
      <table className="cs-table">
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Campaign</th>
            <th>Status</th>
            <th>Mode</th>
            <th>Started</th>
            <th>Operator</th>
          </tr>
        </thead>
        <tbody>
          {runs.map(r => (
            <tr
              key={r.run_id}
              className={openRunId === r.run_id ? 'row-selected' : 'row-clickable'}
              onClick={() => onOpenRun(openRunId === r.run_id ? null : r.run_id)}
            >
              <td><code className="mono small">{r.run_id.slice(0, 8)}…</code></td>
              <td><code className="mono small">{r.campaign_id}</code></td>
              <td>
                <span className={`pill pill-${statusToTone(r.status)}`}>{r.status}</span>
              </td>
              <td>
                {r.dry_run ? (
                  <span className="pill pill-warn">dry-run</span>
                ) : (
                  <span className="pill pill-info">live</span>
                )}
              </td>
              <td className="muted small">{r.started_at?.replace('T', ' ').slice(0, 19) || '–'}</td>
              <td className="muted small">{r.operator || '–'}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {openRunId && (
        <EalRunProgress
          key={openRunId}
          runId={openRunId}
          onClose={() => onOpenRun(null)}
          onMessage={onMessage}
        />
      )}
    </div>
  )
}

function statusToTone(status) {
  if (status === 'complete') return 'success'
  if (status === 'running' || status === 'pending') return 'info'
  if (status === 'failed' || status === 'aborted') return 'error'
  return 'neutral'
}
