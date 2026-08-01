/**
 * Interaction tests for the UC / TC Index surface.
 *
 * Mirrors the TtpBrowserView test shape — fixture API responses for the
 * list + detail endpoints, then render / filter / drill / deep-link.
 *
 * The route matcher in ``src/test/mockFetch.js`` keys on pathname only
 * (query strings are ignored), so one route entry serves every filter
 * permutation the surface might request.
 */
import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import UcTcIndexView from '../UcTcIndexView.jsx'
import { installRoutes } from '../../../test/mockFetch.js'

void React

/* ─── fixtures ──────────────────────────────────────────────────────── */

const TC_EDR_03 = {
  tc_id: 'TC-EDR-03',
  ucs_id: 'UCS-EDR-03',
  ucs_name: 'Credential Theft',
  uc_id: 'UC-EDR',
  use_case: 'Endpoint Detection & Response',
  title: 'Credential dumping detected from process behaviour alone',
  tc_sheet: 'SecOps',
  status: 'Active',
  validation_class: 'DET',
  priority: 'P1',
  differentiation_tier: 'MOAT',
  moat_classification: 'AI-Native',
  primary_kpi: 'Detection Accuracy',
  threshold: '>90%',
  is_scoreable: true,
  needs_detection_content: true,
  detection_source: 'XSIAM (XQL: dataset=xdr_data)',
  detection_type: 'BIOC',
  target_dataset: 'xdr_data',
  pov_scenario_id: 'POV-SC-004',
  evidence: {
    evidenced: true,
    scenario_count: 2,
    scenario_ids: ['SIM-EDR-001', 'SIM-EDR-002'],
    planes: ['EDR'],
    scenario_moat_tiers: ['MOAT'],
    tier_disagreement: false,
  },
}

const TC_EDR_05 = {
  tc_id: 'TC-EDR-05',
  ucs_id: 'UCS-EDR-05',
  ucs_name: 'Agent Posture',
  uc_id: 'UC-EDR',
  use_case: 'Endpoint Detection & Response',
  title: 'Agent deployment posture reported across the estate',
  tc_sheet: 'SecOps',
  validation_class: 'POS',
  priority: 'P2',
  differentiation_tier: 'PARITY',
  primary_kpi: 'Coverage',
  threshold: 'Qualitative pass',
  is_scoreable: false,
  detection_source: '',
  evidence: { evidenced: false, scenario_count: 0, scenario_ids: [], planes: [] },
}

const TC_NDR_06 = {
  tc_id: 'TC-NDR-06',
  ucs_id: 'UCS-NDR-06',
  ucs_name: 'EDL Enforcement',
  uc_id: 'UC-NDR',
  use_case: 'Network Detection & Response',
  title: 'Demonstrate EDL policy management and enforcement',
  tc_sheet: 'SecOps',
  validation_class: 'DET',
  priority: 'P1',
  differentiation_tier: 'LEAD',
  primary_kpi: 'Enforcement Latency',
  threshold: '<60s',
  is_scoreable: true,
  detection_source: 'NGFW (EDL)',
  evidence: { evidenced: false, scenario_count: 0, scenario_ids: [], planes: [] },
}

const TC_TH_05 = {
  tc_id: 'TC-TH-05',
  ucs_id: 'UCS-TH-05',
  ucs_name: 'Hunt Quality',
  uc_id: 'UC-TH',
  use_case: 'Threat Hunting',
  tc_sheet: 'SecOps',
  title: 'Analytics and smart scoring reduce hunt false positives by >50%',
  validation_class: 'HNT',
  priority: 'P3',
  differentiation_tier: 'MOAT',
  primary_kpi: 'FP Reduction',
  threshold: 'TBD',
  is_scoreable: false,
  detection_source: 'XSIAM (XQL)',
  evidence: { evidenced: false, scenario_count: 0, scenario_ids: [], planes: [] },
}

const testCaseList = {
  index_loaded: true,
  index_version: '2.2',
  index_total: 4,
  total: 4,
  test_cases: [TC_EDR_03, TC_EDR_05, TC_NDR_06, TC_TH_05],
}

const useCaseList = {
  index_loaded: true,
  index_version: '2.2',
  total: 3,
  use_cases: [
    {
      uc_id: 'UC-EDR',
      use_case: 'Endpoint Detection & Response',
      fy27_subdomain: 'SOC Transformation',
      base_platform_list: ['Cortex XDR'],
      addons_list: ['Forensics'],
      counts: {
        ucs_groups: 2, test_cases: 2, detection_backable: 1, scoreable: 1,
        evidenced: 1, evidenced_detection_backable: 1,
      },
      coverage_pct: 50.0,
      det_coverage_pct: 100.0,
      scenario_count: 2,
      planes: ['EDR'],
    },
    {
      uc_id: 'UC-NDR',
      use_case: 'Network Detection & Response',
      fy27_subdomain: 'SOC Transformation',
      counts: {
        ucs_groups: 1, test_cases: 1, detection_backable: 1, scoreable: 1,
        evidenced: 0, evidenced_detection_backable: 0,
      },
      coverage_pct: 0.0,
      det_coverage_pct: 0.0,
      scenario_count: 0,
      planes: [],
    },
    {
      uc_id: 'UC-TH',
      use_case: 'Threat Hunting',
      fy27_subdomain: 'Security Automation',
      counts: {
        ucs_groups: 1, test_cases: 1, detection_backable: 1, scoreable: 0,
        evidenced: 0, evidenced_detection_backable: 0,
      },
      coverage_pct: 0.0,
      det_coverage_pct: 0.0,
      scenario_count: 0,
      planes: [],
    },
  ],
}

const summary = {
  index_loaded: true,
  index_version: '2.2',
  totals: { use_cases: 3, ucs_groups: 4, test_cases: 4, payloads: 2, skus: 38 },
  by_validation_class: { DET: 2, HNT: 1, POS: 1, PLT: 0, AUT: 0 },
  by_tier: { MOAT: 2, LEAD: 1, PARITY: 1, EMERGING: 0 },
  by_priority: { P1: 2, P2: 1, P3: 1 },
  by_sheet: { SecOps: 4, Cloud: 0 },
  evidence: {
    scenarios: 2, evidenced: 1, evidenced_pct: 25.0,
    detection_backable: 3, evidenced_detection_backable: 1,
    detection_backable_pct: 33.3, unscoreable: 2, planes: ['EDR'],
  },
}

const detailEdr03 = {
  ...TC_EDR_03,
  index_loaded: true,
  index_version: '2.2',
  description: 'Dump credentials and confirm the platform detects on behaviour.',
  validation_methodology: 'Detection Accuracy',
  measurement_method: 'Compare seeded detections to observed alerts',
  success_criteria: 'At least one BIOC fires within the correlation window',
  expected_signal: 'xdr_data process event with LSASS access',
  simulation_input: 'Run mimipenguin against a seeded host',
  authoring_gap: '',
  entitlements: {
    base_platform: ['Cortex XSIAM', 'Cortex XDR'],
    addons: ['Forensics', 'Host Insights'],
  },
  use_case: 'Endpoint Detection & Response',
  ucs: { ucs_id: 'UCS-EDR-03', ucs_name: 'Credential Theft', sibling_tc_ids: ['TC-EDR-04'] },
  payload: {
    pov_scenario_id: 'POV-SC-004',
    payload: 'Execute multi-stage credential access chain',
    bound_tc_count: 7,
    reuse_flag: 'SPLIT REQUIRED',
    needs_split: true,
    mitre_techniques: ['T1003'],
  },
  evidenced_by: [
    {
      scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR',
      status: 'active', is_primary: true, uc_ref: 'UCS-EDR-03', moat_tier: 'MOAT',
      pov_scenario_id: 'POV-SC-004', validation_methodology: 'Detection Accuracy',
      methodology_family: 'F1', primary_kpi: 'Detection Accuracy',
      threshold: { kpi: 'accuracy', op: '>=', value: 90, unit: '%' },
    },
    {
      scenario_id: 'SIM-EDR-002', name: 'Reverse Shell', plane: 'EDR',
      status: 'active', is_primary: false, uc_ref: 'UCS-EDR-03', moat_tier: 'LEAD',
      pov_scenario_id: 'POV-SC-004', methodology_family: 'F1',
    },
  ],
  runs: {
    total: 3,
    verdicts: { pass: 2, fail: 1, pending: 0, not_applicable: 0 },
    latest: {
      run_id: 'r-42', scenario_id: 'SIM-EDR-001', status: 'completed',
      tc_verdict: 'pass', started_at: '2026-07-30T10:00:00Z',
    },
  },
}

const coveragePayload = {
  index_loaded: true,
  index_version: '2.2',
  totals: summary.evidence,
  by_use_case: [
    { uc_id: 'UC-NDR', use_case: 'Network Detection & Response', test_cases: 1, detection_backable: 1, evidenced: 0, evidenced_detection_backable: 0, coverage_pct: 0, det_coverage_pct: 0, scenario_count: 0, planes: [] },
    { uc_id: 'UC-TH', use_case: 'Threat Hunting', test_cases: 1, detection_backable: 1, evidenced: 0, evidenced_detection_backable: 0, coverage_pct: 0, det_coverage_pct: 0, scenario_count: 0, planes: [] },
    { uc_id: 'UC-EDR', use_case: 'Endpoint Detection & Response', test_cases: 2, detection_backable: 1, evidenced: 1, evidenced_detection_backable: 1, coverage_pct: 50, det_coverage_pct: 100, scenario_count: 2, planes: ['EDR'] },
  ],
  by_plane: [{ plane: 'EDR', scenarios: 2, test_cases_evidenced: 1, test_case_ids: ['TC-EDR-03'], use_cases: ['UC-EDR'] }],
  by_validation_class: [
    { validation_class: 'DET', total: 2, evidenced: 1, coverage_pct: 50 },
    { validation_class: 'HNT', total: 1, evidenced: 0, coverage_pct: 0 },
  ],
  by_tier: [{ tier: 'MOAT', total: 2, evidenced: 1, coverage_pct: 50 }],
  by_priority: [], by_sheet: [],
}

const gapsPayload = {
  index_loaded: true,
  index_version: '2.2',
  total: 2,
  gaps: [TC_NDR_06, TC_TH_05],
  by_use_case: [
    { uc_id: 'UC-NDR', use_case: 'Network Detection & Response', gap_count: 1, p1_gap_count: 1 },
    { uc_id: 'UC-TH', use_case: 'Threat Hunting', gap_count: 1, p1_gap_count: 0 },
  ],
  by_priority: { P1: 1, P2: 0, P3: 1 },
  scope: { validation_class: 'DET,HNT', include_unscoreable: true },
}

function happyRoutes(overrides = {}) {
  return installRoutes({
    'GET /api/uctc/test-cases': testCaseList,
    'GET /api/uctc/test-cases/:id': detailEdr03,
    'GET /api/uctc/use-cases': useCaseList,
    'GET /api/uctc/summary': summary,
    'GET /api/uctc/coverage': coveragePayload,
    'GET /api/uctc/gaps': gapsPayload,
    ...overrides,
  })
}

/* ─── harness ───────────────────────────────────────────────────────── */

/**
 * Mirrors the hash router's setParams semantics: merge, drop null/''.
 */
function Harness({ initialParams = {}, onNavigate = () => {} }) {
  const [params, setP] = React.useState(initialParams)
  const setParams = React.useCallback((obj) => {
    setP((prev) => {
      const next = { ...prev, ...obj }
      for (const k of Object.keys(next)) {
        if (next[k] === null || next[k] === undefined || next[k] === '') delete next[k]
      }
      return next
    })
  }, [])
  return <UcTcIndexView params={params} setParams={setParams} onNavigate={onNavigate} />
}

const rowIds = () =>
  screen.getAllByTestId(/^uctc-row-/).map((el) => el.textContent)

/* ─── tests ─────────────────────────────────────────────────────────── */

describe('UcTcIndexView', () => {
  it('renders the loading state, then every index row and the stat tiles', async () => {
    happyRoutes()
    render(<Harness />)

    expect(screen.getByText(/loading the UC \/ TC index/i)).toBeInTheDocument()

    await waitFor(() => expect(screen.getByTestId('uctc-rowcount')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-rowcount')).toHaveTextContent('4 of 4 test cases')
    expect(rowIds()).toEqual(['TC-EDR-03', 'TC-EDR-05', 'TC-NDR-06', 'TC-TH-05'])

    // Stat tiles come from /summary: 4 TCs, 3 DET/HNT, 1 evidenced, 2 open gaps.
    const stats = document.querySelector('.adapter-registry__stats')
    expect(stats).toHaveTextContent('DET / HNT')
    expect(stats).toHaveTextContent('open gaps')
  })

  it('narrows the table with the validation-class chip and clears again', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-filter-class-DET'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-EDR-03', 'TC-NDR-06']))

    fireEvent.click(screen.getByTestId('uctc-clear-filters'))
    await waitFor(() => expect(rowIds()).toHaveLength(4))
  })

  it('searches by tc id and by the id of an evidencing scenario', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.change(screen.getByTestId('uctc-search'), { target: { value: 'TC-NDR-06' } })
    await waitFor(() => expect(rowIds()).toEqual(['TC-NDR-06']))

    // The reverse index is searchable — a DC who knows the scenario id can
    // find the test cases it evidences.
    fireEvent.change(screen.getByTestId('uctc-search'), { target: { value: 'SIM-EDR-002' } })
    await waitFor(() => expect(rowIds()).toEqual(['TC-EDR-03']))
  })

  it('shows the clear-filters empty state when nothing matches', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.change(screen.getByTestId('uctc-search'), { target: { value: 'zzz-no-such-thing' } })
    await waitFor(() => expect(screen.getByTestId('uctc-empty-filters')).toBeInTheDocument())

    fireEvent.click(within(screen.getByTestId('uctc-empty-filters')).getByRole('button'))
    await waitFor(() => expect(rowIds()).toHaveLength(4))
  })

  it('drills UC → TC: the rail scopes the table to one use case', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-uc-UC-EDR')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-uc-UC-EDR'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-EDR-03', 'TC-EDR-05']))
    expect(screen.getByTestId('uctc-uc-UC-EDR')).toHaveAttribute('aria-pressed', 'true')

    // Clicking the active UC clears the scope (the Library toggle idiom).
    fireEvent.click(screen.getByTestId('uctc-uc-UC-EDR'))
    await waitFor(() => expect(rowIds()).toHaveLength(4))
  })

  it('opens the detail drawer on row click and renders the evidencing scenarios', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-row-TC-EDR-03'))
    await waitFor(() => expect(screen.getByTestId('uctc-detail')).toBeInTheDocument())

    const detail = screen.getByTestId('uctc-detail')
    await waitFor(() => expect(detail).toHaveTextContent('Evidenced by (2)'))
    expect(detail).toHaveTextContent('SIM-EDR-001')
    expect(detail).toHaveTextContent('SIM-EDR-002')
    // Measurement contract + licensing + the SPLIT REQUIRED payload flag.
    expect(detail).toHaveTextContent('Detection Accuracy')
    expect(detail).toHaveTextContent('Forensics')
    expect(detail).toHaveTextContent('SPLIT REQUIRED')
    // Run rollup.
    expect(detail).toHaveTextContent('3 runs')
  })

  it('jumps to the Library with the evidencing scenario id', async () => {
    happyRoutes()
    const onNavigate = vi.fn()
    render(<Harness onNavigate={onNavigate} />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-row-TC-EDR-03'))
    await waitFor(() => expect(screen.getByTestId('uctc-open-library-SIM-EDR-001')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-open-library-SIM-EDR-001'))
    expect(onNavigate).toHaveBeenCalledWith('library', { open: 'SIM-EDR-001' })

    fireEvent.click(screen.getByTestId('uctc-open-run'))
    expect(onNavigate).toHaveBeenCalledWith('runs', { run: 'r-42', tab: 'evidence' })
  })

  it('deep-links straight to a test case via params.tc', async () => {
    happyRoutes()
    render(<Harness initialParams={{ tc: 'TC-EDR-03' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail')).toHaveTextContent('SIM-EDR-001'))
  })

  it('flags an unscoreable test case instead of implying it can pass', async () => {
    happyRoutes({
      'GET /api/uctc/test-cases/:id': { ...detailEdr03, ...TC_TH_05, evidenced_by: [], runs: null },
    })
    render(<Harness initialParams={{ tc: 'TC-TH-05' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-unscoreable')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-detail')).toHaveTextContent('not_applicable')
    expect(screen.getByTestId('uctc-detail-noevidence')).toBeInTheDocument()
  })

  it('gaps mode lists the unevidenced detection test cases with the P1 headline', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-mode-gaps')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-mode-gaps'))
    await waitFor(() => expect(screen.getByTestId('uctc-gaps')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByTestId('uctc-gap-TC-NDR-06')).toBeInTheDocument())

    expect(screen.getByTestId('uctc-gaps-p1')).toHaveTextContent('1')
    expect(screen.getByTestId('uctc-gap-TC-TH-05')).toBeInTheDocument()
    // The evidenced TC never appears in the gap list.
    expect(screen.queryByTestId('uctc-gap-TC-EDR-03')).toBeNull()

    // Excluding unscoreable rows drops the qualitative HNT case.
    fireEvent.click(screen.getByTestId('uctc-gaps-unscoreable'))
    await waitFor(() => expect(screen.queryByTestId('uctc-gap-TC-TH-05')).toBeNull())
    expect(screen.getByTestId('uctc-gap-TC-NDR-06')).toBeInTheDocument()
  })

  it('falls back to a locally derived gap list when /gaps is unavailable', async () => {
    happyRoutes({
      'GET /api/uctc/gaps': () =>
        new Response(JSON.stringify({ detail: 'boom' }), { status: 500 }),
    })
    render(<Harness initialParams={{ tab: 'gaps' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gap-TC-NDR-06')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-gap-TC-TH-05')).toBeInTheDocument()
    expect(screen.queryByTestId('uctc-gap-TC-EDR-03')).toBeNull()
  })

  it('coverage mode sorts use cases worst-first and jumps back into the index', async () => {
    happyRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-mode-coverage')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-mode-coverage'))
    await waitFor(() => expect(screen.getByTestId('uctc-cov-UC-NDR')).toBeInTheDocument())

    const bars = screen.getAllByTestId(/^uctc-cov-/).map((el) => el.dataset.testid)
    expect(bars[bars.length - 1]).toBe('uctc-cov-UC-EDR')  // best covered last

    fireEvent.click(screen.getByTestId('uctc-cov-UC-NDR'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-NDR-06']))
  })

  it('renders the degraded state — not "0 test cases" — when the snapshot is missing', async () => {
    happyRoutes({
      'GET /api/uctc/test-cases': { index_loaded: false, index_version: null, test_cases: [], total: 0, index_total: 0 },
      'GET /api/uctc/use-cases': { index_loaded: false, index_version: null, use_cases: [], total: 0 },
      'GET /api/uctc/summary': { index_loaded: false, index_version: null, totals: {}, evidence: {} },
    })
    render(<Harness />)

    await waitFor(() => expect(screen.getByTestId('uctc-degraded')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-degraded')).toHaveTextContent('docs/uc_tc_mapping/_v2.2-source/')
    expect(screen.queryByTestId('uctc-rowcount')).toBeNull()
    // Every numeric tile reads as an em-dash, never a fabricated zero.
    const stats = document.querySelector('.adapter-registry__stats')
    expect(stats.textContent).not.toMatch(/0/)
  })

  it('surfaces a fetch error without blanking the surface', async () => {
    happyRoutes({
      'GET /api/uctc/test-cases': () =>
        new Response(JSON.stringify({ detail: { error: 'Index unavailable', code: 'BOOM' } }), { status: 500 }),
    })
    render(<Harness />)

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('Index unavailable [BOOM]')
    expect(screen.getByTestId('uctc-index')).toBeInTheDocument()
  })

  it('links out to the ATT&CK coverage and TTP surfaces', async () => {
    happyRoutes()
    const onNavigate = vi.fn()
    render(<Harness onNavigate={onNavigate} />)
    await waitFor(() => expect(screen.getByTestId('uctc-goto-coverage')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-goto-coverage'))
    expect(onNavigate).toHaveBeenCalledWith('coverage')

    fireEvent.click(screen.getByTestId('uctc-goto-ttps'))
    expect(onNavigate).toHaveBeenCalledWith('ttps')
  })
})

/* ─── mechanism surface ─────────────────────────────────────────────────
 *
 * The index is not one population. DET/HNT rows are proven by an attack
 * scenario; POS/PLT/AUT rows are proven by an assertion; and some rows cannot
 * be proven by this engine at all because their evidence lives in a console it
 * has no read client for. These tests pin all three states being SAYABLE —
 * conflating them is the failure mode this surface exists to prevent.
 */

const TC_CSPM_01 = {
  tc_id: 'TC-CSPM-01',
  ucs_id: 'UCS-CSPM-01',
  ucs_name: 'Posture Discovery',
  uc_id: 'UC-CSPM',
  use_case: 'Cloud Posture Management',
  title: 'Validate unified asset onboarding and multi-cloud visibility',
  tc_sheet: 'Cloud',
  validation_class: 'POS',
  priority: 'P1',
  differentiation_tier: 'LEAD',
  primary_kpi: 'Coverage',
  threshold: 'Qualitative pass',
  is_scoreable: false,
  detection_source: 'Cortex Cloud Posture Dashboard + REST API',
  evidence: { evidenced: false, scenario_count: 0, scenario_ids: [], planes: [] },
}

const TC_IR_08 = {
  tc_id: 'TC-IR-08',
  ucs_id: 'UCS-IR-04',
  ucs_name: 'Data Onboarding',
  uc_id: 'UC-IR',
  use_case: 'Incident Response',
  title: 'Validate ingestion and normalization of 3+ heterogeneous sources',
  tc_sheet: 'SecOps',
  validation_class: 'PLT',
  priority: 'P1',
  differentiation_tier: 'MOAT',
  primary_kpi: 'Detection Accuracy',
  threshold: 'Qualitative pass',
  is_scoreable: false,
  detection_source: 'XSIAM UI + XQL',
  evidence: { evidenced: false, scenario_count: 0, scenario_ids: [], planes: [] },
}

const PLT_IR_008 = {
  assertion_id: 'PLT-IR-008',
  name: 'Broker VM heterogeneous ingest and normalization',
  version: '1.0',
  status: 'active',
  validation_class: 'PLT',
  kind: 'state',
  tc_ref: 'TC-IR-08',
  tc_refs: ['TC-IR-08'],
  scope_limitations:
    'Does NOT prove the records traversed a Broker VM specifically — the operator supplies the collector URL.',
  primary_kpi: 'sources normalized',
  tc_scoreable: false,
  stimulus: { kind: 'eal_campaign', detail: 'run three emitters with one canary' },
  checks: [
    {
      check_id: 'chk-01',
      title: 'Each source lands in its own normalized dataset',
      probe: 'xql_distinct',
      primary: true,
      threshold: { kpi: 'sources normalized', op: '>=', value: 3, unit: '', source: 'authored_sharpening' },
      negative_control: { description: 'the NGFW records never normalize', measured_value: 2 },
      failure_conditions: ['fewer distinct values than the floor'],
    },
  ],
}

const assertionsPayload = {
  assertions: [PLT_IR_008],
  count: 1,
  total: 1,
  by_validation_class: { PLT: 1 },
  rejected: [{
    source: 'assertions/pos/POS-CSPM-999-tautology.yml',
    diagnostics: [{ code: 'A-17', detail: 'this check can never fail and therefore proves nothing' }],
  }],
  warnings: [],
  probes: [{ name: 'xql_rows' }, { name: 'xql_distinct' }],
}

const wideTestCaseList = {
  ...testCaseList,
  index_total: 6,
  total: 6,
  test_cases: [TC_EDR_03, TC_EDR_05, TC_NDR_06, TC_TH_05, TC_CSPM_01, TC_IR_08],
}

/** Detail route that answers per tc_id instead of one fixed payload. */
function detailRoute(url) {
  const id = new URL(url, 'http://localhost').pathname.split('/').pop()
  const row = wideTestCaseList.test_cases.find((t) => t.tc_id === id)
  if (!row) return detailEdr03
  if (id === 'TC-EDR-03') return detailEdr03
  return { ...row, index_loaded: true, entitlements: {}, evidenced_by: [], runs: null }
}

function mechRoutes(overrides = {}) {
  return happyRoutes({
    'GET /api/uctc/test-cases': wideTestCaseList,
    'GET /api/uctc/test-cases/:id': detailRoute,
    'GET /api/assertions': assertionsPayload,
    'GET /api/uctc/gaps': {
      ...gapsPayload,
      gaps: [TC_NDR_06, TC_TH_05, TC_CSPM_01, TC_IR_08],
      total: 4,
    },
    ...overrides,
  })
}

describe('UcTcIndexView — coverage by validation class', () => {
  it('breaks the headline into one cell per class, each naming its mechanism', async () => {
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-classstrip')).toBeInTheDocument())

    const strip = screen.getByTestId('uctc-classstrip')
    // DET/HNT are proven by scenarios, POS/PLT by assertions — and the cell
    // says so, because "18/110" is meaningless without it.
    expect(within(strip).getByTestId('uctc-class-DET')).toHaveTextContent('Attack scenario')
    expect(within(strip).getByTestId('uctc-class-POS')).toHaveTextContent('Posture assertion')
    expect(within(strip).getByTestId('uctc-class-PLT')).toHaveTextContent('Platform assertion')
    // PLT is 1/1 — proven by the assertion, with no scenario anywhere near it.
    expect(within(strip).getByTestId('uctc-class-PLT')).toHaveTextContent('1/1')
    // POS is 0 of 2: one row is measurable today, the other is blocked on a
    // data path, and the strip must not fuse those.
    expect(within(strip).getByTestId('uctc-class-POS')).toHaveTextContent('0/2')
  })

  it('keeps the header tiles agreeing with the class strip', async () => {
    // /api/uctc/summary counts SCENARIO evidence only. If the tiles read from
    // it while the strip counts assertions too, the two disagree by exactly the
    // assertion-proven rows — and the tile is the number a DC reads out loud.
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-classstrip')).toBeInTheDocument())

    const stats = document.querySelector('.adapter-registry__stats')
    // 6 rows: TC-EDR-03 (scenario) + TC-IR-08 (assertion) each have an artifact
    // BOUND. The tile must not call that "evidenced" — TC-IR-08's assertion has
    // never been run, so the number is authorship, not proof.
    expect(stats).toHaveTextContent('2artifact bound')
    expect(stats).not.toHaveTextContent('2evidenced')
    // …and the proof half is reported separately, and is zero until a tenant run
    // records a pass or fail. This pair is the anti-inflation guard on the tile.
    expect(stats).toHaveTextContent('0tenant-measured')
    // TC-CSPM-01 is blocked on ingest, so it is NOT counted as an open gap…
    expect(stats).toHaveTextContent('3open gaps')
    // …it is counted, separately and visibly, as needing a data path.
    expect(stats).toHaveTextContent('1need a data path')
    expect(screen.getByTestId('uctc-classstrip')).toHaveTextContent('2 of 6 have an artifact bound')
  })

  it('scopes the table to one class when a cell is clicked, and toggles back', async () => {
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-class-DET')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-class-DET'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-EDR-03', 'TC-NDR-06']))
    expect(screen.getByTestId('uctc-class-DET')).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByTestId('uctc-class-DET'))
    await waitFor(() => expect(rowIds()).toHaveLength(6))
  })

  it('marks a blocked row "needs a data path" instead of an empty dash', async () => {
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-CSPM-01')).toBeInTheDocument())

    expect(screen.getByTestId('uctc-datapath-cell-TC-CSPM-01')).toHaveTextContent('needs a data path')
    // An open row the engine can measure today keeps the plain dash.
    expect(screen.queryByTestId('uctc-datapath-cell-TC-NDR-06')).toBeNull()
  })

  it('names the assertion as the evidence for a PLT row', async () => {
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-IR-08')).toBeInTheDocument())

    const row = screen.getByTestId('uctc-row-TC-IR-08').closest('tr')
    expect(within(row).getByText(/1 assertion/)).toBeInTheDocument()
  })

  it('filters to the rows blocked on a data path', async () => {
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-filter-engine reach-needs-data-path'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-CSPM-01']))
  })

  it('filters by which mechanism supplied the evidence', async () => {
    mechRoutes()
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-row-TC-EDR-03')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-filter-evidence-assertion'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-IR-08']))

    fireEvent.click(screen.getByTestId('uctc-filter-evidence-scenario'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-EDR-03']))
  })
})

describe('UcTcIndexView — mechanisms mode', () => {
  it('states what proves each class and the ceiling the engine can reach', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-mechanisms')).toBeInTheDocument())

    const det = screen.getByTestId('uctc-mech-DET')
    expect(det).toHaveTextContent('Attack scenario')
    expect(det).toHaveTextContent('scenarios/{plane}/*.yml')

    const pos = screen.getByTestId('uctc-mech-POS')
    expect(pos).toHaveTextContent('Posture assertion')
    // Authored is not measured, and the card must not blur the two.
    expect(pos).toHaveTextContent('authored claims awaiting a run')
    expect(pos).toHaveTextContent('assertions/pos/*.yml')
    // Both POS fixtures are qualitative, so NEITHER can ever return a pass —
    // the ceiling the surface must state rather than imply otherwise.
    expect(pos).toHaveTextContent('0 of 2')
    expect(pos).toHaveTextContent('can ever return a machine')
    // …and the console that blocks the open row is named, not hand-waved.
    expect(screen.getByTestId('uctc-datapath-POS')).toHaveTextContent('Cortex Cloud Posture Dashboard')
  })

  it('keeps the unscoreable verdict cap visible per class', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-mech-PLT')).toBeInTheDocument())

    expect(screen.getByTestId('uctc-mech-PLT')).toHaveTextContent('not_applicable')
    expect(screen.getByTestId('uctc-mech-PLT')).toHaveTextContent('no measurable threshold')
  })

  it('shows the loader-refused assertions — a guard nobody can see is no guard', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertion-panel')).toBeInTheDocument())

    const rejected = screen.getByTestId('uctc-assertions-rejected')
    expect(rejected).toHaveTextContent('A-17')
    expect(rejected).toHaveTextContent('can never fail')
  })

  it('jumps from a class card into the filtered index', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-mech-open-DET')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-mech-open-DET'))
    await waitFor(() => expect(rowIds()).toEqual(['TC-EDR-03', 'TC-NDR-06']))
  })

  it('says "mechanism not deployed" — not "0 covered" — when /api/assertions is absent', async () => {
    // No route for /api/assertions: mockFetch answers 404, exactly like a
    // SimCore built before that router landed.
    happyRoutes({ 'GET /api/uctc/test-cases': wideTestCaseList })
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertions-absent')).toBeInTheDocument())

    expect(screen.getByTestId('uctc-assertions-absent')).toHaveTextContent('/api/assertions')
    expect(screen.getByTestId('uctc-mech-POS')).toHaveTextContent('NOT DEPLOYED')
  })

  it('says "deployed, nothing authored" when the catalog is empty', async () => {
    mechRoutes({
      'GET /api/assertions': { assertions: [], count: 0, total: 0, by_validation_class: {}, rejected: [], probes: [] },
    })
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertions-empty')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-mech-PLT')).toHaveTextContent('no assertion artifacts authored yet')
  })
})

describe('UcTcIndexView — the honest backlog', () => {
  it('splits the gap list by which mechanism closes it', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'gaps' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gap-TC-NDR-06')).toBeInTheDocument())

    // Default scope: the scenario backlog only.
    expect(screen.queryByTestId('uctc-gap-TC-CSPM-01')).toBeNull()

    fireEvent.click(screen.getByTestId('uctc-gapscope-assertion'))
    // TC-IR-08 is already proven by an assertion, so it is NOT a gap — even
    // though /api/uctc/gaps (which only knows scenarios) still returns it.
    await waitFor(() => expect(screen.queryByTestId('uctc-gap-TC-NDR-06')).toBeNull())
    expect(screen.queryByTestId('uctc-gap-TC-IR-08')).toBeNull()
  })

  it('sizes the blocked rows apart from the work that can start today', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'gaps' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gaps')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-gapscope-assertion'))
    await waitFor(() => expect(screen.getByTestId('uctc-gaps-blocked')).toBeInTheDocument())

    const blocked = screen.getByTestId('uctc-gaps-blocked')
    expect(blocked).toHaveTextContent('blocked on a data path')
    expect(within(blocked).getByTestId('uctc-blockedgap-TC-CSPM-01')).toBeInTheDocument()
    // …and it never appears in the closable list above it.
    expect(screen.queryByTestId('uctc-gap-TC-CSPM-01')).toBeNull()
  })

  it('warns that assertion gaps are unclosable when the mechanism is absent', async () => {
    happyRoutes({
      'GET /api/uctc/test-cases': wideTestCaseList,
      'GET /api/uctc/gaps': { ...gapsPayload, gaps: [TC_CSPM_01, TC_IR_08], total: 2 },
    })
    render(<Harness initialParams={{ tab: 'gaps' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gaps')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('uctc-gapscope-assertion'))
    await waitFor(() => expect(screen.getByTestId('uctc-gaps-noassertions')).toBeInTheDocument())
  })
})

describe('UcTcIndexView — the detail drawer names its proof', () => {
  it('says an authored assertion has never been run rather than implying a pass', async () => {
    // /api/assertions/runs is unmocked here — mockFetch 404s it, exactly like a
    // deployment where nobody has evaluated anything yet.
    mechRoutes()
    render(<Harness initialParams={{ tc: 'TC-IR-08' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertion-PLT-IR-008')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-assertion-unrun-PLT-IR-008'))
      .toHaveTextContent('never run — no tenant verdict recorded')
  })

  it('shows the recorded verdict once the assertion has actually been evaluated', async () => {
    mechRoutes({
      'GET /api/assertions/runs': {
        runs: [{
          assertion_id: 'PLT-IR-008', run_id: 'AR-7', tc_verdict: 'fail',
          reason: 'threshold_miss', tenant: 'acme', completed_at: '2026-08-01T09:00:00Z',
        }],
        count: 1,
      },
    })
    render(<Harness initialParams={{ tc: 'TC-IR-08' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertion-PLT-IR-008')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-assertion-PLT-IR-008')).toHaveTextContent('fail · threshold_miss')
    expect(screen.queryByTestId('uctc-assertion-unrun-PLT-IR-008')).toBeNull()
  })

  it('renders the bound assertion with what it does NOT prove and how it fails', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tc: 'TC-IR-08' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail-assertions')).toBeInTheDocument())

    const card = screen.getByTestId('uctc-assertion-PLT-IR-008')
    expect(card).toHaveTextContent('does NOT prove')
    expect(card).toHaveTextContent('Does NOT prove the records traversed a Broker VM')
    // The negative control — the named condition that turns the check red.
    expect(card).toHaveTextContent('the NGFW records never normalize')
    expect(card).toHaveTextContent('measured 2')
    // A qualitative index row can never be machine-passed, and the card says so.
    expect(card).toHaveTextContent('not_applicable')
  })

  it('names the prerequisite for a blocked row instead of calling it impossible', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tc: 'TC-CSPM-01' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail-datapath')).toBeInTheDocument())

    const banner = screen.getByTestId('uctc-detail-datapath')
    expect(banner).toHaveTextContent('Needs a data path first')
    expect(banner).toHaveTextContent('Cortex Cloud Posture Dashboard + REST API')
    // The worked example matters: an authored assertion already does this.
    expect(banner).toHaveTextContent('POS-CSPM-001')
    expect(banner).toHaveTextContent('not a closed door')
  })

  it('tells the author which artifact would close a reachable assertion row', async () => {
    mechRoutes({
      'GET /api/assertions': { assertions: [], total: 0, by_validation_class: {}, rejected: [], probes: [] },
    })
    render(<Harness initialParams={{ tc: 'TC-IR-08' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail-needs-assertion')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-detail-needs-assertion'))
      .toHaveTextContent('assertions/plt/*.yml')
  })

  it('flags a DET row the engine can drive but cannot read back as manual-only', async () => {
    const manualTc = {
      ...TC_NDR_06,
      tc_id: 'TC-CDR-09',
      detection_source: 'Cortex Cloud Runtime Alerts + /api/v1/alerts',
    }
    mechRoutes({
      'GET /api/uctc/test-cases': {
        ...wideTestCaseList, test_cases: [...wideTestCaseList.test_cases, manualTc],
      },
      'GET /api/uctc/test-cases/:id': () => ({ ...manualTc, index_loaded: true, evidenced_by: [], runs: null }),
    })
    render(<Harness initialParams={{ tc: 'TC-CDR-09' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail-manual')).toBeInTheDocument())

    // Not blocked — the engine still generates the signal — just not auto-reconciled.
    expect(screen.queryByTestId('uctc-detail-datapath')).toBeNull()
    expect(screen.getByTestId('uctc-detail-manual')).toHaveTextContent('Manual verification only')
  })
})
