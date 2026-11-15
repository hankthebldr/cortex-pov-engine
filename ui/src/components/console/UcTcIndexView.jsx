import React, { useCallback, useEffect, useMemo, useState } from 'react'

import {
  getUcTcSummary,
  getUcTcUseCases,
  getUcTcUseCase,
  getUcTcTestCases,
  getUcTcTestCase,
  getAssertions,
  getAssertionRuns,
  getDoorReadiness,
  getDoors,
} from '../../api/client.js'
import { agentIdOf } from '../../api/ids.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { componentConfigure, componentRoute } from '../../app/componentRoutes.js'
import {
  MECHANISM,
  REACH,
  assertionAvailability,
  indexAssertionRuns,
  indexAssertions,
  latestVerdict,
  proofScope,
  readPath,
} from './uctcMechanisms.js'
import {
  GAP_CELL_WORDS,
  GAP_KINDS,
  GAP_LABELS,
  GAP_STAGE,
  GAP_STATUS,
  STAGES,
  STAGE_DONE_WORDS,
  anyDoorChecked,
  buildProofPaths,
  funnel,
  gapCounts,
  gaps as gapList,
  indexTiles,
  isDetectionTestCase,
  mechanismModel,
  pathsByUseCase,
  runFacts,
  runsByScenario,
  scenariosByTc,
  sourceState,
  useCaseVerdict,
} from './uctcProofPath.js'
import { Button, StatusIcon } from './kit/index.js'
import { COMPONENTS } from './componentModel.js'
import DetectionTypeChip from './DetectionTypeChip.jsx'
import VerdictReason from './VerdictReason.jsx'
import { verdictReason } from './verdictReason.js'
import useReadBack from './useReadBack.jsx'
import './UcTcIndexView.css'
import '../../styles/destinations/uctc.css'

void React

/**
 * UcTcIndexView — the FY27 v2.2 master Use-Case / Test-Case index, joined to
 * what this SimCore has actually done about each row.
 *
 * Four tabs, persisted in the hash router as ``?tab=`` (design C5 / C6):
 *   index      — four use-case tiles, the UC rail, the filterable test-case
 *                table with a Verdict and a Runs rollup per row, and the
 *                per-TC / per-UC drawers.
 *   mechanisms — one card per detection mechanism (BIOC · ABIOC · XQL ·
 *                Correlation · IOC): how many test cases rely on it, how many
 *                objects their scenarios declare, and what the tenant shows.
 *   coverage   — the proof path as five counts, then one row per test case
 *                with a status per stage.
 *   gaps       — every test case not yet detected, earliest stall first, each
 *                with one sentence and one verb.
 *
 * AUTHORED IS NOT PROVEN. A test case is covered only once a read-back run of
 * a scenario that evidences it saw a detection (uctcProofPath.js). Every
 * number comes from a SimCore API; a source that did not load reads "Can't
 * check", never zero and never green. No SimCore API reports whether a
 * mechanism's content is present in the tenant, so every mechanism reads
 * "Can't check".
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

const TABS = [
  { id: 'index', label: 'Index' },
  { id: 'mechanisms', label: 'Mechanisms' },
  { id: 'coverage', label: 'Coverage' },
  { id: 'gaps', label: 'Gaps' },
]

const COMPONENT_NAME = Object.fromEntries(COMPONENTS.map((c) => [c.key, c.name]))

/** What each source is called when it did not load. */
const SOURCE_NAMES = {
  scenarios: 'The scenario list',
  runs: 'The run list',
  readiness: 'Component readiness',
  doors: 'Door states',
}

function shortRun(id) {
  const s = String(id || '')
  return s.length > 12 ? s.slice(0, 8) : s
}

function isStaged(run) {
  return String((run && run.status) || '').toLowerCase() === 'staged'
}

/* ─── surface ───────────────────────────────────────────────────────── */

export default function UcTcIndexView({
  params = {},
  setParams = () => {},
  onNavigate = () => {},
}) {
  const tab = TABS.some((t) => t.id === params.tab) ? params.tab : 'index'
  const selectedUc = params.uc || null
  const selectedTc = params.tc || null
  const env = useEnvironment()
  const { reading, readBack } = useReadBack()

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

  // ── door readiness: one fetch for every scenario, keyed on the selected
  //    target agent (as the Library does). A failure is "Can't check" for the
  //    Components stage, never "not ready".
  const doorAgentId = agentIdOf(env.agent)
  const [doorState, setDoorState] = useState({ map: null, rows: null, error: null, loading: true })
  useEffect(() => {
    let cancelled = false
    setDoorState((s) => ({ ...s, loading: true }))
    Promise.all([getDoorReadiness(doorAgentId), getDoors()])
      .then(([ready, doors]) => {
        if (!cancelled) setDoorState({ map: ready.scenarios, rows: doors.doors, error: null, loading: false })
      })
      .catch((err) => {
        if (!cancelled) {
          setDoorState({ map: null, rows: null, error: err?.message || 'door readiness failed', loading: false })
        }
      })
    return () => { cancelled = true }
  }, [doorAgentId])

  const indexLoaded = envelope ? envelope.index_loaded !== false : true

  /* ── the proof path: every stage from its own source ── */
  const envLoading = env.loading || {}
  const envErrors = env.errors || {}
  const sources = useMemo(() => ({
    scenarios: sourceState({
      loading: envLoading.scenarios, error: envErrors.scenarios, data: env.scenarios, emptyIsFault: true,
    }),
    runs: sourceState({ loading: envLoading.runs, error: envErrors.runs, data: env.runs }),
    readiness: doorState.loading ? 'loading' : (doorState.map ? 'ok' : 'error'),
    doors: doorState.loading ? 'loading' : (doorState.rows ? 'ok' : 'error'),
  }), [env.scenarios, env.runs, envLoading.scenarios, envLoading.runs, envErrors.scenarios,
    envErrors.runs, doorState])
  const sourceErrors = { scenarios: envErrors.scenarios, runs: envErrors.runs, readiness: doorState.error, doors: doorState.error }

  const paths = useMemo(() => buildProofPaths(testCases, {
    scenarios: env.scenarios || [],
    runs: env.runs || [],
    readiness: doorState.map,
    doorRows: doorState.rows,
    sources,
  }), [testCases, env.scenarios, env.runs, doorState.map, doorState.rows, sources])
  const pathByTc = useMemo(() => new Map(paths.map((p) => [p.tcId, p])), [paths])
  const pathsByUc = useMemo(() => pathsByUseCase(paths), [paths])

  // The Runs rollup covers every row, detection or not: it is what ran.
  const scenIdx = useMemo(() => scenariosByTc(env.scenarios || []), [env.scenarios])
  const runIdx = useMemo(() => runsByScenario(env.runs || []), [env.runs])
  const rollupFor = useCallback((tcId) => runFacts(
    (scenIdx.get(tcId) || []).flatMap((s) => runIdx.get(s.scenario_id) || []),
  ), [scenIdx, runIdx])

  const useCaseIds = useMemo(() => (useCases.length
    ? useCases.map((u) => u.uc_id)
    : Array.from(new Set(testCases.map((t) => t.uc_id).filter(Boolean)))), [useCases, testCases])
  const tiles = useMemo(() => indexTiles({
    useCaseIds, testCases, paths, sources, doorsChecked: anyDoorChecked(doorState.rows),
  }), [useCaseIds, testCases, paths, sources, doorState.rows])
  const mech = useMemo(() => mechanismModel(paths, env.scenarios || []), [paths, env.scenarios])
  const counts = useMemo(() => gapCounts(paths), [paths])
  const reachKnown = sources.scenarios === 'ok' && sources.runs === 'ok'
  const covered = paths.filter((p) => p.covered).length

  /* ── the assertion mechanism (drawer + the Mechanisms footnote) ── */
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
      const bound = Boolean(ev.evidenced) || asserts.length > 0
      if (selectedUc && t.uc_id !== selectedUc) return false
      if (fClass !== 'all' && t.validation_class !== fClass) return false
      if (fTier !== 'all' && t.differentiation_tier !== fTier) return false
      if (fPriority !== 'all' && t.priority !== fPriority) return false
      if (fSheet !== 'all' && t.tc_sheet !== fSheet) return false
      if (fEvidence === 'evidenced' && !bound) return false
      if (fEvidence === 'unevidenced' && bound) return false
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

  /* ── use-case detail drawer (drill-down) ── */
  // Selecting a UC row already filters the TC list; it now ALSO drills into the
  // use case itself — its entitlements, coverage, UCS sub-groups and the test
  // cases under it. A selected TC's own drawer wins, so the UC drawer only shows
  // when a use case is open WITHOUT a specific test case.
  const [ucDetail, setUcDetail] = useState(null)
  const [ucDetailLoading, setUcDetailLoading] = useState(false)
  useEffect(() => {
    if (!selectedUc || selectedTc) { setUcDetail(null); return undefined }
    let cancelled = false
    setUcDetailLoading(true)
    setUcDetail(null)
    getUcTcUseCase(selectedUc)
      .then((d) => { if (!cancelled) setUcDetail(d) })
      .catch((e) => {
        if (!cancelled) setUcDetail({ _error: e?.message || 'Failed to load use case' })
      })
      .finally(() => { if (!cancelled) setUcDetailLoading(false) })
    return () => { cancelled = true }
  }, [selectedUc, selectedTc])

  /* ── handlers ── */
  const selectUc = useCallback((ucId) => {
    setParams({ uc: selectedUc === ucId ? null : ucId, tc: null })
  }, [setParams, selectedUc])

  const openTc = useCallback((tcId) => { setParams({ tc: tcId }) }, [setParams])
  const closeTc = useCallback(() => { setParams({ tc: null }) }, [setParams])
  const setTab = useCallback((next) => { setParams({ tab: next }) }, [setParams])
  const openComponent = useCallback((key) => {
    const r = componentRoute(key)
    onNavigate(r.destination, r.params)
  }, [onNavigate])

  // The four verbs a gap can offer, plus the two links where no verb fits.
  const actions = useMemo(() => ({
    buildChain: () => onNavigate('composer'),
    configure: (key) => {
      const r = componentConfigure(key) || componentRoute(key)
      onNavigate(r.destination, r.params)
    },
    run: (scenarioId) => onNavigate('preflight', { arm: scenarioId }),
    readBack: (runId) => readBack(runId),
    openResults: (runId) => onNavigate('proof', { run: runId }),
    openRun: (runId) => onNavigate('runs', { run: runId }),
  }), [onNavigate, readBack])

  // A missing snapshot is no index, not an empty one: no tab shows a number.
  const tabCounts = !indexLoaded || loading
    ? { index: '—', mechanisms: '—', coverage: '—', gaps: '—' }
    : {
      index: String(useCaseIds.length),
      mechanisms: String(mech.cards.length),
      coverage: reachKnown ? `${covered}/${paths.length}` : '—',
      gaps: reachKnown ? String(counts.all) : '—',
    }

  /* ── render ── */
  return (
    <div className="adapter-registry uctc" data-testid="uctc-index">
      <div className="uctc__pagehead">
        <div className="uctc__pagehead-bar" aria-hidden="true" />
        {/* Masthead eyebrow: the nav group alone, matching every other
            redesigned destination (M-4) — not group+destination-name. */}
        <h1 className="uctc__pagehead-title">UC / TC index</h1>
      </div>
      <p className="uctc__lede" data-testid="uctc-lede">
        {loading ? 'Loading the FY27 Use-Case / Test-Case index…' : indexLoaded ? (
          <>
            The FY27 Use-Case / Test-Case index{' '}
            <span className="mono">v{String(envelope?.index_version || summary?.index_version || '—').replace(/^v/, '')}</span>:{' '}
            <span className="mono">{summary?.totals?.test_cases ?? envelope?.index_total ?? testCases.length}</span> test cases
            across <span className="mono">{summary?.totals?.use_cases ?? useCases.length}</span> use cases.
            A test case counts as covered only once a read-back run detected it.
          </>
        ) : (
          <>The UC/TC index snapshot is not present on this SimCore, so nothing can
            be joined to what it has run.</>
        )}
      </p>

      {error && (
        <div className="adapter-registry__error mono" role="alert">{error}</div>
      )}

      <div className="uctc__modebar">
        <TabStrip active={tab} counts={tabCounts} onChange={setTab} />
        <div className="uctc__modebar-meta mono">
          methodology coverage — for MITRE ATT&amp;CK technique coverage see{' '}
          <button type="button" className="uctc__link" data-testid="uctc-goto-coverage" onClick={() => onNavigate('coverage', { tab: 'mitre' })}>
            Dashboards →
          </button>
          {' · '}
          <button type="button" className="uctc__link" data-testid="uctc-goto-ttps" onClick={() => onNavigate('ttps')}>
            TTP cards →
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
        <MechanismsTab
          model={mech}
          paths={paths}
          sources={sources}
          sourceErrors={sourceErrors}
          availability={availability}
          onOpenTc={openTc}
          onOpenComponent={openComponent}
        />
      ) : tab === 'coverage' ? (
        <CoverageTab
          paths={paths}
          sources={sources}
          sourceErrors={sourceErrors}
          onOpenTc={openTc}
        />
      ) : tab === 'gaps' ? (
        <GapsTab
          paths={paths}
          counts={counts}
          sources={sources}
          sourceErrors={sourceErrors}
          reading={reading}
          actions={actions}
          onOpenTc={openTc}
        />
      ) : (
        <>
          <IndexTiles tiles={tiles} sources={sources} />
          <div className="uctc__split">
            <UcRail
              groups={ucGroups}
              selected={selectedUc}
              onSelect={selectUc}
              verdictFor={(ucId) => useCaseVerdict(pathsByUc.get(ucId) || [])}
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
                pathByTc={pathByTc}
                rollupFor={rollupFor}
                sources={sources}
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
        </>
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

      {selectedUc && !selectedTc && indexLoaded && (
        <UseCaseDetail
          ucId={selectedUc}
          detail={ucDetail}
          loading={ucDetailLoading}
          onClose={() => setParams({ uc: null, tc: null })}
          onPickTc={openTc}
          onNavigate={onNavigate}
        />
      )}
    </div>
  )
}

/* ─── tab strip (design C6: selected = green wash + line) ──────────── */

function TabStrip({ active, counts, onChange }) {
  return (
    <div className="uctc__tabs" role="tablist" aria-label="UC / TC views">
      {TABS.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={active === t.id}
          className={'uctc__tab' + (active === t.id ? ' is-active' : '')}
          data-testid={`uctc-mode-${t.id}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          <span className="uctc__tab-count mono">{counts[t.id]}</span>
        </button>
      ))}
    </div>
  )
}

/* ─── which sources could not be read, and what that hides ─────────── */

function SourceNotice({ sources, errors = {}, need }) {
  // Readiness and door states arrive in one fetch: say it once.
  const down = need.filter((k) => sources[k] !== 'ok' && !(k === 'doors' && sources.readiness !== 'ok'))
  if (!down.length) return null
  const lines = down.map((k) => {
    const st = sources[k]
    if (st === 'loading') return `${SOURCE_NAMES[k]} is still loading.`
    if (st === 'empty') return 'SimCore listed no scenarios. An empty catalog is a fault, not zero coverage.'
    return `${SOURCE_NAMES[k]} did not load${errors[k] ? ` (${errors[k]})` : ''}.`
  })
  return (
    <div className="uctc__notice" role="status" data-testid="uctc-source-notice">
      {`${lines.join(' ')} What depends on it reads Can't check.`}
    </div>
  )
}

/* ─── Index: four use-case tiles ────────────────────────────────────── */

function IndexTiles({ tiles, sources }) {
  const dash = (v) => (v == null ? '—' : v)
  const cant = sources.scenarios !== 'ok' || sources.runs !== 'ok'
  const cells = [
    {
      key: 'use-cases', value: tiles.useCases, label: 'Use cases',
      sub: `${tiles.testCases} test cases under them, ${tiles.detectionTestCases} of them detections`,
    },
    {
      key: 'partial', value: dash(tiles.partlyProven), label: 'Partly proven', tone: 'partial',
      sub: tiles.partlyProven == null
        ? "Can't check — runs or scenarios did not load"
        : `at least one test case detected in a read-back run${tiles.proven ? ` · ${tiles.proven} fully` : ''}`,
    },
    {
      key: 'pending', value: dash(tiles.pending), label: 'Pending',
      sub: cant ? "Can't check — runs or scenarios did not load" : 'no read-back detection yet',
    },
    {
      key: 'blocked', value: dash(tiles.blocked), label: 'Blocked', tone: 'blocked',
      sub: tiles.blocked == null
        ? "Can't check — no component's doors have been checked yet"
        : 'a component they need is checked and not ready',
    },
  ]
  return (
    <div className="uctc__tiles" data-testid="uctc-tiles">
      {cells.map((c) => (
        <div key={c.key} className="uctc__tile" data-testid={`uctc-tile-${c.key}`}>
          <div className={'uctc__tile-value mono' + (c.tone && c.value !== '—' ? ` uctc__tile-value--${c.tone}` : '')}>
            {c.value}
          </div>
          <div className="uctc__tile-label">{c.label}</div>
          <div className="uctc__tile-sub">{c.sub}</div>
        </div>
      ))}
    </div>
  )
}

/* ─── Index: a test case's verdict, and its runs ────────────────────── */

const UC_VERDICT = {
  proven: { status: 'up', word: 'Proven' },
  partial: { status: 'degraded', word: 'Partly proven' },
  blocked: { status: 'down', word: 'Blocked' },
  pending: { status: 'off', word: 'Pending' },
  unknown: { status: 'unknown', word: "Can't check" },
}

function TcVerdict({ tc, path }) {
  if (!isDetectionTestCase(tc) || !path) {
    return (
      <span className="uctc__tone-muted" title="Posture, platform and automation test cases are proven by an assertion run, not by a detection.">
        by assertion
      </span>
    )
  }
  let status = 'off'
  let word = 'Pending'
  if (path.covered) { status = 'up'; word = 'Detected' }
  else if (path.stall === 'unknown') { status = 'unknown'; word = "Can't check" }
  else if (path.stall === 'component') { status = 'down'; word = 'Blocked' }
  else if (path.stall === 'not_seen') { status = 'degraded'; word = 'Not seen yet' }
  else if (path.stall === 'no_scenario') { status = 'off'; word = 'No scenario' }
  return (
    <span data-testid={`uctc-verdict-${tc.tc_id}`}>
      <StatusIcon status={status} word={word} />
    </span>
  )
}

function RunsRollup({ tcId, facts, sources }) {
  if (sources.scenarios !== 'ok' || sources.runs !== 'ok') {
    return (
      <span data-testid={`uctc-runs-${tcId}`} title="The run or scenario list did not load.">
        <StatusIcon status="unknown" word="Can't check" />
      </span>
    )
  }
  if (!facts.total) {
    return <span className="uctc__tone-muted" data-testid={`uctc-runs-${tcId}`}>No runs</span>
  }
  const unread = facts.executed.length - facts.readBack.length
  const parts = [`${facts.total} run${facts.total === 1 ? '' : 's'}`]
  if (facts.running.length) parts.push(`${facts.running.length} in progress`)
  if (facts.waiting.length) parts.push(`${facts.waiting.length} waiting`)
  if (unread > 0) parts.push(`${unread} not read back`)
  if (facts.readBack.length) parts.push(`${facts.readBack.length} read back`)
  return (
    <span className="uctc__runs" data-testid={`uctc-runs-${tcId}`}>
      <span>{parts.join(' · ')}</span>
      {Object.entries(facts.pendingReasons).map(([k, v]) => (
        <VerdictReason key={k} reason={k} count={v} testId={`uctc-runs-reason-${tcId}-${k}`} />
      ))}
    </span>
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

function UcRail({ groups, selected, onSelect, verdictFor = () => 'none' }) {
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
            const verdict = UC_VERDICT[verdictFor(uc.uc_id)]
            return (
              <button
                key={uc.uc_id}
                type="button"
                className={'uctc__rail-item' + (isActive ? ' is-active' : '')}
                data-testid={`uctc-uc-${uc.uc_id}`}
                aria-pressed={isActive}
                onClick={() => onSelect(uc.uc_id)}
                title={`${uc.uc_id} — ${evidenced}/${total} test cases bound by a scenario or assertion`}
              >
                <div className="uctc__rail-item-head">
                  <span className="mono uctc__rail-id">{uc.uc_id}</span>
                  <span className="mono uctc__rail-count">{evidenced}/{total}</span>
                </div>
                <div className="uctc__rail-name">{uc.use_case || uc.uc_id}</div>
                {verdict && (
                  <div className="uctc__rail-verdict" data-testid={`uctc-uc-verdict-${uc.uc_id}`}>
                    <StatusIcon status={verdict.status} word={verdict.word} />
                  </div>
                )}
                <div className="coverage__summary-bar uctc__rail-bar">
                  {evidenced > 0 && (
                    <div
                      className="coverage__summary-seg uctc__bar-seg--bound"
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
  assertionsByTc = new Map(), pathByTc = new Map(), rollupFor = () => runFacts([]),
  sources = {},
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
              <th scope="col">Verdict</th>
              <th scope="col">Runs</th>
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
                      <th colSpan={10} scope="colgroup">
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
                    <td className="uctc__cell-verdict">
                      <TcVerdict tc={tc} path={pathByTc.get(tc.tc_id)} />
                    </td>
                    <td className="uctc__cell-runs">
                      <RunsRollup tcId={tc.tc_id} facts={rollupFor(tc.tc_id)} sources={sources} />
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
      <span className="uctc__tone-bound">
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
                          Open in Scenarios →
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
            {/* Of the pending runs, the ones with nothing to score: they need a
                re-run or a closed soak window, not more verification. */}
            {Object.entries(runs.pending_reasons || {}).map(([k, v]) => (
              <VerdictReason key={k} reason={k} count={v} testId={`uctc-pending-reason-${k}`} />
            ))}
            {runs.latest?.run_id && (
              <button
                type="button"
                className="btn uctc__btn-inline"
                data-testid="uctc-open-run"
                onClick={() => onNavigate('runs', { run: runs.latest.run_id, tab: 'evidence' })}
              >
                latest {runs.latest.run_id}
                {runs.latest.reason ? ` · ${verdictReason({ reason: runs.latest.reason }).label}` : ''} →
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

/* ─── use-case detail drawer (drill-down) ─────────────────────────────── */

function UseCaseDetail({ ucId, detail, loading, onClose, onPickTc }) {
  if (loading || !detail) {
    return (
      <section className="competitive__detail uctc__detail" data-testid="uctc-uc-detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">use case</div>
            <div className="competitive__detail-title mono">{ucId}</div>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <div className="coverage__empty mono">
          {loading ? 'loading use case…' : 'no detail'}
        </div>
      </section>
    )
  }

  if (detail._error) {
    return (
      <section className="competitive__detail uctc__detail" data-testid="uctc-uc-detail">
        <div className="competitive__detail-head">
          <div>
            <div className="competitive__detail-eyebrow mono">use case</div>
            <div className="competitive__detail-title mono">{ucId}</div>
          </div>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
        <div className="adapter-registry__error mono" role="alert">{detail._error}</div>
      </section>
    )
  }

  const uc = detail.use_case || {}
  const groups = detail.ucs_groups || []
  const testCases = detail.test_cases || []
  const base = uc.base_platform_list || []
  const addons = uc.addons_list || []

  return (
    <section className="competitive__detail uctc__detail" data-testid="uctc-uc-detail">
      <div className="competitive__detail-head">
        <div>
          <div className="competitive__detail-eyebrow mono">use case</div>
          <div className="competitive__detail-title mono">{uc.uc_id || ucId}</div>
          {uc.use_case && <div className="competitive__detail-sub">{uc.use_case}</div>}
        </div>
        <button type="button" className="btn" onClick={onClose}>Close</button>
      </div>

      {(uc.domain || uc.fy27_subdomain) && (
        <div className="uctc__detail-meta mono">
          {[uc.domain, uc.fy27_subdomain].filter(Boolean).join(' · ')}
        </div>
      )}

      {/* Coverage — real counts joined by the engine, never invented. */}
      <div className="uctc__detail-stats" data-testid="uctc-uc-detail-stats">
        <div className="stack-coverage__stat">
          <div className="stack-coverage__stat-value mono">
            {uc.scenario_count ?? 0}
          </div>
          <div className="stack-coverage__stat-label">scenarios</div>
        </div>
        <div className="stack-coverage__stat">
          <div className="stack-coverage__stat-value mono">
            {uc.coverage_pct != null ? `${uc.coverage_pct}%` : '—'}
          </div>
          <div className="stack-coverage__stat-label">TC coverage</div>
        </div>
        <div className="stack-coverage__stat">
          <div className="stack-coverage__stat-value mono">
            {uc.det_coverage_pct != null ? `${uc.det_coverage_pct}%` : '—'}
          </div>
          <div className="stack-coverage__stat-label">DET coverage</div>
        </div>
      </div>

      {/* Entitlements — what a tenant must own to run this use case. */}
      {(base.length > 0 || addons.length > 0 || uc.min_license_path) && (
        <div className="uctc__detail-section">
          <div className="competitive__detail-eyebrow mono">entitlements</div>
          {base.length > 0 && (
            <div className="uctc__detail-row">
              <span className="uctc__detail-key mono">base</span>
              <span className="uctc__detail-val mono">{base.join(' · ')}</span>
            </div>
          )}
          {addons.length > 0 && (
            <div className="uctc__detail-row">
              <span className="uctc__detail-key mono">add-ons</span>
              <span className="uctc__detail-val mono">{addons.join(' · ')}</span>
            </div>
          )}
          {uc.min_license_path && (
            <div className="uctc__detail-row">
              <span className="uctc__detail-key mono">min path</span>
              <span className="uctc__detail-val mono">{uc.min_license_path}</span>
            </div>
          )}
        </div>
      )}

      {/* UCS sub-groups — the use case's internal structure. */}
      {groups.length > 0 && (
        <div className="uctc__detail-section">
          <div className="competitive__detail-eyebrow mono">
            use-case scenarios ({groups.length})
          </div>
          {groups.map((g) => (
            <div className="uctc__detail-row" key={g.ucs_id}>
              <span className="uctc__detail-key mono">{g.ucs_id}</span>
              <span className="uctc__detail-val">
                {g.ucs_name}
                <span className="uctc__detail-count mono">
                  {' '}· {g.evidenced_count ?? 0}/{g.test_case_count ?? 0} evidenced
                </span>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Test cases under this use case — each drills into its own detail. */}
      <div className="uctc__detail-section">
        <div className="competitive__detail-eyebrow mono">
          test cases ({testCases.length})
        </div>
        {testCases.length === 0 ? (
          <div className="coverage__empty mono">no test cases</div>
        ) : (
          <ul className="uctc__detail-tclist" data-testid="uctc-uc-detail-tclist">
            {testCases.map((tc) => (
              <li key={tc.tc_id}>
                <button
                  type="button"
                  className="uctc__detail-tc"
                  data-testid={`uctc-uc-detail-tc-${tc.tc_id}`}
                  onClick={() => onPickTc(tc.tc_id)}
                  title={`Open ${tc.tc_id}`}
                >
                  <span className="uctc__detail-tc-id mono">{tc.tc_id}</span>
                  {tc.validation_class && (
                    <span className="chip mono">{tc.validation_class}</span>
                  )}
                  <span className="uctc__detail-tc-title">{tc.title || ''}</span>
                  <span className="uctc__detail-tc-arrow" aria-hidden="true">▸</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
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


/* ─── Mechanisms (design C6) ────────────────────────────────────────── */

/**
 * Which detection mechanisms each test case relies on, and what the tenant
 * shows about them.
 *
 * The mechanisms come from the expected detections the evidencing scenarios
 * declare (GET /api/scenarios). The tenant column is "Can't check" for every
 * mechanism: no SimCore API reports whether a mechanism's content is present
 * in the tenant, and a green here would be an assumption.
 */
function MechanismsTab({ model, paths, sources, sourceErrors, availability, onOpenTc, onOpenComponent }) {
  const known = sources.scenarios === 'ok'
  const noScenario = paths.filter((p) => p.stall === 'no_scenario').length
  return (
    <div className="uctc__tabpanel" data-testid="uctc-mechanisms" role="tabpanel" aria-label="Mechanisms">
      <p className="uctc__tab-lede">
        The detection mechanisms each test case relies on, from the detections its
        scenarios declare, and what the tenant shows about them. POVengine has no
        read of rule or query content from the tenant yet, so every mechanism{' '}
        {"reads Can't check."}
      </p>
      <SourceNotice sources={sources} errors={sourceErrors} need={['scenarios']} />

      <div className="uctc__mechgrid">
        {model.cards.map((c) => (
          <section key={c.type} className="uctc__mech-card" data-testid={`uctc-mech-${c.type}`}
            aria-label={`${c.type} mechanism`}>
            <div className="uctc__mech-card-head">
              <DetectionTypeChip type={c.type} />
              <span className="uctc__mech-card-num mono">{known ? c.tcs : '—'}</span>
            </div>
            <div className="uctc__mech-card-sub">
              test cases rely on it · {known ? c.objects : '—'} objects in their scenarios
            </div>
            <div className="uctc__mech-card-state">
              <StatusIcon status="unknown" word="Can't check" />
            </div>
            <div className="uctc__mech-card-note">
              No SimCore API reads this from the tenant yet.
            </div>
          </section>
        ))}
      </div>

      {known && (
        <div className="uctc__tablewrap uctc__tablewrap--panel">
          <table className="uctc__table" data-testid="uctc-mech-rows">
            <thead>
              <tr>
                <th scope="col">Test case</th>
                <th scope="col">What it asserts</th>
                <th scope="col">Mechanisms</th>
                <th scope="col">Signal comes from</th>
                <th scope="col">In the tenant</th>
              </tr>
            </thead>
            <tbody>
              {model.rows.map(({ path, mechanisms }) => (
                <tr key={path.tcId} data-testid={`uctc-mech-row-${path.tcId}`}>
                  <td>
                    <button type="button" className="uctc__idbtn mono" onClick={() => onOpenTc(path.tcId)}>
                      {path.tcId}
                    </button>
                  </td>
                  <td className="uctc__cell-title" title={path.tc.title || ''}>{path.tc.title || '—'}</td>
                  <td>
                    <span className="uctc__mech-chips">
                      {mechanisms.map((m) => <DetectionTypeChip key={m} type={m} />)}
                    </span>
                  </td>
                  <td>
                    {path.component ? (
                      <button
                        type="button"
                        className="uctc__complink"
                        data-testid={`uctc-mech-component-${path.tcId}`}
                        title={path.door ? `via ${path.door.label || path.door.door_id}` : undefined}
                        onClick={() => onOpenComponent(path.component)}
                      >
                        {COMPONENT_NAME[path.component] || path.component} ›
                      </button>
                    ) : (
                      <span className="uctc__tone-muted"
                        title={sources.readiness === 'ok' ? 'no door is listed for its scenarios' : 'component readiness did not load'}>
                        —
                      </span>
                    )}
                  </td>
                  <td><StatusIcon status="unknown" word="Can't check" /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {noScenario > 0 && (
            <p className="uctc__tab-foot" data-testid="uctc-mech-noscenario">
              {noScenario} detection test case{noScenario === 1 ? ' has' : 's have'} no scenario yet, so
              {noScenario === 1 ? ' it relies' : ' they rely'} on no mechanism. They are listed under Gaps.
            </p>
          )}
        </div>
      )}

      <AssertionCatalogPanel availability={availability} />
    </div>
  )
}

/* ─── Coverage (design C6) ──────────────────────────────────────────── */

/** One stage's cell on a test case's row: an icon and a word, or a dash. */
function StageCell({ path, stage }) {
  const state = path.stages[stage]
  if (state === 'done') return <StatusIcon status="up" word={STAGE_DONE_WORDS[stage]} />
  if (state === 'unknown') return <StatusIcon status="unknown" word="Can't check" />
  if (state === 'na') return <span className="uctc__tone-muted" aria-label="not reached">—</span>
  // todo
  if (path.stall && GAP_STAGE[path.stall] === stage) {
    let word = GAP_CELL_WORDS[path.stall]
    if (path.stall === 'never_ran' && path.inFlight) {
      // A staged push bundle is waiting to be run (design C2b), not running.
      word = isStaged(path.run) ? 'Waiting' : 'In progress'
    }
    return (
      <span className="uctc__stall">
        <StatusIcon status={GAP_STATUS[path.stall]} word={word} />
        {path.reason && (
          <VerdictReason reason={path.reason} testId={`uctc-cov-reason-${path.tcId}`} />
        )}
      </span>
    )
  }
  // A door checked and closed now, on a test case that already got further.
  if (stage === 'components') return <StatusIcon status="degraded" word="Not ready" />
  return <span className="uctc__tone-muted" aria-label="not reached">—</span>
}

function CoverageTab({ paths, sources, sourceErrors, onOpenTc }) {
  const steps = funnel(paths)
  return (
    <div className="uctc__tabpanel" data-testid="uctc-coverage" role="tabpanel" aria-label="Coverage">
      <p className="uctc__tab-lede">
        How far each detection test case has got along the proof path. Only
        Detected, after a read back, counts as covered: authored is not proven.
        Posture, platform and automation test cases are proven by an assertion
        run instead, so they are not on this path.
      </p>
      <SourceNotice sources={sources} errors={sourceErrors} need={['scenarios', 'readiness', 'doors', 'runs']} />

      <ol className="uctc__funnel" aria-label="Proof path" data-testid="uctc-funnel">
        {steps.map((s) => (
          <li key={s.stage} className="uctc__funnel-step" data-testid={`uctc-funnel-${s.stage}`}>
            <div className="uctc__funnel-figure">
              <span className={'uctc__funnel-num mono' + (s.stage === 'detected' && s.done ? ' uctc__funnel-num--detected' : '')}>
                {s.done == null ? '—' : s.done}
              </span>
              <span className="uctc__funnel-of mono">of {s.of}</span>
            </div>
            <div className="uctc__funnel-label">{s.label}</div>
            <div className="uctc__funnel-bar" aria-hidden="true">
              <div
                className={'uctc__funnel-fill' + (s.stage === 'detected' ? ' uctc__funnel-fill--detected' : '')}
                style={{ width: `${s.done && s.of ? Math.round((s.done / s.of) * 100) : 0}%` }}
              />
            </div>
            {s.unknown > 0 && (
              <div className="uctc__funnel-cant">{`${s.unknown} can't check`}</div>
            )}
          </li>
        ))}
      </ol>

      {paths.length === 0 ? (
        <div className="coverage__empty mono">the index carries no detection test cases</div>
      ) : (
        <div className="uctc__tablewrap uctc__tablewrap--panel">
          <table className="uctc__table" data-testid="uctc-cov-rows">
            <thead>
              <tr>
                <th scope="col">Test case</th>
                <th scope="col">Use case · title</th>
                <th scope="col">Scenario</th>
                <th scope="col">Components</th>
                <th scope="col">Ran</th>
                <th scope="col">Read back</th>
                <th scope="col">Detected</th>
              </tr>
            </thead>
            <tbody>
              {paths.map((p) => (
                <tr key={p.tcId} data-testid={`uctc-cov-row-${p.tcId}`}>
                  <td>
                    <button type="button" className="uctc__idbtn mono" onClick={() => onOpenTc(p.tcId)}>
                      {p.tcId}
                    </button>
                  </td>
                  <td className="uctc__cell-title" title={p.tc.title || ''}>
                    <span className="uctc__cov-uc mono">{p.ucId}</span>
                    <span className="uctc__cov-title">{p.tc.title || '—'}</span>
                  </td>
                  {STAGES.map((stage) => (
                    <td key={stage} data-testid={`uctc-cov-${p.tcId}-${stage}`}>
                      <StageCell path={p} stage={stage} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* ─── Gaps (design C6) ──────────────────────────────────────────────── */

/** Why a test case stalls where it does, in one sentence. */
function gapWhy(p) {
  const first = p.scenarioIds[0]
  const run = shortRun(p.runId)
  switch (p.stall) {
    case 'no_scenario':
      return 'No scenario exercises this test case yet.'
    case 'component': {
      const where = COMPONENT_NAME[p.component] || 'A component'
      const door = (p.door && (p.door.label || p.door.door_id)) || 'a door it needs'
      return `${where}: ${door} is checked and closed, so the detections behind it cannot fire.`
    }
    case 'never_ran':
      if (p.inFlight) {
        return isStaged(p.run)
          ? `Run ${run} is a staged push bundle that has not reported running.`
          : `Run ${run} has not finished yet.`
      }
      if (p.run) return `Its last run (${run}) executed no step that carries a detection.`
      return p.stages.components === 'unknown'
        ? `${first} has never run, and whether its components are ready can't be checked yet.`
        : `${first} has never run.`
    case 'not_read_back':
      return `Run ${run} finished but has not been read back, so it has no result.`
    case 'not_seen':
      return `Run ${run} was read back and the tenant has shown none of its detections yet.`
    case 'unknown':
      if (p.unknownSource === 'scenarios-empty') return 'SimCore listed no scenarios, so how far this got can\'t be checked.'
      if (p.unknownSource === 'scenarios') return 'The scenario list did not load, so how far this got can\'t be checked.'
      if (p.unknownSource === 'runs') return 'The run list did not load, so how far this got can\'t be checked.'
      if (p.unknownSource === 'counts') return `Run ${run} was read back, but SimCore did not report how many detections it saw.`
      return 'How far this got can\'t be checked.'
    default:
      return ''
  }
}

/** The one thing to do about a gap: a verb from the closed set, or a link. */
function GapAction({ p, reading, actions }) {
  if (p.stall === 'no_scenario') {
    return (
      <button type="button" className="uctc__gaplink" onClick={actions.buildChain}>
        Build a chain ›
      </button>
    )
  }
  if (p.stall === 'component' && p.component) {
    return <Button onClick={() => actions.configure(p.component)}>Configure</Button>
  }
  if (p.stall === 'never_ran') {
    if (p.inFlight && p.runId) {
      return (
        <button type="button" className="uctc__gaplink" onClick={() => actions.openRun(p.runId)}>
          Open run ›
        </button>
      )
    }
    return p.scenarioIds[0] ? <Button onClick={() => actions.run(p.scenarioIds[0])}>Run</Button> : null
  }
  if (p.stall === 'not_read_back' && p.runId) {
    const busy = reading && reading.has && reading.has(p.runId)
    return (
      <Button onClick={() => actions.readBack(p.runId)} disabled={busy} aria-busy={busy || undefined}>
        Read back
      </Button>
    )
  }
  if ((p.stall === 'not_seen' || (p.stall === 'unknown' && p.unknownSource === 'counts')) && p.runId) {
    return <Button onClick={() => actions.openResults(p.runId)}>Open results</Button>
  }
  return null
}

function GapsTab({ paths, counts, sources, sourceErrors, reading, actions, onOpenTc }) {
  const [kind, setKind] = useState('all')
  const all = gapList(paths)
  const shown = kind === 'all' ? all : all.filter((p) => p.stall === kind)
  // "Can't check" is listed only when something could not be checked: it is
  // not a kind of gap, it is the absence of an answer.
  const kinds = GAP_KINDS.filter((k) => k !== 'unknown' || counts.unknown > 0)
  return (
    <div className="uctc__tabpanel" data-testid="uctc-gaps" role="tabpanel" aria-label="Gaps">
      <p className="uctc__tab-lede">
        Every detection test case not yet detected in a read-back run, the earliest
        stall first. Each says why in one sentence and offers one thing to do.
      </p>
      <SourceNotice sources={sources} errors={sourceErrors} need={['scenarios', 'readiness', 'doors', 'runs']} />

      <div className="uctc__gapfilters" role="group" aria-label="Filter gaps by kind">
        {['all', ...kinds].map((k) => (
          <button
            key={k}
            type="button"
            aria-pressed={kind === k}
            className={'uctc__gapfilter' + (kind === k ? ' is-active' : '')}
            data-testid={`uctc-gapfilter-${k}`}
            onClick={() => setKind(k)}
          >
            {k === 'all' ? 'All' : GAP_LABELS[k]}
            <span className="uctc__gapfilter-count mono">{counts[k]}</span>
          </button>
        ))}
      </div>

      <ul className="uctc__gaplist" data-testid="uctc-gaplist">
        {shown.map((p) => (
          <li key={p.tcId} className="uctc__gaprow" data-testid={`uctc-gap-${p.tcId}`} data-kind={p.stall}>
            <span className="uctc__gaprow-kind">
              <StatusIcon status={GAP_STATUS[p.stall]} word={GAP_LABELS[p.stall]} />
            </span>
            <span className="uctc__gaprow-body">
              <span className="uctc__gaprow-head">
                <button type="button" className="uctc__idbtn mono" onClick={() => onOpenTc(p.tcId)}>
                  {p.tcId}
                </button>
                <span className="uctc__gaprow-title">{p.tc.title || '—'}</span>
                {p.reason && <VerdictReason reason={p.reason} testId={`uctc-gap-reason-${p.tcId}`} />}
              </span>
              <span className="uctc__gaprow-why" data-testid={`uctc-gap-why-${p.tcId}`}>{gapWhy(p)}</span>
            </span>
            <span className="uctc__gaprow-act">
              <GapAction p={p} reading={reading} actions={actions} />
            </span>
          </li>
        ))}
        {shown.length === 0 && (
          <li className="uctc__gaprow uctc__gaprow--empty" data-testid="uctc-gaps-none">
            {paths.length === 0
              ? 'The index carries no detection test cases.'
              : all.length === 0
                ? 'Every detection test case was detected in a read-back run.'
                : 'No gaps of this kind.'}
          </li>
        )}
      </ul>
    </div>
  )
}
