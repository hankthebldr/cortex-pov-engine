import React, { useEffect, useMemo, useState } from 'react'
import '../../styles/destinations/adapters.css'
import { getToolAdapters, getToolAdapter } from '../../api/client.js'
import useShelf from './useShelf.js'
import { SUPPLY, supplyOf, scenarioIdsFrom } from './supplyState.js'
import StagePayloadDialog from './StagePayloadDialog.jsx'
import PayloadComposer from './PayloadComposer.jsx'
import ProvenanceBlock from './ProvenanceBlock.jsx'
import Button from './kit/Button.jsx'
import StatusIcon from './kit/StatusIcon.jsx'
import { useChainTray, packageTrayItem } from '../../context/ChainTrayContext.jsx'

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
/** `?supply=` in plain words, for the scope line. */
const READINESS_SCOPE = {
  [SUPPLY.STAGED]: 'tools ready on this instance',
  [SUPPLY.UNSTAGED]: 'tools not ready on this instance',
  [SUPPLY.RUNTIME_FETCH]: 'tools installed on the target when a step runs',
  [SUPPLY.NOT_APPLICABLE]: 'tools provided by the lab',
  [SUPPLY.UNKNOWN]: 'tools that could not be checked',
}

export default function ToolAdapterCatalog({ params = {}, setParams = () => {}, onNavigate = () => {} }) {
  const tray = useChainTray()
  const [adapters, setAdapters] = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)
  const [selectedDetail, setSelectedDetail] = useState(null)

  const shelf = useShelf({ adapters })

  const selectedId    = params.tool || null
  const panel         = params.panel || null
  const filterPlane   = params.plane || 'all'
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
      if (filterCategory !== 'all' && a.category !== filterCategory)           return false
      if (filterSupply   !== 'all' && supplyByAdapter[a.adapter_id]?.state !== filterSupply) return false
      if (scopedAdapterIds && !scopedAdapterIds.has(a.adapter_id))             return false
      if (q) {
        const hay = `${a.name} ${a.adapter_id} ${(a.tags || []).join(' ')} ${a.category}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [adapters, filterPlane, filterCategory, filterSupply,
      search, supplyByAdapter, scopedAdapterIds])

  const resetFilters = () => setParams({
    plane: null, category: null, supply: null, q: null, scenario: null,
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
          {/* Agrees with the rail group. A package is something you deploy to
              drive a detection — a composition input, not proof output. */}
          <h1 className="tools-destination__title">Packages</h1>
        </div>
      </div>

      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          The tools a step can run. Each lists the Cortex planes and the techniques its signal
          lands on.
        </p>
      </div>

      {error && <div className="adapter-registry__error mono" role="alert">{error}</div>}

      {/* A link can still scope the list by readiness (`?supply=`), for example
          from a refused launch; the scope is named in plain words and can be
          cleared. How and when a tool reaches the host is not a filter. */}
      {filterSupply !== 'all' && (
        <div className="adapter-registry__scope mono" data-testid="adapter-supply-scope">
          showing <strong>{READINESS_SCOPE[filterSupply] || filterSupply}</strong>
          <button type="button" className="btn btn--xs" onClick={() => setParams({ supply: null })}>
            clear
          </button>
        </div>
      )}

      {scenarioScope && (
        <div className="adapter-registry__scope mono" data-testid="adapter-scenario-scope">
          scoped to <strong>{scenarioScope}</strong>
          {scopedAdapterIds && scopedAdapterIds.size === 0 && (
            <> — no package is recorded as used by it</>
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
          placeholder="Search packages"
          aria-label="Search tool adapters"
          value={search}
          onChange={(e) => setParams({ q: e.target.value || null })}
          data-testid="adapter-search"
        />
      </div>

      <FilterRow label="plane" active={filterPlane} options={planes}
        onChange={(v) => setParams({ plane: v === 'all' ? null : v })} />
      <FilterRow label="category" active={filterCategory} options={categories}
        onChange={(v) => setParams({ category: v === 'all' ? null : v })} />
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
              onAdd={() => tray.add(packageTrayItem(a))}
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
          onAddToChain={() => tray.add(packageTrayItem(selectedDetail))}
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

function ToolAdapterCard({ adapter, supply, isSelected, onSelect, onAdd = null }) {
  const tids    = adapter.expected_techniques || []
  const planes  = adapter.planes || []

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
        {/* The one readiness fact a card states: a tool this instance cannot
            supply, so a run that needs it is refused. Everything else about
            how a tool reaches the host is the system's business. */}
        {supply?.state === SUPPLY.UNSTAGED && (
          <span data-testid={`not-ready-${adapter.adapter_id}`}>
            <StatusIcon status="degraded" word="Not ready" />
          </span>
        )}
      </div>
      <div className="adapter-card__head">
        <div className="adapter-card__name mono">{adapter.name}</div>
        <div className="adapter-card__version mono">v{adapter.version}</div>
      </div>
      <div className="adapter-card__category mono">
        {adapter.category} · {adapter.target_platform || 'any'}
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

      {/* Catalog is never a dead end (v26 §4.4): every package can be added
          to the chain tray, and from there to Build a chain. */}
      {onAdd && (
        <div className="adapter-card__supply">
          <Button
            variant="secondary"
            className="adapter-card__add"
            onClick={(e) => { e.stopPropagation(); onAdd() }}
            onKeyDown={(e) => e.stopPropagation()}
          >
            Add to chain
          </Button>
        </div>
      )}
    </article>
  )
}

/* ─── Detail panel ───────────────────────────────────────────────── */

function ToolAdapterDetail({
  detail, supply, shelf, onClose, onStage, onCompose, onNavigateAdapter, onNavigateTtp, onAddToChain = null,
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
            {detail.adapter_id} · v{detail.version}
          </div>
          <h3 className="competitive__detail-title">{detail.name}</h3>
        </div>
        <span className="adapter-detail__actions">
          {onAddToChain && <Button variant="primary" onClick={onAddToChain}>Add to chain</Button>}
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </span>
      </div>

      {/* Availability first: whether a run that needs this tool can start. */}
      <DetailSection label="Availability">
        <SupplyDetail
          supply={supply}
          detail={detail}
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

/** What a DC needs to know about a tool's availability, in plain words. */
const AVAILABILITY = {
  [SUPPLY.STAGED]: 'Ready on this instance.',
  [SUPPLY.UNSTAGED]: 'Not ready on this instance. A run that needs this tool is refused until it is.',
  [SUPPLY.RUNTIME_FETCH]: 'Installed on the target host when a step runs it. The target needs internet access for that.',
  [SUPPLY.NOT_APPLICABLE]: 'Provided by the lab or your own environment.',
  [SUPPLY.UNKNOWN]: 'Could not check from here.',
}

function SupplyDetail({ supply, detail, shelf, onStage, onCompose }) {
  if (!supply) return <p className="mono">Could not check from here.</p>
  const ready = supply.state === SUPPLY.STAGED

  return (
    <div className="supply-detail" data-testid="supply-detail">
      <div className="supply-detail__head">
        <StatusIcon
          status={ready ? 'up' : supply.state === SUPPLY.UNSTAGED ? 'degraded' : 'unknown'}
          word={ready ? 'Ready' : supply.state === SUPPLY.UNSTAGED ? 'Not ready' : 'Not checked here'}
        />
      </div>
      <p className="supply-detail__note">{AVAILABILITY[supply.state] || AVAILABILITY[SUPPLY.UNKNOWN]}</p>

      {(supply.state === SUPPLY.STAGED || supply.state === SUPPLY.UNSTAGED) && (
        <ProvenanceBlock supply={supply} adapter={detail} />
      )}

      <div className="supply-detail__actions">
        {supply.actionable && (
          <button
            type="button"
            className={'btn btn--xs' + (shelf?.writable !== false ? ' btn--primary' : '')}
            onClick={onStage}
          >
            Make ready ▸
          </button>
        )}
        {ready && (
          <button type="button" className="btn btn--primary btn--xs" onClick={onCompose}>
            Use in a run ▸
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
