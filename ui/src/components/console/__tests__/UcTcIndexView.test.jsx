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
import { EnvironmentContext, DEFAULT_ENV } from '../../../context/EnvironmentContext.jsx'
import { VERBS } from '../kit/index.js'
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
  it('renders the loading state, then every index row and the use-case tiles', async () => {
    happyRoutes()
    render(<Harness />)

    expect(screen.getByText(/loading the UC \/ TC index/i)).toBeInTheDocument()

    await waitFor(() => expect(screen.getByTestId('uctc-rowcount')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-rowcount')).toHaveTextContent('4 of 4 test cases')
    expect(rowIds()).toEqual(['TC-EDR-03', 'TC-EDR-05', 'TC-NDR-06', 'TC-TH-05'])

    // Three use cases; 4 test cases under them, 3 of them detections.
    expect(screen.getByTestId('uctc-tile-use-cases')).toHaveTextContent('3')
    expect(screen.getByTestId('uctc-tile-use-cases')).toHaveTextContent('4 test cases under them, 3 of them detections')
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

  it('drills into a use case: selecting a UC opens its detail drawer, and a TC in it drills further', async () => {
    const useCaseDetail = {
      index_loaded: true,
      index_version: '2.2',
      use_case: {
        uc_id: 'UC-EDR',
        use_case: 'Endpoint Detection & Response',
        domain: 'SecOps',
        min_license_path: 'Cortex XSIAM',
        base_platform_list: ['Cortex XSIAM'],
        addons_list: ['Endpoint Protection'],
        coverage_pct: 50,
        det_coverage_pct: 100,
        scenario_count: 2,
      },
      ucs_groups: [
        { ucs_id: 'UCS-EDR-01', ucs_name: 'Credential access', test_case_count: 2, evidenced_count: 1 },
      ],
      test_cases: [
        { tc_id: 'TC-EDR-03', title: 'Credential dumping', validation_class: 'DET' },
        { tc_id: 'TC-EDR-05', title: 'Reverse shell', validation_class: 'DET' },
      ],
    }
    happyRoutes({ 'GET /api/uctc/use-cases/:id': useCaseDetail })
    render(<Harness />)
    await waitFor(() => expect(screen.getByTestId('uctc-uc-UC-EDR')).toBeInTheDocument())

    // Selecting the use case opens its own drill-down drawer (was: filter only).
    fireEvent.click(screen.getByTestId('uctc-uc-UC-EDR'))
    await waitFor(() => expect(screen.getByTestId('uctc-uc-detail')).toBeInTheDocument())
    const drawer = screen.getByTestId('uctc-uc-detail')
    expect(drawer).toHaveTextContent('Endpoint Detection & Response')
    expect(drawer).toHaveTextContent('Cortex XSIAM')                 // entitlement
    expect(screen.getByTestId('uctc-uc-detail-stats')).toHaveTextContent('50%')
    expect(screen.getByTestId('uctc-uc-detail-tclist')).toBeInTheDocument()

    // One level deeper: a test case inside the UC drawer opens the TC drawer.
    fireEvent.click(screen.getByTestId('uctc-uc-detail-tc-TC-EDR-05'))
    await waitFor(() => expect(screen.getByTestId('uctc-detail')).toBeInTheDocument())
    expect(screen.queryByTestId('uctc-uc-detail')).toBeNull()
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

  it('splits pending runs where nothing executed out of the run rollup', async () => {
    happyRoutes({
      'GET /api/uctc/test-cases/:id': {
        ...detailEdr03,
        runs: {
          total: 3,
          verdicts: { pass: 1, fail: 0, pending: 2, not_applicable: 0 },
          pending_reasons: { not_executed: 1 },
          latest: { run_id: 'r-43', scenario_id: 'SIM-EDR-001', status: 'complete',
            tc_verdict: 'pending', reason: 'not_executed', started_at: '2026-07-31T10:00:00Z' },
        },
      },
    })
    render(<Harness initialParams={{ tc: 'TC-EDR-03' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail')).toHaveTextContent('3 runs'))
    const reason = screen.getByTestId('uctc-pending-reason-not_executed')
    expect(reason).toHaveTextContent('1 nothing executed')
    expect(reason.className).toContain('verdict-reason--warn')
    expect(screen.getByTestId('uctc-open-run')).toHaveTextContent('nothing executed')
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
    // No tile renders, and every tab count reads as an em-dash, never a zero.
    expect(screen.queryByTestId('uctc-tiles')).toBeNull()
    for (const id of ['index', 'mechanisms', 'coverage', 'gaps']) {
      expect(screen.getByTestId(`uctc-mode-${id}`).textContent).not.toMatch(/\d/)
    }
    expect(screen.getByTestId('uctc-lede').textContent).not.toMatch(/\d/)
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
    expect(onNavigate).toHaveBeenCalledWith('coverage', { tab: 'mitre' })

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

describe('UcTcIndexView — evidence and reach in the table', () => {
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

describe('UcTcIndexView — the assertion mechanism stays sayable', () => {
  it('shows the loader-refused assertions — a guard nobody can see is no guard', async () => {
    mechRoutes()
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertion-panel')).toBeInTheDocument())

    const rejected = screen.getByTestId('uctc-assertions-rejected')
    expect(rejected).toHaveTextContent('A-17')
    expect(rejected).toHaveTextContent('can never fail')
  })

  it('says "mechanism not deployed" — not "0 covered" — when /api/assertions is absent', async () => {
    // No route for /api/assertions: mockFetch answers 404, exactly like a
    // SimCore built before that router landed.
    happyRoutes({ 'GET /api/uctc/test-cases': wideTestCaseList })
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertions-absent')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-assertions-absent')).toHaveTextContent('/api/assertions')
  })

  it('says "deployed, nothing authored" when the catalog is empty', async () => {
    mechRoutes({
      'GET /api/assertions': { assertions: [], count: 0, total: 0, by_validation_class: {}, rejected: [], probes: [] },
    })
    render(<Harness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-assertions-empty')).toBeInTheDocument())
  })

  it('names the absent mechanism in the drawer of a row only an assertion can prove', async () => {
    happyRoutes({
      'GET /api/uctc/test-cases': wideTestCaseList,
      'GET /api/uctc/test-cases/:id': detailRoute,
    })
    render(<Harness initialParams={{ tc: 'TC-IR-08' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-detail-needs-assertion')).toBeInTheDocument())
    expect(screen.getByTestId('uctc-detail-needs-assertion')).toHaveTextContent('not deployed here')
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

/* ─── the proof path (design C5 / C6) ───────────────────────────────────
 *
 * Every number on these tabs comes from a SimCore API: scenarios and their
 * tc_refs (/api/scenarios), door readiness (/api/doors/readiness + /api/doors),
 * runs (/api/runs) and read back (readBackOf). Only a read-back run that saw
 * a detection makes a test case covered, and a source that did not load reads
 * "Can't check", never zero, never green.
 */

const SCENARIOS = [
  {
    scenario_id: 'SIM-EDR-001', tc_ref: 'TC-EDR-03', tc_refs: ['TC-EDR-03'],
    steps: [{ id: 'step-01', expected_detections: [{ type: 'BIOC' }, { type: 'XQL' }] }],
  },
  {
    scenario_id: 'SIM-EDR-002', tc_ref: 'TC-EDR-03', tc_refs: ['TC-EDR-03'],
    steps: [{ id: 'step-01', expected_detections: [{ type: 'ABIOC' }] }],
  },
  {
    scenario_id: 'SIM-TH-001', tc_ref: 'TC-TH-05', tc_refs: ['TC-TH-05'],
    steps: [{ id: 'step-01', expected_detections: [{ type: 'XQL' }] }],
  },
]

const runRow = (over) => ({
  mode: 'pull', status: 'complete', delivered_at: '2026-11-13T10:00:05',
  started_at: '2026-11-13T10:00:00', tc_verdict: 'pending',
  tc_verdict_detail: { source: 'complete' }, expected_detections: 2, detected_count: 0,
  ...over,
})

const RUNS = [
  // Read back from the tenant, and it saw detections: TC-EDR-03 is covered.
  runRow({
    run_id: 'r-1', scenario_id: 'SIM-EDR-001', tc_verdict: 'pass', detected_count: 2,
    tc_verdict_detail: { source: 'reconcile:xsiam:acme', scored_at: '2026-11-13T11:00:00' },
  }),
  // Finished, never read back — whatever its count says, it proves nothing yet.
  runRow({ run_id: 'r-2', scenario_id: 'SIM-TH-001', detected_count: 3 }),
]

const needsDoor = (id, doorId, n = 1) => ({
  scenario_id: id, state: 'needs_doors', scorable: 0, total: n, not_open: [{ door_id: doorId, detections: n }],
})

const READINESS = {
  registry_loaded: true,
  scenarios: {
    'SIM-EDR-001': needsDoor('SIM-EDR-001', 'xdr_agent', 2),
    'SIM-EDR-002': needsDoor('SIM-EDR-002', 'xdr_agent'),
    'SIM-TH-001': needsDoor('SIM-TH-001', 'ngfw_traffic'),
  },
}

const DOORS = {
  doors: [
    { key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', scope: 'agent', state: 'unknown', rung: 'authored' },
    { key: 'ngfw_traffic', door_id: 'ngfw_traffic', label: 'NGFW traffic logs', scope: 'tenant', state: 'unknown', rung: 'authored' },
  ],
}

function proofRoutes(overrides = {}) {
  return happyRoutes({
    'GET /api/doors/readiness': READINESS,
    'GET /api/doors': DOORS,
    ...overrides,
  })
}

const ENV = { scenarios: SCENARIOS, runs: RUNS, errors: {}, loading: {} }

function EnvHarness({ env = ENV, ...props }) {
  return (
    <EnvironmentContext.Provider value={{ ...DEFAULT_ENV, ...env }}>
      <Harness {...props} />
    </EnvironmentContext.Provider>
  )
}

describe('UC / TC Index — tiles, Verdict and the Runs rollup (C5)', () => {
  it('rolls use cases up into the four tiles from read-back runs only', async () => {
    proofRoutes()
    render(<EnvHarness />)
    await waitFor(() => expect(screen.getByTestId('uctc-tiles')).toBeInTheDocument())

    expect(screen.getByTestId('uctc-tile-use-cases')).toHaveTextContent('3')
    // UC-EDR: its one detection test case was detected in a read-back run.
    expect(screen.getByTestId('uctc-tile-partial')).toHaveTextContent('1')
    expect(screen.getByTestId('uctc-tile-partial')).toHaveTextContent('1 fully')
    // UC-NDR (no scenario) and UC-TH (run not read back) are pending.
    expect(screen.getByTestId('uctc-tile-pending')).toHaveTextContent('2')
    // No door has been checked, so "0 blocked" would be a guess.
    expect(screen.getByTestId('uctc-tile-blocked')).toHaveTextContent('—')
    expect(screen.getByTestId('uctc-tile-blocked')).toHaveTextContent("Can't check")
  })

  it('shows each test case verdict as an icon and a word', async () => {
    proofRoutes()
    render(<EnvHarness />)
    await waitFor(() => expect(screen.getByTestId('uctc-verdict-TC-EDR-03')).toBeInTheDocument())

    const verdict = (id) => screen.getByTestId(`uctc-verdict-${id}`)
    expect(within(verdict('TC-EDR-03')).getByRole('img', { name: 'Detected' })).toHaveTextContent('✓')
    expect(within(verdict('TC-NDR-06')).getByRole('img', { name: 'No scenario' })).toBeInTheDocument()
    // Ran, never read back: Pending, not Detected, however many it counted.
    expect(within(verdict('TC-TH-05')).getByRole('img', { name: 'Pending' })).toBeInTheDocument()
    // A posture row is proven by an assertion, not by this path.
    expect(screen.getByTestId('uctc-row-TC-EDR-05').closest('tr')).toHaveTextContent('by assertion')
    // The rail carries the use case's verdict too.
    expect(within(screen.getByTestId('uctc-uc-verdict-UC-EDR')).getByRole('img', { name: 'Proven' })).toBeInTheDocument()
  })

  it('rolls the runs up per test case, with the VerdictReason chip for runs that tested nothing', async () => {
    proofRoutes()
    const runs = [...RUNS, runRow({
      run_id: 'r-3', scenario_id: 'SIM-EDR-002', delivered_at: null,
      tc_verdict_detail: { source: 'complete', reason: 'not_executed' },
    })]
    render(<EnvHarness env={{ ...ENV, runs }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-runs-TC-EDR-03')).toBeInTheDocument())

    expect(screen.getByTestId('uctc-runs-TC-EDR-03')).toHaveTextContent('2 runs · 1 read back')
    const chip = screen.getByTestId('uctc-runs-reason-TC-EDR-03-not_executed')
    expect(chip).toHaveTextContent('1 nothing executed')
    expect(chip.className).toContain('verdict-reason--warn')
    expect(screen.getByTestId('uctc-runs-TC-TH-05')).toHaveTextContent('1 run · 1 not read back')
    expect(screen.getByTestId('uctc-runs-TC-NDR-06')).toHaveTextContent('No runs')
  })

  it('says "Can\'t check" — never "No runs" — when the run list did not load', async () => {
    proofRoutes()
    render(<EnvHarness env={{ ...ENV, runs: [], errors: { runs: 'SimCore restarting' } }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-runs-TC-EDR-03')).toBeInTheDocument())

    expect(within(screen.getByTestId('uctc-runs-TC-EDR-03')).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
    expect(within(screen.getByTestId('uctc-verdict-TC-EDR-03')).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
    expect(screen.getByTestId('uctc-tile-partial')).toHaveTextContent('—')
    expect(screen.getByTestId('uctc-tile-pending')).toHaveTextContent('—')
  })
})

describe('UC / TC tabs — the strip (C6)', () => {
  it('marks the selected tab and counts each view', async () => {
    proofRoutes()
    render(<EnvHarness initialParams={{ tab: 'coverage' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-coverage')).toBeInTheDocument())

    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Index3', 'Mechanisms5', 'Coverage1/3', 'Gaps2'])
    expect(screen.getByTestId('uctc-mode-coverage')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('uctc-mode-coverage').className).toContain('is-active')
    expect(screen.getByTestId('uctc-mode-index')).toHaveAttribute('aria-selected', 'false')
  })
})

describe('UC / TC Mechanisms (C6)', () => {
  it('draws one card per mechanism, never claiming the tenant has it', async () => {
    proofRoutes()
    render(<EnvHarness initialParams={{ tab: 'mechanisms' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-mech-BIOC')).toBeInTheDocument())

    for (const m of ['BIOC', 'ABIOC', 'XQL', 'Correlation', 'IOC']) {
      expect(within(screen.getByTestId(`uctc-mech-${m}`)).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
    }
    // XQL: TC-EDR-03 (via SIM-EDR-001) and TC-TH-05 rely on it; 2 objects.
    expect(screen.getByTestId('uctc-mech-XQL')).toHaveTextContent('2test cases rely on it · 2 objects')
    expect(screen.getByTestId('uctc-mech-BIOC')).toHaveTextContent('1test cases rely on it · 1 objects')
    expect(screen.getByTestId('uctc-mech-Correlation')).toHaveTextContent('0test cases rely on it · 0 objects')
    expect(screen.queryByRole('img', { name: 'Present' })).toBeNull()
    expect(screen.queryByRole('img', { name: 'Up' })).toBeNull()
  })

  it('lists each test case with its mechanisms and the component its signal comes from', async () => {
    proofRoutes()
    const onNavigate = vi.fn()
    render(<EnvHarness initialParams={{ tab: 'mechanisms' }} onNavigate={onNavigate} />)
    await waitFor(() => expect(screen.getByTestId('uctc-mech-row-TC-EDR-03')).toBeInTheDocument())

    const row = screen.getByTestId('uctc-mech-row-TC-EDR-03')
    expect(row).toHaveTextContent('BIOC')
    expect(row).toHaveTextContent('ABIOC')
    expect(row).toHaveTextContent('XQL')
    expect(within(row).getByRole('img', { name: "Can't check" })).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('uctc-mech-component-TC-EDR-03'))
    expect(onNavigate).toHaveBeenCalledWith('component-agents', undefined)
    expect(screen.getByTestId('uctc-mech-component-TC-TH-05')).toHaveTextContent('Firewall')
    // TC-NDR-06 has no scenario, so it relies on nothing yet.
    expect(screen.queryByTestId('uctc-mech-row-TC-NDR-06')).toBeNull()
    expect(screen.getByTestId('uctc-mech-noscenario')).toHaveTextContent('1 detection test case has no scenario yet')
  })
})

describe('UC / TC Coverage (C6)', () => {
  it('counts the five stages, and only a read back counts as Detected', async () => {
    proofRoutes()
    render(<EnvHarness initialParams={{ tab: 'coverage' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-funnel')).toBeInTheDocument())

    const step = (s) => screen.getByTestId(`uctc-funnel-${s}`)
    expect(step('scenario')).toHaveTextContent('2of 3Has a scenario')
    // No door has been checked: no number, and it says so.
    expect(step('components')).toHaveTextContent('—of 3Components ready')
    expect(step('components')).toHaveTextContent("2 can't check")
    expect(step('ran')).toHaveTextContent('2of 3Ran')
    expect(step('readBack')).toHaveTextContent('1of 3Read back')
    // r-2 counted 3 detections but was never read back: it does not count.
    expect(step('detected')).toHaveTextContent('1of 3Detected')
  })

  it('draws a status per stage, stopping where each test case stalls', async () => {
    proofRoutes()
    render(<EnvHarness initialParams={{ tab: 'coverage' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-cov-row-TC-EDR-03')).toBeInTheDocument())

    const cell = (tc, stage) => screen.getByTestId(`uctc-cov-${tc}-${stage}`)
    expect(within(cell('TC-EDR-03', 'detected')).getByRole('img', { name: 'Detected' })).toBeInTheDocument()
    expect(within(cell('TC-EDR-03', 'components')).getByRole('img', { name: "Can't check" })).toBeInTheDocument()
    expect(within(cell('TC-NDR-06', 'scenario')).getByRole('img', { name: 'None' })).toBeInTheDocument()
    expect(cell('TC-NDR-06', 'ran')).toHaveTextContent('—')
    expect(within(cell('TC-TH-05', 'readBack')).getByRole('img', { name: 'Not read back' })).toBeInTheDocument()
    expect(cell('TC-TH-05', 'detected')).toHaveTextContent('—')
    // Posture rows are not on the detection path.
    expect(screen.queryByTestId('uctc-cov-row-TC-EDR-05')).toBeNull()
  })

  it('says a staged push bundle is waiting, not running and not ran', async () => {
    proofRoutes()
    const runs = [RUNS[0], runRow({
      run_id: 'r-s', scenario_id: 'SIM-TH-001', mode: 'push', status: 'staged',
      delivered_at: null, tc_verdict: null, tc_verdict_detail: null,
    })]
    render(<EnvHarness env={{ ...ENV, runs }} initialParams={{ tab: 'coverage' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-cov-row-TC-TH-05')).toBeInTheDocument())

    expect(within(screen.getByTestId('uctc-cov-TC-TH-05-ran')).getByRole('img', { name: 'Waiting' })).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('uctc-mode-index'))
    await waitFor(() => expect(screen.getByTestId('uctc-runs-TC-TH-05')).toHaveTextContent('1 run · 1 waiting'))
  })

  it('carries the VerdictReason chip where a test case stalls', async () => {
    proofRoutes()
    const runs = [RUNS[0], runRow({
      run_id: 'r-2', scenario_id: 'SIM-TH-001',
      tc_verdict_detail: { source: 'reconcile:xsiam:acme', reason: 'soak_pending' },
    })]
    render(<EnvHarness env={{ ...ENV, runs }} initialParams={{ tab: 'coverage' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-cov-reason-TC-TH-05')).toBeInTheDocument())

    const cell = screen.getByTestId('uctc-cov-TC-TH-05-detected')
    expect(within(cell).getByRole('img', { name: 'Not seen yet' })).toBeInTheDocument()
    expect(screen.getByTestId('uctc-cov-reason-TC-TH-05')).toHaveTextContent('detectors soaking')
  })

  it('names the sources it could not read instead of drawing zeros', async () => {
    proofRoutes({
      'GET /api/doors/readiness': () => new Response(JSON.stringify({ detail: 'boom' }), { status: 500 }),
    })
    render(<EnvHarness env={{ ...ENV, runs: [], errors: { runs: 'SimCore restarting' } }} initialParams={{ tab: 'coverage' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-source-notice')).toHaveTextContent('Component readiness did not load'))

    expect(screen.getByTestId('uctc-source-notice')).toHaveTextContent('The run list did not load (SimCore restarting)')
    expect(screen.getByTestId('uctc-funnel-ran')).toHaveTextContent('—of 3')
    expect(screen.getByTestId('uctc-funnel-detected')).toHaveTextContent('—of 3')
    expect(screen.getByTestId('uctc-mode-coverage')).toHaveTextContent('—')
  })
})

describe('UC / TC Gaps (C6)', () => {
  it('lists every test case not yet detected, earliest stall first, with counts per kind', async () => {
    proofRoutes()
    render(<EnvHarness initialParams={{ tab: 'gaps' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gaplist')).toBeInTheDocument())

    const order = within(screen.getByTestId('uctc-gaplist')).getAllByRole('listitem').map((li) => li.dataset.testid)
    expect(order).toEqual(['uctc-gap-TC-NDR-06', 'uctc-gap-TC-TH-05'])
    expect(screen.queryByTestId('uctc-gap-TC-EDR-03')).toBeNull()

    expect(screen.getByTestId('uctc-gapfilter-all')).toHaveTextContent('All2')
    expect(screen.getByTestId('uctc-gapfilter-no_scenario')).toHaveTextContent('No scenario1')
    expect(screen.getByTestId('uctc-gapfilter-component')).toHaveTextContent('Component not ready0')
    expect(screen.getByTestId('uctc-gapfilter-never_ran')).toHaveTextContent('Never ran0')
    expect(screen.getByTestId('uctc-gapfilter-not_read_back')).toHaveTextContent('Not read back1')
    expect(screen.getByTestId('uctc-gapfilter-not_seen')).toHaveTextContent('Not seen yet0')
    // "Can't check" appears only when something could not be checked.
    expect(screen.queryByTestId('uctc-gapfilter-unknown')).toBeNull()

    fireEvent.click(screen.getByTestId('uctc-gapfilter-not_read_back'))
    expect(screen.getByTestId('uctc-gapfilter-not_read_back')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByTestId('uctc-gap-TC-NDR-06')).toBeNull()
    expect(screen.getByTestId('uctc-gap-why-TC-TH-05')).toHaveTextContent('Run r-2 finished but has not been read back')
  })

  it('offers no verb for "No scenario", only the link to Build a chain', async () => {
    proofRoutes()
    const onNavigate = vi.fn()
    render(<EnvHarness initialParams={{ tab: 'gaps' }} onNavigate={onNavigate} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gap-TC-NDR-06')).toBeInTheDocument())

    const row = screen.getByTestId('uctc-gap-TC-NDR-06')
    expect(within(row).getByRole('img', { name: 'No scenario' })).toBeInTheDocument()
    const buttons = within(row).getAllByRole('button').map((b) => b.textContent)
    expect(buttons).toEqual(['TC-NDR-06', 'Build a chain ›'])
    fireEvent.click(within(row).getByText('Build a chain ›'))
    expect(onNavigate).toHaveBeenCalledWith('composer')
  })

  it('reads a finished run back from the gap row', async () => {
    const fetchMock = proofRoutes({ 'POST /api/runs/:id/reconcile': { observed: 1, denominator: 1, queries_issued: 2 } })
    render(<EnvHarness initialParams={{ tab: 'gaps' }} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gap-TC-TH-05')).toBeInTheDocument())

    fireEvent.click(within(screen.getByTestId('uctc-gap-TC-TH-05')).getByRole('button', { name: 'Read back' }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, init]) => String(u).includes('/api/runs/r-2/reconcile')
      && init && init.method === 'POST')).toBe(true))
  })

  it('offers Run, Configure and Open results from the closed verb set where each fits', async () => {
    const onNavigate = vi.fn()
    const TC_NDR_07 = { ...TC_NDR_06, tc_id: 'TC-NDR-07', title: 'Second NDR detection' }
    proofRoutes({
      'GET /api/uctc/test-cases': { ...testCaseList, test_cases: [...testCaseList.test_cases, TC_NDR_07] },
      'GET /api/doors/readiness': {
        ...READINESS,
        scenarios: {
          ...READINESS.scenarios,
          'SIM-NDR-001': needsDoor('SIM-NDR-001', 'ngfw_traffic'),
          'SIM-NDR-002': needsDoor('SIM-NDR-002', 'xdr_agent'),
        },
      },
      'GET /api/doors': { doors: [
        { key: 'ngfw_traffic', door_id: 'ngfw_traffic', label: 'NGFW traffic logs', state: 'closed', rung: 'authored' },
        { key: 'xdr_agent', door_id: 'xdr_agent', label: 'XDR agent', state: 'unknown', rung: 'authored' },
      ] },
    })
    const scenarios = [
      ...SCENARIOS,
      { scenario_id: 'SIM-NDR-001', tc_ref: 'TC-NDR-06', tc_refs: ['TC-NDR-06'], steps: [] },
      { scenario_id: 'SIM-NDR-002', tc_ref: 'TC-NDR-07', tc_refs: ['TC-NDR-07'], steps: [] },
    ]
    const runs = [RUNS[0], runRow({
      run_id: 'r-2', scenario_id: 'SIM-TH-001',
      tc_verdict_detail: { source: 'reconcile:xsiam:acme', scored_at: '2026-11-13T11:00:00' },
    })]
    render(<EnvHarness env={{ ...ENV, scenarios, runs }} initialParams={{ tab: 'gaps' }} onNavigate={onNavigate} />)
    await waitFor(() => expect(screen.getByTestId('uctc-gap-TC-NDR-06')).toHaveAttribute('data-kind', 'component'))

    // A checked, closed door: Configure opens that component's setup.
    expect(screen.getByTestId('uctc-gap-why-TC-NDR-06')).toHaveTextContent('Firewall: NGFW traffic logs is checked and closed')
    fireEvent.click(within(screen.getByTestId('uctc-gap-TC-NDR-06')).getByRole('button', { name: 'Configure' }))
    expect(onNavigate).toHaveBeenCalledWith('component-firewall', { tab: 'setup' })

    // An unchecked door is not "not ready": it never ran, so the verb is Run.
    expect(screen.getByTestId('uctc-gap-TC-NDR-07')).toHaveAttribute('data-kind', 'never_ran')
    expect(screen.getByTestId('uctc-gap-why-TC-NDR-07')).toHaveTextContent("whether its components are ready can't be checked yet")
    fireEvent.click(within(screen.getByTestId('uctc-gap-TC-NDR-07')).getByRole('button', { name: 'Run' }))
    expect(onNavigate).toHaveBeenCalledWith('preflight', { arm: 'SIM-NDR-002' })

    // Read back, nothing seen yet: Open results.
    expect(screen.getByTestId('uctc-gap-TC-TH-05')).toHaveAttribute('data-kind', 'not_seen')
    fireEvent.click(within(screen.getByTestId('uctc-gap-TC-TH-05')).getByRole('button', { name: 'Open results' }))
    expect(onNavigate).toHaveBeenCalledWith('proof', { run: 'r-2' })

    // Every verb on the page is from the closed set.
    const verbs = screen.getAllByRole('button').filter((b) => b.className.includes('kit-btn')).map((b) => b.textContent)
    expect(verbs.length).toBeGreaterThan(0)
    for (const v of verbs) expect(VERBS).toContain(v)
  })
})
