/**
 * uctcMechanisms — the model behind "can this engine prove your whole index?"
 *
 * A flat "84 of 266 evidenced" actively misleads, because the 266 rows are not
 * one population. They split five ways by ``validation_class``, and each class
 * is proven by a DIFFERENT mechanism with a DIFFERENT reachable ceiling:
 *
 *   DET · HNT   an attack scenario firing a detection      (scenarios/**.yml)
 *   POS · PLT · AUT   an assertion measuring state / capability / outcome
 *                     (assertions/{pos,plt,aut}/*.yml)
 *
 * Authoring 140 more attack scenarios cannot close a POS row: a posture finding
 * must be *discovered*, not *detected*. So this module keeps three questions
 * separate that a single percentage fuses into one lie:
 *
 *   1. is it evidenced?            — does an artifact bind to it today
 *   2. is it REACHABLE?            — does the engine have a read path at all
 *   3. is it SCOREABLE?            — does the index carry a measurable threshold
 *
 * (2) is the honest ceiling. (3) is the honest verdict cap: an unscoreable row
 * can only ever return ``not_applicable``, never ``pass``, so a surface that
 * counts it as closable is selling something the verifier will never deliver.
 *
 * Everything here is pure: no fetch, no React. The view renders it, the unit
 * tests pin it.
 */

export const VALIDATION_CLASSES = ['DET', 'HNT', 'POS', 'PLT', 'AUT']

/**
 * What actually proves a class, and which artifact carries it.
 *
 * ``artifact`` is the thing a content author writes. ``proves`` is the sentence
 * a DC says in front of a customer. Both are deliberately concrete — "coverage"
 * as an abstraction is exactly how 92 unproven POS rows got counted as a gap
 * somebody could close by writing more attack scenarios.
 */
export const MECHANISM = {
  DET: {
    id: 'scenario',
    label: 'Attack scenario',
    proves: 'a detection fires on generated signal',
    artifact: 'scenarios/{plane}/*.yml → Run → Result',
    verdictPath: 'orchestrator seeds Results · connector reconciles observed alerts',
  },
  HNT: {
    id: 'scenario',
    label: 'Attack scenario + hunt query',
    proves: 'an analyst query retrieves the seeded activity',
    artifact: 'scenarios/{plane}/*.yml → Run → Result',
    verdictPath: 'orchestrator seeds Results · connector reconciles observed alerts',
  },
  POS: {
    id: 'assertion',
    label: 'Posture assertion',
    proves: 'planted known-bad state is discovered by the platform',
    artifact: 'assertions/pos/*.yml → AssertionRun → AssertionCheck',
    verdictPath: 'read-only XQL probe measured against an authored threshold',
  },
  PLT: {
    id: 'assertion',
    label: 'Platform assertion',
    proves: 'a platform capability is present and measurable',
    artifact: 'assertions/plt/*.yml → AssertionRun → AssertionCheck',
    verdictPath: 'read-only XQL probe measured against an authored threshold',
  },
  AUT: {
    id: 'assertion',
    label: 'Automation assertion',
    proves: 'an automation outcome occurred inside its budget',
    artifact: 'assertions/aut/*.yml → AssertionRun → AssertionCheck',
    verdictPath: 'precursor → outcome latency measured in the platform clock',
  },
}

/**
 * The engine has exactly ONE tenant read client — ``core/integrations/xsiam``
 * — and it speaks XQL. Every assertion probe (`xql_rows`, `xql_distinct`,
 * `xql_scalar`, `xql_ratio`, `xql_latency`) and the alert connector go through
 * it, so a probe can only measure what is queryable as an XSIAM dataset.
 *
 * The index's ``detection_source`` column names the console a HUMAN would look
 * in — which is a weaker claim than "the engine cannot get at it". Some of
 * those consoles' records are ingested into XSIAM and are then perfectly
 * queryable: ``POS-CSPM-001`` proves a row whose source reads "Cortex Cloud
 * Posture Dashboard + REST API" by querying ``dataset = cortex_cloud_posture``.
 *
 * So this classifier answers the honest, weaker question — **does the index
 * point at XQL, or at something that must be ingested first?** — and nothing
 * stronger. Declaring a row unprovable when a data path would in fact reach it
 * suppresses work somebody should do, which is the mirror image of inflating
 * coverage and just as dishonest.
 */
export const XQL_MARKERS = ['XQL', 'XSIAM', 'xdr_data', 'NGFW']
export const OFF_PLATFORM_MARKERS = [
  'Cortex Cloud', 'ASPM Command Center', 'Xpanse', 'Cortex XDL', 'AgentiX',
]

export const REACH = {
  DIRECT: 'direct',      // the index itself points at XQL
  INGEST: 'ingest',      // a console outside XQL — measurable once ingested
  UNKNOWN: 'unknown',    // index declares no source
}

/**
 * Classify one test case's declared evidence surface.
 *
 * Precedence is XQL-then-off-platform: a compound source like "XSIAM Threat
 * Intel + Cortex XDL" is partly queryable and must not be rounded down.
 */
export function readPath(tc) {
  const raw = String((tc && tc.detection_source) || '').trim()
  if (!raw) return { reach: REACH.UNKNOWN, surfaces: [], via: [] }
  const surfaces = raw.split('·').map((s) => s.trim()).filter(Boolean)
  const via = surfaces.filter((s) => XQL_MARKERS.some((m) => s.includes(m)))
  if (via.length) return { reach: REACH.DIRECT, surfaces, via }
  if (OFF_PLATFORM_MARKERS.some((m) => raw.includes(m))) {
    return { reach: REACH.INGEST, surfaces, via: [] }
  }
  return { reach: REACH.UNKNOWN, surfaces, via: [] }
}

/**
 * What stands between this row and a machine verdict.
 *
 * Two prerequisites, and they are NOT the same thing:
 *
 * · ``needsDataPath`` — the index points at a console outside XQL. An assertion
 *   probe can still measure it, but only once that source is ingested as a
 *   queryable dataset. A prerequisite, not a wall. For a DET/HNT row it is
 *   weaker still: the engine generates the signal regardless, and only the
 *   automatic reconcile is lost — the DC confirms in the vendor console and
 *   validates the Result by hand.
 *
 * · ``certifiable`` — the INDEX carries a measurable threshold. When it does
 *   not, ``verifier.score_run`` clamps a PASS to ``not_applicable``: the row can
 *   be evidenced but can never be machine-certified. That ceiling is absolute
 *   and lives in the engine's own code, not in a heuristic.
 */
export function proofScope(tc) {
  const mech = MECHANISM[tc && tc.validation_class]
  const { reach, surfaces } = readPath(tc)
  const needsDataPath = reach === REACH.INGEST
  const isAssertion = Boolean(mech && mech.id === 'assertion')
  return {
    needsDataPath,
    // DET/HNT stay fully actionable without a data path; POS/PLT/AUT do not,
    // because their whole mechanism IS the read.
    blocked: needsDataPath && isAssertion,
    autoVerifiable: !needsDataPath,
    certifiable: tc ? tc.is_scoreable !== false : true,
    surfaces,
    reason: !needsDataPath
      ? ''
      : isAssertion
        ? `the index points at ${surfaces.join(' · ')} — a probe can measure this once that source is ingested as an XQL-queryable dataset`
        : `the engine generates the signal, but ${surfaces.join(' · ')} is not XQL-queryable, so the Result is validated by hand rather than auto-reconciled`,
  }
}

/** Does an engine artifact of any kind bind to this row? */
export function isEvidenced(tc, assertionsByTc) {
  return Boolean(
    (tc && tc.evidence && tc.evidence.evidenced)
    || (assertionsByTc && (assertionsByTc.get(tc.tc_id) || []).length),
  )
}

/**
 * Build ``tc_id -> [assertion]`` from a ``GET /api/assertions`` payload.
 *
 * Returns an empty Map for every failure mode — endpoint absent, 404, empty
 * catalog — because "no assertions loaded" and "assertions unavailable" both
 * mean the same thing to the coverage arithmetic: nothing is proven by that
 * mechanism here. The DIFFERENCE between those two states is surfaced
 * separately by ``assertionAvailability`` so it never silently reads as zero.
 */
export function indexAssertions(payload) {
  const byTc = new Map()
  const list = Array.isArray(payload && payload.assertions) ? payload.assertions : []
  for (const a of list) {
    const refs = Array.isArray(a.tc_refs) && a.tc_refs.length
      ? a.tc_refs
      : (a.tc_ref ? [a.tc_ref] : [])
    for (const ref of refs) {
      if (!byTc.has(ref)) byTc.set(ref, [])
      byTc.get(ref).push(a)
    }
  }
  return byTc
}

/**
 * Index recorded assertion runs by assertion id, newest first.
 *
 * An AUTHORED assertion is a claim. Only a run carries a ``tc_verdict``, and
 * only a credential-backed, non-dry run can carry anything other than
 * ``pending``. Keeping these apart is the difference between "we wrote the
 * question down" and "we asked the tenant and it answered".
 */
export function indexAssertionRuns(payload) {
  const byId = new Map()
  const runs = Array.isArray(payload && payload.runs) ? payload.runs : []
  for (const r of runs) {
    if (!r || !r.assertion_id) continue
    if (!byId.has(r.assertion_id)) byId.set(r.assertion_id, [])
    byId.get(r.assertion_id).push(r)
  }
  return byId
}

/** The strongest verdict recorded for one assertion, or null if never run. */
export function latestVerdict(runs) {
  if (!runs || !runs.length) return null
  const r = runs[0]
  return { verdict: r.tc_verdict || 'pending', reason: r.reason || '', run_id: r.run_id,
    tenant: r.tenant || null, at: r.completed_at || r.started_at || null }
}

/**
 * Distinguish "the mechanism is not deployed" from "the mechanism is deployed
 * and nobody has authored content for it yet". Both render as 0 coverage; only
 * one of them is a content backlog item.
 */
export function assertionAvailability({ payload, error }) {
  if (error) {
    // A 404 is the expected shape on a SimCore built before the router landed.
    const notFound = error.status === 404 || /not found/i.test(error.message || '')
    return {
      endpoint: notFound ? 'absent' : 'error',
      authored: 0,
      rejected: 0,
      detail: notFound
        ? 'this SimCore does not serve /api/assertions — the POS/PLT/AUT proof mechanism is not deployed here'
        : (error.message || 'assertion catalog unavailable'),
    }
  }
  if (!payload) return { endpoint: 'absent', authored: 0, rejected: 0, detail: 'not queried' }
  const authored = Number(payload.total ?? (payload.assertions || []).length) || 0
  const rejected = Array.isArray(payload.rejected) ? payload.rejected.length : 0
  return {
    endpoint: authored || rejected ? 'live' : 'empty',
    authored,
    rejected,
    // The refused artifacts themselves, not just the count — the guard is only
    // worth something if the author can read why their file was refused.
    rejectedRows: Array.isArray(payload.rejected) ? payload.rejected : [],
    byClass: payload.by_validation_class || {},
    probes: Array.isArray(payload.probes) ? payload.probes : [],
    detail: authored
      ? `${authored} assertion${authored === 1 ? '' : 's'} loaded`
      : 'the mechanism is deployed but no assertion artifacts are authored yet',
  }
}

/**
 * Per-class coverage arithmetic — the five numbers that replace the one.
 *
 * Segments always sum to ``total``:
 *   proven + open_direct + open_needs_data_path === total
 *
 * Two ceilings are reported and they answer different questions:
 *
 * · ``certifiable`` — how many rows of this class could EVER return ``pass``.
 *   The rest carry no measurable threshold in the index, so the verifier clamps
 *   them to ``not_applicable`` by construction. This ceiling is absolute.
 * · ``open_needs_data_path`` — how many open rows point at a console outside
 *   XQL and therefore need that source ingested before a probe can measure
 *   them. A prerequisite, not a wall — reported separately so a DC sizes the
 *   work honestly instead of discovering it in front of a customer.
 */
export function classCoverage(testCases, assertionsByTc = new Map(), runsByAssertion = new Map()) {
  const rows = VALIDATION_CLASSES.map((cls) => {
    const inClass = testCases.filter((t) => t.validation_class === cls)
    const proven = []
    const openDirect = []
    const openNeedsPath = []
    let byScenario = 0
    let byAssertion = 0
    let classMismatch = 0
    let manualOnly = 0
    let measured = 0

    for (const tc of inClass) {
      const scenarioEv = Boolean(tc.evidence && tc.evidence.evidenced)
      const assertionEv = (assertionsByTc.get(tc.tc_id) || []).length > 0
      const scope = proofScope(tc)
      if (scenarioEv) byScenario += 1
      if (assertionEv) byAssertion += 1
      // Authored ≠ measured. A row only counts as measured when one of its
      // assertions has a recorded run carrying a real pass/fail.
      if (assertionEv && (assertionsByTc.get(tc.tc_id) || []).some((a) => {
        const v = latestVerdict(runsByAssertion.get(a.assertion_id))
        return v && (v.verdict === 'pass' || v.verdict === 'fail')
      })) measured += 1
      if (!scope.autoVerifiable && !scope.blocked) manualOnly += 1
      // A POS row proven only by an attack scenario is the S-14 binding: the
      // artifact type does not match what the row asks for. Counted, but named.
      if (scenarioEv && !assertionEv && MECHANISM[cls] && MECHANISM[cls].id === 'assertion') {
        classMismatch += 1
      }
      if (scenarioEv || assertionEv) { proven.push(tc); continue }
      if (scope.blocked) openNeedsPath.push(tc)
      else openDirect.push(tc)
    }

    const total = inClass.length
    const certifiable = inClass.filter((t) => t.is_scoreable !== false).length
    return {
      validation_class: cls,
      mechanism: MECHANISM[cls],
      total,
      proven: proven.length,
      by_scenario: byScenario,
      by_assertion: byAssertion,
      class_mismatch: classMismatch,
      measured: measured,
      open_direct: openDirect.length,
      open_needs_data_path: openNeedsPath.length,
      manual_only: manualOnly,
      certifiable,
      certifiable_pct: total ? Math.round((certifiable / total) * 1000) / 10 : 0,
      coverage_pct: total ? Math.round((proven.length / total) * 1000) / 10 : 0,
      unscoreable: total - certifiable,
      unscoreable_proven: proven.filter((t) => t.is_scoreable === false).length,
      needs_data_path_ids: openNeedsPath.map((t) => t.tc_id),
      needs_data_path_surfaces: tallySurfaces(openNeedsPath),
    }
  })
  return rows.filter((r) => r.total > 0)
}

/** Which off-platform consoles the open rows actually point at. */
export function tallySurfaces(testCases) {
  const counts = new Map()
  for (const tc of testCases) {
    for (const s of readPath(tc).surfaces) counts.set(s, (counts.get(s) || 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([surface, count]) => ({ surface, count }))
    .sort((a, b) => b.count - a.count || a.surface.localeCompare(b.surface))
}

/** Whole-index totals, derived from the same per-class rows so they can never disagree. */
export function coverageTotals(rows) {
  const sum = (k) => rows.reduce((n, r) => n + r[k], 0)
  const total = sum('total')
  const certifiable = sum('certifiable')
  return {
    total,
    proven: sum('proven'),
    open_direct: sum('open_direct'),
    open_needs_data_path: sum('open_needs_data_path'),
    certifiable,
    certifiable_pct: total ? Math.round((certifiable / total) * 1000) / 10 : 0,
    coverage_pct: total ? Math.round((sum('proven') / total) * 1000) / 10 : 0,
    unscoreable: sum('unscoreable'),
    manual_only: sum('manual_only'),
    measured: sum('measured'),
    by_scenario: sum('by_scenario'),
    by_assertion: sum('by_assertion'),
  }
}
