import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { getTtps, getTtp } from '../../api/client.js'

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

  const [filterStatus, setFilterStatus]     = useState('all')
  const [filterTactic, setFilterTactic]     = useState('all')
  const [filterPlatform, setFilterPlatform] = useState('all')

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
    try {
      const detail = await getTtp(ttpId)
      setSelectedDetail(detail)
    } catch (e) {
      setSelectedDetail({ _error: e?.message || 'Failed to load TTP detail' })
    }
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
    return ttps.filter((t) => {
      if (filterStatus   !== 'all' && t.status !== filterStatus)         return false
      if (filterTactic   !== 'all' && !(t.tactic_ids || []).includes(filterTactic))   return false
      if (filterPlatform !== 'all' && !(t.platforms  || []).includes(filterPlatform)) return false
      return true
    })
  }, [ttps, filterStatus, filterTactic, filterPlatform])

  const resetFilters = () => {
    setFilterStatus('all')
    setFilterTactic('all')
    setFilterPlatform('all')
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
          onClose={() => { setSelectedId(null); setSelectedDetail(null) }}
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

function TtpDetail({ detail, onClose }) {
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
        <button type="button" className="btn" onClick={onClose}>Close</button>
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
        <div className="adapter-schema">
          {[
            ['iocs',              detections.iocs?.length              || 0],
            ['biocs',             detections.biocs?.length             || 0],
            ['xql_queries',       detections.xql_queries?.length       || 0],
            ['correlation_rules', detections.correlation_rules?.length || 0],
            ['analytics_modules', detections.analytics_modules?.length || 0],
          ].filter(([, n]) => n > 0).map(([k, n]) => (
            <div key={k} className="adapter-schema__row">
              <div className="adapter-schema__name mono">{k}</div>
              <div className="adapter-schema__desc mono">{n}</div>
            </div>
          ))}
        </div>
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
