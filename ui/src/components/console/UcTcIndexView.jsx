import React, { useCallback, useEffect, useMemo, useState } from 'react'

import {
  getUcTcSummary,
  getUcTcUseCases,
  getUcTcTestCases,
  getUcTcTestCase,
  getUcTcCoverage,
  getUcTcGaps,
} from '../../api/client.js'
import './UcTcIndexView.css'

/**
 * UcTcIndexView — read-only browser over the FY27 v2.2 master
 * Use-Case / Test-Case index, joined to the engine's own evidence.
 *
 * The index (docs/uc_tc_mapping/_v2.2-source/) is the customer-facing
 * methodology: 49 use cases → 203 UCS groups → 266 test cases, each with a
 * validation class (DET / HNT / POS / PLT / AUT), a differentiation tier, a
 * primary KPI and a threshold. CortexSim's 161 scenarios evidence a subset of
 * it. This surface makes BOTH halves visible: what is proven, and — just as
 * important — what is not.
 *
 * Three modes, persisted in the hash router as ``?tab=``:
 *   index    — UC rail (grouped by FY27 subdomain) → filterable TC table →
 *              per-TC detail with measurement contract, entitlements, the
 *              scenarios that evidence it, and their run verdicts.
 *   coverage — per-UC coverage bars (worst first) + class / tier / plane rollups.
 *   gaps     — the UNEVIDENCED detection-backable test cases, P1 first.
 *
 * Deep-linkable: ``#/uctc?tab=index&uc=UC-EDR&tc=TC-EDR-03``.
 *
 * Degraded mode is a first-class state: the UC/TC registry is deliberately
 * fail-soft, so every endpoint answers 200 with ``index_loaded: false`` when
 * the snapshot is missing from a deploy. That renders as an explicit "snapshot
 * not loaded" banner — NEVER as "0 test cases".
 */

const DET_CLASSES = ['DET', 'HNT']

const CLASS_HINT = {
  DET: 'Detection — a detection must fire',
  HNT: 'Hunt — analyst-driven query validation',
  POS: 'Posture — a configuration finding',
  PLT: 'Platform — a platform capability demo',
  AUT: 'Automation — a playbook / response action',
}

const TIER_HINT = {
  MOAT: 'MOAT — PANW-only capability',
  LEAD: 'LEAD — PANW materially ahead',
  PARITY: 'PARITY — table stakes',
  EMERGING: 'EMERGING — early capability',
}

/* ─── small helpers ─────────────────────────────────────────────────── */

function pct(n, d) {
  if (!d) return 0
  return Math.round((n / d) * 1000) / 10
}

function evidenceOf(tc) {
  return (tc && tc.evidence) || {}
}

function isDetectionBackable(tc) {
  return DET_CLASSES.includes(tc?.validation_class)
}

/** Stable sort key: priority P1→P3, then UC, then TC id. */
function gapSort(a, b) {
  const p = String(a.priority || 'P9').localeCompare(String(b.priority || 'P9'))
  if (p !== 0) return p
  const u = String(a.uc_id || '').localeCompare(String(b.uc_id || ''))
  if (u !== 0) return u
  return String(a.tc_id || '').localeCompare(String(b.tc_id || ''))
}

/* ─── surface ───────────────────────────────────────────────────────── */

export default function UcTcIndexView({
  params = {},
  setParams = () => {},
  onNavigate = () => {},
}) {
  const tab = ['index', 'coverage', 'gaps'].includes(params.tab) ? params.tab : 'index'
  const selectedUc = params.uc || null
  const selectedTc = params.tc || null

  const [summary, setSummary] = useState(null)
  const [useCases, setUseCases] = useState([])
  const [testCases, setTestCases] = useState([])
  const [envelope, setEnvelope] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Client-side filter state (NOT deep-linked — setParams would churn the
  // hash on every keystroke; only tab / uc / tc are addressable).
  const [query, setQuery] = useState('')
  const [fClass, setFClass] = useState('all')
  const [fTier, setFTier] = useState('all')
  const [fPriority, setFPriority] = useState('all')
  const [fEvidence, setFEvidence] = useState('all')
  const [fScoreable, setFScoreable] = useState('all')
  const [fPlane, setFPlane] = useState('all')
  const [fSheet, setFSheet] = useState('all')

  // ── initial load: one full fetch, then everything filters in memory ──
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.all([
      getUcTcTestCases({}).catch((e) => ({ _error: e })),
      getUcTcUseCases({}).catch(() => ({ use_cases: [] })),
      getUcTcSummary().catch(() => null),
    ])
      .then(([tcRes, ucRes, sumRes]) => {
        if (cancelled) return
        if (tcRes && tcRes._error) {
          setError(tcRes._error?.message || 'Failed to load the UC/TC index')
          return
        }
        setTestCases(Array.isArray(tcRes?.test_cases) ? tcRes.test_cases : [])
        setUseCases(Array.isArray(ucRes?.use_cases) ? ucRes.use_cases : [])
        setSummary(sumRes || null)
        setEnvelope({
          index_loaded: tcRes?.index_loaded !== false,
          index_version: tcRes?.index_version ?? sumRes?.index_version ?? null,
          index_total: tcRes?.index_total ?? null,
        })
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const indexLoaded = envelope ? envelope.index_loaded !== false : true

  /* ── derived stats (fall back to the rows on screen when /summary is
        unavailable, so the tiles can never disagree with the table) ── */
  const derived = useMemo(() => {
    const detBackable = testCases.filter(isDetectionBackable)
    const evidenced = testCases.filter((t) => evidenceOf(t).evidenced)
    const detEvidenced = detBackable.filter((t) => evidenceOf(t).evidenced)
    return {
      total: testCases.length,
      detection_backable: detBackable.length,
      evidenced: evidenced.length,
      evidenced_detection_backable: detEvidenced.length,
      gaps: detBackable.length - detEvidenced.length,
      unscoreable: detBackable.filter((t) => t.is_scoreable === false).length,
    }
  }, [testCases])

  const stats = useMemo(() => {
    const ev = summary?.evidence || {}
    const tot = summary?.totals || {}
    return {
      total: tot.test_cases ?? envelope?.index_total ?? derived.total,
      detection_backable: ev.detection_backable ?? derived.detection_backable,
      evidenced: ev.evidenced ?? derived.evidenced,
      evidenced_detection_backable:
        ev.evidenced_detection_backable ?? derived.evidenced_detection_backable,
      gaps:
        ev.detection_backable != null && ev.evidenced_detection_backable != null
          ? ev.detection_backable - ev.evidenced_detection_backable
          : derived.gaps,
      version: envelope?.index_version || summary?.index_version || '—',
    }
  }, [summary, envelope, derived])

  /* ── filter option vocabularies, derived from the payload ── */
  const options = useMemo(() => {
    const uniq = (vals) => Array.from(new Set(vals.filter(Boolean))).sort()
    return {
      classes: uniq(testCases.map((t) => t.validation_class)),
      tiers: uniq(testCases.map((t) => t.differentiation_tier)),
      priorities: uniq(testCases.map((t) => t.priority)),
      sheets: uniq(testCases.map((t) => t.tc_sheet)),
      planes: uniq(testCases.flatMap((t) => evidenceOf(t).planes || [])),
    }
  }, [testCases])

  const visible = useMemo(() => {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    return testCases.filter((t) => {
      const ev = evidenceOf(t)
      if (selectedUc && t.uc_id !== selectedUc) return false
      if (fClass !== 'all' && t.validation_class !== fClass) return false
      if (fTier !== 'all' && t.differentiation_tier !== fTier) return false
      if (fPriority !== 'all' && t.priority !== fPriority) return false
      if (fSheet !== 'all' && t.tc_sheet !== fSheet) return false
      if (fEvidence === 'evidenced' && !ev.evidenced) return false
      if (fEvidence === 'unevidenced' && ev.evidenced) return false
      if (fScoreable === 'scoreable' && t.is_scoreable === false) return false
      if (fScoreable === 'qualitative' && t.is_scoreable !== false) return false
      if (fPlane !== 'all' && !(ev.planes || []).includes(fPlane)) return false
      if (tokens.length) {
        const haystack = [
          t.tc_id, t.title, t.use_case, t.ucs_name, t.uc_id,
          t.primary_kpi, t.detection_source, t.pov_scenario_id,
          ...(ev.scenario_ids || []),
        ].filter(Boolean).join(' ').toLowerCase()
        if (!tokens.every((tok) => haystack.includes(tok))) return false
      }
      return true
    })
  }, [testCases, selectedUc, fClass, fTier, fPriority, fSheet, fEvidence, fScoreable, fPlane, query])

  const hasActiveFilters =
    query.trim() !== '' || fClass !== 'all' || fTier !== 'all' ||
    fPriority !== 'all' || fEvidence !== 'all' || fScoreable !== 'all' ||
    fPlane !== 'all' || fSheet !== 'all'

  const resetFilters = useCallback(() => {
    setQuery(''); setFClass('all'); setFTier('all'); setFPriority('all')
    setFEvidence('all'); setFScoreable('all'); setFPlane('all'); setFSheet('all')
  }, [])

  /* ── UC rail, grouped by FY27 subdomain ── */
  const ucGroups = useMemo(() => {
    const order = []
    const byDomain = new Map()
    for (const uc of useCases) {
      const key = uc.fy27_subdomain || 'Unclassified'
      if (!byDomain.has(key)) { byDomain.set(key, []); order.push(key) }
      byDomain.get(key).push(uc)
    }
    return order.map((label) => ({ label, useCases: byDomain.get(label) }))
  }, [useCases])

  const activeUc = useMemo(
    () => useCases.find((u) => u.uc_id === selectedUc) || null,
    [useCases, selectedUc],
  )

  /* ── detail drawer ── */
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  useEffect(() => {
    if (!selectedTc) { setDetail(null); return undefined }
    let cancelled = false
    setDetailLoading(true)
    setDetail(null)
    getUcTcTestCase(selectedTc)
      .then((d) => { if (!cancelled) setDetail(d) })
      .catch((e) => {
        if (!cancelled) setDetail({ _error: e?.message || 'Failed to load test case' })
      })
      .finally(() => { if (!cancelled) setDetailLoading(false) })
    return () => { cancelled = true }
  }, [selectedTc])

  /* ── coverage mode (lazy) ── */
  const [coverage, setCoverage] = useState(null)
  const [coverageError, setCoverageError] = useState(null)
  useEffect(() => {
    if (tab !== 'coverage' || coverage || coverageError) return undefined
    let cancelled = false
    getUcTcCoverage()
      .then((d) => { if (!cancelled) setCoverage(d) })
      .catch((e) => { if (!cancelled) setCoverageError(e?.message || 'Failed to load coverage') })
    return () => { cancelled = true }
  }, [tab, coverage, coverageError])

  /* ── gaps mode (lazy, with a client-side fallback) ── */
  const [includeUnscoreable, setIncludeUnscoreable] = useState(true)
  const [gaps, setGaps] = useState(null)
  const [gapsTried, setGapsTried] = useState(false)
  useEffect(() => {
    if (tab !== 'gaps' || gapsTried) return undefined
    let cancelled = false
    setGapsTried(true)
    getUcTcGaps({ validation_class: 'DET,HNT' })
      .then((d) => { if (!cancelled) setGaps(Array.isArray(d?.gaps) ? d.gaps : []) })
      // The gap list is fully derivable from the rows already on screen, so a
      // /gaps failure degrades to the local derivation rather than an error.
      .catch(() => { if (!cancelled) setGaps(null) })
    return () => { cancelled = true }
  }, [tab, gapsTried])

  const gapRows = useMemo(() => {
    const source = gaps
      || testCases.filter((t) => isDetectionBackable(t) && !evidenceOf(t).evidenced)
    return source
      .filter((t) => (includeUnscoreable ? true : t.is_scoreable !== false))
      .filter((t) => (selectedUc ? t.uc_id === selectedUc : true))
      .slice()
      .sort(gapSort)
  }, [gaps, testCases, includeUnscoreable, selectedUc])

  const p1Gaps = gapRows.filter((t) => t.priority === 'P1').length

  /* ── handlers ── */
  const selectUc = useCallback((ucId) => {
    setParams({ uc: selectedUc === ucId ? null : ucId, tc: null })
  }, [setParams, selectedUc])

  const openTc = useCallback((tcId) => { setParams({ tc: tcId }) }, [setParams])
  const closeTc = useCallback(() => { setParams({ tc: null }) }, [setParams])
  const setTab = useCallback((next) => { setParams({ tab: next }) }, [setParams])

  // A missing snapshot is NOT zero coverage — never render it as a number.
  const dash = (v) => (indexLoaded ? v : '—')

  /* ── render ── */
  return (
    <div className="adapter-registry uctc" data-testid="uctc-index">
      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          The FY27 master <strong>Use-Case / Test-Case index</strong> —{' '}
          {indexLoaded ? (
            <>
              <span className="mono">{stats.total}</span> test cases across{' '}
              <span className="mono">{summary?.totals?.use_cases ?? useCases.length}</span>{' '}
              use cases. Rows marked evidenced are proven by at least one engine
              scenario; the rest are the honest gap.
            </>
          ) : (
            <>the snapshot is not present on this SimCore, so nothing can be
              joined to the engine&rsquo;s evidence.</>
          )}
        </p>
        <div className="adapter-registry__stats">
          <Stat value={dash(stats.total)} label="test cases" />
          <Stat value={dash(stats.detection_backable)} label="DET / HNT" />
          <Stat value={dash(stats.evidenced)} label="evidenced" tone="detected" />
          <Stat value={dash(stats.evidenced_detection_backable)} label="DET/HNT evidenced" tone="detected" />
          <Stat value={dash(stats.gaps)} label="open gaps" tone="pending" />
          <Stat value={indexLoaded ? `v${String(stats.version).replace(/^v/, '')}` : '—'} label="index" />
        </div>
      </div>

      {error && (
        <div className="adapter-registry__error mono" role="alert">{error}</div>
      )}

      <div className="uctc__modebar">
        <div className="lab__segmented" role="tablist" aria-label="UC/TC index view mode">
          <ModeTab id="index" active={tab} onChange={setTab} title="Browse the index use case by use case">Index</ModeTab>
          <ModeTab id="coverage" active={tab} onChange={setTab} title="Per-use-case coverage rollups">Coverage</ModeTab>
          <ModeTab id="gaps" active={tab} onChange={setTab} title="Detection test cases no scenario evidences">Gaps</ModeTab>
        </div>
        <div className="uctc__modebar-meta mono">
          methodology coverage — for MITRE ATT&amp;CK technique coverage see{' '}
          <button type="button" className="uctc__link" data-testid="uctc-goto-coverage" onClick={() => onNavigate('coverage')}>
            Coverage →
          </button>
          {' · '}
          <button type="button" className="uctc__link" data-testid="uctc-goto-ttps" onClick={() => onNavigate('ttps')}>
            TTP Cards →
          </button>
        </div>
      </div>

      {loading ? (
        <div className="coverage__empty mono">loading the UC / TC index…</div>
      ) : !indexLoaded ? (
        <div className="coverage__empty mono" data-testid="uctc-degraded">
          UC/TC index snapshot not loaded on this SimCore —{' '}
          <span className="mono">docs/uc_tc_mapping/_v2.2-source/</span> is missing
          from the deploy. This is not zero coverage; it is no index.
        </div>
      ) : tab === 'coverage' ? (
        <CoverageMode
          coverage={coverage}
          error={coverageError}
          useCases={useCases}
          onPickUc={(ucId) => setParams({ tab: 'index', uc: ucId, tc: null })}
        />
      ) : tab === 'gaps' ? (
        <GapsMode
          rows={gapRows}
          p1={p1Gaps}
          includeUnscoreable={includeUnscoreable}
          onToggleUnscoreable={() => setIncludeUnscoreable((v) => !v)}
          scopedUc={activeUc}
          onClearUc={() => setParams({ uc: null })}
          onOpen={openTc}
        />
      ) : (
        <div className="uctc__split">
          <UcRail
            groups={ucGroups}
            selected={selectedUc}
            onSelect={selectUc}
          />
          <div className="uctc__main">
            <div className="adapter-registry__search">
              <input
                type="search"
                className="adapter-registry__search-input mono"
                data-testid="uctc-search"
                placeholder="Search tc id · title · use case · KPI · detection source · scenario id…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search the UC/TC index"
              />
              {hasActiveFilters && (
                <button
                  type="button"
                  className="btn"
                  data-testid="uctc-clear-filters"
                  style={{ height: 28, padding: '0 10px' }}
                  onClick={resetFilters}
                >
                  Clear
                </button>
              )}
            </div>

            <FilterRow label="class" active={fClass} options={options.classes} onChange={setFClass} titles={CLASS_HINT} />
            <FilterRow label="tier" active={fTier} options={options.tiers} onChange={setFTier} titles={TIER_HINT} />
            <FilterRow label="priority" active={fPriority} options={options.priorities} onChange={setFPriority} />
            <FilterRow
              label="evidence"
              active={fEvidence}
              options={['evidenced', 'unevidenced']}
              onChange={setFEvidence}
            />
            <FilterRow
              label="scoreable"
              active={fScoreable}
              options={['scoreable', 'qualitative']}
              onChange={setFScoreable}
            />
            {options.planes.length > 0 && (
              <FilterRow label="plane" active={fPlane} options={options.planes} onChange={setFPlane} />
            )}
            {options.sheets.length > 1 && (
              <FilterRow label="sheet" active={fSheet} options={options.sheets} onChange={setFSheet} />
            )}

            <TestCaseTable
              rows={visible}
              total={testCases.length}
              groupByUcs={Boolean(selectedUc)}
              selectedTc={selectedTc}
              onOpen={openTc}
              hasActiveFilters={hasActiveFilters}
              onResetFilters={resetFilters}
              activeUc={activeUc}
              onSeeGaps={() => setParams({ tab: 'gaps' })}
            />
          </div>
        </div>
      )}

      {selectedTc && indexLoaded && (
        <TestCaseDetail
          tcId={selectedTc}
          detail={detail}
          loading={detailLoading}
          onClose={closeTc}
          onPickUc={(ucId) => setParams({ tab: 'index', uc: ucId, tc: null })}
          onNavigate={onNavigate}
        />
      )}
    </div>
  )
}

/* ─── stat tile ─────────────────────────────────────────────────────── */

function Stat({ value, label, tone }) {
  return (
    <div className="stack-coverage__stat">
      <div
        className="stack-coverage__stat-value mono"
        style={tone ? { color: `var(--c-${tone})` } : undefined}
      >
        {value}
      </div>
      <div className="stack-coverage__stat-label">{label}</div>
    </div>
  )
}

function ModeTab({ id, active, onChange, title, children }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active === id}
      className={active === id ? 'is-active' : ''}
      data-testid={`uctc-mode-${id}`}
      title={title}
      onClick={() => onChange(id)}
    >
      {children}
    </button>
  )
}

/* ─── filter chip row (mirrors TtpBrowserView) ──────────────────────── */

function FilterRow({ label, active, options, onChange, titles = {} }) {
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
          title={titles[opt] || undefined}
          data-testid={`uctc-filter-${label}-${opt}`}
          className={'competitive__filter' + (active === opt ? ' is-active' : '')}
          onClick={() => onChange(opt)}
        >
          {opt}
        </button>
      ))}
    </div>
  )
}

/* ─── UC rail ───────────────────────────────────────────────────────── */

function UcRail({ groups, selected, onSelect }) {
  if (!groups.length) {
    return (
      <nav className="uctc__rail" aria-label="Use cases">
        <div className="coverage__empty mono" style={{ padding: 16 }}>no use cases</div>
      </nav>
    )
  }
  return (
    <nav className="uctc__rail" aria-label="Use cases">
      {groups.map((g) => (
        <div key={g.label} className="uctc__rail-group">
          <div className="uctc__rail-group-label mono">{g.label}</div>
          {g.useCases.map((uc) => {
            const c = uc.counts || {}
            const total = c.test_cases || 0
            const evidenced = c.evidenced || 0
            const detOpen = Math.max(
              0, (c.detection_backable || 0) - (c.evidenced_detection_backable || 0),
            )
            const rest = Math.max(0, total - evidenced - detOpen)
            const isActive = selected === uc.uc_id
            return (
              <button
                key={uc.uc_id}
                type="button"
                className={'uctc__rail-item' + (isActive ? ' is-active' : '')}
                data-testid={`uctc-uc-${uc.uc_id}`}
                aria-pressed={isActive}
                onClick={() => onSelect(uc.uc_id)}
                title={`${uc.uc_id} — ${evidenced}/${total} test cases evidenced`}
              >
                <div className="uctc__rail-item-head">
                  <span className="mono uctc__rail-id">{uc.uc_id}</span>
                  <span className="mono uctc__rail-count">{evidenced}/{total}</span>
                </div>
                <div className="uctc__rail-name">{uc.use_case || uc.uc_id}</div>
                <div className="coverage__summary-bar uctc__rail-bar">
                  {evidenced > 0 && (
                    <div
                      className="coverage__summary-seg"
                      style={{ width: `${pct(evidenced, total)}%`, background: 'var(--c-detected)' }}
                    />
                  )}
                  {detOpen > 0 && (
                    <div
                      className="coverage__summary-seg"
                      style={{ width: `${pct(detOpen, total)}%`, background: 'var(--c-pending)' }}
                    />
                  )}
                  {rest > 0 && (
                    <div
                      className="coverage__summary-seg"
                      style={{ width: `${pct(rest, total)}%`, background: 'var(--c-hairline-strong)' }}
                    />
                  )}
                </div>
              </button>
            )
          })}
        </div>
      ))}
    </nav>
  )
}

/* ─── test-case table ───────────────────────────────────────────────── */

function TestCaseTable({
  rows, total, groupByUcs, selectedTc, onOpen,
  hasActiveFilters, onResetFilters, activeUc, onSeeGaps,
}) {
  if (!rows.length) {
    if (activeUc && !hasActiveFilters) {
      const c = activeUc.counts || {}
      return (
        <div className="coverage__empty mono" data-testid="uctc-empty-uc">
          no test cases in <span className="mono">{activeUc.uc_id}</span> match —{' '}
          {(c.detection_backable || 0)} detection rows open{' '}
          <button type="button" className="btn" style={{ height: 22, padding: '0 8px', marginLeft: 4 }} onClick={onSeeGaps}>
            See gaps →
          </button>
        </div>
      )
    }
    return (
      <div className="coverage__empty mono" data-testid="uctc-empty-filters">
        no test cases match the current filters —{' '}
        <button
          type="button"
          className="btn"
          style={{ height: 22, padding: '0 8px', marginLeft: 4 }}
          onClick={onResetFilters}
        >
          clear filters
        </button>
      </div>
    )
  }

  // Group-header rows for the UCS label, but only when a single UC is in
  // scope — 203 UCS groups holding a median of 1 TC is noise otherwise.
  // Pre-computed so the render pass stays free of assignment side effects.
  const headerFor = new Set()
  if (groupByUcs) {
    let last = null
    for (const tc of rows) {
      if (tc.ucs_id !== last) { headerFor.add(tc.tc_id); last = tc.ucs_id }
    }
  }

  return (
    <>
      <div className="uctc__tablemeta mono" data-testid="uctc-rowcount">
        {rows.length} of {total} test cases
      </div>
      <div className="uctc__tablewrap">
        <table className="uctc__table">
          <thead>
            <tr>
              <th scope="col">TC</th>
              <th scope="col">Title</th>
              <th scope="col">Class</th>
              <th scope="col">Tier</th>
              <th scope="col">Pri</th>
              <th scope="col">Primary KPI</th>
              <th scope="col">Threshold</th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((tc) => {
              const ev = evidenceOf(tc)
              return (
                <React.Fragment key={tc.tc_id}>
                  {headerFor.has(tc.tc_id) && (
                    <tr className="uctc__table-groupheader">
                      <th colSpan={8} scope="colgroup">
                        <span className="mono">{tc.ucs_id}</span>
                        {tc.ucs_name ? ` · ${tc.ucs_name}` : ''}
                      </th>
                    </tr>
                  )}
                  <tr
                    className={'uctc__table-row' + (selectedTc === tc.tc_id ? ' is-active' : '')}
                    onClick={() => onOpen(tc.tc_id)}
                  >
                    <td>
                      <button
                        type="button"
                        className="uctc__idbtn mono"
                        data-testid={`uctc-row-${tc.tc_id}`}
                        onClick={(e) => { e.stopPropagation(); onOpen(tc.tc_id) }}
                      >
                        {tc.tc_id}
                      </button>
                    </td>
                    <td className="uctc__cell-title" title={tc.title || ''}>{tc.title || '—'}</td>
                    <td><ClassChip value={tc.validation_class} /></td>
                    <td><TierChip value={tc.differentiation_tier} /></td>
                    <td className="mono">{tc.priority || '—'}</td>
                    <td className="uctc__cell-kpi">{tc.primary_kpi || '—'}</td>
                    <td className="mono">
                      {tc.is_scoreable === false
                        ? <span className="chip uctc__chip-qual" title="The index carries no measurable threshold — the verifier reports not_applicable, never a pass.">qualitative</span>
                        : (tc.threshold || '—')}
                    </td>
                    <td className="mono uctc__cell-evidence">
                      {ev.evidenced
                        ? (
                          <span style={{ color: 'var(--c-detected)' }}>
                            {ev.scenario_count || (ev.scenario_ids || []).length} scenario
                            {(ev.scenario_count || (ev.scenario_ids || []).length) === 1 ? '' : 's'}
                          </span>
                        )
                        : <span style={{ color: 'var(--c-text-muted)' }}>—</span>}
                    </td>
                  </tr>
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

function ClassChip({ value }) {
  if (!value) return <span className="mono">—</span>
  const det = DET_CLASSES.includes(value)
  return (
    <span
      className="chip uctc__chip"
      title={CLASS_HINT[value] || value}
      style={{ color: det ? 'var(--c-action)' : 'var(--c-text-muted)' }}
    >
      {value}
    </span>
  )
}

function TierChip({ value }) {
  if (!value) return <span className="mono">—</span>
  const color =
    value === 'MOAT' ? 'var(--c-detected)'
      : value === 'LEAD' ? 'var(--c-signal)'
        : value === 'EMERGING' ? 'var(--c-pending)'
          : 'var(--c-text-muted)'
  return (
    <span className="chip uctc__chip" title={TIER_HINT[value] || value} style={{ color }}>
      {value}
    </span>
  )
}

/* ─── detail drawer ─────────────────────────────────────────────────── */

function TestCaseDetail({ tcId, detail, loading, onClose, onPickUc, onNavigate }) {
  if (loading || !detail) {
    return (
      <section className="competitive__detail uctc__detail" data-testid="uctc-detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">test case</div>
            <div className="competitive__detail-title mono">{tcId}</div>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <div className="coverage__empty mono">
          {loading ? 'loading test case…' : 'no detail'}
        </div>
      </section>
    )
  }

  if (detail._error) {
    return (
      <section className="competitive__detail uctc__detail" data-testid="uctc-detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">test case</div>
            <div className="competitive__detail-title mono">{tcId}</div>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <div className="adapter-registry__error mono" role="alert">{detail._error}</div>
      </section>
    )
  }

  const ent = detail.entitlements || {}
  const payload = detail.payload || null
  const evidencedBy = detail.evidenced_by || []
  const runs = detail.runs || null

  return (
    <section className="competitive__detail uctc__detail" data-testid="uctc-detail">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">
            <button type="button" className="uctc__link" onClick={() => onPickUc(detail.uc_id)}>
              {detail.uc_id}
            </button>
            {' › '}
            <span>{detail.ucs_id || detail.ucs?.ucs_id || '—'}</span>
          </div>
          <div className="competitive__detail-title mono">{detail.tc_id}</div>
          <div className="uctc__detail-subtitle">{detail.title}</div>
          <div className="uctc__detail-chips">
            <ClassChip value={detail.validation_class} />
            <TierChip value={detail.differentiation_tier} />
            <span className="chip uctc__chip mono">{detail.priority || '—'}</span>
            {detail.tc_sheet && <span className="chip uctc__chip mono">{detail.tc_sheet}</span>}
          </div>
        </div>
        <button type="button" className="btn" data-testid="uctc-detail-close" onClick={onClose}>Close</button>
      </div>

      {detail.is_scoreable === false && (
        <div className="uctc__banner mono" data-testid="uctc-unscoreable">
          The index carries no measurable threshold for this test case — the
          verifier reports <span className="mono">not_applicable</span>, never a pass.
        </div>
      )}

      <DetailSection title="Measurement contract">
        <dl className="cov-detail__kv">
          <KV k="validation methodology" v={detail.validation_methodology} />
          <KV k="primary KPI" v={detail.primary_kpi} />
          <KV k="threshold" v={detail.threshold} mono />
          <KV k="measurement method" v={detail.measurement_method} />
          <KV k="success criteria" v={detail.success_criteria} />
          <KV k="detection source" v={detail.detection_source} mono />
          <KV k="expected signal" v={detail.expected_signal} />
          <KV k="simulation input" v={detail.simulation_input} />
        </dl>
        {detail.description && (
          <p className="uctc__detail-prose">{detail.description}</p>
        )}
      </DetailSection>

      <DetailSection title="Licensing">
        <div className="uctc__chiprow">
          <span className="competitive__filter-label mono">base:</span>
          {(ent.base_platform || []).length
            ? (ent.base_platform || []).map((b) => (
              <span key={b} className="chip chip--signal">{b}</span>
            ))
            : <span className="mono uctc__muted">none derived</span>}
        </div>
        <div className="uctc__chiprow">
          <span className="competitive__filter-label mono">add-ons:</span>
          {(ent.addons || []).length
            ? (ent.addons || []).map((a) => (
              <span key={a} className="chip">{a}</span>
            ))
            : <span className="mono uctc__muted">none derived</span>}
        </div>
      </DetailSection>

      {payload && (
        <DetailSection title="POV payload">
          <div className="uctc__chiprow">
            <span className="mono">{payload.pov_scenario_id || '—'}</span>
            <span className="mono uctc__muted">
              · {payload.bound_tc_count ?? '?'} bound test cases
            </span>
            {payload.needs_split && (
              <span
                className="chip uctc__chip-split"
                title="one payload fires all bound test cases at once — none can be independently validated."
              >
                SPLIT REQUIRED
              </span>
            )}
          </div>
          {payload.payload && <p className="uctc__detail-prose">{payload.payload}</p>}
        </DetailSection>
      )}

      <DetailSection title={`Evidenced by (${evidencedBy.length})`}>
        {evidencedBy.length === 0 ? (
          <div className="coverage__empty mono" data-testid="uctc-detail-noevidence">
            no engine scenario evidences this test case yet
          </div>
        ) : (
          <div className="uctc__tablewrap">
            <table className="uctc__table uctc__table--compact">
              <thead>
                <tr>
                  <th scope="col">Scenario</th>
                  <th scope="col">Name</th>
                  <th scope="col">Plane</th>
                  <th scope="col">Tier</th>
                  <th scope="col" />
                </tr>
              </thead>
              <tbody>
                {evidencedBy.map((s) => {
                  const delta = s.moat_tier && detail.differentiation_tier
                    && s.moat_tier !== detail.differentiation_tier
                  return (
                    <tr key={s.scenario_id}>
                      <td className="mono">{s.scenario_id}</td>
                      <td className="uctc__cell-title" title={s.name || ''}>{s.name || '—'}</td>
                      <td><span className="chip uctc__chip mono">{s.plane || '—'}</span></td>
                      <td className="mono">
                        {s.moat_tier || '—'}
                        {delta && (
                          <span
                            className="chip uctc__chip-delta"
                            title={`positioning delta — the scenario claims ${s.moat_tier}, the index carries ${detail.differentiation_tier}. Informational, not a defect.`}
                          >
                            index {detail.differentiation_tier}
                          </span>
                        )}
                      </td>
                      <td>
                        {s.is_primary && <span className="chip chip--signal">primary</span>}
                        <button
                          type="button"
                          className="btn"
                          data-testid={`uctc-open-library-${s.scenario_id}`}
                          style={{ height: 22, padding: '0 8px', marginLeft: 6 }}
                          onClick={() => onNavigate('library', { open: s.scenario_id })}
                        >
                          Open in Library →
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </DetailSection>

      {runs && (runs.total || 0) > 0 && (
        <DetailSection title="Runs">
          <div className="uctc__chiprow mono">
            <span>{runs.total} run{runs.total === 1 ? '' : 's'}</span>
            {Object.entries(runs.verdicts || {}).map(([k, v]) => (
              <span key={k} className="chip uctc__chip" style={{ color: verdictColor(k) }}>
                {v} {k}
              </span>
            ))}
            {runs.latest?.run_id && (
              <button
                type="button"
                className="btn"
                data-testid="uctc-open-run"
                style={{ height: 22, padding: '0 8px' }}
                onClick={() => onNavigate('runs', { run: runs.latest.run_id, tab: 'evidence' })}
              >
                latest {runs.latest.run_id} →
              </button>
            )}
          </div>
        </DetailSection>
      )}
    </section>
  )
}

function verdictColor(v) {
  if (v === 'pass') return 'var(--c-detected)'
  if (v === 'fail') return 'var(--c-missed)'
  return 'var(--c-text-muted)'
}

function DetailSection({ title, children }) {
  return (
    <div className="competitive__detail-section">
      <div className="competitive__detail-label mono">{title}</div>
      {children}
    </div>
  )
}

function KV({ k, v, mono }) {
  if (v === null || v === undefined || v === '') return null
  return (
    <>
      <dt className="mono">{k}</dt>
      <dd className={mono ? 'mono' : undefined}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
    </>
  )
}

/* ─── coverage mode ─────────────────────────────────────────────────── */

function CoverageMode({ coverage, error, useCases, onPickUc }) {
  // The /coverage payload is a convenience rollup; when it is unavailable the
  // per-UC counts already on the use-case list carry the same numbers.
  const rows = useMemo(() => {
    if (Array.isArray(coverage?.by_use_case) && coverage.by_use_case.length) {
      return coverage.by_use_case.slice().sort((a, b) => (a.coverage_pct || 0) - (b.coverage_pct || 0))
    }
    return useCases
      .map((uc) => ({
        uc_id: uc.uc_id,
        use_case: uc.use_case,
        test_cases: uc.counts?.test_cases || 0,
        detection_backable: uc.counts?.detection_backable || 0,
        evidenced: uc.counts?.evidenced || 0,
        evidenced_detection_backable: uc.counts?.evidenced_detection_backable || 0,
        coverage_pct: uc.coverage_pct ?? pct(uc.counts?.evidenced || 0, uc.counts?.test_cases || 0),
        det_coverage_pct: uc.det_coverage_pct ?? pct(
          uc.counts?.evidenced_detection_backable || 0, uc.counts?.detection_backable || 0,
        ),
      }))
      .sort((a, b) => (a.coverage_pct || 0) - (b.coverage_pct || 0))
  }, [coverage, useCases])

  if (!rows.length) {
    return <div className="coverage__empty mono">no coverage data</div>
  }

  return (
    <div className="uctc__coverage" data-testid="uctc-coverage">
      {error && <div className="adapter-registry__error mono" role="alert">{error}</div>}
      <div className="uctc__coverage-hint mono">
        worst-covered use case first — click a bar to open it in the index
      </div>
      {rows.map((r) => {
        const total = r.test_cases || 0
        const evidenced = r.evidenced || 0
        const detOpen = Math.max(0, (r.detection_backable || 0) - (r.evidenced_detection_backable || 0))
        const rest = Math.max(0, total - evidenced - detOpen)
        return (
          <button
            key={r.uc_id}
            type="button"
            className="uctc__covrow"
            data-testid={`uctc-cov-${r.uc_id}`}
            onClick={() => onPickUc(r.uc_id)}
          >
            <span className="mono uctc__covrow-id">{r.uc_id}</span>
            <span className="uctc__covrow-name">{r.use_case || r.uc_id}</span>
            <span className="coverage__summary-bar uctc__covrow-bar">
              {evidenced > 0 && <span className="coverage__summary-seg" style={{ width: `${pct(evidenced, total)}%`, background: 'var(--c-detected)' }} />}
              {detOpen > 0 && <span className="coverage__summary-seg" style={{ width: `${pct(detOpen, total)}%`, background: 'var(--c-pending)' }} />}
              {rest > 0 && <span className="coverage__summary-seg" style={{ width: `${pct(rest, total)}%`, background: 'var(--c-hairline-strong)' }} />}
            </span>
            <span className="mono uctc__covrow-num">
              {evidenced}/{total} · {Math.round(r.det_coverage_pct || 0)}% DET
            </span>
          </button>
        )
      })}

      {Array.isArray(coverage?.by_validation_class) && coverage.by_validation_class.length > 0 && (
        <RollupTable title="by validation class" keyName="validation_class" rows={coverage.by_validation_class} />
      )}
      {Array.isArray(coverage?.by_tier) && coverage.by_tier.length > 0 && (
        <RollupTable title="by differentiation tier" keyName="tier" rows={coverage.by_tier} />
      )}
      {Array.isArray(coverage?.by_plane) && coverage.by_plane.length > 0 && (
        <div className="uctc__rollup">
          <div className="competitive__detail-label mono">by detection plane</div>
          <div className="uctc__tablewrap">
            <table className="uctc__table uctc__table--compact">
              <thead>
                <tr><th scope="col">Plane</th><th scope="col">Scenarios</th><th scope="col">TCs evidenced</th></tr>
              </thead>
              <tbody>
                {coverage.by_plane.map((p) => (
                  <tr key={p.plane}>
                    <td className="mono">{p.plane}</td>
                    <td className="mono">{p.scenarios}</td>
                    <td className="mono">{p.test_cases_evidenced}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function RollupTable({ title, keyName, rows }) {
  return (
    <div className="uctc__rollup">
      <div className="competitive__detail-label mono">{title}</div>
      <div className="uctc__tablewrap">
        <table className="uctc__table uctc__table--compact">
          <thead>
            <tr>
              <th scope="col">{keyName}</th>
              <th scope="col">Total</th>
              <th scope="col">Evidenced</th>
              <th scope="col">Coverage</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r[keyName] || r.label}>
                <td className="mono">{r[keyName] || r.label}</td>
                <td className="mono">{r.total}</td>
                <td className="mono">{r.evidenced}</td>
                <td className="mono">{Math.round(r.coverage_pct || 0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/* ─── gaps mode ─────────────────────────────────────────────────────── */

function GapsMode({
  rows, p1, includeUnscoreable, onToggleUnscoreable, scopedUc, onClearUc, onOpen,
}) {
  return (
    <div className="uctc__gaps" data-testid="uctc-gaps">
      <div className="uctc__gaps-head">
        <div className="uctc__gaps-headline">
          <span className="mono" data-testid="uctc-gaps-p1">{p1}</span>{' '}
          P1 detection test cases with no engine scenario
          <span className="uctc__muted mono"> · {rows.length} open DET/HNT total</span>
        </div>
        <div className="uctc__gaps-actions">
          {scopedUc && (
            <button type="button" className="btn" onClick={onClearUc}>
              scoped to {scopedUc.uc_id} — clear
            </button>
          )}
          <label className="uctc__toggle mono">
            <input
              type="checkbox"
              checked={includeUnscoreable}
              data-testid="uctc-gaps-unscoreable"
              onChange={onToggleUnscoreable}
            />
            include unscoreable
          </label>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="coverage__empty mono" data-testid="uctc-gaps-empty">
          no unevidenced detection test cases in scope
        </div>
      ) : (
        <div className="uctc__tablewrap">
          <table className="uctc__table">
            <thead>
              <tr>
                <th scope="col">TC</th>
                <th scope="col">Use case</th>
                <th scope="col">Title</th>
                <th scope="col">Pri</th>
                <th scope="col">Class</th>
                <th scope="col">Tier</th>
                <th scope="col">Threshold</th>
                <th scope="col">Detection source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((tc) => (
                <tr key={tc.tc_id} className="uctc__table-row" onClick={() => onOpen(tc.tc_id)}>
                  <td>
                    <button
                      type="button"
                      className="uctc__idbtn mono"
                      data-testid={`uctc-gap-${tc.tc_id}`}
                      onClick={(e) => { e.stopPropagation(); onOpen(tc.tc_id) }}
                    >
                      {tc.tc_id}
                    </button>
                  </td>
                  <td className="mono">{tc.uc_id || '—'}</td>
                  <td className="uctc__cell-title" title={tc.title || ''}>{tc.title || '—'}</td>
                  <td className="mono">{tc.priority || '—'}</td>
                  <td><ClassChip value={tc.validation_class} /></td>
                  <td><TierChip value={tc.differentiation_tier} /></td>
                  <td className="mono">
                    {tc.is_scoreable === false
                      ? <span className="chip uctc__chip-qual">qualitative</span>
                      : (tc.threshold || '—')}
                  </td>
                  <td className="mono uctc__cell-kpi">{tc.detection_source || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
