import React, { useCallback, useEffect, useMemo, useState } from 'react'

import {
  getUcTcSummary,
  getUcTcUseCases,
  getUcTcTestCases,
  getUcTcTestCase,
  getUcTcCoverage,
  getUcTcGaps,
  getAssertions,
  getAssertionRuns,
} from '../../api/client.js'
import {
  MECHANISM,
  REACH,
  assertionAvailability,
  classCoverage,
  coverageTotals,
  indexAssertionRuns,
  indexAssertions,
  latestVerdict,
  proofScope,
  readPath,
} from './uctcMechanisms.js'
import './UcTcIndexView.css'
import '../../styles/destinations/uctc.css'

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
 * Four modes, persisted in the hash router as ``?tab=``:
 *   index      — UC rail (grouped by FY27 subdomain) → filterable TC table →
 *                per-TC detail with measurement contract, entitlements, the
 *                artifacts that evidence it, and their run verdicts.
 *   mechanisms — coverage BY VALIDATION CLASS, with the mechanism that proves
 *                each class, the verdict ceiling, and what still blocks it.
 *   coverage   — per-UC coverage bars (worst first) + class / tier / plane rollups.
 *   gaps       — the UNEVIDENCED test cases, P1 first, split by whether the
 *                engine can close them at all.
 *
 * The whole-index question this surface answers is NOT "what is the number".
 * It is five different questions, because the 266 rows split five ways by
 * validation class and each class is proven by a different mechanism:
 * DET/HNT by attack scenarios, POS/PLT/AUT by assertions. A flat 84/266 fuses
 * them and misleads. ``uctcMechanisms.js`` carries that arithmetic; this file
 * only renders it.
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

// "bound by", never "proven by". An artifact binding a row is authorship; only a
// recorded tenant verdict is proof, and conflating the two is how a coverage
// number rises without anything being demonstrated.
const EVIDENCE_HINT = {
  evidenced: 'bound by an engine artifact of either kind',
  unevidenced: 'no engine artifact binds to this row',
  scenario: 'bound by an attack scenario (DET/HNT mechanism)',
  assertion: 'bound by an assertion (POS/PLT/AUT mechanism)',
}

const SCOREABLE_HINT = {
  scoreable: 'the index carries a measurable threshold — a pass verdict is possible',
  qualitative: 'no measurable threshold — the verifier reports not_applicable, never pass',
}

const REACH_HINT = {
  'xql-direct': 'the index points at XQL — a probe can measure this against a registered tenant today',
  'needs-data-path': 'the index points at a console outside XQL — an assertion can measure it '
    + 'only once that source is ingested as a queryable dataset',
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
  const tab = ['index', 'mechanisms', 'coverage', 'gaps'].includes(params.tab)
    ? params.tab
    : 'index'
  const selectedUc = params.uc || null
  const selectedTc = params.tc || null

  const [summary, setSummary] = useState(null)
  const [useCases, setUseCases] = useState([])
  const [testCases, setTestCases] = useState([])
  const [envelope, setEnvelope] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  // The assertion catalog is the POS/PLT/AUT mechanism. It is OPTIONAL — a
  // SimCore built before that router landed answers 404 — so its failure is
  // carried as a state, never as a surface error. "mechanism not deployed" and
  // "mechanism deployed, nothing authored" are different sentences and both
  // must be sayable.
  const [assertionPayload, setAssertionPayload] = useState(null)
  const [assertionError, setAssertionError] = useState(null)
  const [assertionRuns, setAssertionRuns] = useState(null)

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
  const [fReach, setFReach] = useState('all')

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

  // Fetched separately from the index so a missing/failing assertion router can
  // never take the index surface down with it.
  useEffect(() => {
    let cancelled = false
    getAssertions()
      .then((d) => { if (!cancelled) setAssertionPayload(d) })
      .catch((e) => { if (!cancelled) setAssertionError(e || new Error('unavailable')) })
    // Recorded evaluations are fetched alongside the catalog, because an
    // authored assertion is a claim and only a run is a measurement. A failure
    // here degrades to "no verdicts recorded", never to a green.
    getAssertionRuns({ limit: 500 })
      .then((d) => { if (!cancelled) setAssertionRuns(d) })
      .catch(() => { if (!cancelled) setAssertionRuns(null) })
    return () => { cancelled = true }
  }, [])

  const indexLoaded = envelope ? envelope.index_loaded !== false : true

  /* ── the mechanism model: five classes, five different stories ── */
  const assertionsByTc = useMemo(
    () => indexAssertions(assertionPayload), [assertionPayload],
  )
  const availability = useMemo(
    () => assertionAvailability({ payload: assertionPayload, error: assertionError }),
    [assertionPayload, assertionError],
  )
  const runsByAssertion = useMemo(
    () => indexAssertionRuns(assertionRuns), [assertionRuns],
  )
  const classRows = useMemo(
    () => classCoverage(testCases, assertionsByTc, runsByAssertion),
    [testCases, assertionsByTc, runsByAssertion],
  )
  const mechanismTotals = useMemo(() => coverageTotals(classRows), [classRows])

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
    // The evidenced / open numbers come from the CLASS model, not from
    // /summary: the server counts scenario evidence only, so a row proven by an
    // assertion would make the tile and the class strip disagree by one — and
    // the tile is the number a DC reads out loud.
    const haveClasses = classRows.length > 0
    return {
      total: tot.test_cases ?? envelope?.index_total ?? derived.total,
      detection_backable: ev.detection_backable ?? derived.detection_backable,
      evidenced: haveClasses ? mechanismTotals.proven : (ev.evidenced ?? derived.evidenced),
      evidenced_detection_backable:
        ev.evidenced_detection_backable ?? derived.evidenced_detection_backable,
      // "Open" means open AND closable. Rows the engine has no read path to are
      // counted separately, because planning work against them is wasted work.
      gaps: haveClasses
        ? mechanismTotals.open_direct
        : (ev.detection_backable != null && ev.evidenced_detection_backable != null
          ? ev.detection_backable - ev.evidenced_detection_backable
          : derived.gaps),
      needs_data_path: haveClasses ? mechanismTotals.open_needs_data_path : null,
      // The count of rows with a RECORDED tenant verdict. Zero until an
      // AssertionRun returns pass or fail, which is the honest state today.
      measured: haveClasses ? mechanismTotals.measured : null,
      version: envelope?.index_version || summary?.index_version || '—',
    }
  }, [summary, envelope, derived, classRows, mechanismTotals])

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
      const asserts = assertionsByTc.get(t.tc_id) || []
      const proven = Boolean(ev.evidenced) || asserts.length > 0
      if (selectedUc && t.uc_id !== selectedUc) return false
      if (fClass !== 'all' && t.validation_class !== fClass) return false
      if (fTier !== 'all' && t.differentiation_tier !== fTier) return false
      if (fPriority !== 'all' && t.priority !== fPriority) return false
      if (fSheet !== 'all' && t.tc_sheet !== fSheet) return false
      if (fEvidence === 'evidenced' && !proven) return false
      if (fEvidence === 'unevidenced' && proven) return false
      if (fEvidence === 'scenario' && !ev.evidenced) return false
      if (fEvidence === 'assertion' && !asserts.length) return false
      if (fScoreable === 'scoreable' && t.is_scoreable === false) return false
      if (fScoreable === 'qualitative' && t.is_scoreable !== false) return false
      // Reachability is about the ENGINE's read path, not about coverage: an
      // out-of-scope row is not a backlog item, so it must be isolatable.
      if (fReach !== 'all') {
        const needsPath = proofScope(t).needsDataPath
        if (fReach === 'needs-data-path' && !needsPath) return false
        if (fReach === 'xql-direct' && needsPath) return false
      }
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
  }, [testCases, assertionsByTc, selectedUc, fClass, fTier, fPriority, fSheet,
    fEvidence, fScoreable, fPlane, fReach, query])

  const hasActiveFilters =
    query.trim() !== '' || fClass !== 'all' || fTier !== 'all' ||
    fPriority !== 'all' || fEvidence !== 'all' || fScoreable !== 'all' ||
    fPlane !== 'all' || fSheet !== 'all' || fReach !== 'all'

  const resetFilters = useCallback(() => {
    setQuery(''); setFClass('all'); setFTier('all'); setFPriority('all')
    setFEvidence('all'); setFScoreable('all'); setFPlane('all'); setFSheet('all')
    setFReach('all')
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
  // Which mechanism's backlog is on screen. DET/HNT gaps are closed by
  // authoring a scenario; POS/PLT/AUT gaps are closed by authoring an
  // assertion. Showing them in one undifferentiated list is what makes 140
  // rows look like "write more attack scenarios".
  const [gapScope, setGapScope] = useState('scenario')
  const [gaps, setGaps] = useState(null)
  const [gapsTried, setGapsTried] = useState(false)
  useEffect(() => {
    if (tab !== 'gaps' || gapsTried) return undefined
    let cancelled = false
    setGapsTried(true)
    // Ask for every class: the mode now covers the whole index, and the server
    // default (DET,HNT) would silently amputate the assertion backlog.
    getUcTcGaps({ validation_class: 'DET,HNT,POS,PLT,AUT' })
      .then((d) => { if (!cancelled) setGaps(Array.isArray(d?.gaps) ? d.gaps : []) })
      // The gap list is fully derivable from the rows already on screen, so a
      // /gaps failure degrades to the local derivation rather than an error.
      .catch(() => { if (!cancelled) setGaps(null) })
    return () => { cancelled = true }
  }, [tab, gapsTried])

  const gapBuckets = useMemo(() => {
    // /uctc/gaps only knows about SCENARIO evidence, so a row an assertion
    // already proves would otherwise be reported as an open gap.
    const source = (gaps || testCases.filter((t) => !evidenceOf(t).evidenced))
      .filter((t) => !(assertionsByTc.get(t.tc_id) || []).length)
      .filter((t) => (gapScope === 'all'
        ? true
        : gapScope === 'scenario'
          ? isDetectionBackable(t)
          : !isDetectionBackable(t)))
      .filter((t) => (includeUnscoreable ? true : t.is_scoreable !== false))
      .filter((t) => (selectedUc ? t.uc_id === selectedUc : true))
      .slice()
      .sort(gapSort)

    const closable = []
    const blocked = []
    for (const t of source) {
      // "blocked" is a prerequisite (ingest the source), not an impossibility —
      // listed apart so the backlog count above is work that can start today.
      if (proofScope(t).blocked) blocked.push(t)
      else closable.push(t)
    }
    return { closable, blocked }
  }, [gaps, testCases, assertionsByTc, gapScope, includeUnscoreable, selectedUc])

  const p1Gaps = gapBuckets.closable.filter((t) => t.priority === 'P1').length

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
      <div className="uctc__pagehead">
        <div className="uctc__pagehead-bar" aria-hidden="true" />
        {/* Masthead eyebrow: the nav group alone, matching every other
            redesigned destination (M-4) — not group+destination-name. */}
        <div className="uctc__pagehead-eyebrow mono">Analyze</div>
        <h1 className="uctc__pagehead-title">UC / TC Index</h1>
      </div>
      <div className="adapter-registry__intro">
        <p className="adapter-registry__intro-prose">
          The FY27 master <strong>Use-Case / Test-Case index</strong> —{' '}
          {indexLoaded ? (
            <>
              <span className="mono">{stats.total}</span> test cases across{' '}
              <span className="mono">{summary?.totals?.use_cases ?? useCases.length}</span>{' '}
              use cases. A row is bound by an attack <strong>scenario</strong>{' '}
              (DET/HNT) or by an <strong>assertion</strong> (POS/PLT/AUT) — two
              different mechanisms, two different ceilings. A binding is not a
              verdict: only a recorded tenant run makes a row measured. The rest is the
              honest gap — separated from the rows that need a data source
              onboarded first, and from the rows no threshold can ever score.
            </>
          ) : (
            <>the snapshot is not present on this SimCore, so nothing can be
              joined to the engine&rsquo;s evidence.</>
          )}
        </p>
        <div className="adapter-registry__stats">
          <Stat value={dash(stats.total)} label="test cases" />
          <Stat value={dash(stats.detection_backable)} label="DET / HNT" />
          {/* "artifact bound" is NOT "evidenced". This tile counts rows a scenario
              OR an assertion binds; an assertion binding is authorship, not proof,
              until a tenant run records a pass/fail (assertions.md §10). Labelling
              it "evidenced" moved the number a DC reads out loud from 86 to 94 on
              authorship alone — the exact inflation this surface exists to expose.
              The two fields ship side by side so neither can be read as the other. */}
          <Stat value={dash(stats.evidenced)} label="artifact bound" tone="detected" />
          {stats.measured !== null && (
            <Stat value={dash(stats.measured)} label="tenant-measured" tone={stats.measured ? 'detected' : 'pending'} />
          )}
          <Stat value={dash(stats.evidenced_detection_backable)} label="DET/HNT evidenced" tone="detected" />
          <Stat value={dash(stats.gaps)} label="open gaps" tone="pending" />
          {stats.needs_data_path !== null && (
            <Stat value={dash(stats.needs_data_path)} label="need a data path" />
          )}
          <Stat value={indexLoaded ? `v${String(stats.version).replace(/^v/, '')}` : '—'} label="index" />
        </div>
      </div>

      {indexLoaded && classRows.length > 0 && (
        <ClassStrip
          rows={classRows}
          totals={mechanismTotals}
          activeClass={fClass}
          onPick={(cls) => {
            setFClass((cur) => (cur === cls ? 'all' : cls))
            setParams({ tab: 'index' })
          }}
          onExplain={() => setTab('mechanisms')}
        />
      )}

      {error && (
        <div className="adapter-registry__error mono" role="alert">{error}</div>
      )}

      <div className="uctc__modebar">
        <div className="lab__segmented" role="tablist" aria-label="UC/TC index view mode">
          <ModeTab id="index" active={tab} onChange={setTab} title="Browse the index use case by use case">Index</ModeTab>
          <ModeTab id="mechanisms" active={tab} onChange={setTab} title="Coverage by validation class — what proves each class, what it can ever certify, and what blocks the rest">Mechanisms</ModeTab>
          <ModeTab id="coverage" active={tab} onChange={setTab} title="Per-use-case coverage rollups">Coverage</ModeTab>
          <ModeTab id="gaps" active={tab} onChange={setTab} title="Test cases no artifact evidences, split by whether the engine can close them">Gaps</ModeTab>
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
      ) : tab === 'mechanisms' ? (
        <MechanismsMode
          rows={classRows}
          totals={mechanismTotals}
          availability={availability}
          onPickClass={(cls) => { setFClass(cls); setParams({ tab: 'index' }) }}
          onPickOutOfScope={(cls) => {
            setFClass(cls); setFReach('needs-data-path'); setFEvidence('unevidenced')
            setParams({ tab: 'index' })
          }}
        />
      ) : tab === 'coverage' ? (
        <CoverageMode
          coverage={coverage}
          error={coverageError}
          useCases={useCases}
          onPickUc={(ucId) => setParams({ tab: 'index', uc: ucId, tc: null })}
        />
      ) : tab === 'gaps' ? (
        <GapsMode
          rows={gapBuckets.closable}
          blocked={gapBuckets.blocked}
          p1={p1Gaps}
          scope={gapScope}
          onScope={setGapScope}
          availability={availability}
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
                  className="btn uctc__btn--clear"
                  data-testid="uctc-clear-filters"
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
              options={['evidenced', 'unevidenced', 'scenario', 'assertion']}
              onChange={setFEvidence}
              titles={EVIDENCE_HINT}
            />
            <FilterRow
              label="scoreable"
              active={fScoreable}
              options={['scoreable', 'qualitative']}
              onChange={setFScoreable}
              titles={SCOREABLE_HINT}
            />
            <FilterRow
              label="engine reach"
              active={fReach}
              options={['xql-direct', 'needs-data-path']}
              onChange={setFReach}
              titles={REACH_HINT}
            />
            {options.planes.length > 0 && (
              <FilterRow label="plane" active={fPlane} options={options.planes} onChange={setFPlane} />
            )}
            {options.sheets.length > 1 && (
              <FilterRow label="sheet" active={fSheet} options={options.sheets} onChange={setFSheet} />
            )}

            <TestCaseTable
              rows={visible}
              assertionsByTc={assertionsByTc}
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
          assertions={assertionsByTc.get(selectedTc) || []}
          runsByAssertion={runsByAssertion}
          availability={availability}
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
        className={'stack-coverage__stat-value mono' + (tone ? ` uctc__tone-${tone}` : '')}
      >
        {value}
      </div>
      <div className="stack-coverage__stat-label">{label}</div>
    </div>
  )
}

/* ─── class strip — the five numbers that replace the one ───────────── */

/**
 * Always-on breakdown by validation class.
 *
 * Each cell reads ``proven / total`` over a three-segment bar:
 *   green  — an artifact binds it (a binding, not yet a measured verdict)
 *   amber  — open, and an artifact can measure it against XQL today
 *   steel  — open, and the index points at a console outside XQL: real work,
 *            but it needs that source ingested first, so it must not be
 *            counted in the same breath as the amber rows
 *
 * The mechanism name sits under each cell because "18/110" means nothing until
 * you know that closing one means authoring an assertion, not a scenario.
 */
function ClassStrip({ rows, totals, activeClass, onPick, onExplain }) {
  return (
    <section className="uctc__classstrip" data-testid="uctc-classstrip"
      aria-label="Coverage by validation class">
      <div className="uctc__classstrip-head mono">
        <span>
          coverage by validation class — <strong>{totals.proven}</strong> of{' '}
          <strong>{totals.total}</strong> have an artifact bound · only{' '}
          <strong>{totals.certifiable}</strong> ({totals.certifiable_pct}%) carry a
          threshold that can ever return a machine{' '}
          <span className="mono">pass</span> · <strong>
            {totals.open_needs_data_path}
          </strong> open rows need a data source onboarded first
        </span>
        <button
          type="button"
          className="uctc__link"
          data-testid="uctc-classstrip-explain"
          onClick={onExplain}
        >
          what proves each class →
        </button>
      </div>
      <div className="uctc__classstrip-cells">
        {rows.map((r) => {
          const isActive = activeClass === r.validation_class
          return (
            <button
              key={r.validation_class}
              type="button"
              className={'uctc__classcell' + (isActive ? ' is-active' : '')}
              data-testid={`uctc-class-${r.validation_class}`}
              aria-pressed={isActive}
              title={`${r.validation_class} — ${CLASS_HINT[r.validation_class] || ''}. `
                + `Proven by: ${r.mechanism ? r.mechanism.label : 'no mechanism'}. `
                + `${r.measured} carry a recorded tenant verdict. `
                + `${r.certifiable} of ${r.total} can ever return a machine pass; `
                + `${r.open_needs_data_path} open row(s) need a data path first.`}
              onClick={() => onPick(r.validation_class)}
            >
              <span className="uctc__classcell-head mono">
                <span className="uctc__classcell-id">{r.validation_class}</span>
                <span className="uctc__classcell-num">{r.proven}/{r.total}</span>
              </span>
              <span className="coverage__summary-bar uctc__classcell-bar">
                {r.proven > 0 && (
                  <span className="coverage__summary-seg uctc__bar-seg--detected"
                    style={{ width: `${pct(r.proven, r.total)}%` }} />
                )}
                {r.open_direct > 0 && (
                  <span className="coverage__summary-seg uctc__bar-seg--pending"
                    style={{ width: `${pct(r.open_direct, r.total)}%` }} />
                )}
                {r.open_needs_data_path > 0 && (
                  <span className="coverage__summary-seg uctc__bar-seg--hairline"
                    style={{ width: `${pct(r.open_needs_data_path, r.total)}%` }} />
                )}
              </span>
              <span className="uctc__classcell-mech mono">
                {r.mechanism ? r.mechanism.label : 'no mechanism'}
              </span>
            </button>
          )
        })}
      </div>
      <div className="uctc__classstrip-legend mono">
        <LegendKey tone="detected" label="an artifact binds it" />
        <LegendKey tone="pending" label="open — measurable over XQL today" />
        <LegendKey tone="hairline" label="open — needs a data path first" />
      </div>
    </section>
  )
}

function LegendKey({ tone, label }) {
  return (
    <span className="uctc__legendkey">
      <span className={`uctc__legendswatch uctc__legendswatch--${tone}`} aria-hidden="true" />
      {label}
    </span>
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
        <div className="coverage__empty mono uctc__rail-empty">no use cases</div>
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
                      className="coverage__summary-seg uctc__bar-seg--detected"
                      style={{ width: `${pct(evidenced, total)}%` }}
                    />
                  )}
                  {detOpen > 0 && (
                    <div
                      className="coverage__summary-seg uctc__bar-seg--pending"
                      style={{ width: `${pct(detOpen, total)}%` }}
                    />
                  )}
                  {rest > 0 && (
                    <div
                      className="coverage__summary-seg uctc__bar-seg--hairline"
                      style={{ width: `${pct(rest, total)}%` }}
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
  assertionsByTc = new Map(),
}) {
  if (!rows.length) {
    if (activeUc && !hasActiveFilters) {
      const c = activeUc.counts || {}
      return (
        <div className="coverage__empty mono" data-testid="uctc-empty-uc">
          no test cases in <span className="mono">{activeUc.uc_id}</span> match —{' '}
          {(c.detection_backable || 0)} detection rows open{' '}
          <button type="button" className="btn uctc__btn-inline--ml4" onClick={onSeeGaps}>
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
          className="btn uctc__btn-inline--ml4"
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
              const asserts = assertionsByTc.get(tc.tc_id) || []
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
                      <EvidenceCell tc={tc} evidence={ev} assertions={asserts} />
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

/**
 * What proves this row, named by mechanism — or, when nothing does, whether the
 * engine could prove it at all.
 *
 * "—" for an unevidenced row conflates two very different states, and the
 * difference is the whole point of this surface: a row the engine can reach is
 * a backlog item, a row it cannot is a scope boundary.
 */
function EvidenceCell({ tc, evidence, assertions }) {
  const scenarioCount = evidence.scenario_count || (evidence.scenario_ids || []).length
  if (evidence.evidenced || assertions.length) {
    return (
      <span className="uctc__tone-detected">
        {evidence.evidenced && (
          <span title={(evidence.scenario_ids || []).join(', ')}>
            {scenarioCount} scenario{scenarioCount === 1 ? '' : 's'}
          </span>
        )}
        {evidence.evidenced && assertions.length ? ' · ' : ''}
        {assertions.length > 0 && (
          <span title={assertions.map((a) => a.assertion_id).join(', ')}>
            {assertions.length} assertion{assertions.length === 1 ? '' : 's'}
          </span>
        )}
      </span>
    )
  }
  const scope = proofScope(tc)
  if (scope.blocked) {
    return (
      <span
        className="chip uctc__chip-oos"
        data-testid={`uctc-datapath-cell-${tc.tc_id}`}
        title={scope.reason}
      >
        needs a data path
      </span>
    )
  }
  return <span className="uctc__tone-muted">—</span>
}

function ClassChip({ value }) {
  if (!value) return <span className="mono">—</span>
  const det = DET_CLASSES.includes(value)
  return (
    <span
      className={'chip uctc__chip ' + (det ? 'uctc__tone-action' : 'uctc__tone-muted')}
      title={CLASS_HINT[value] || value}
    >
      {value}
    </span>
  )
}

function TierChip({ value }) {
  if (!value) return <span className="mono">—</span>
  const tone =
    value === 'MOAT' ? 'detected'
      : value === 'LEAD' ? 'signal'
        : value === 'EMERGING' ? 'pending'
          : 'muted'
  return (
    <span className={`chip uctc__chip uctc__tone-${tone}`} title={TIER_HINT[value] || value}>
      {value}
    </span>
  )
}

/* ─── detail drawer ─────────────────────────────────────────────────── */

function TestCaseDetail({
  tcId, detail, loading, onClose, onPickUc, onNavigate,
  assertions = [], availability = {}, runsByAssertion = new Map(),
}) {
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

      <ProofMechanism
        detail={detail}
        assertions={assertions}
        availability={availability}
        runsByAssertion={runsByAssertion}
      />

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
                          className="btn uctc__btn-inline--ml6"
                          data-testid={`uctc-open-library-${s.scenario_id}`}
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
              <span key={k} className={`chip uctc__chip ${verdictTone(k)}`}>
                {v} {k}
              </span>
            ))}
            {runs.latest?.run_id && (
              <button
                type="button"
                className="btn uctc__btn-inline"
                data-testid="uctc-open-run"
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

function verdictTone(v) {
  if (v === 'pass') return 'uctc__tone-detected'
  if (v === 'fail') return 'uctc__tone-missed'
  return 'uctc__tone-muted'
}

/**
 * "What would prove this row, and can this engine do it?" — answered before
 * the measurement contract, because it decides whether the rest is reachable.
 */
function ProofMechanism({ detail, assertions, availability, runsByAssertion = new Map() }) {
  const cls = detail.validation_class
  const m = MECHANISM[cls]
  const { reach, surfaces } = readPath(detail)
  const scope = proofScope(detail)
  const proven = Boolean(detail.evidence && detail.evidence.evidenced) || assertions.length > 0

  return (
    <DetailSection title="Proof mechanism">
      <dl className="cov-detail__kv">
        <dt className="mono">proof mechanism</dt>
        <dd className="mono">
          {m ? `${m.label} — ${m.proves}` : 'no mechanism defined for this class'}
        </dd>
        <dt className="mono">artifact</dt>
        <dd className="mono">{m ? m.artifact : '—'}</dd>
        <dt className="mono">engine read path</dt>
        <dd className="mono">
          {reach === REACH.DIRECT && (
            <span className="uctc__tone-detected">
              XQL against a registered XSIAM tenant
            </span>
          )}
          {reach === REACH.INGEST && (
            <span className="uctc__tone-muted">
              the index points at {surfaces.join(' · ')} — outside XQL
            </span>
          )}
          {reach === REACH.UNKNOWN && (
            <span className="uctc__tone-muted">
              the index declares no source for this row
            </span>
          )}
        </dd>
        <dt className="mono">can it ever pass</dt>
        <dd className="mono">
          {scope.certifiable
            ? <span className="uctc__tone-detected">
              yes — the index carries a measurable threshold
            </span>
            : <span className="uctc__tone-muted">
              no — no measurable threshold, so the verifier clamps to not_applicable
            </span>}
        </dd>
      </dl>

      {scope.blocked && (
        <div className="uctc__banner uctc__banner--muted mono" data-testid="uctc-detail-datapath">
          <strong>Needs a data path first.</strong> This row is measured by a
          read-only probe, and every probe queries XQL
          (<span className="mono">core/integrations/xsiam</span>). The index
          points at {surfaces.join(' · ')}, so an assertion can measure it only
          once that source is ingested as a queryable dataset — the way
          <span className="mono"> POS-CSPM-001</span> measures Cortex Cloud
          posture findings through <span className="mono">cortex_cloud_posture</span>.
          Real work with a prerequisite, not a closed door.
        </div>
      )}

      {!scope.blocked && !scope.autoVerifiable && (
        <div className="uctc__banner uctc__banner--muted mono" data-testid="uctc-detail-manual">
          <strong>Manual verification only.</strong> The engine generates the
          signal for this row, but the index points at {surfaces.join(' · ')},
          which is not XQL-queryable — so the detection is confirmed in that
          console and the Result is validated by hand. Still provable; just not
          auto-reconciled.
        </div>
      )}

      {m && m.id === 'assertion' && assertions.length === 0 && !scope.blocked && (
        <div className="uctc__banner mono" data-testid="uctc-detail-needs-assertion">
          {availability.endpoint === 'absent'
            ? 'No assertion binds this row, and this SimCore does not serve /api/assertions — the mechanism that would prove it is not deployed here.'
            : 'No assertion binds this row yet. Closing it means authoring '
              + `assertions/${String(cls || '').toLowerCase()}/*.yml — not another attack scenario.`}
        </div>
      )}

      {assertions.length > 0 && (
        <div className="uctc__assertions" data-testid="uctc-detail-assertions">
          {assertions.map((a) => (
            <AssertionCard
              key={a.assertion_id}
              assertion={a}
              verdict={latestVerdict(runsByAssertion.get(a.assertion_id))}
            />
          ))}
        </div>
      )}
    </DetailSection>
  )
}

/**
 * One bound assertion, rendered so the DC can read exactly what it does and
 * does NOT prove.
 *
 * ``scope_limitations`` is shown in full and never collapsed: a partial proof
 * that hides its edges is a false claim. Each check shows its threshold AND its
 * negative control — the named condition that turns it red — because an
 * assertion that cannot fail proves nothing, and the reader is entitled to
 * check that claim rather than take it on trust.
 */
function AssertionCard({ assertion, verdict }) {
  const checks = assertion.checks || []
  return (
    <article className="uctc__assertcard" data-testid={`uctc-assertion-${assertion.assertion_id}`}>
      <header className="uctc__assertcard-head">
        <span className="mono uctc__assertcard-id">{assertion.assertion_id}</span>
        <span className="uctc__assertcard-name">{assertion.name}</span>
        <span className="chip uctc__chip mono">{assertion.kind}</span>
        {assertion.status && assertion.status !== 'active' && (
          <span className="chip uctc__chip-qual mono">{assertion.status}</span>
        )}
        {/* Authored is not measured. Say which one this is, every time. */}
        {verdict
          ? (
            <span className={`chip uctc__chip mono ${verdictTone(verdict.verdict)}`}>
              {verdict.verdict}{verdict.reason ? ` · ${verdict.reason}` : ''}
            </span>
          )
          : (
            <span className="chip uctc__chip-qual mono" data-testid={`uctc-assertion-unrun-${assertion.assertion_id}`}>
              never run — no tenant verdict recorded
            </span>
          )}
      </header>

      {assertion.tc_scoreable === false && (
        <div className="uctc__banner mono">
          The index carries no measurable threshold for the bound test case, so a
          PASS is clamped to <span className="mono">not_applicable</span> — this is
          evidence, not a scored pass. A FAIL is never clamped.
        </div>
      )}

      {assertion.scope_limitations && (
        <div className="uctc__assertcard-scope">
          <div className="competitive__detail-label mono">does NOT prove</div>
          <p className="uctc__detail-prose">{assertion.scope_limitations}</p>
        </div>
      )}

      {assertion.stimulus && assertion.stimulus.kind && (
        <div className="uctc__chiprow mono">
          <span className="competitive__filter-label mono">stimulus:</span>
          <span className="chip uctc__chip">{assertion.stimulus.kind}</span>
          {assertion.stimulus.detail && (
            <span className="uctc__muted">{assertion.stimulus.detail}</span>
          )}
        </div>
      )}

      {checks.length > 0 && (
        <div className="uctc__tablewrap">
          <table className="uctc__table uctc__table--compact">
            <thead>
              <tr>
                <th scope="col">Check</th>
                <th scope="col">Probe</th>
                <th scope="col">Threshold</th>
                <th scope="col">Turns red when</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((c) => {
                const t = c.threshold || {}
                const nc = c.negative_control || {}
                return (
                  <tr key={c.check_id}>
                    <td className="uctc__cell-title" title={c.title || ''}>
                      <span className="mono">{c.check_id}</span>
                      {c.primary ? <span className="chip chip--signal">primary</span> : null}
                      <div className="uctc__muted">{c.title}</div>
                    </td>
                    <td className="mono">{c.probe}</td>
                    <td className="mono">
                      {t.kpi ? `${t.kpi} ` : ''}{t.op} {t.value}{t.unit || ''}
                      {t.source && <div className="uctc__muted">{t.source}</div>}
                    </td>
                    <td className="uctc__cell-kpi">
                      {nc.description || '—'}
                      {nc.measured_value !== undefined && nc.measured_value !== null && (
                        <span className="mono uctc__muted"> (measured {String(nc.measured_value)})</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </article>
  )
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

/* ─── mechanisms mode ───────────────────────────────────────────────── */

/**
 * The whole-index answer: for each validation class, what proves it, how much
 * is proven, and — the part a flat percentage destroys — how much the engine
 * could ever prove.
 *
 * Three separations are load-bearing here and none may be collapsed:
 *   · open-and-reachable  vs  out-of-engine-scope     (backlog vs not)
 *   · mechanism deployed  vs  content authored        (0 ≠ 0)
 *   · scoreable           vs  qualitative             (pass possible vs never)
 */
function MechanismsMode({ rows, totals, availability, onPickClass, onPickOutOfScope }) {
  if (!rows.length) {
    return <div className="coverage__empty mono" data-testid="uctc-mech-empty">no test cases to classify</div>
  }
  return (
    <div className="uctc__mech" data-testid="uctc-mechanisms">
      <p className="uctc__mech-prose">
        The index is not one population. Its {totals.total} test cases split five
        ways by validation class, and each class is proven by a{' '}
        <strong>different mechanism</strong>. DET and HNT rows are proven by an
        attack scenario firing a detection. POS, PLT and AUT rows are not
        detections at all — a posture finding must be <em>discovered</em>, a
        platform capability must be <em>present</em>, an automation outcome must{' '}
        <em>occur inside a budget</em> — so they are proven by an assertion.
        Authoring more attack scenarios cannot close a single one of them.
      </p>

      {rows.map((r) => (
        <MechanismCard
          key={r.validation_class}
          row={r}
          availability={availability}
          onPickClass={onPickClass}
          onPickOutOfScope={onPickOutOfScope}
        />
      ))}

      <AssertionCatalogPanel availability={availability} />
    </div>
  )
}

function MechanismCard({ row, availability, onPickClass, onPickOutOfScope }) {
  const m = row.mechanism || {}
  const isAssertion = m.id === 'assertion'
  // For the assertion classes the mechanism can be absent from the deployment
  // entirely. That is a different sentence from "nothing authored yet", and
  // both are different from "covered".
  const mechState = !isAssertion
    ? {
      tone: 'detected',
      text: row.by_scenario
        ? `deployed · ${row.by_scenario} row(s) bound by the scenario corpus`
        : 'deployed · no scenario binds a row of this class yet',
    }
    : availability.endpoint === 'absent'
      ? { tone: 'missed', text: 'NOT DEPLOYED on this SimCore — /api/assertions is absent' }
      : availability.endpoint === 'error'
        ? { tone: 'missed', text: `unavailable — ${availability.detail}` }
        : availability.authored === 0
          ? { tone: 'pending', text: 'deployed · no assertion artifacts authored yet' }
          : { tone: 'detected', text: `deployed · ${availability.authored} assertion(s) loaded` }

  return (
    <section className="uctc__mechcard" data-testid={`uctc-mech-${row.validation_class}`}>
      <header className="uctc__mechcard-head">
        <div>
          <div className="uctc__mechcard-eyebrow mono">
            <ClassChip value={row.validation_class} />{' '}
            {CLASS_HINT[row.validation_class] || row.validation_class}
          </div>
          <h3 className="uctc__mechcard-title">{m.label || 'no mechanism'}</h3>
          <p className="uctc__mechcard-proves">proves {m.proves || '—'}</p>
        </div>
        <div className="uctc__mechcard-score mono">
          <span className="uctc__mechcard-score-num">{row.proven}</span>
          <span className="uctc__mechcard-score-den">/ {row.total}</span>
          <span className="uctc__mechcard-score-label">artifact bound</span>
        </div>
      </header>

      <div className="coverage__summary-bar uctc__mechcard-bar">
        {row.proven > 0 && (
          <span className="coverage__summary-seg uctc__bar-seg--detected"
            style={{ width: `${pct(row.proven, row.total)}%` }} />
        )}
        {row.open_direct > 0 && (
          <span className="coverage__summary-seg uctc__bar-seg--pending"
            style={{ width: `${pct(row.open_direct, row.total)}%` }} />
        )}
        {row.open_needs_data_path > 0 && (
          <span className="coverage__summary-seg uctc__bar-seg--hairline"
            style={{ width: `${pct(row.open_needs_data_path, row.total)}%` }} />
        )}
      </div>

      <dl className="cov-detail__kv uctc__mechcard-kv">
        <dt className="mono">artifact</dt>
        <dd className="mono">{m.artifact || '—'}</dd>
        <dt className="mono">how it is scored</dt>
        <dd className="mono">{m.verdictPath || '—'}</dd>
        <dt className="mono">mechanism status</dt>
        <dd className={`mono uctc__tone-${mechState.tone}`}>{mechState.text}</dd>
        <dt className="mono">measured</dt>
        <dd className="mono">
          {isAssertion
            ? (
              <span className={row.measured ? 'uctc__tone-detected' : 'uctc__tone-pending'}>
                {row.measured} of {row.proven} bound row(s) carry a recorded tenant
                verdict — the rest are authored claims awaiting a run
              </span>
            )
            : 'per-run, on the Runs & Proof surface — a binding here is not a verdict'}
        </dd>
        <dt className="mono">verdict ceiling</dt>
        <dd className="mono">
          {row.certifiable} of {row.total} ({row.certifiable_pct}%) can ever
          return a machine <span className="mono">pass</span> — the rest carry no
          measurable threshold in the index and are clamped to{' '}
          <span className="chip uctc__chip-qual">not_applicable</span>
        </dd>
        {row.manual_only > 0 && (
          <>
            <dt className="mono">read-back</dt>
            <dd className="mono">
              {row.manual_only} of {row.total} have no XQL read path — the
              detection is confirmed in the vendor console and the Result is
              validated by hand, not auto-reconciled
            </dd>
          </>
        )}
        {row.open_needs_data_path > 0 && (
          <>
            <dt className="mono">blocked on ingest</dt>
            <dd className="mono">
              {row.open_needs_data_path} open row(s) point at a console outside
              XQL — a probe reaches them once that source is onboarded as a
              queryable dataset
            </dd>
          </>
        )}
      </dl>

      <div className="uctc__mechcard-actions">
        <button
          type="button"
          className="btn"
          data-testid={`uctc-mech-open-${row.validation_class}`}
          onClick={() => onPickClass(row.validation_class)}
        >
          {row.open_direct} open · measurable today →
        </button>
        {row.open_needs_data_path > 0 && (
          <button
            type="button"
            className="btn"
            data-testid={`uctc-mech-datapath-${row.validation_class}`}
            onClick={() => onPickOutOfScope(row.validation_class)}
          >
            {row.open_needs_data_path} need a data path →
          </button>
        )}
        {row.class_mismatch > 0 && (
          <span className="chip uctc__chip-delta mono"
            title="These rows are bound by an attack scenario, but the index asks for a posture / platform / automation assertion (loader code S-14). Counted as evidence, but the artifact type does not match the claim.">
            {row.class_mismatch} bound by a scenario, not an assertion
          </span>
        )}
      </div>

      {row.open_needs_data_path > 0 && (
        <div className="uctc__mechcard-oos" data-testid={`uctc-datapath-${row.validation_class}`}>
          <div className="competitive__detail-label mono">
            open, but blocked on a data path
          </div>
          <p className="uctc__mechcard-oosprose">
            Every probe queries XQL
            (<span className="mono">core/integrations/xsiam</span>), and the index
            points these rows at a console outside it. They are measurable once
            that source is ingested as a queryable dataset — sized separately
            because the onboarding is real work a DC should not discover in front
            of a customer.
          </p>
          <ul className="uctc__oos-list mono">
            {row.needs_data_path_surfaces.map((s) => (
              <li key={s.surface}>
                <span className="mono uctc__oos-count">{s.count}</span> {s.surface}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

/**
 * The assertion catalog, including what the loader REFUSED.
 *
 * A falsifiability guard nobody can see is the same as no guard: if an
 * assertion was rejected because its threshold could never fail, the person who
 * authored it has to be able to read that here.
 */
function AssertionCatalogPanel({ availability }) {
  const rejected = availability.rejectedRows || []
  return (
    <section className="uctc__mechcard uctc__assertpanel" data-testid="uctc-assertion-panel">
      <header className="uctc__mechcard-head">
        <div>
          <h3 className="uctc__mechcard-title">Assertion catalog</h3>
          <p className="uctc__mechcard-proves">
            the POS / PLT / AUT proof mechanism as deployed on this SimCore
          </p>
        </div>
      </header>
      {availability.endpoint === 'absent' && (
        <div className="uctc__banner mono" data-testid="uctc-assertions-absent">
          This SimCore does not serve <span className="mono">/api/assertions</span>.
          The POS / PLT / AUT mechanism is not deployed here, so those classes read
          as unproven — which is the honest answer, not zero coverage.
        </div>
      )}
      {availability.endpoint === 'error' && (
        <div className="adapter-registry__error mono" role="alert">
          assertion catalog unavailable — {availability.detail}
        </div>
      )}
      {availability.endpoint === 'empty' && (
        <div className="uctc__banner mono" data-testid="uctc-assertions-empty">
          The mechanism is deployed and no assertion artifacts are authored yet.
          Every POS / PLT / AUT row is therefore still owed —{' '}
          <span className="mono">assertions/{'{pos,plt,aut}'}/*.yml</span>.
        </div>
      )}
      {availability.endpoint === 'live' && (
        <div className="uctc__chiprow mono" data-testid="uctc-assertions-live">
          <span className="chip chip--signal">{availability.authored} loaded</span>
          {Object.entries(availability.byClass || {}).map(([k, v]) => (
            <span key={k} className="chip uctc__chip">{v} {k}</span>
          ))}
          {(availability.probes || []).length > 0 && (
            <span className="uctc__muted">
              · probes: {(availability.probes || []).map((p) => p.name).join(', ')}
            </span>
          )}
        </div>
      )}
      {availability.rejected > 0 && (
        <div className="uctc__mechcard-oos" data-testid="uctc-assertions-rejected">
          <div className="competitive__detail-label mono">
            refused by the loader ({availability.rejected})
          </div>
          <p className="uctc__mechcard-oosprose">
            An assertion whose check can never fail proves nothing and does not
            load. These artifacts were rejected — they are not counted anywhere
            on this surface.
          </p>
          <ul className="uctc__oos-list mono">
            {rejected.map((r, i) => (
              <li key={r.source || i}>
                <span className="mono">{r.source}</span>
                {(r.diagnostics || []).map((d) => (
                  <div key={d.code} className="uctc__muted">
                    {d.code} — {d.detail}
                  </div>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
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
              {evidenced > 0 && <span className="coverage__summary-seg uctc__bar-seg--detected" style={{ width: `${pct(evidenced, total)}%` }} />}
              {detOpen > 0 && <span className="coverage__summary-seg uctc__bar-seg--pending" style={{ width: `${pct(detOpen, total)}%` }} />}
              {rest > 0 && <span className="coverage__summary-seg uctc__bar-seg--hairline" style={{ width: `${pct(rest, total)}%` }} />}
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

const GAP_SCOPE = [
  { id: 'scenario', label: 'DET / HNT', hint: 'closed by authoring an attack scenario' },
  { id: 'assertion', label: 'POS / PLT / AUT', hint: 'closed by authoring an assertion' },
  { id: 'all', label: 'all classes', hint: 'the whole index backlog' },
]

function GapsMode({
  rows, blocked = [], p1, scope = 'scenario', onScope = () => {},
  availability = {}, includeUnscoreable, onToggleUnscoreable,
  scopedUc, onClearUc, onOpen,
}) {
  const active = GAP_SCOPE.find((s) => s.id === scope) || GAP_SCOPE[0]
  return (
    <div className="uctc__gaps" data-testid="uctc-gaps">
      <div className="uctc__gaps-scope" role="group" aria-label="Gap backlog scope">
        {GAP_SCOPE.map((s) => (
          <button
            key={s.id}
            type="button"
            className={'competitive__filter' + (scope === s.id ? ' is-active' : '')}
            data-testid={`uctc-gapscope-${s.id}`}
            aria-pressed={scope === s.id}
            title={s.hint}
            onClick={() => onScope(s.id)}
          >
            {s.label}
          </button>
        ))}
        <span className="uctc__muted mono">— {active.hint}</span>
      </div>

      {scope !== 'scenario' && availability.endpoint === 'absent' && (
        <div className="uctc__banner mono" data-testid="uctc-gaps-noassertions">
          These rows are closed by an assertion, and this SimCore does not serve{' '}
          <span className="mono">/api/assertions</span> — the mechanism is not
          deployed here, so none of them is closable on this box today.
        </div>
      )}

      <div className="uctc__gaps-head">
        <div className="uctc__gaps-headline">
          <span className="mono" data-testid="uctc-gaps-p1">{p1}</span>{' '}
          P1 test cases with no engine artifact
          <span className="uctc__muted mono">
            {' '}· {rows.length} open in {active.label} an artifact can measure today
          </span>
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
          no unevidenced test cases an artifact can measure today in this scope
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

      {blocked.length > 0 && (
        <section className="uctc__gaps-oos" data-testid="uctc-gaps-blocked">
          <div className="competitive__detail-label mono">
            blocked on a data path ({blocked.length}) — real work, with a prerequisite
          </div>
          <p className="uctc__mechcard-oosprose">
            The index points these rows at a console outside XQL. An assertion
            probe reaches them only once that source is ingested as a queryable
            dataset, so they are sized apart from the backlog above rather than
            mixed into a number that implies they can start today.
          </p>
          <div className="uctc__tablewrap">
            <table className="uctc__table uctc__table--compact">
              <thead>
                <tr>
                  <th scope="col">TC</th>
                  <th scope="col">Title</th>
                  <th scope="col">Class</th>
                  <th scope="col">Evidence lives in</th>
                </tr>
              </thead>
              <tbody>
                {blocked.map((tc) => (
                  <tr key={tc.tc_id} className="uctc__table-row" onClick={() => onOpen(tc.tc_id)}>
                    <td>
                      <button
                        type="button"
                        className="uctc__idbtn mono"
                        data-testid={`uctc-blockedgap-${tc.tc_id}`}
                        onClick={(e) => { e.stopPropagation(); onOpen(tc.tc_id) }}
                      >
                        {tc.tc_id}
                      </button>
                    </td>
                    <td className="uctc__cell-title" title={tc.title || ''}>{tc.title || '—'}</td>
                    <td><ClassChip value={tc.validation_class} /></td>
                    <td className="mono uctc__cell-kpi">{tc.detection_source || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
