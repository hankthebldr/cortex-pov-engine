import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { getTtps, getTtp, getTtpRuns, getScenarios, postRun } from '../../api/client.js'
import { downloadTtpLayer } from './exportNavigatorLayer.js'
import TtpEditorView from './TtpEditorView.jsx'
import DetectionTypeChip from './DetectionTypeChip.jsx'
import { tokeniserFor } from './syntaxHighlight.js'
import { runIdOf } from '../../api/ids.js'
import '../../styles/destinations/ttps.css'

// Maps a card detection-family key to the canonical detection-type chip token
// so Correlation (the XSIAM differentiator) and XQL render with their distinct
// colors in the accordion headers. See DetectionTypeChip / GAP-2.
const DETECTION_KIND_CHIP = {
  biocs:             'BIOC',
  xql_queries:       'XQL',
  correlation_rules: 'Correlation',
  iocs:              'IOC',
  analytics_modules: 'Analytics',
}


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
 * Rendered as the "TTP Cards" destination (persistent nav → Analyze).
 * Layout: a filterable master list on the left, a sticky detail rail
 * on the right once a card is selected — mirrors the PANW redesign's
 * list+rail pattern (`.design-ref/06-ttp-cards.html` /
 * `07-ttpdetail-name.html`).
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

  // Issue #59 — TTP authoring. `editorMode` is:
  //   null         → editor closed
  //   'new'        → create a new draft
  //   '<ttp_id>'   → edit existing
  const [editorMode, setEditorMode] = useState(null)

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

  const showRail = !!selectedDetail && !editorMode

  return (
    <div className="ttpb" data-testid="ttp-browser">
      <div className="view-head">
        <div>
          <h1>TTP Cards</h1>
          <div className="view-head__meta">
            {ttps.length} TTPs · {tactics.length} tactics · {platforms.length} platforms · {visible.length} visible
          </div>
        </div>
      </div>

      <p className="ttpb-intro">
        Browser over the <strong>TTP corpus</strong> — every Tactic /
        Technique / Procedure card under{' '}
        <span className="mono">detection_scanner/ttps/</span>. Each
        card pairs a MITRE technique with the deployable BIOC / XQL /
        correlation logic Cortex ships to detect it. Click any card to
        see the full chain + the tool adapters that exercise it.
      </p>

      <div className="ttpb-stats">
        <div className="ttpb-stat">
          <div className="ttpb-stat__value mono">{ttps.length}</div>
          <div className="ttpb-stat__label">TTPs</div>
        </div>
        <div className="ttpb-stat">
          <div className="ttpb-stat__value mono">{tactics.length}</div>
          <div className="ttpb-stat__label">tactics</div>
        </div>
        <div className="ttpb-stat">
          <div className="ttpb-stat__value mono">{platforms.length}</div>
          <div className="ttpb-stat__label">platforms</div>
        </div>
        <div className="ttpb-stat">
          <div className="ttpb-stat__value mono">{visible.length}</div>
          <div className="ttpb-stat__label">visible</div>
        </div>
      </div>

      {error && (
        <div className="ttpb-error mono" role="alert">{error}</div>
      )}

      <div className="ttpb-toolbar">
        <input
          type="search"
          className="ttpb-search mono"
          data-testid="ttp-search"
          placeholder="Search id · name · summary · tag · technique · actor…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search TTP corpus"
        />
        {hasActiveFilters && (
          <button
            type="button"
            className="btn ttpb-btn--sm"
            data-testid="ttp-clear-filters"
            onClick={resetFilters}
          >
            Clear
          </button>
        )}
        <button
          type="button"
          className="btn ttpb-btn--sm ttpb-btn--accent"
          data-testid="ttp-author-new"
          title="Author a new TTP card (requires CORTEXSIM_AUTHORING_ENABLED=true on the server)"
          onClick={() => setEditorMode('new')}
        >
          Author new
        </button>
      </div>

      <div className="ttpb-filters">
        <FilterRow label="status"   active={filterStatus}   options={statuses}  onChange={setFilterStatus} />
        <FilterRow label="tactic"   active={filterTactic}   options={tactics}   onChange={setFilterTactic} />
        <FilterRow label="platform" active={filterPlatform} options={platforms} onChange={setFilterPlatform} />
      </div>

      <div className={'ttpb-body' + (showRail ? ' ttpb-body--split' : '')}>
        <div className="ttpb-list">
          {loading ? (
            <div className="ttpb-empty mono">loading TTP corpus…</div>
          ) : visible.length === 0 ? (
            <div className="ttpb-empty mono">
              no TTPs match the current filters —{' '}
              <button
                type="button"
                className="btn ttpb-btn--xs"
                onClick={resetFilters}
              >
                clear filters
              </button>
            </div>
          ) : (
            visible.map((t) => (
              <TtpCard
                key={t.id}
                ttp={t}
                isSelected={t.id === selectedId}
                onSelect={() => handleSelect(t.id)}
              />
            ))
          )}
        </div>

        {showRail && (
          <TtpDetail
            detail={selectedDetail}
            runs={selectedRuns}
            onClose={() => {
              setSelectedId(null)
              setSelectedDetail(null)
              setSelectedRuns(null)
            }}
            onEdit={() => setEditorMode(selectedDetail.id)}
          />
        )}
      </div>

      {editorMode && (
        <TtpEditorView
          editingTtpId={editorMode === 'new' ? null : editorMode}
          onClose={() => setEditorMode(null)}
          onSaved={(res) => {
            // Reload the grid so the new/promoted card shows.
            setLoading(true)
            getTtps()
              .then((d) => setTtps(Array.isArray(d?.ttps) ? d.ttps : []))
              .catch(() => {})
              .finally(() => setLoading(false))
            // For promotion the card is now active — keep the editor
            // open with the saved state visible so the operator can
            // confirm before closing.
            if (res?.status === 'active' && editorMode === 'new') {
              setEditorMode(null)
            }
          }}
        />
      )}
    </div>
  )
}

/* ─── Filter chip row ───────────────────────────────────────────────── */

function FilterRow({ label, active, options, onChange }) {
  return (
    <div className="ttpb-filter-row">
      <span className="ttpb-filter-row__label mono">{label}</span>
      <button
        type="button"
        className={'ttpb-chip' + (active === 'all' ? ' is-active' : '')}
        onClick={() => onChange('all')}
      >
        All
      </button>
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          className={'ttpb-chip' + (active === opt ? ' is-active' : '')}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  )
}

/* ─── TTP row card ──────────────────────────────────────────────────── */

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
      className={'ttpb-row' + (isSelected ? ' ttpb-row--selected' : '')}
      role="button"
      tabIndex={0}
      data-testid={`ttp-card-${ttp.id}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() }
      }}
    >
      <div className="ttpb-row__head">
        <span className="ttpb-row__id mono">{ttp.id}</span>
        <span className="ttpb-chip--tag ttpb-chip--tag-status">{ttp.status}</span>
        <span className="ttpb-row__name">{ttp.name}</span>
        <span className="ttpb-row__count mono">
          {totalDetections} det{totalDetections === 1 ? '' : 's'}
        </span>
      </div>
      <div className="ttpb-row__cat mono">
        {ttp.simulation_class || 'other'} · {ttp.kill_chain_phase || '—'}
      </div>
      <p className="ttpb-row__summary">
        {(ttp.summary || '').slice(0, 200)}
        {(ttp.summary || '').length > 200 ? '…' : ''}
      </p>
      <div className="ttpb-row__meta">
        {techniques.length > 0 && (
          <div className="ttpb-row__chips">
            {techniques.slice(0, 4).map((t) => (
              <span key={t} className="ttpb-chip--tag mono">{t}</span>
            ))}
            {techniques.length > 4 && (
              <span className="ttpb-row__more mono">+{techniques.length - 4}</span>
            )}
          </div>
        )}
        {(tactics.length > 0 || platforms.length > 0) && (
          <div className="ttpb-row__chips">
            {tactics.map((t) => (
              <span key={t} className="ttpb-chip--tag ttpb-chip--tag-signal mono">{t}</span>
            ))}
            {platforms.map((p) => (
              <span key={p} className="ttpb-chip--tag ttpb-chip--tag-muted mono">{p}</span>
            ))}
          </div>
        )}
        {/* Detection-kind chips — Correlation/XQL stand out at a glance so a
            DC can spot stitching coverage without opening the card. */}
        {totalDetections > 0 && (
          <div className="ttpb-row__det-kinds">
            {[
              { type: 'BIOC',        n: counts.biocs },
              { type: 'XQL',         n: counts.xql_queries },
              { type: 'Correlation', n: counts.correlation_rules },
              { type: 'Analytics',   n: counts.analytics_modules },
              { type: 'IOC',         n: counts.iocs },
            ].filter((k) => k.n > 0).map((k) => (
              <DetectionTypeChip
                key={k.type}
                type={k.type}
                title={`${k.n} ${k.type} detection${k.n === 1 ? '' : 's'}`}
              />
            ))}
          </div>
        )}
      </div>
    </article>
  )
}

/* ─── TTP detail rail ───────────────────────────────────────────────── */

function TtpDetail({ detail, runs, onClose, onEdit }) {
  if (detail._error) {
    return (
      <aside className="ttpb-detail" data-testid="ttp-detail">
        <div className="ttpb-detail__head">
          <div>
            <div className="ttpb-detail__eyebrow mono">TTP detail</div>
            <h3 className="ttpb-detail__title">Load failed</h3>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <p className="ttpb-error mono">{detail._error}</p>
      </aside>
    )
  }

  const [launcherOpen, setLauncherOpen] = useState(false)

  const identity = detail.identity || {}
  const metadata = detail.metadata || {}
  const mitre    = detail.mitre_attack || {}
  const threat   = detail.threat_context || {}
  const detections = detail.detections || {}
  const panw     = detail.panw_mapping || {}
  const adapters = detail.referenced_by_adapters || []
  const execution = detail.execution || {}
  const remediation = detail.remediation_guidance || null

  const techniques = mitre.techniques || []
  const actors     = threat.actors || []
  const tags       = metadata.tags || []
  const products   = panw.products || []

  const totalDetections =
    (detections.iocs || []).length +
    (detections.biocs || []).length +
    (detections.xql_queries || []).length +
    (detections.correlation_rules || []).length +
    (detections.analytics_modules || []).length

  // Real, already-fetched fields the pre-restyle panel discarded — a
  // compact stat row so the operator doesn't have to scroll to size up
  // the card. Only tiles with a real value render.
  const stats = [
    { k: 'Detections', v: totalDetections },
    { k: 'Techniques', v: techniques.length },
    execution.target_platform    && { k: 'Platform',  v: execution.target_platform },
    execution.privilege_required && { k: 'Privilege',  v: execution.privilege_required },
  ].filter(Boolean)

  const teardown = execution?.cleanup?.code || execution?.cleanup || null

  return (
    <aside className="ttpb-detail" data-testid="ttp-detail">
      <div className="ttpb-detail__head">
        <div>
          <div className="ttpb-detail__eyebrow mono">
            {detail.id} · {detail.status}
          </div>
          <h3 className="ttpb-detail__title">
            {identity.name || detail.id}
          </h3>
        </div>
        <button type="button" className="btn ttpb-btn--xs" onClick={onClose}>Close</button>
      </div>

      <div className="ttpb-detail__actions">
        <button
          type="button"
          className="btn ttpb-btn--sm ttpb-btn--accent"
          data-testid="ttp-launch-all"
          title="Launch every scenario whose expected_detections cite this TTP"
          onClick={() => setLauncherOpen(true)}
        >
          Launch all&hellip;
        </button>
        {techniques.length > 0 && (
          <button
            type="button"
            className="btn ttpb-btn--sm"
            data-testid="ttp-export-navigator"
            title="Download a MITRE ATT&CK Navigator layer scoped to this TTP — paste into the customer's Navigator for the briefing"
            onClick={() => downloadTtpLayer(detail)}
          >
            Export ATT&amp;CK layer
          </button>
        )}
        {onEdit && (
          <button
            type="button"
            className="btn ttpb-btn--sm"
            data-testid="ttp-edit"
            onClick={onEdit}
            title="Open this TTP in the authoring editor"
          >
            Edit&hellip;
          </button>
        )}
      </div>

      {launcherOpen && (
        <LaunchAllModal
          ttpId={detail.id}
          onClose={() => setLauncherOpen(false)}
        />
      )}

      {identity.summary && (
        <DetailSection label="Summary">
          <p className="ttpb-detail__summary">{identity.summary}</p>
        </DetailSection>
      )}

      {stats.length > 0 && (
        <div className="ttpb-detail__stats">
          {stats.map((s) => (
            <div key={s.k} className="ttpb-stat-tile">
              <div className="ttpb-stat-tile__k mono">{s.k}</div>
              <div className="ttpb-stat-tile__v mono">{s.v}</div>
            </div>
          ))}
        </div>
      )}

      {techniques.length > 0 && (
        <DetailSection label="MITRE ATT&CK">
          <table className="ttpb-table">
            <thead>
              <tr>
                <th>Technique</th>
                <th>Name</th>
                <th>Tactics</th>
              </tr>
            </thead>
            <tbody>
              {techniques.map((t, i) => (
                <tr key={i}>
                  <td className="mono">
                    {t.subtechnique_id || t.technique_id}
                  </td>
                  <td>{t.name}</td>
                  <td className="mono">
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
          <div className="ttpb-chip-row">
            {actors.map((a, i) => (
              <span
                key={i}
                className="ttpb-chip--tag"
                title={(a.aliases || []).join(' · ')}
              >
                {a.name}
                {a.unit42_actor_id && (
                  <span className="mono ttpb-detail__meta-tag">
                    u42
                  </span>
                )}
                {a.mitre_group_id && (
                  <span className="mono ttpb-detail__meta-tag">
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
          <div className="ttpb-chip-row ttpb-chip-row--tight">
            {products.map((p, i) => (
              <span key={i} className="ttpb-chip--tag ttpb-chip--tag-signal">
                {p.module}
                {p.submodule && (
                  <span className="mono ttpb-detail__meta-tag ttpb-detail__meta-tag--loose">
                    / {p.submodule}
                  </span>
                )}
              </span>
            ))}
          </div>
        </DetailSection>
      )}

      {remediation && (remediation.preventive_controls?.length > 0 || remediation.detection_engineering?.length > 0 || remediation.response_playbook) && (
        <DetailSection label="Remediation guidance">
          <div className="ttpb-callout ttpb-callout--warn">
            {remediation.preventive_controls?.length > 0 && (
              <>
                <div className="ttpb-callout__label mono">Preventive controls</div>
                <ul className="ttpb-callout__list">
                  {remediation.preventive_controls.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </>
            )}
            {remediation.detection_engineering?.length > 0 && (
              <>
                <div className="ttpb-callout__label mono">Detection engineering</div>
                <ul className="ttpb-callout__list">
                  {remediation.detection_engineering.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </>
            )}
            {remediation.response_playbook && (
              <div className="ttpb-callout__playbook mono">
                playbook: {remediation.response_playbook}
              </div>
            )}
          </div>
        </DetailSection>
      )}

      <DetailSection label="Recent runs">
        <RunHistory runs={runs} />
      </DetailSection>

      {adapters.length > 0 && (
        <DetailSection label="Referenced by tool adapters">
          <p className="mono ttpb-detail__note">
            Adapters in <span className="mono">tools/packs/</span> that
            cite this TTP in <span className="mono">ttp_refs[]</span>:
          </p>
          <div className="ttpb-chip-row ttpb-chip-row--tight">
            {adapters.map((a) => (
              <span
                key={a.adapter_id}
                className="ttpb-chip--tag"
                title={`${a.adapter_id} · T${a.tier} · ${a.category} · ${a.safety_class}`}
                data-testid={`ttp-adapter-ref-${a.adapter_id}`}
              >
                {a.name}
                <span className="mono ttpb-detail__meta-tag">
                  T{a.tier}
                </span>
              </span>
            ))}
          </div>
        </DetailSection>
      )}

      {teardown && (
        <DetailSection label="Teardown">
          <pre className="ttpb-teardown mono">{teardown}</pre>
        </DetailSection>
      )}

      {tags.length > 0 && (
        <DetailSection label="Tags">
          <div className="ttpb-chip-row ttpb-chip-row--tight">
            {tags.map((t) => (
              <span key={t} className="ttpb-chip--tag ttpb-chip--tag-xs">{t}</span>
            ))}
          </div>
        </DetailSection>
      )}
    </aside>
  )
}

function DetailSection({ label, children }) {
  return (
    <div className="ttpb-section">
      <div className="ttpb-section__label mono">{label}</div>
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
      <div className="ttpb-empty mono" data-testid="ttp-runs-loading">
        loading run history…
      </div>
    )
  }
  if (runs._error) {
    return (
      <div className="ttpb-error mono">
        couldn't load run history
      </div>
    )
  }
  const rows = runs.runs || []
  if (rows.length === 0) {
    return (
      <div className="ttpb-empty mono" data-testid="ttp-runs-empty">
        no runs have exercised this TTP yet
      </div>
    )
  }
  return (
    <table
      className="ttpb-table"
      data-testid="ttp-runs-table"
    >
      <thead>
        <tr>
          <th>Run</th>
          <th>Scenario</th>
          <th>Started</th>
          <th className="th--num">Coverage</th>
          <th className="th--num">Min MTTD</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.run_id}
            className="ttpb-run-row"
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
            title={`Open run ${r.run_id} in the validation wizard`}
          >
            <td className="mono">{r.run_id}</td>
            <td className="mono">{r.scenario_id}</td>
            <td className="mono td--sm">
              {formatStartedAt(r.started_at)}
            </td>
            <td className="mono td--num">
              <span
                className={
                  'ttpb-badge ' +
                  (r.observed === r.expected
                    ? 'ttpb-badge--pass'
                    : r.observed === 0
                      ? 'ttpb-badge--fail'
                      : 'ttpb-badge--partial')
                }
              >
                {r.observed}/{r.expected}
              </span>
            </td>
            <td className="mono td--num">
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
      <div className="ttpb-empty mono">
        no detections shipped with this card
      </div>
    )
  }
  return (
    <div className="ttpb-det-groups">
      {kinds.map(({ key, label, bodyKey }) => {
        const items = detections[key] || []
        if (items.length === 0) return null
        return (
          <div key={key} className="ttpb-det-group">
            <div className="ttpb-det-group__head">
              <DetectionTypeChip type={DETECTION_KIND_CHIP[key] || label} />
              <span className="ttpb-det-group__count mono">
                {items.length}
              </span>
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
    <div className={'ttpb-det-item' + (expanded ? ' is-expanded' : '')}>
      <button
        type="button"
        className="ttpb-det-item__head"
        data-testid={`ttp-det-${kind}-${index}`}
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <div className="ttpb-det-item__head-row">
          <span className="mono ttpb-det-item__caret">
            {expanded ? '▼' : '▶'}
          </span>
          <span className="ttpb-det-item__name">{name}</span>
          {severity && (
            <span className="ttpb-chip--tag ttpb-chip--tag-xs">{severity}</span>
          )}
          {detId && (
            <span
              className="mono ttpb-det-item__id"
              title={detId}
            >
              {detId}
            </span>
          )}
        </div>
        {desc && !expanded && (
          <div className="ttpb-det-item__preview">
            {desc}
          </div>
        )}
      </button>
      {expanded && (
        <div className="ttpb-det-item__body">
          {desc && (
            <p className="ttpb-det-item__desc">
              {desc}
            </p>
          )}
          {body ? (
            <>
              <div className="ttpb-det-item__body-head">
                <span className="mono ttpb-det-item__body-key">
                  {bodyKey}
                </span>
                <button
                  type="button"
                  className="btn ttpb-btn--copy"
                  onClick={handleCopy}
                  data-testid={`ttp-det-copy-${kind}-${index}`}
                >
                  {copied ? '✓ copied' : 'Copy'}
                </button>
              </div>
              <pre
                className="mono ttpb-pre"
                data-testid={`ttp-det-body-${kind}-${index}`}
              >
                {tokeniserFor(kind)(body).map((tok, ti) => (
                  <span key={ti} className={`syn syn-${tok.type}`}>
                    {tok.text}
                  </span>
                ))}
              </pre>
            </>
          ) : (
            <p className="mono ttpb-det-item__no-body">
              (no body in corpus entry)
            </p>
          )}
        </div>
      )}
    </div>
  )
}

/* ─── Launch-all modal ─────────────────────────────────────────────── */

/**
 * Modal — load scenarios citing this TTP, let the operator pick a
 * subset + identity + mode, then fire one POST /api/run per
 * selected scenario. The first successful launch emits
 * cortex:navigate-run so App.jsx jumps to the validation wizard.
 *
 * Closes the action loop the TTP browser opened — the operator can
 * exercise this technique end-to-end without leaving the panel.
 */
function LaunchAllModal({ ttpId, onClose }) {
  const [scenarios, setScenarios] = useState(null)   // null = loading, [] = empty, [...] = loaded
  const [error, setError]         = useState(null)
  const [selected, setSelected]   = useState(() => new Set())
  const [mode, setMode]           = useState('push')
  const [identity, setIdentity]   = useState('')
  const [launching, setLaunching] = useState(false)
  const [launchSummary, setLaunchSummary] = useState(null)  // { ok: [...], failed: [...] }

  useEffect(() => {
    let cancelled = false
    getScenarios({ ttp_ref: ttpId })
      .then((s) => {
        if (cancelled) return
        const arr = Array.isArray(s) ? s : []
        setScenarios(arr)
        // Pre-select everything by default — the operator's intent is
        // typically "run them all", and unchecking is one click.
        setSelected(new Set(arr.map((sc) => sc.scenario_id)))
      })
      .catch((e) => { if (!cancelled) setError(e?.message || 'Failed to load scenarios') })
    return () => { cancelled = true }
  }, [ttpId])

  const toggle = (id) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const launchAll = async () => {
    setLaunching(true)
    const ok = []
    const failed = []
    // Fire in parallel — the orchestrator queues runs independently;
    // serial launch would just stall the operator.
    const calls = [...selected].map((scenario_id) =>
      postRun({
        scenario_id,
        mode,
        identity: identity || undefined,
      })
        .then((r) => ok.push({ scenario_id, run_id: runIdOf(r) }))
        .catch((e) => failed.push({ scenario_id, error: e?.message || String(e) }))
    )
    await Promise.all(calls)
    setLaunching(false)
    setLaunchSummary({ ok, failed })

    // Drill into the first successful run — matches the
    // cortex:navigate-run pattern the TTP run-history rows use.
    if (ok.length > 0 && ok[0].run_id) {
      window.dispatchEvent(new CustomEvent('cortex:navigate-run', {
        detail: { runId: ok[0].run_id },
      }))
    }
  }

  return (
    <div
      className="ttpb-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Launch scenarios for this TTP"
      data-testid="ttp-launcher-modal"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="ttpb-modal">
        <div className="ttpb-modal__head">
          <div>
            <div className="ttpb-detail__eyebrow mono">{ttpId}</div>
            <h3 className="ttpb-modal__title">Launch all citing scenarios</h3>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>

        {scenarios === null && !error && (
          <div className="ttpb-empty mono">loading scenarios…</div>
        )}
        {error && (
          <div className="ttpb-error mono" role="alert">{error}</div>
        )}
        {scenarios && scenarios.length === 0 && !error && (
          <div className="ttpb-empty mono" data-testid="ttp-launcher-empty">
            no scenarios cite this TTP in their expected_detections — author one or
            add a <span className="mono">ttp_ref</span> entry to an existing
            step.
          </div>
        )}

        {scenarios && scenarios.length > 0 && (
          <>
            <div className="ttpb-modal__table-wrap">
              <table className="ttpb-table">
                <thead>
                  <tr>
                    <th className="th--check"></th>
                    <th>Scenario</th>
                    <th>Plane</th>
                    <th>Technique</th>
                  </tr>
                </thead>
                <tbody>
                  {scenarios.map((s) => (
                    <tr key={s.scenario_id} data-testid={`ttp-launcher-row-${s.scenario_id}`}>
                      <td>
                        <input
                          type="checkbox"
                          checked={selected.has(s.scenario_id)}
                          onChange={() => toggle(s.scenario_id)}
                          aria-label={`Include ${s.scenario_id}`}
                        />
                      </td>
                      <td className="mono">
                        <div>{s.scenario_id}</div>
                        <div className="ttpb-modal__sub">{s.name}</div>
                      </td>
                      <td className="mono">{s.plane}</td>
                      <td className="mono">{s.mitre_technique || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="ttpb-modal__row">
              <label className="ttpb-modal__field">
                Mode:{' '}
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value)}
                  data-testid="ttp-launcher-mode"
                  className="ttpb-modal__select"
                >
                  <option value="push">push</option>
                  <option value="pull">pull</option>
                </select>
              </label>
              <label className="ttpb-modal__field ttpb-modal__field--grow">
                Identity (optional):{' '}
                <input
                  type="text"
                  value={identity}
                  onChange={(e) => setIdentity(e.target.value)}
                  placeholder="leave blank for scenario default"
                  data-testid="ttp-launcher-identity"
                  className="ttpb-modal__input"
                />
              </label>
            </div>

            <div className="ttpb-modal__footer-row">
              <span className="mono ttpb-modal__count">
                {selected.size} of {scenarios.length} selected
              </span>
              <button
                type="button"
                className="btn ttpb-btn--accent"
                data-testid="ttp-launcher-confirm"
                disabled={launching || selected.size === 0}
                onClick={launchAll}
              >
                {launching ? 'Launching…' : `Launch ${selected.size}`}
              </button>
            </div>
          </>
        )}

        {launchSummary && (
          <div className="mono ttpb-modal__summary" data-testid="ttp-launcher-summary">
            <div>launched <strong>{launchSummary.ok.length}</strong>, failed <strong>{launchSummary.failed.length}</strong></div>
            {launchSummary.failed.length > 0 && (
              <ul className="ttpb-modal__fail-list">
                {launchSummary.failed.map((f) => (
                  <li key={f.scenario_id} className="ttpb-modal__fail-item">
                    {f.scenario_id}: {f.error}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
