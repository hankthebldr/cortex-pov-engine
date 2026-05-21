import React, { useEffect, useMemo, useState } from 'react'
import { getToolAdapters, getToolAdapter } from '../../api/client.js'

/**
 * ToolAdapterCatalog — surface the static tool-adapter catalog
 * (``tools/packs/*.yml``).
 *
 * Distinct from ``AdapterRegistryView``: that component lists the
 * **EAL plugins** the engine *uses* to generate attack signal
 * (browser_attack_runner, oauth_grant_emulator, etc). This one lists the
 * **tool adapters** — the static catalog of offensive / defensive tools
 * a scenario can reference via ``external_tools[].adapter_ref``, each
 * with tier / safety-class / licence trail / Cortex plane mapping.
 *
 * Rendered as the "Tool Adapters" tab in the Coverage view-mode toggle
 * (Coverage tab → ATT&CK | PANW Stack | Advantage | EAL Plugins | Tool Adapters).
 *
 * Operator UX:
 *   - Filter strip across the top: plane · tier · safety class · category
 *   - Grid of cards, one per adapter
 *   - Each card shows name, version, tier, category, safety chip, planes,
 *     licence, expected MITRE techniques
 *   - Click → drill-down panel with the full pack: install + invoke +
 *     cleanup + ttp_refs + equivalents + author / dates / tags
 */
export default function ToolAdapterCatalog() {
  const [adapters, setAdapters] = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  const [selectedId, setSelectedId]         = useState(null)
  const [selectedDetail, setSelectedDetail] = useState(null)

  // Server-side filters — keep these in URL-ish state so a deep link could
  // pre-select them later. For now they're local-only.
  const [filterPlane, setFilterPlane]   = useState('all')
  const [filterTier, setFilterTier]     = useState('all')
  const [filterSafety, setFilterSafety] = useState('all')
  const [filterCategory, setFilterCategory] = useState('all')

  // Initial load is unfiltered — gives the user the full picture and the
  // filter chips are derived from the response so we never offer an
  // option the catalog can't satisfy.
  useEffect(() => {
    setLoading(true)
    getToolAdapters()
      .then((d) => setAdapters(Array.isArray(d?.adapters) ? d.adapters : []))
      .catch((e) => setError(e?.message || 'Failed to load tool-adapter catalog'))
      .finally(() => setLoading(false))
  }, [])

  // Chip values derived from the loaded corpus — never hard-coded so a
  // new adapter category doesn't require a UI patch.
  const planes = useMemo(
    () => Array.from(new Set(adapters.flatMap((a) => a.planes || []))).sort(),
    [adapters],
  )
  const tiers = useMemo(
    () => Array.from(new Set(adapters.map((a) => a.tier))).sort((x, y) => x - y),
    [adapters],
  )
  const safetyClasses = useMemo(
    () => Array.from(new Set(adapters.map((a) => a.safety_class))).sort(),
    [adapters],
  )
  const categories = useMemo(
    () => Array.from(new Set(adapters.map((a) => a.category))).sort(),
    [adapters],
  )

  // Client-side filter so chip toggles feel instant; the server-side
  // filter on the endpoint stays as the canonical implementation for
  // programmatic API consumers + deep-linkable UIs to come.
  const visible = useMemo(() => {
    return adapters.filter((a) => {
      if (filterPlane    !== 'all' && !(a.planes || []).includes(filterPlane)) return false
      if (filterTier     !== 'all' && a.tier !== Number(filterTier))           return false
      if (filterSafety   !== 'all' && a.safety_class !== filterSafety)         return false
      if (filterCategory !== 'all' && a.category !== filterCategory)           return false
      return true
    })
  }, [adapters, filterPlane, filterTier, filterSafety, filterCategory])

  const handleSelect = async (adapterId) => {
    setSelectedId(adapterId)
    setSelectedDetail(null)
    try {
      const detail = await getToolAdapter(adapterId)
      setSelectedDetail(detail)
    } catch (e) {
      setSelectedDetail({ _error: e?.message || 'Failed to load adapter detail' })
    }
  }

  const resetFilters = () => {
    setFilterPlane('all')
    setFilterTier('all')
    setFilterSafety('all')
    setFilterCategory('all')
  }

  return (
    <div className="adapter-registry" data-testid="tool-adapter-catalog">
      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          Static catalog of every offensive and defensive tool CortexSim can
          reference from a scenario via{' '}
          <strong>external_tools[].adapter_ref</strong>. Adapters are
          versioned, licence-tagged, and tier-classified — tier 2 (submodule)
          through tier 4 (runtime-fetched). Click any card to inspect its
          install plan, invoke template, cleanup commands, and cross-references
          to the TTP corpus.
        </p>
        <div className="adapter-registry__stats">
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{adapters.length}</div>
            <div className="stack-coverage__stat-label">adapters</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{categories.length}</div>
            <div className="stack-coverage__stat-label">categories</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{tiers.length}</div>
            <div className="stack-coverage__stat-label">tiers</div>
          </div>
          <div className="stack-coverage__stat">
            <div className="stack-coverage__stat-value mono">{visible.length}</div>
            <div className="stack-coverage__stat-label">visible</div>
          </div>
        </div>
      </div>

      {error && (
        <div className="adapter-registry__error mono" role="alert">
          {error}
        </div>
      )}

      <FilterRow
        label="plane"
        active={filterPlane}
        options={planes}
        onChange={setFilterPlane}
      />
      <FilterRow
        label="tier"
        active={filterTier}
        options={tiers.map(String)}
        onChange={setFilterTier}
        formatOption={(t) => `T${t}`}
      />
      <FilterRow
        label="safety"
        active={filterSafety}
        options={safetyClasses}
        onChange={setFilterSafety}
      />
      <FilterRow
        label="category"
        active={filterCategory}
        options={categories}
        onChange={setFilterCategory}
      />

      {loading ? (
        <div className="coverage__empty mono">loading tool-adapter catalog…</div>
      ) : visible.length === 0 ? (
        <div className="coverage__empty mono">
          no adapters match the current filters —{' '}
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
          {visible.map((a) => (
            <ToolAdapterCard
              key={a.adapter_id}
              adapter={a}
              isSelected={a.adapter_id === selectedId}
              onSelect={() => handleSelect(a.adapter_id)}
            />
          ))}
        </div>
      )}

      {selectedDetail && (
        <ToolAdapterDetail
          detail={selectedDetail}
          onClose={() => { setSelectedId(null); setSelectedDetail(null) }}
        />
      )}
    </div>
  )
}

/* ─── Filter chip row ─────────────────────────────────────────────── */

function FilterRow({ label, active, options, onChange, formatOption }) {
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
          {formatOption ? formatOption(opt) : opt}
        </button>
      ))}
    </div>
  )
}

/* ─── Card ────────────────────────────────────────────────────────── */

function ToolAdapterCard({ adapter, isSelected, onSelect }) {
  const tids    = adapter.expected_techniques || []
  const planes  = adapter.planes || []
  const safety  = adapter.safety_class || ''
  const safetyChip = safetyChipVariant(safety)

  return (
    <article
      className={'adapter-card' + (isSelected ? ' adapter-card--selected' : '')}
      role="button"
      tabIndex={0}
      data-testid={`tool-adapter-card-${adapter.adapter_id}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() }
      }}
    >
      <div className="adapter-card__head">
        <div className="adapter-card__name mono">{adapter.name}</div>
        <div className="adapter-card__version mono">v{adapter.version}</div>
      </div>
      <div className="adapter-card__category mono">
        T{adapter.tier} · {adapter.category} · {adapter.target_platform || 'any'}
      </div>
      <div className="adapter-card__desc">
        <span
          className={'chip chip--' + safetyChip}
          title={`safety class: ${safety}`}
        >
          {safety}
        </span>
        {' '}
        <span className="mono" style={{ fontSize: 10, color: 'var(--c-text-muted)' }}>
          · {adapter.license}
        </span>
      </div>
      <div className="adapter-card__meta">
        {planes.length > 0 && (
          <div className="adapter-card__tids">
            {planes.map((p) => (
              <span key={p} className="chip chip--signal" style={{ fontSize: 9 }}>
                {p}
              </span>
            ))}
          </div>
        )}
        {tids.length > 0 && (
          <div className="adapter-card__tids" style={{ marginTop: 4 }}>
            {tids.slice(0, 6).map((t) => (
              <span key={t} className="chip" style={{ fontSize: 9 }}>{t}</span>
            ))}
            {tids.length > 6 && (
              <span className="adapter-card__more mono">+{tids.length - 6}</span>
            )}
          </div>
        )}
      </div>
    </article>
  )
}

/* ─── Detail panel ───────────────────────────────────────────────── */

function ToolAdapterDetail({ detail, onClose }) {
  if (detail._error) {
    return (
      <div className="competitive__detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">tool adapter detail</div>
            <h3 className="competitive__detail-title">Load failed</h3>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <p className="adapter-registry__error mono">{detail._error}</p>
      </div>
    )
  }

  const invoke   = detail.invoke   || {}
  const install  = detail.install  || {}
  const upstream = detail.upstream || {}
  const cleanup  = detail.cleanup  || {}
  const ttpRefs  = detail.ttp_refs || []
  const equivs   = detail.equivalents || []
  const renderedTemplate = invoke.run_template || ''

  return (
    <div className="competitive__detail" data-testid="tool-adapter-detail">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            {detail.adapter_id} · v{detail.version} · tier {detail.tier}
          </div>
          <h3 className="competitive__detail-title">{detail.name}</h3>
        </div>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>

      <DetailSection label="Upstream">
        <div className="mono" style={{ fontSize: 12 }}>
          <a href={upstream.repo} target="_blank" rel="noreferrer">
            {upstream.repo}
          </a>
        </div>
        <div className="mono" style={{ fontSize: 11, color: 'var(--c-text-secondary)' }}>
          {upstream.license} · {upstream.attribution}
        </div>
      </DetailSection>

      <DetailSection label="Cortex signal">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {(detail.cortex_signal?.planes || []).map((p) => (
            <span key={p} className="chip chip--signal">{p}</span>
          ))}
        </div>
        {(detail.cortex_signal?.expected_techniques || []).length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
            {detail.cortex_signal.expected_techniques.map((t) => (
              <span key={t} className="chip">{t}</span>
            ))}
          </div>
        )}
      </DetailSection>

      <DetailSection label="Install">
        <KeyValueGrid
          rows={[
            ['tier',                     `${detail.tier}`],
            ['iac_module',               install.iac_module],
            ['source_path',              install.source_path],
            ['runtime_install_command',  install.runtime_install_command],
            ['binary',                   install.binary],
            ['build_cmd',                install.build_cmd],
          ]}
        />
      </DetailSection>

      <DetailSection label="Invoke">
        <KeyValueGrid
          rows={[
            ['target_platform',   invoke.target_platform],
            ['identity_required', invoke.identity_required],
            ['run_template',      renderedTemplate],
          ]}
        />
        {invoke.default_args && Object.keys(invoke.default_args).length > 0 && (
          <>
            <div className="competitive__detail-label" style={{ marginTop: 8 }}>default_args</div>
            <pre
              className="mono"
              style={{
                background: 'var(--c-bg-elevated)',
                padding: 8,
                fontSize: 11,
                borderRadius: 4,
                overflow: 'auto',
              }}
            >
              {JSON.stringify(invoke.default_args, null, 2)}
            </pre>
          </>
        )}
      </DetailSection>

      {cleanup.commands && cleanup.commands.length > 0 && (
        <DetailSection label="Cleanup">
          <ul className="mono" style={{ fontSize: 11, paddingLeft: 18, margin: 0 }}>
            {cleanup.commands.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </DetailSection>
      )}

      {(ttpRefs.length > 0 || equivs.length > 0) && (
        <DetailSection label="Cross-references">
          {ttpRefs.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <span
                className="mono"
                style={{ fontSize: 10, color: 'var(--c-text-muted)' }}
              >
                ttp_refs:
              </span>
              {ttpRefs.map((r) => (
                <span key={r} className="chip" style={{ marginLeft: 4 }}>{r}</span>
              ))}
            </div>
          )}
          {equivs.length > 0 && (
            <div>
              <span
                className="mono"
                style={{ fontSize: 10, color: 'var(--c-text-muted)' }}
              >
                equivalents:
              </span>
              {equivs.map((r) => (
                <span key={r} className="chip" style={{ marginLeft: 4 }}>{r}</span>
              ))}
            </div>
          )}
        </DetailSection>
      )}

      {(detail.tags || []).length > 0 && (
        <DetailSection label="Tags">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {detail.tags.map((t) => (
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

function KeyValueGrid({ rows }) {
  const present = rows.filter(([, v]) => v !== undefined && v !== null && v !== '')
  if (present.length === 0) {
    return (
      <p className="mono" style={{ fontSize: 11, color: 'var(--c-text-muted)' }}>
        (none)
      </p>
    )
  }
  return (
    <div className="adapter-schema">
      {present.map(([k, v]) => (
        <div key={k} className="adapter-schema__row">
          <div className="adapter-schema__name mono">{k}</div>
          <div className="adapter-schema__desc mono" style={{ whiteSpace: 'pre-wrap' }}>
            {String(v)}
          </div>
        </div>
      ))}
    </div>
  )
}

function safetyChipVariant(safety) {
  // Maps onto the existing chip-modifier palette: signal (blue), warn
  // (yellow), danger (red). Keeps the visual grammar consistent with
  // chips elsewhere in the console.
  switch (safety) {
    case 'safe':              return 'signal'
    case 'dual-use-lab-only': return 'warn'
    case 'c2-framework':      return 'danger'
    case 'destructive':       return 'danger'
    default:                  return 'signal'
  }
}
