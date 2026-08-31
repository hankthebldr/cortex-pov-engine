import React, { useEffect, useMemo, useState } from 'react'
import { getToolAdapters, getToolAdapter } from '../../api/client.js'
import useShelf from './useShelf.js'
import { SUPPLY, SUPPLY_LABEL, supplyOf, shortDigest, scenarioIdsFrom } from './supplyState.js'
import PayloadShelfBanner from './PayloadShelfBanner.jsx'
import StagePayloadDialog from './StagePayloadDialog.jsx'
import PayloadComposer from './PayloadComposer.jsx'
import ProvenanceBlock from './ProvenanceBlock.jsx'

/**
 * ToolAdapterCatalog — "Tools & Payloads".
 *
 * Distinct from ``AdapterRegistryView``: that lists the **EAL plugins** the
 * engine uses to generate signal. This lists the **tool adapters** — the static
 * catalog of tools a scenario references via ``external_tools[].adapter_ref``.
 *
 * WHAT THIS SURFACE GAINED, AND WHY IT LIVES HERE
 * ----------------------------------------------
 * Every tier-4 pack installs its tool from the public internet ON THE TARGET
 * HOST at dispatch. That is the first thing a customer's default-deny network
 * blocks, and a step whose tool never arrived runs anyway and produces a
 * manufactured false negative in a POV report. So each card now carries its
 * SUPPLY state (staged here vs fetched there) and, where the pack declares an
 * artifact, a one-click stage onto this SimCore.
 *
 * Staging is an inventory operation on the shelf; composition is a per-run
 * parameter. Both are properties of an adapter, which is why this is the same
 * destination rather than an eleventh one — a separate "Payloads" surface would
 * list `linpeas.sh` in one place and `TOOL-LINPEAS` in another and make the DC
 * hold the join in their head.
 *
 * Filters, selection and the open panel all live in URL params so a DC can send
 * a colleague "the twelve tools this POV needs, three of them unstaged".
 */
export default function ToolAdapterCatalog({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const [adapters, setAdapters] = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [selectedDetail, setSelectedDetail] = useState(null)

  const shelf = useShelf({ adapters })

  const selectedId    = params.tool || null
  const panel         = params.panel || null
  const filterPlane   = params.plane || 'all'
  const filterTier    = params.tier || 'all'
  const filterSafety  = params.safety || 'all'
  const filterCategory = params.category || 'all'
  const filterSupply  = params.supply || 'all'
  const search        = params.q || ''

  useEffect(() => {
    setLoading(true)
    getToolAdapters()
      .then((d) => setAdapters(Array.isArray(d?.adapters) ? d.adapters : []))
      .catch((e) => setError(e?.message || 'Failed to load tool-adapter catalog'))
      .finally(() => setLoading(false))
  }, [])

  // Detail fetch keyed off the URL, so a deep link opens the panel too.
  useEffect(() => {
    if (!selectedId) { setSelectedDetail(null); return undefined }
    let cancelled = false
    setSelectedDetail(null)
    getToolAdapter(selectedId)
      .then((d) => { if (!cancelled) setSelectedDetail(d) })
      .catch((e) => { if (!cancelled) setSelectedDetail({ _error: e?.message || 'Failed to load adapter detail' }) })
    return () => { cancelled = true }
  }, [selectedId])

  const planes = useMemo(
    () => Array.from(new Set(adapters.flatMap((a) => a.planes || []))).sort(), [adapters])
  const tiers = useMemo(
    () => Array.from(new Set(adapters.map((a) => a.tier))).sort((x, y) => x - y), [adapters])
  const safetyClasses = useMemo(
    () => Array.from(new Set(adapters.map((a) => a.safety_class))).sort(), [adapters])
  const categories = useMemo(
    () => Array.from(new Set(adapters.map((a) => a.category))).sort(), [adapters])

  const supplyByAdapter = useMemo(() => {
    const out = {}
    for (const a of adapters) out[a.adapter_id] = supplyOf(a, shelf.shelf)
    return out
  }, [adapters, shelf.shelf])

  // Scenario scoping — `?scenario=SIM-CDR-001` narrows the grid to that
  // scenario's adapters. Sourced from the shelf's own `used_by` annotation
  // (which artifact is referenced by which scenario), the only adapter↔scenario
  // join the API exposes.
  const scenarioScope = params.scenario || null
  const scopedAdapterIds = useMemo(() => {
    if (!scenarioScope) return null
    const ids = new Set()
    for (const d of shelf.shelf.declared || []) {
      if (scenarioIdsFrom(d.used_by).includes(scenarioScope) && d.adapter_id) ids.add(d.adapter_id)
    }
    return ids
  }, [scenarioScope, shelf.shelf])

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return adapters.filter((a) => {
      if (filterPlane    !== 'all' && !(a.planes || []).includes(filterPlane)) return false
      if (filterTier     !== 'all' && a.tier !== Number(filterTier))           return false
      if (filterSafety   !== 'all' && a.safety_class !== filterSafety)         return false
      if (filterCategory !== 'all' && a.category !== filterCategory)           return false
      if (filterSupply   !== 'all' && supplyByAdapter[a.adapter_id]?.state !== filterSupply) return false
      if (scopedAdapterIds && !scopedAdapterIds.has(a.adapter_id))             return false
      if (q) {
        const hay = `${a.name} ${a.adapter_id} ${(a.tags || []).join(' ')} ${a.category}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [adapters, filterPlane, filterTier, filterSafety, filterCategory, filterSupply,
      search, supplyByAdapter, scopedAdapterIds])

  const resetFilters = () => setParams({
    plane: null, tier: null, safety: null, category: null, supply: null, q: null, scenario: null,
  })

  const selectedSupply = selectedDetail && !selectedDetail._error
    ? supplyOf(selectedDetail, shelf.shelf)
    : null

  // Scenarios that reference the selected adapter, for the composer's picker.
  const candidateScenarioIds = useMemo(() => {
    if (!selectedId) return []
    const d = (shelf.shelf.declared || []).find((x) => x.adapter_id === selectedId)
    return scenarioIdsFrom(d?.used_by)
  }, [selectedId, shelf.shelf])

  return (
    <div className="adapter-registry tools-destination" data-testid="tool-adapter-catalog">
      <div className="tools-destination__head">
        <div className="tools-destination__head-copy">
          <div className="tools-destination__accent-bar" aria-hidden="true" />
          <div className="tools-destination__eyebrow mono">Analyze</div>
          <h1 className="tools-destination__title">Tools &amp; Payloads</h1>
        </div>
      </div>

      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          Every offensive and defensive tool CortexSim can reference from a scenario via{' '}
          <strong>external_tools[].adapter_ref</strong>, with the one fact a POV depends on:{' '}
          <strong>where the bytes come from at run time</strong>. Tier-4 tools install themselves
          from the public internet <em>on the target host</em> unless their artifact is staged on
          this SimCore.
        </p>
        <div className="adapter-registry__stats">
          <Stat value={adapters.length} label="adapters" />
          <Stat value={shelf.counts.staged} label="staged" testid="stat-staged" />
          <Stat value={shelf.counts.target_egress} label="target egress" testid="stat-egress" />
          <Stat value={shelf.counts.unknown} label="unknown" testid="stat-unknown" />
          <Stat value={visible.length} label="visible" />
        </div>
      </div>

      {error && <div className="adapter-registry__error mono" role="alert">{error}</div>}

      <PayloadShelfBanner
        counts={shelf.counts}
        shelf={shelf.shelf}
        available={shelf.available}
        error={shelf.error}
        loading={shelf.loading && shelf.available === null}
        onShowUnstaged={() => setParams({ supply: SUPPLY.UNSTAGED })}
        onRetry={shelf.refresh}
      />

      {scenarioScope && (
        <div className="adapter-registry__scope mono" data-testid="adapter-scenario-scope">
          scoped to <strong>{scenarioScope}</strong>
          {scopedAdapterIds && scopedAdapterIds.size === 0 && (
            <> — no staged-artifact adapter is annotated as used by it, so this list is empty rather
              than wrong</>
          )}
          <button type="button" className="btn btn--xs" onClick={() => setParams({ scenario: null })}>
            clear
          </button>
        </div>
      )}

      <div className="adapter-registry__search">
        <input
          type="search"
          className="adapter-registry__search-input"
          placeholder="search tools — name, adapter id, tag"
          aria-label="Search tool adapters"
          value={search}
          onChange={(e) => setParams({ q: e.target.value || null })}
          data-testid="adapter-search"
        />
      </div>

      <FilterRow label="plane" active={filterPlane} options={planes}
        onChange={(v) => setParams({ plane: v === 'all' ? null : v })} />
      <FilterRow label="tier" active={filterTier} options={tiers.map(String)}
        onChange={(v) => setParams({ tier: v === 'all' ? null : v })} formatOption={(t) => `T${t}`} />
      <FilterRow label="safety" active={filterSafety} options={safetyClasses}
        onChange={(v) => setParams({ safety: v === 'all' ? null : v })} />
      <FilterRow label="category" active={filterCategory} options={categories}
        onChange={(v) => setParams({ category: v === 'all' ? null : v })} />
      <FilterRow
        label="supply"
        active={filterSupply}
        options={[SUPPLY.STAGED, SUPPLY.UNSTAGED, SUPPLY.RUNTIME_FETCH, SUPPLY.NOT_APPLICABLE, SUPPLY.UNKNOWN]}
        onChange={(v) => setParams({ supply: v === 'all' ? null : v })}
        formatOption={(s) => SUPPLY_LABEL[s] || s}
      />

      {loading ? (
        <div className="coverage__empty mono">loading tool-adapter catalog…</div>
      ) : visible.length === 0 ? (
        <div className="coverage__empty mono">
          no adapters match the current filters —{' '}
          <button
            type="button"
            className="btn adapter-registry__clear-btn"
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
              supply={supplyByAdapter[a.adapter_id]}
              isSelected={a.adapter_id === selectedId}
              onSelect={() => setParams({ tool: a.adapter_id, panel: null })}
              onStage={() => setParams({ tool: a.adapter_id, panel: 'stage' })}
            />
          ))}
        </div>
      )}

      {selectedDetail && (
        <ToolAdapterDetail
          detail={selectedDetail}
          supply={selectedSupply}
          shelf={shelf.shelf}
          onClose={() => setParams({ tool: null, panel: null })}
          onStage={() => setParams({ panel: 'stage' })}
          onCompose={() => setParams({ panel: 'compose' })}
          onNavigateAdapter={(id) => setParams({ tool: id, panel: null })}
          onNavigateTtp={(ttpId) => onNavigate('ttps', { ttp: ttpId })}
        />
      )}

      {panel === 'stage' && selectedDetail && !selectedDetail._error && selectedSupply && (
        <StagePayloadDialog
          adapter={selectedDetail}
          supply={selectedSupply}
          job={shelf.jobs[selectedId] || null}
          writable={shelf.shelf.writable}
          distDir={shelf.shelf.distDir}
          onStage={() => shelf.stage(selectedId)}
          onClose={() => { shelf.clearJob(selectedId); setParams({ panel: null }) }}
          onCompose={() => { shelf.clearJob(selectedId); setParams({ panel: 'compose' }) }}
        />
      )}

      {panel === 'compose' && selectedDetail && !selectedDetail._error && selectedSupply && (
        <PayloadComposer
          adapter={selectedDetail}
          supply={selectedSupply}
          candidateScenarioIds={candidateScenarioIds}
          initialScenarioId={params.scenario || null}
          onClose={() => setParams({ panel: null })}
          onArm={(scenarioId, plan) => onNavigate('guided', {
            arm: scenarioId,
            plan: encodePlan(scenarioId, plan),
          })}
        />
      )}

      {panel && !selectedId && (
        <div className="adapter-registry__error mono" role="alert">
          ?panel={panel} needs a ?tool=&lt;adapter_id&gt; to act on — nothing was opened.
        </div>
      )}
    </div>
  )
}

/**
 * The plan rides the URL so the guided flow is deep-linkable and reload-safe.
 * Over 1500 chars it is stashed in sessionStorage and only a key travels — a
 * truncated URL is a payload that quietly runs under the wrong name.
 */
export function encodePlan(scenarioId, plan) {
  const artifacts = (plan?.artifacts || []).map((a) => ({
    adapter_id: a.adapter_id, payload_name: a.name, sha256: a.sha256,
    dest_path: a.dest_path, mode: a.mode || '0755', renamed: Boolean(a.renamed),
  }))
  const json = JSON.stringify({ v: 1, scenario_id: scenarioId, artifacts })
  const encoded = btoa(unescape(encodeURIComponent(json))).replace(/=+$/, '')
  if (encoded.length <= 1500) return encoded
  const key = `cortexsim.payloadPlan.${scenarioId}`
  try { window.sessionStorage.setItem(key, json) } catch { /* private mode */ }
  return `ref:${key}`
}

export function decodePlan(encoded) {
  if (!encoded) return null
  try {
    if (encoded.startsWith('ref:')) {
      const raw = window.sessionStorage.getItem(encoded.slice(4))
      return raw ? JSON.parse(raw) : null
    }
    return JSON.parse(decodeURIComponent(escape(atob(encoded))))
  } catch {
    return null
  }
}

function Stat({ value, label, testid }) {
  return (
    <div className="stack-coverage__stat" data-testid={testid}>
      <div className="stack-coverage__stat-value mono">{value}</div>
      <div className="stack-coverage__stat-label">{label}</div>
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

function ToolAdapterCard({ adapter, supply, isSelected, onSelect, onStage }) {
  const tids    = adapter.expected_techniques || []
  const planes  = adapter.planes || []
  const safety  = adapter.safety_class || ''

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
      <div className="adapter-card__top">
        <span className="adapter-card__id mono">{adapter.adapter_id}</span>
        {/* Supply first — before this work the first thing a DC read was the
            licence, which says nothing about whether the tool will be there. */}
        <span
          className={supply?.chipClass || 'chip'}
          data-testid={`supply-chip-${adapter.adapter_id}`}
          title={supply?.egressLabel}
        >
          {supply?.label || SUPPLY_LABEL[SUPPLY.UNKNOWN]}
        </span>
      </div>
      <div className="adapter-card__head">
        <div className="adapter-card__name mono">{adapter.name}</div>
        <div className="adapter-card__version mono">v{adapter.version}</div>
      </div>
      <div className="adapter-card__category mono">
        T{adapter.tier} · {adapter.category} · {adapter.target_platform || 'any'}
      </div>

      <div className="adapter-card__supply">
        {supply?.actionable && (
          <button
            type="button"
            className="btn btn--xs"
            data-testid={`stage-button-${adapter.adapter_id}`}
            onClick={(e) => { e.stopPropagation(); onStage() }}
          >
            Stage ▸
          </button>
        )}
      </div>
      <div className="adapter-card__egress mono">{supply?.egressLabel}</div>

      {supply?.state === SUPPLY.STAGED && (
        <div className="adapter-card__provenance mono">
          sha256 {shortDigest(supply.sha256)} · {supply.license || '—'}
          {supply.pinned === false && <span className="chip chip--pending adapter-card__unpinned">UNPINNED</span>}
        </div>
      )}

      <div className="adapter-card__desc">
        <span className={'chip chip--' + safetyChipVariant(safety)} title={`safety class: ${safety}`}>
          {safety}
        </span>
        {' '}
        <span className="mono adapter-card__license">
          · {adapter.license}
        </span>
      </div>
      <div className="adapter-card__meta">
        {planes.length > 0 && (
          <div className="adapter-card__tids">
            {planes.map((p) => (
              <span key={p} className="chip chip--signal tool-adapter-catalog__chip-sm">{p}</span>
            ))}
          </div>
        )}
        {tids.length > 0 && (
          <div className="adapter-card__tids adapter-card__tids--gap">
            {tids.slice(0, 6).map((t) => (
              <span key={t} className="chip tool-adapter-catalog__chip-sm">{t}</span>
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

function ToolAdapterDetail({
  detail, supply, shelf, onClose, onStage, onCompose, onNavigateAdapter, onNavigateTtp,
}) {
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

      {/* SUPPLY FIRST. "Will this tool be there, and who has to reach the
          internet for it" outranks the repo URL. */}
      <DetailSection label="Supply">
        <SupplyDetail
          supply={supply}
          detail={detail}
          install={install}
          shelf={shelf}
          onStage={onStage}
          onCompose={onCompose}
        />
      </DetailSection>

      <DetailSection label="Upstream">
        <div className="mono tool-adapter-catalog__repo">
          <a href={upstream.repo} target="_blank" rel="noreferrer">{upstream.repo}</a>
        </div>
        <div className="mono tool-adapter-catalog__repo-meta">
          {upstream.license} · {upstream.attribution}
        </div>
      </DetailSection>

      <DetailSection label="Cortex signal">
        <div className="tool-adapter-catalog__chip-row">
          {(detail.cortex_signal?.planes || []).map((p) => (
            <span key={p} className="chip chip--signal">{p}</span>
          ))}
        </div>
        {(detail.cortex_signal?.expected_techniques || []).length > 0 && (
          <div className="tool-adapter-catalog__chip-row tool-adapter-catalog__chip-row--top">
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
            ['artifact.filename',        install.artifact?.filename],
            ['artifact.url',             install.artifact?.url],
            ['artifact.sha256',          install.artifact?.sha256],
            ['artifact.stage_path',      install.artifact?.stage_path],
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
            <div className="competitive__detail-label competitive__detail-label--top">default_args</div>
            <pre className="mono tool-adapter-catalog__args-pre">
              {JSON.stringify(invoke.default_args, null, 2)}
            </pre>
          </>
        )}
      </DetailSection>

      {cleanup.commands && cleanup.commands.length > 0 && (
        <DetailSection label="Cleanup">
          <ul className="mono tool-adapter-catalog__cleanup-list">
            {cleanup.commands.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </DetailSection>
      )}

      {(ttpRefs.length > 0 || equivs.length > 0) && (
        <DetailSection label="Cross-references">
          {ttpRefs.length > 0 && (
            <div className="tool-adapter-catalog__xref-block">
              <span className="mono tool-adapter-catalog__xref-label">ttp_refs:</span>
              {ttpRefs.map((r) => (
                <button
                  key={r}
                  type="button"
                  className="chip chip--clickable tool-adapter-catalog__xref-chip"
                  title="Open this TTP card"
                  data-testid={`ttp-ref-chip-${r}`}
                  onClick={() => onNavigateTtp && onNavigateTtp(r)}
                >
                  {r}
                </button>
              ))}
            </div>
          )}
          {equivs.length > 0 && (
            <div>
              <span className="mono tool-adapter-catalog__xref-label">equivalents:</span>
              {equivs.map((r) => (
                <button
                  key={r}
                  type="button"
                  className="chip chip--clickable tool-adapter-catalog__xref-chip"
                  title="Open this adapter's detail panel"
                  data-testid={`equivalent-chip-${r}`}
                  onClick={() => onNavigateAdapter && onNavigateAdapter(r)}
                >
                  {r}
                </button>
              ))}
            </div>
          )}
        </DetailSection>
      )}

      {(detail.tags || []).length > 0 && (
        <DetailSection label="Tags">
          <div className="tool-adapter-catalog__chip-row">
            {detail.tags.map((t) => <span key={t} className="chip tool-adapter-catalog__chip-sm">{t}</span>)}
          </div>
        </DetailSection>
      )}
    </div>
  )
}

function SupplyDetail({ supply, detail, install, shelf, onStage, onCompose }) {
  if (!supply) return <p className="mono">unknown</p>

  return (
    <div className="supply-detail" data-testid="supply-detail">
      <div className="supply-detail__head">
        <span className={supply.chipClass}>{supply.label}</span>
        <span className="mono supply-detail__egress">{supply.egressLabel}</span>
      </div>

      {supply.state === SUPPLY.UNKNOWN && (
        <p className="supply-detail__note">
          This SimCore did not report shelf state, so this is neither &ldquo;staged&rdquo; nor
          &ldquo;not staged&rdquo;. Do not plan egress on the strength of this card.
        </p>
      )}

      {supply.state === SUPPLY.RUNTIME_FETCH && (
        <>
          <p className="supply-detail__note">
            This tool is installed <strong>by the target host</strong> at run time, with the command
            below. A default-deny egress policy — which is what the customers who buy Cortex run —
            blocks it. The step then fails, or worse, runs without the tool and the absent detection
            reads as a miss.
          </p>
          {install.runtime_install_command && (
            <pre className="supply-detail__cmd mono">{install.runtime_install_command}</pre>
          )}
          <p className="supply-detail__note supply-detail__note--muted">
            No <span className="mono">install.artifact</span> is declared on this pack, so the
            console cannot stage it: the URL is never guessed out of that shell string. Most of these
            are apt/pip/git-clone installs that are not single-file artifacts at all. Declare an
            artifact block in <span className="mono">tools/packs/{'{tool}'}.yml</span> to make it
            stageable.
          </p>
        </>
      )}

      {(supply.state === SUPPLY.STAGED || supply.state === SUPPLY.UNSTAGED) && (
        <ProvenanceBlock supply={supply} adapter={detail} />
      )}

      {supply.state === SUPPLY.UNSTAGED && (
        <p className="supply-detail__note">
          The bytes are not on this SimCore, so the target fetches them itself at run time. Staging
          moves that request onto this host, where internet egress is already accepted, and makes
          the artifact checksummable before it reaches the customer.
        </p>
      )}

      <div className="supply-detail__actions">
        {supply.actionable && shelf?.writable !== false && (
          <button type="button" className="btn btn--primary btn--xs" onClick={onStage}>
            Stage on SimCore ▸
          </button>
        )}
        {supply.actionable && shelf?.writable === false && (
          <button type="button" className="btn btn--xs" onClick={onStage}>Stage offline ▸</button>
        )}
        {supply.state === SUPPLY.STAGED && (
          <button type="button" className="btn btn--primary btn--xs" onClick={onCompose}>
            Compose into a run ▸
          </button>
        )}
      </div>
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
    return <p className="mono tool-adapter-catalog__empty-note">(none)</p>
  }
  return (
    <div className="adapter-schema adapter-schema--kv">
      {present.map(([k, v]) => (
        <div key={k} className="adapter-schema__row">
          <div className="adapter-schema__name mono">{k}</div>
          <div className="adapter-schema__desc mono adapter-schema__desc--pre">{String(v)}</div>
        </div>
      ))}
    </div>
  )
}

/**
 * Maps onto chip modifiers that EXIST. The previous 'warn'/'danger' values had
 * no CSS anywhere in the tree, so every safety chip silently fell through to the
 * base `.chip` — a class of bug worth naming: styling that was never rendered.
 */
function safetyChipVariant(safety) {
  switch (safety) {
    case 'safe':              return 'signal'
    case 'dual-use-lab-only': return 'pending'
    case 'c2-framework':      return 'missed'
    case 'destructive':       return 'missed'
    default:                  return 'signal'
  }
}
