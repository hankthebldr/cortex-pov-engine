import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { getTtps, getTtp, getTtpRuns } from '../../api/client.js'
import { downloadTtpLayer } from './exportNavigatorLayer.js'

/**
 * TtpBrowserView — surface the TTP corpus that lives under
 * ``detection_scanner/ttps/*.json``.
 *
 * Closes the cross-link loop PR #46+#49 set up: clicking a TTP-ref chip
 * in the Tool Adapter detail panel emits a ``cortex:navigate-ttp``
 * CustomEvent (PR #49). This view subscribes via its parent's
 * ``initialTtpId`` prop — Coverage flips to this sub-tab + pre-selects
 * the TTP when the event fires.
 *
 * Rendered as the "TTP Browser" tab in Coverage's view-mode toggle
 * (Coverage tab → ATT&CK | PANW Stack | Advantage | EAL Plugins |
 *   Tool Adapters | TTP Browser).
 */
export default function TtpBrowserView({ initialTtpId = null }) {
  const [ttps, setTtps]         = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  const [selectedId, setSelectedId]         = useState(null)
  const [selectedDetail, setSelectedDetail] = useState(null)
  const [selectedRuns, setSelectedRuns]     = useState(null)

  const [filterStatus, setFilterStatus]     = useState('all')
  const [filterTactic, setFilterTactic]     = useState('all')
  const [filterPlatform, setFilterPlatform] = useState('all')
  const [query, setQuery]                   = useState('')

  // Initial load — full corpus; chips derive from the response so the
  // catalog can grow without UI patches.
  useEffect(() => {
    setLoading(true)
    getTtps()
      .then((d) => setTtps(Array.isArray(d?.ttps) ? d.ttps : []))
      .catch((e) => setError(e?.message || 'Failed to load TTP corpus'))
      .finally(() => setLoading(false))
  }, [])

  const handleSelect = useCallback(async (ttpId) => {
    setSelectedId(ttpId)
    setSelectedDetail(null)
    setSelectedRuns(null)
    // Detail + run history are independent — fire both in parallel.
    // The runs panel renders in-place with its own loading affordance,
    // so we don't block the static-content render on the DB read.
    getTtp(ttpId)
      .then(setSelectedDetail)
      .catch((e) => setSelectedDetail({ _error: e?.message || 'Failed to load TTP detail' }))
    getTtpRuns(ttpId)
      .then(setSelectedRuns)
      .catch(() => setSelectedRuns({ runs: [], total: 0, _error: true }))
  }, [])

  // When CoverageView passes initialTtpId (from a cortex:navigate-ttp
  // event), auto-open that card's detail panel as soon as the list
  // loads.
  useEffect(() => {
    if (initialTtpId && !loading) {
      handleSelect(initialTtpId)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTtpId, loading])

  const statuses = useMemo(
    () => Array.from(new Set(ttps.map((t) => t.status))).sort(),
    [ttps],
  )
  const tactics = useMemo(
    () => Array.from(new Set(ttps.flatMap((t) => t.tactic_ids || []))).sort(),
    [ttps],
  )
  const platforms = useMemo(
    () => Array.from(new Set(ttps.flatMap((t) => t.platforms || []))).sort(),
    [ttps],
  )

  const visible = useMemo(() => {
    // Free-text match runs over the fields a DC actually searches by:
    // id, name, summary, tags, technique ids, and actor names. Tokenised
    // on whitespace so "dcsync windows" narrows by AND across tokens.
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return ttps.filter((t) => {
      if (filterStatus   !== 'all' && t.status !== filterStatus)         return false
      if (filterTactic   !== 'all' && !(t.tactic_ids || []).includes(filterTactic))   return false
      if (filterPlatform !== 'all' && !(t.platforms  || []).includes(filterPlatform)) return false
      if (tokens.length > 0) {
        const haystack = [
          t.id, t.name, t.summary,
          ...(t.tags || []),
          ...(t.technique_ids || []),
          ...(t.actor_names || []),
        ].join(' ').toLowerCase()
        if (!tokens.every((tok) => haystack.includes(tok))) return false
      }
      return true
    })
  }, [ttps, filterStatus, filterTactic, filterPlatform, query])

  const hasActiveFilters =
    filterStatus !== 'all' || filterTactic !== 'all' ||
    filterPlatform !== 'all' || query.trim() !== ''

  const resetFilters = () => {
    setFilterStatus('all')
    setFilterTactic('all')
    setFilterPlatform('all')
    setQuery('')
  }

  return (
    <div className="adapter-registry" data-testid="ttp-browser">
      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          Browser over the <strong>TTP corpus</strong> — every Tactic /
          Technique / Procedure card under{' '}
          <span className="mono">detection_scanner/ttps/</span>. Each
          card pairs a MITRE technique with the deployable BIOC / XQL /
          correlation logic Cortex ships to detect it. Click any card to
          see the full chain + the tool adapters that exercise it.
        </p>
        <div className="adapter-registry__stats">
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{ttps.length}</div>
            <div className="stack-coverage__stat-label">TTPs</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{tactics.length}</div>
            <div className="stack-coverage__stat-label">tactics</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{platforms.length}</div>
            <div className="stack-coverage__stat-label">platforms</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{visible.length}</div>
            <div className="stack-coverage__stat-label">visible</div>
          </div>
        </div>
      </div>

      {error && (
        <div className="adapter-registry__error mono" role="alert">{error}</div>
      )}

      <div className="adapter-registry__search">
        <input
          type="search"
          className="adapter-registry__search-input mono"
          data-testid="ttp-search"
          placeholder="Search id · name · summary · tag · technique · actor…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search TTP corpus"
        />
        {hasActiveFilters && (
          <button
            type="button"
            className="btn"
            data-testid="ttp-clear-filters"
            style={{ height: 28, padding: '0 10px' }}
            onClick={resetFilters}
          >
            Clear
          </button>
        )}
      </div>

      <FilterRow label="status"   active={filterStatus}   options={statuses}  onChange={setFilterStatus} />
      <FilterRow label="tactic"   active={filterTactic}   options={tactics}   onChange={setFilterTactic} />
      <FilterRow label="platform" active={filterPlatform} options={platforms} onChange={setFilterPlatform} />

      {loading ? (
        <div className="coverage__empty mono">loading TTP corpus…</div>
      ) : visible.length === 0 ? (
        <div className="coverage__empty mono">
          no TTPs match the current filters —{' '}
          <button
            type="button"
            className="btn"
            style={{ height: 22, padding: '0 8px', marginLeft: 4 }}
            onClick={resetFilters}
          >
            clear filters
          </button>
        </div>
      ) : (
        <div className="adapter-registry__grid">
          {visible.map((t) => (
            <TtpCard
              key={t.id}
              ttp={t}
              isSelected={t.id === selectedId}
              onSelect={() => handleSelect(t.id)}
            />
          ))}
        </div>
      )}

      {selectedDetail && (
        <TtpDetail
          detail={selectedDetail}
          runs={selectedRuns}
          onClose={() => {
            setSelectedId(null)
            setSelectedDetail(null)
            setSelectedRuns(null)
          }}
        />
      )}
    </div>
  )
}

/* ─── Filter chip row (mirrors ToolAdapterCatalog) ─────────────────── */

function FilterRow({ label, active, options, onChange }) {
  return (
    <div className="adapter-registry__filters">
      <span className="competitive__filter-label mono">{label}:</span>
      <button
        type="button"
        className={'competitive__filter' + (active === 'all' ? ' is-active' : '')}
        onClick={() => onChange('all')}
      >
        All
      </button>
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          className={'competitive__filter' + (active === opt ? ' is-active' : '')}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  )
}

/* ─── TTP card ─────────────────────────────────────────────────────── */

function TtpCard({ ttp, isSelected, onSelect }) {
  const techniques = ttp.technique_ids || []
  const tactics    = ttp.tactic_ids    || []
  const platforms  = ttp.platforms     || []
  const counts     = ttp.detection_counts || {}
  const totalDetections =
    (counts.iocs || 0) +
    (counts.biocs || 0) +
    (counts.xql_queries || 0) +
    (counts.correlation_rules || 0) +
    (counts.analytics_modules || 0)

  return (
    <article
      className={'adapter-card' + (isSelected ? ' adapter-card--selected' : '')}
      role="button"
      tabIndex={0}
      data-testid={`ttp-card-${ttp.id}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() }
      }}
    >
      <div className="adapter-card__head">
        <div className="adapter-card__name mono">{ttp.id}</div>
        <div className="adapter-card__version mono">
          {totalDetections} det{totalDetections === 1 ? '' : 's'}
        </div>
      </div>
      <div className="adapter-card__category mono">
        {ttp.status} · {ttp.simulation_class || 'other'} · {ttp.kill_chain_phase || '—'}
      </div>
      <div className="adapter-card__desc">
        <strong style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
          {ttp.name}
        </strong>
        <span style={{ fontSize: 11, color: 'var(--c-text-secondary)' }}>
          {(ttp.summary || '').slice(0, 200)}
          {(ttp.summary || '').length > 200 ? '…' : ''}
        </span>
      </div>
      <div className="adapter-card__meta">
        {techniques.length > 0 && (
          <div className="adapter-card__tids">
            {techniques.slice(0, 4).map((t) => (
              <span key={t} className="chip" style={{ fontSize: 9 }}>{t}</span>
            ))}
            {techniques.length > 4 && (
              <span className="adapter-card__more mono">+{techniques.length - 4}</span>
            )}
          </div>
        )}
        {(tactics.length > 0 || platforms.length > 0) && (
          <div className="adapter-card__tids" style={{ marginTop: 4 }}>
            {tactics.map((t) => (
              <span key={t} className="chip chip--signal" style={{ fontSize: 9 }}>{t}</span>
            ))}
            {platforms.map((p) => (
              <span
                key={p}
                className="chip"
                style={{ fontSize: 9, color: 'var(--c-text-muted)' }}
              >
                {p}
              </span>
            ))}
          </div>
        )}
      </div>
    </article>
  )
}

/* ─── TTP detail panel ─────────────────────────────────────────────── */

function TtpDetail({ detail, runs, onClose }) {
  if (detail._error) {
    return (
      <div className="competitive__detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">TTP detail</div>
            <h3 className="competitive__detail-title">Load failed</h3>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <p className="adapter-registry__error mono">{detail._error}</p>
      </div>
    )
  }

  const identity = detail.identity || {}
  const metadata = detail.metadata || {}
  const mitre    = detail.mitre_attack || {}
  const threat   = detail.threat_context || {}
  const detections = detail.detections || {}
  const panw     = detail.panw_mapping || {}
  const adapters = detail.referenced_by_adapters || []

  const techniques = mitre.techniques || []
  const actors     = threat.actors || []
  const tags       = metadata.tags || []
  const products   = panw.products || []

  return (
    <div className="competitive__detail" data-testid="ttp-detail">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            {detail.id} · {detail.status}
          </div>
          <h3 className="competitive__detail-title">
            {identity.name || detail.id}
          </h3>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {techniques.length > 0 && (
            <button
              type="button"
              className="btn"
              data-testid="ttp-export-navigator"
              title="Download a MITRE ATT&CK Navigator layer scoped to this TTP — paste into the customer's Navigator for the briefing"
              onClick={() => downloadTtpLayer(detail)}
            >
              Export ATT&amp;CK layer
            </button>
          )}
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
      </div>

      {identity.summary && (
        <DetailSection label="Summary">
          <p style={{ whiteSpace: 'pre-line', fontSize: 12 }}>{identity.summary}</p>
        </DetailSection>
      )}

      {techniques.length > 0 && (
        <DetailSection label="MITRE ATT&CK">
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--c-text-muted)' }}>
                <th style={{ padding: '2px 6px 2px 0' }}>Technique</th>
                <th style={{ padding: '2px 6px 2px 0' }}>Name</th>
                <th style={{ padding: '2px 6px 2px 0' }}>Tactics</th>
              </tr>
            </thead>
            <tbody>
              {techniques.map((t, i) => (
                <tr key={i}>
                  <td className="mono" style={{ padding: '2px 6px 2px 0' }}>
                    {t.subtechnique_id || t.technique_id}
                  </td>
                  <td style={{ padding: '2px 6px 2px 0' }}>{t.name}</td>
                  <td className="mono" style={{ padding: '2px 6px 2px 0' }}>
                    {(t.tactic_ids || []).join(', ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </DetailSection>
      )}

      {actors.length > 0 && (
        <DetailSection label="Threat actors">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {actors.map((a, i) => (
              <span
                key={i}
                className="chip"
                title={(a.aliases || []).join(' · ')}
              >
                {a.name}
                {a.unit42_actor_id && (
                  <span className="mono" style={{ marginLeft: 4, fontSize: 9, opacity: 0.6 }}>
                    u42
                  </span>
                )}
                {a.mitre_group_id && (
                  <span className="mono" style={{ marginLeft: 4, fontSize: 9, opacity: 0.6 }}>
                    {a.mitre_group_id}
                  </span>
                )}
              </span>
            ))}
          </div>
        </DetailSection>
      )}

      <DetailSection label="Detection coverage">
        <DetectionsBreakdown detections={detections} />
      </DetailSection>

      {products.length > 0 && (
        <DetailSection label="Cortex products">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {products.map((p, i) => (
              <span key={i} className="chip chip--signal">
                {p.module}
                {p.submodule && (
                  <span className="mono" style={{ marginLeft: 4, fontSize: 9, opacity: 0.7 }}>
                    / {p.submodule}
                  </span>
                )}
              </span>
            ))}
          </div>
        </DetailSection>
      )}

      <DetailSection label="Recent runs">
        <RunHistory runs={runs} />
      </DetailSection>

      {adapters.length > 0 && (
        <DetailSection label="Referenced by tool adapters">
          <p
            className="mono"
            style={{ fontSize: 10, color: 'var(--c-text-muted)', margin: '0 0 6px' }}
          >
            Adapters in <span className="mono">tools/packs/</span> that
            cite this TTP in <span className="mono">ttp_refs[]</span>:
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {adapters.map((a) => (
              <span
                key={a.adapter_id}
                className="chip"
                title={`${a.adapter_id} · T${a.tier} · ${a.category} · ${a.safety_class}`}
                data-testid={`ttp-adapter-ref-${a.adapter_id}`}
              >
                {a.name}
                <span className="mono" style={{ marginLeft: 4, fontSize: 9, opacity: 0.6 }}>
                  T{a.tier}
                </span>
              </span>
            ))}
          </div>
        </DetailSection>
      )}

      {tags.length > 0 && (
        <DetailSection label="Tags">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {tags.map((t) => (
              <span key={t} className="chip" style={{ fontSize: 9 }}>{t}</span>
            ))}
          </div>
        </DetailSection>
      )}
    </div>
  )
}

function DetailSection({ label, children }) {
  return (
    <div className="competitive__detail-section">
      <div className="competitive__detail-label">{label}</div>
      {children}
    </div>
  )
}

/* ─── Run history table ────────────────────────────────────────────── */

/**
 * Render the rolled-up run history for the selected TTP — one row per
 * Run that fired Results citing this ttp_ref. Closes the temporal loop
 * the static detail panel left open: "did we exercise it, and how?"
 */
function RunHistory({ runs }) {
  if (runs === null || runs === undefined) {
    return (
      <div className="coverage__empty mono" style={{ fontSize: 11 }} data-testid="ttp-runs-loading">
        loading run history…
      </div>
    )
  }
  if (runs._error) {
    return (
      <div className="adapter-registry__error mono" style={{ fontSize: 11 }}>
        couldn't load run history
      </div>
    )
  }
  const rows = runs.runs || []
  if (rows.length === 0) {
    return (
      <div className="coverage__empty mono" style={{ fontSize: 11 }} data-testid="ttp-runs-empty">
        no runs have exercised this TTP yet
      </div>
    )
  }
  return (
    <table
      style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}
      data-testid="ttp-runs-table"
    >
      <thead>
        <tr style={{ textAlign: 'left', color: 'var(--c-text-muted)' }}>
          <th style={{ padding: '2px 6px 2px 0' }}>Run</th>
          <th style={{ padding: '2px 6px 2px 0' }}>Scenario</th>
          <th style={{ padding: '2px 6px 2px 0' }}>Started</th>
          <th style={{ padding: '2px 6px 2px 0', textAlign: 'right' }}>Coverage</th>
          <th style={{ padding: '2px 6px 2px 0', textAlign: 'right' }}>Min MTTD</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.run_id}
            data-testid={`ttp-run-${r.run_id}`}
            onClick={() => {
              window.dispatchEvent(new CustomEvent('cortex:navigate-run', {
                detail: { runId: r.run_id },
              }))
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                window.dispatchEvent(new CustomEvent('cortex:navigate-run', {
                  detail: { runId: r.run_id },
                }))
              }
            }}
            style={{ cursor: 'pointer' }}
            title={`Open run ${r.run_id} in the validation wizard`}
          >
            <td className="mono" style={{ padding: '2px 6px 2px 0' }}>{r.run_id}</td>
            <td className="mono" style={{ padding: '2px 6px 2px 0' }}>{r.scenario_id}</td>
            <td className="mono" style={{ padding: '2px 6px 2px 0', fontSize: 10 }}>
              {formatStartedAt(r.started_at)}
            </td>
            <td className="mono" style={{ padding: '2px 6px 2px 0', textAlign: 'right' }}>
              <span
                className="chip"
                style={{
                  fontSize: 9,
                  background:
                    r.observed === r.expected
                      ? 'var(--c-success-bg, rgba(0,255,160,0.1))'
                      : r.observed === 0
                        ? 'var(--c-error-bg, rgba(255,80,80,0.1))'
                        : 'var(--c-warning-bg, rgba(255,200,0,0.1))',
                }}
              >
                {r.observed}/{r.expected}
              </span>
            </td>
            <td className="mono" style={{ padding: '2px 6px 2px 0', textAlign: 'right' }}>
              {formatMttd(r.min_mttd_seconds)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function formatStartedAt(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
  } catch {
    return iso
  }
}

function formatMttd(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

/* ─── Detection accordion (XQL / BIOC / correlation body reveal) ───── */

/**
 * Render every detection across BIOC / XQL / correlation / IOC as an
 * expandable card with the raw logic body + copy-to-clipboard.
 *
 * Goal: an operator who reads the detail panel can grab the exact XQL
 * string (or BIOC body, or correlation expression) Cortex ships and
 * paste it straight into XSIAM Query Center without a hop through the
 * filesystem. Closes the "show me the actual detection" gap PR #50
 * left as a follow-up.
 */
function DetectionsBreakdown({ detections }) {
  const kinds = [
    { key: 'biocs',             label: 'BIOCs',        bodyKey: 'logic' },
    { key: 'xql_queries',       label: 'XQL queries',  bodyKey: 'query' },
    { key: 'correlation_rules', label: 'Correlation',  bodyKey: 'logic' },
    { key: 'iocs',              label: 'IOCs',         bodyKey: 'value' },
    { key: 'analytics_modules', label: 'Analytics',    bodyKey: 'logic' },
  ]
  const hasAny = kinds.some(({ key }) => (detections[key] || []).length > 0)
  if (!hasAny) {
    return (
      <div className="coverage__empty mono" style={{ fontSize: 11 }}>
        no detections shipped with this card
      </div>
    )
  }
  return (
    <div className="ttp-detections">
      {kinds.map(({ key, label, bodyKey }) => {
        const items = detections[key] || []
        if (items.length === 0) return null
        return (
          <div key={key} className="ttp-detections__group">
            <div
              className="competitive__detail-label mono"
              style={{ fontSize: 10, opacity: 0.7, marginBottom: 4 }}
            >
              {label} · {items.length}
            </div>
            {items.map((item, idx) => (
              <DetectionItem
                key={`${key}-${idx}`}
                kind={key}
                index={idx}
                item={item}
                bodyKey={bodyKey}
              />
            ))}
          </div>
        )
      })}
    </div>
  )
}

function DetectionItem({ kind, index, item, bodyKey }) {
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied]     = useState(false)

  // BIOC / correlation: name + description + logic
  // XQL:               name + purpose     + query
  // IOC:               ioc_type + value   (value becomes the body)
  const name = item.name
    || (kind === 'iocs' ? `${item.ioc_type || 'ioc'}: ${item.value || ''}` : `${kind}-${index + 1}`)
  const desc = item.description || item.purpose || item.context || ''
  const body = item[bodyKey] || ''
  const severity = item.severity
  const detId = item.detection_id || item.rule_id

  const handleCopy = (e) => {
    e.stopPropagation()
    if (!body) return
    if (navigator?.clipboard?.writeText) {
      navigator.clipboard.writeText(body).catch(() => {})
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className={'ttp-detection-item' + (expanded ? ' is-expanded' : '')}>
      <button
        type="button"
        className="ttp-detection-item__head"
        data-testid={`ttp-det-${kind}-${index}`}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        style={{
          width: '100%',
          textAlign: 'left',
          background: 'none',
          border: 0,
          padding: '6px 4px',
          cursor: 'pointer',
          borderTop: '1px solid var(--c-border-subtle, rgba(255,255,255,0.05))',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span className="mono" style={{ fontSize: 9, opacity: 0.5 }}>
            {expanded ? '▼' : '▶'}
          </span>
          <span style={{ fontSize: 11, fontWeight: 500, flex: 1 }}>{name}</span>
          {severity && (
            <span className="chip" style={{ fontSize: 9 }}>{severity}</span>
          )}
          {detId && (
            <span
              className="mono"
              style={{ fontSize: 9, opacity: 0.6 }}
              title={detId}
            >
              {detId}
            </span>
          )}
        </div>
        {desc && !expanded && (
          <div
            style={{
              fontSize: 10,
              color: 'var(--c-text-muted)',
              marginLeft: 16,
              marginTop: 2,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {desc}
          </div>
        )}
      </button>
      {expanded && (
        <div className="ttp-detection-item__body" style={{ padding: '4px 4px 8px 16px' }}>
          {desc && (
            <p style={{ fontSize: 11, color: 'var(--c-text-secondary)', margin: '0 0 6px' }}>
              {desc}
            </p>
          )}
          {body ? (
            <>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  marginBottom: 4,
                }}
              >
                <span className="mono" style={{ fontSize: 9, opacity: 0.6 }}>
                  {bodyKey}
                </span>
                <button
                  type="button"
                  className="btn"
                  style={{ height: 20, padding: '0 8px', fontSize: 10 }}
                  onClick={handleCopy}
                  data-testid={`ttp-det-copy-${kind}-${index}`}
                >
                  {copied ? '✓ copied' : 'Copy'}
                </button>
              </div>
              <pre
                className="mono"
                style={{
                  fontSize: 10,
                  background: 'var(--c-bg-subtle, rgba(0,0,0,0.25))',
                  border: '1px solid var(--c-border-subtle, rgba(255,255,255,0.05))',
                  padding: '6px 8px',
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  maxHeight: 260,
                  overflowY: 'auto',
                }}
              >
                {body}
              </pre>
            </>
          ) : (
            <p className="mono" style={{ fontSize: 10, color: 'var(--c-text-muted)' }}>
              (no body in corpus entry)
            </p>
          )}
        </div>
      )}
    </div>
  )
}
