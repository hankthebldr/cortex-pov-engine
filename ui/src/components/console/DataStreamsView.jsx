import React, { useCallback, useEffect, useMemo, useState } from 'react'
import '../../styles/destinations/eal.css'
import '../../styles/destinations/datastreams.css'
import { getEalDataStreams, launchEalCampaign } from '../../api/client.js'
import EalCampaignBuilder from '../EalCampaignBuilder.jsx'
import EalRunProgress from '../EalRunProgress.jsx'

/**
 * DataStreamsView — the Data Streams console destination.
 *
 * The second half of the Traffic/EAL split: where Traffic/EAL shows the live
 * network-behaviour plugins, this shows the analytics log-streamer family —
 * emitters that POST shape-true log records to a collector so a customer's
 * Cortex Analytics/ABIOC detectors fire on the data source.
 *
 * It reports coverage against the vendor's 34-source catalogue, NOT against our
 * plugin count, and it renders three honesty invariants the brief and CLAUDE.md
 * require and this surface must never soften:
 *   - a gap is shown as a gap (degraded), never omitted — an unlisted gap reads
 *     as no gap;
 *   - authored is not proven — every covered source is authored, and
 *     tenant-verified is 0, shown as two separate figures;
 *   - a delivery 2xx is a delivery verdict, never a "detector fired" claim.
 */
const STATE_TONE = {
  covered: 'pill-success',
  partial: 'pill-info',
  gap: 'pill-warn',
  not_addressable: 'pill-neutral',
}
const STATE_LABEL = {
  covered: 'covered',
  partial: 'partial',
  gap: 'gap',
  not_addressable: 'n/a',
}
const VERDICT_TONE = {
  delivered: 'pill-success',
  partial: 'pill-warn',
  not_delivered: 'pill-error',
  not_applicable: 'pill-neutral',
}

export default function DataStreamsView({ onMessage }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('coverage') // 'coverage' | 'emitters'
  const [stateFilter, setStateFilter] = useState('all')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getEalDataStreams()
      .then((d) => { if (!cancelled) { setData(d); setError(null) } })
      .catch((err) => {
        if (!cancelled) { setError(err.message); onMessage?.(`Failed to load data streams: ${err.message}`, 'error') }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [onMessage])

  const counts = data?.counts || null
  const sources = data?.sources || []
  const emitters = data?.emitters || []

  const filteredSources = useMemo(() => {
    if (stateFilter === 'all') return sources
    return sources.filter((s) => s.state === stateFilter)
  }, [sources, stateFilter])

  if (loading) {
    return <section className="eal-console"><p className="muted" style={{ padding: 24 }}>Loading data streams…</p></section>
  }
  if (error) {
    return (
      <section className="eal-console">
        <div className="ds-banner ds-banner--error" role="alert" style={{ margin: 24 }}>
          Could not load the analytics coverage surface: {error}
        </div>
      </section>
    )
  }

  return (
    <section className="eal-console" data-testid="data-streams-view">
      <header className="eal-console__head">
        <div className="eal-console__hero">
          <div className="eal-console__heading">
            <div className="eal-console__accent-bar" aria-hidden="true" />
            <div className="eal-console__eyebrow">Traffic</div>
            <h2 className="eal-console__title">
              <span className="eal-console__title-accent">Data</span> Streams
            </h2>
            <p className="eal-console__meta">
              Analytics log-streamer coverage against the vendor's{' '}
              <span className="mono">{counts?.total ?? '—'}</span>-source catalogue
            </p>
          </div>
        </div>

        {/* Honesty banner — surfaced verbatim from the backend. authored != proven. */}
        <div className="ds-banner ds-banner--warn" role="note" data-testid="ds-authored-not-proven">
          {data?.authored_not_proven}
        </div>

        {/* Counts — a zero is degraded, not ok; gaps and tenant-verified are first-class. */}
        {counts && (
          <div className="ds-counts" data-testid="ds-counts">
            <Stat k="Covered" v={counts.covered} sub={`of ${counts.addressable} addressable`} tone="ok" />
            <Stat k="Partial" v={counts.partial} sub="reuse, not detector-true" tone="info" />
            <Stat k="Gaps" v={counts.gap} sub="no emitter yet" tone={counts.gap > 0 ? 'warn' : 'ok'} />
            <Stat k="Negative controls" v={counts.with_negative_control} sub={`of ${counts.covered} covered`} tone="info" />
            <Stat k="Tenant-verified" v={counts.proven} sub="authored is not proven" tone={counts.proven === 0 ? 'warn' : 'ok'} />
          </div>
        )}

        <nav className="eal-console__tabs" role="tablist" aria-label="Data Streams views">
          <button
            role="tab" aria-selected={tab === 'coverage'}
            className={`eal-console__tab ${tab === 'coverage' ? 'is-active' : ''}`}
            onClick={() => setTab('coverage')}
          >
            Catalogue coverage
            <span className="badge eal-console__tab-count">{sources.length}</span>
          </button>
          <button
            role="tab" aria-selected={tab === 'emitters'}
            className={`eal-console__tab ${tab === 'emitters' ? 'is-active' : ''}`}
            onClick={() => setTab('emitters')}
          >
            Emitters
            <span className="badge eal-console__tab-count">{emitters.length}</span>
          </button>
          <button
            role="tab" aria-selected={tab === 'new'}
            className={`btn btn-navy eal-console__tab--new ${tab === 'new' ? 'is-active' : ''}`}
            onClick={() => setTab('new')}
          >
            + New campaign
          </button>
        </nav>
      </header>

      <div className="eal-console__body">
        {tab === 'coverage' && (
          <CoverageTable
            sources={filteredSources}
            stateFilter={stateFilter}
            onStateFilter={setStateFilter}
            total={sources.length}
          />
        )}
        {tab === 'emitters' && <EmittersTable emitters={emitters} />}
        {tab === 'new' && <NewStreamCampaign onMessage={onMessage} />}
      </div>
    </section>
  )
}

/**
 * NewStreamCampaign — author + launch an analytics log-streamer campaign from
 * the Data Streams destination. The plugin picker is scoped to the analytics
 * family (the split's whole point), and launch reuses the shared EAL launch +
 * progress machinery so nothing is duplicated.
 */
function NewStreamCampaign({ onMessage }) {
  const [created, setCreated] = useState(null)
  const [runId, setRunId] = useState(null)
  const [busy, setBusy] = useState(false)

  const handleLaunch = useCallback(async (dryRun) => {
    if (!created) return
    setBusy(true)
    try {
      const resp = await launchEalCampaign(created.campaign_id, { dry_run: dryRun, operator: 'cortexsim-ui' })
      onMessage?.(`Campaign ${created.campaign_id} launched (run ${resp.run_id})`, 'success')
      setRunId(resp.run_id)
    } catch (err) {
      onMessage?.(`Launch failed: ${err.message}`, 'error')
    } finally {
      setBusy(false)
    }
  }, [created, onMessage])

  return (
    <div className="ds-new">
      <EalCampaignBuilder
        family="analytics_log_streamer"
        onCreated={(c) => { setCreated(c); setRunId(null); onMessage?.(`Campaign ${c.campaign_id} saved`, 'success') }}
        onError={(msg) => onMessage?.(msg, 'error')}
      />
      {created && (
        <div className="ds-launch-panel" data-testid="ds-launch-panel">
          <p>
            Saved <code className="mono">{created.campaign_id}</code>. Launch it as a data-stream
            campaign — a dry-run renders records without POSTing; a live run POSTs to the collector.
          </p>
          <div className="flex-row eal-modal__actions">
            <button className="btn btn-sm btn-secondary" disabled={busy} onClick={() => handleLaunch(true)}>
              Dry-run
            </button>
            <button
              className="btn btn-sm btn-navy"
              disabled={busy || !created.simulation_authorized}
              title={created.simulation_authorized ? 'Run live against the campaign target_allowlist' : 'Live execution requires simulation_authorized=true'}
              onClick={() => handleLaunch(false)}
            >
              Run live
            </button>
          </div>
        </div>
      )}
      {runId && (
        <EalRunProgress key={runId} runId={runId} onClose={() => setRunId(null)} onMessage={onMessage} />
      )}
    </div>
  )
}

function Stat({ k, v, sub, tone }) {
  return (
    <div className={`ds-stat ds-stat--${tone}`}>
      <div className="ds-stat__k">{k}</div>
      <div className="mono ds-stat__v">{v}</div>
      <div className="ds-stat__sub">{sub}</div>
    </div>
  )
}

function CoverageTable({ sources, stateFilter, onStateFilter, total }) {
  return (
    <div className="ds-coverage">
      <div className="flex-row eal-list__toolbar">
        <p className="muted small">
          {sources.length} of {total} sources
        </p>
        <label className="ds-filter">
          <span className="muted small">Show</span>
          <select value={stateFilter} onChange={(e) => onStateFilter(e.target.value)}>
            <option value="all">all states</option>
            <option value="covered">covered</option>
            <option value="partial">partial</option>
            <option value="gap">gaps only</option>
            <option value="not_addressable">not addressable</option>
          </select>
        </label>
      </div>
      <div className="eal-table-wrap">
        <table className="cs-table">
          <thead>
            <tr>
              <th>Data source</th>
              <th>Category</th>
              <th>Dataset</th>
              <th>State</th>
              <th>Emitter(s)</th>
              <th>Neg. control</th>
              <th>Proven</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.key}>
                <td>{s.name}</td>
                <td className="muted small">{s.category}</td>
                <td><code className="mono small">{s.dataset || '—'}</code></td>
                <td>
                  <span className={`pill ${STATE_TONE[s.state] || 'pill-neutral'}`}>
                    {STATE_LABEL[s.state] || s.state}
                  </span>
                </td>
                <td className="mono small">
                  {s.emitters?.length
                    ? s.emitters.join(', ')
                    : s.partial_emitters?.length
                      ? <span className="muted">{s.partial_emitters.join(', ')} (partial)</span>
                      : <span className="muted">—</span>}
                </td>
                <td>
                  {s.state === 'covered'
                    ? (s.has_negative_control
                        ? <span className="pill pill-success">yes</span>
                        : <span className="pill pill-warn">no</span>)
                    : <span className="muted small">—</span>}
                </td>
                <td>
                  {/* Authored is not proven: covered rows are authored, never proven. */}
                  {s.authored
                    ? <span className="pill pill-warn" title="Authored, not proven — tenant-verified is 0">authored</span>
                    : <span className="muted small">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function EmittersTable({ emitters }) {
  if (!emitters.length) {
    return <div className="empty-state"><p>No analytics log-streamer emitters registered.</p></div>
  }
  return (
    <div className="ds-emitters">
      <div className="eal-table-wrap">
        <table className="cs-table">
          <thead>
            <tr>
              <th>Emitter</th>
              <th>Sources</th>
              <th>Dataset(s)</th>
              <th>Detectors</th>
              <th>Neg. control</th>
              <th>Last delivery</th>
            </tr>
          </thead>
          <tbody>
            {emitters.map((e) => {
              const d = e.latest_delivery
              return (
                <tr key={e.name}>
                  <td><code className="mono small">{e.name}</code></td>
                  <td className="small">
                    {(e.sources || []).map((s) => (
                      <span key={s.key} className={`ds-src-chip ${s.coverage === 'partial' ? 'ds-src-chip--partial' : ''}`}>
                        {s.name}{s.coverage === 'partial' ? ' (partial)' : ''}
                      </span>
                    ))}
                  </td>
                  <td className="mono small">{(e.datasets || []).join(', ') || '—'}</td>
                  <td className="mono small">{(e.detectors || []).length || '—'}</td>
                  <td>
                    {e.supports_negative_control
                      ? <span className="pill pill-success">yes</span>
                      : <span className="pill pill-warn">no</span>}
                  </td>
                  <td>
                    {d
                      ? <span className={`pill ${VERDICT_TONE[d.delivery_verdict] || 'pill-neutral'}`} title={`run ${d.run_id}`}>{d.delivery_verdict}</span>
                      : <span className="muted small">not run yet</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
