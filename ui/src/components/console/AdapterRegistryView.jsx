import React, { useEffect, useMemo, useState } from 'react'
import { getEalPlugins, getEalPlugin } from '../../api/client.js'

/**
 * AdapterRegistryView — surface the installed EAL simulator plugins.
 *
 * Discoverability surface for the operator: every attack adapter that
 * ships with this CortexSim build, grouped by category, with its MITRE
 * mapping and parameter schema visible without leaving the console.
 *
 * Rendered as the "Adapters" tab in the Coverage view-mode toggle
 * (Coverage tab → ATT&CK | PANW Stack | Advantage | Adapters).
 *
 * Operator UX:
 *   - Grid of cards, one per plugin
 *   - Each card shows name, version, description, MITRE TIDs, EAL targets
 *   - Click → drill-down with the full Pydantic params schema rendered
 *     as readable field list (name, type, required, default, description)
 *   - Category filter chip strip across the top
 */
export default function AdapterRegistryView() {
  const [plugins, setPlugins] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)
  const [selectedName, setSelectedName] = useState(null)
  const [selectedDetail, setSelectedDetail] = useState(null)
  const [filterCategory, setFilterCategory] = useState('all')

  useEffect(() => {
    setLoading(true)
    getEalPlugins()
      .then((d) => {
        const list = Array.isArray(d) ? d : (d && d.plugins) || []
        setPlugins(list)
      })
      .catch((e) => setError(e.message || 'Failed to load adapter registry'))
      .finally(() => setLoading(false))
  }, [])

  // Derive a category for each plugin from its name pattern + MITRE
  // tactics. The naming convention is well-established: c2_, dns_,
  // smb_, browser_, oauth_, idp_, agentic_, airs_, llm_, bulk_.
  const categorize = (p) => {
    const n = (p.name || '').toLowerCase()
    if (/airs|llm|ai_/.test(n))                return 'AI / LLM'
    if (/browser/.test(n))                     return 'Browser'
    if (/agentic|mcp|skill|extension/.test(n)) return 'Agentic / Supply chain'
    if (/oauth|idp|sign|cred/.test(n))         return 'Identity / SaaS'
    if (/c2_|stratum|tcp_/.test(n))            return 'Command & control'
    if (/exfil|bulk|dns_tun/.test(n))          return 'Exfiltration'
    if (/smb|rpc|sweep|lateral/.test(n))       return 'Lateral / Network'
    return 'Other'
  }

  const categorized = useMemo(
    () => plugins.map((p) => ({ ...p, _category: categorize(p) })),
    [plugins],
  )

  const categories = useMemo(
    () => Array.from(new Set(categorized.map((p) => p._category))).sort(),
    [categorized],
  )

  const visible = useMemo(
    () => filterCategory === 'all'
      ? categorized
      : categorized.filter((p) => p._category === filterCategory),
    [categorized, filterCategory],
  )

  const handleSelect = async (name) => {
    setSelectedName(name)
    setSelectedDetail(null)
    try {
      const detail = await getEalPlugin(name)
      setSelectedDetail(detail)
    } catch (e) {
      setSelectedDetail({ _error: e.message || 'Failed to load plugin detail' })
    }
  }

  return (
    <div className="adapter-registry">
      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          Every attack adapter that ships with this CortexSim build. Adapters
          are the <strong>EAL plugins</strong> the agent invokes to produce
          high-fidelity attack traffic — outbound HTTPS beacons, DNS tunnels,
          OAuth consent flows, LLM prompt-attack pipelines, agentic supply-
          chain pulls, browser drive-by sequences, and more. Click any
          adapter to inspect its parameter schema.
        </p>
        <div className="adapter-registry__stats">
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{plugins.length}</div>
            <div className="stack-coverage__stat-label">adapters installed</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{categories.length}</div>
            <div className="stack-coverage__stat-label">categories</div>
          </div>
        </div>
      </div>

      {error && (
        <div className="adapter-registry__error mono">
          {error}
        </div>
      )}

      <div className="adapter-registry__filters">
        <span className="competitive__filter-label mono">category:</span>
        <button
          type="button"
          className={'competitive__filter' + (filterCategory === 'all' ? ' is-active' : '')}
          onClick={() => setFilterCategory('all')}
        >
          All
        </button>
        {categories.map((c) => (
          <button
            key={c}
            type="button"
            className={'competitive__filter' + (filterCategory === c ? ' is-active' : '')}
            onClick={() => setFilterCategory(c)}
          >
            {c}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="coverage__empty mono">loading adapter registry…</div>
      ) : visible.length === 0 ? (
        <div className="coverage__empty mono">
          no adapters in this category — try "All"
        </div>
      ) : (
        <div className="adapter-registry__grid">
          {visible.map((p) => (
            <AdapterCard
              key={p.name}
              plugin={p}
              isSelected={p.name === selectedName}
              onSelect={() => handleSelect(p.name)}
            />
          ))}
        </div>
      )}

      {selectedDetail && (
        <AdapterDetailPanel
          detail={selectedDetail}
          onClose={() => { setSelectedName(null); setSelectedDetail(null) }}
        />
      )}
    </div>
  )
}

/* ─── Card ────────────────────────────────────────────────────────── */

function AdapterCard({ plugin, isSelected, onSelect }) {
  const tids = plugin.mitre_techniques || []
  const targets = plugin.eal_targets || []
  return (
    <article
      className={'adapter-card' + (isSelected ? ' adapter-card--selected' : '')}
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }}
    >
      <div className="adapter-card__head">
        <div className="adapter-card__name mono">{plugin.name}</div>
        <div className="adapter-card__version mono">v{plugin.version || '1.0.0'}</div>
      </div>
      <div className="adapter-card__category mono">{plugin._category}</div>
      <div className="adapter-card__desc">
        {(plugin.description || '').trim() || 'No description.'}
      </div>
      <div className="adapter-card__meta">
        {tids.length > 0 && (
          <div className="adapter-card__tids">
            {tids.slice(0, 6).map((t) => (
              <span key={t} className="chip" style={{ fontSize: 9 }}>{t}</span>
            ))}
            {tids.length > 6 && (
              <span className="adapter-card__more mono">+{tids.length - 6}</span>
            )}
          </div>
        )}
        {targets.length > 0 && (
          <div className="adapter-card__targets mono">
            <span className="adapter-card__targets-label">EAL:</span>{' '}
            {targets.join(' · ')}
          </div>
        )}
      </div>
    </article>
  )
}

/* ─── Detail panel ───────────────────────────────────────────────── */

function AdapterDetailPanel({ detail, onClose }) {
  if (detail._error) {
    return (
      <div className="competitive__detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">adapter detail</div>
            <h3 className="competitive__detail-title">Load failed</h3>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <p className="adapter-registry__error mono">{detail._error}</p>
      </div>
    )
  }

  const schema = detail.params_schema || {}
  const props = schema.properties || {}
  const required = new Set(schema.required || [])
  const fields = Object.entries(props)

  return (
    <div className="competitive__detail">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            {detail.name} · v{detail.version || '1.0.0'}
          </div>
          <h3 className="competitive__detail-title">
            {schema.title || detail.name}
          </h3>
        </div>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>

      {detail.description && (
        <div className="competitive__detail-section">
          <div className="competitive__detail-label">Description</div>
          <p style={{ whiteSpace: 'pre-line' }}>{detail.description}</p>
        </div>
      )}

      {detail.mitre_techniques && detail.mitre_techniques.length > 0 && (
        <div className="competitive__detail-section">
          <div className="competitive__detail-label">MITRE techniques</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {detail.mitre_techniques.map((t) => (
              <span key={t} className="chip">{t}</span>
            ))}
          </div>
        </div>
      )}

      {detail.eal_targets && detail.eal_targets.length > 0 && (
        <div className="competitive__detail-section">
          <div className="competitive__detail-label">EAL targets</div>
          <div className="mono" style={{ fontSize: 11, color: 'var(--c-text-secondary)' }}>
            {detail.eal_targets.join(' · ')}
          </div>
        </div>
      )}

      <div className="competitive__detail-section">
        <div className="competitive__detail-label">Parameters</div>
        {fields.length === 0 ? (
          <p className="mono" style={{ color: 'var(--c-text-muted)', fontSize: 11 }}>
            no parameters defined
          </p>
        ) : (
          <div className="adapter-schema">
            {fields.map(([name, spec]) => (
              <div key={name} className="adapter-schema__row">
                <div className="adapter-schema__name mono">
                  {name}
                  {required.has(name) && (
                    <span className="adapter-schema__required" aria-label="required"> *</span>
                  )}
                </div>
                <div className="adapter-schema__type mono">
                  {spec.type || (spec.anyOf ? spec.anyOf.map((x) => x.type).filter(Boolean).join('|') : 'any')}
                  {spec.format ? `:${spec.format}` : ''}
                </div>
                <div className="adapter-schema__desc">
                  {spec.description || spec.title || ''}
                  {spec.default !== undefined && (
                    <span className="adapter-schema__default mono">
                      {' '}· default: {formatDefault(spec.default)}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function formatDefault(v) {
  if (v == null) return 'null'
  if (typeof v === 'string') return `"${v}"`
  if (Array.isArray(v)) return JSON.stringify(v)
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
