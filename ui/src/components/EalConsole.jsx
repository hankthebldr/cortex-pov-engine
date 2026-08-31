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

  const runningCount = runs.filter(r => r.status === 'running' || r.status === 'pending').length
  const heroMeta = campaigns.length === 0
    ? 'Enterprise Activity Layer · no campaigns persisted yet'
    : `Enterprise Activity Layer · ${campaigns.length} campaign${campaigns.length === 1 ? '' : 's'}` +
      (runningCount > 0 ? ` · ${runningCount} run${runningCount === 1 ? '' : 's'} in progress` : '')

  return (
    <section className="eal-console">
      <header className="eal-console__head">
        <div className="eal-console__hero">
          <div className="eal-console__heading">
            <div className="eal-console__accent-bar" aria-hidden="true" />
            <div className="eal-console__eyebrow">Traffic</div>
            <h2 className="eal-console__title">
              <span className="eal-console__title-accent">EAL</span> Traffic Simulator
            </h2>
            <p className="eal-console__meta">{heroMeta}</p>
          </div>
          <div className="eal-console__actions">
            {onClose && (
              <button
                className="btn btn-sm btn-secondary eal-console__close"
                onClick={onClose}
              >
                Close
              </button>
            )}
          </div>
        </div>
        {/*
         * M-8: "+ New Campaign" used to sit OUTSIDE this tablist as a header
         * action while still driving the same tab state, so when it was
         * active none of the role="tab" elements had aria-selected="true" —
         * a screen-reader user was told nothing was selected. It is now a
         * real tab (still visually distinguished via .eal-console__tab--new)
         * so exactly one tab is always the selected one, and each tab is
         * associated to its panel via aria-controls/aria-labelledby.
         */}
        <nav className="eal-console__tabs" role="tablist" aria-label="EAL views">
          <button
            id="eal-tab-campaigns"
            role="tab"
            aria-selected={tab === 'campaigns'}
            aria-controls="eal-panel-campaigns"
            className={`eal-console__tab ${tab === 'campaigns' ? 'is-active' : ''}`}
            onClick={() => setTab('campaigns')}
          >
            Campaigns
            {campaigns.length > 0 && <span className="badge eal-console__tab-count">{campaigns.length}</span>}
          </button>
          <button
            id="eal-tab-new"
            role="tab"
            aria-selected={tab === 'new'}
            aria-controls="eal-panel-new"
            className={`btn btn-navy eal-console__tab--new ${tab === 'new' ? 'is-active' : ''}`}
            onClick={() => setTab('new')}
          >
            + New Campaign
          </button>
          <button
            id="eal-tab-runs"
            role="tab"
            aria-selected={tab === 'runs'}
            aria-controls="eal-panel-runs"
            className={`eal-console__tab ${tab === 'runs' ? 'is-active' : ''}`}
            onClick={() => setTab('runs')}
          >
            Runs
            {runs.length > 0 && <span className="badge eal-console__tab-count">{runs.length}</span>}
          </button>
        </nav>
      </header>

      <div className="eal-console__body">
        {tab === 'campaigns' && (
          <div id="eal-panel-campaigns" role="tabpanel" aria-labelledby="eal-tab-campaigns">
            <EalCampaignsList
              campaigns={campaigns}
              loading={loadingCampaigns}
              onLaunch={handleLaunch}
              onRefresh={refreshCampaigns}
            />
          </div>
        )}
        {tab === 'new' && (
          <div id="eal-panel-new" role="tabpanel" aria-labelledby="eal-tab-new">
            <EalCampaignBuilder
              onCreated={handleCampaignCreated}
              onError={(msg) => onMessage?.(msg, 'error')}
            />
          </div>
        )}
        {tab === 'runs' && (
          <div id="eal-panel-runs" role="tabpanel" aria-labelledby="eal-tab-runs">
            <EalRunsList
              runs={runs}
              loading={loadingRuns}
              openRunId={openRunId}
              onOpenRun={setOpenRunId}
              onRefresh={refreshRuns}
              onMessage={onMessage}
            />
          </div>
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
      <div className="flex-row eal-list__toolbar">
        <p className="muted small eal-list__count">
          {campaigns.length} campaign(s)
        </p>
        <button className="btn btn-sm btn-secondary" onClick={onRefresh}>Refresh</button>
      </div>
      <div className="eal-table-wrap">
      <table className="cs-table">
        <thead>
          <tr>
            <th>Campaign ID</th>
            <th>Name</th>
            <th>Steps</th>
            <th>Authorized</th>
            <th>Allowlist</th>
            <th>Created</th>
            <th className="eal-table__col-actions">Actions</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((c, i) => {
            const steps = c.spec?.steps?.length ?? '–'
            const rowKey = c.campaign_id ?? c.id ?? `campaign-${i}`
            return (
              <tr key={rowKey}>
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
                    className="btn btn-sm btn-navy eal-campaigns__run-live-btn"
                    disabled={busyId === c.campaign_id || !c.simulation_authorized}
                    onClick={() => setConfirmLive(c)}
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
      </div>

      {confirmLive && (
        <div className="modal-backdrop" onClick={() => setConfirmLive(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="eal-modal__title">Confirm live campaign launch</h3>
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
            <div className="flex-row eal-modal__actions">
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
      <div className="flex-row eal-list__toolbar">
        <p className="muted small eal-list__count">{runs.length} run(s)</p>
        <button className="btn btn-sm btn-secondary" onClick={onRefresh}>Refresh</button>
      </div>
      <div className="eal-table-wrap">
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
      </div>

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
