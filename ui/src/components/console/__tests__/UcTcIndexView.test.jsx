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
