/**
 * Scenarios library completeness (design v33, handoff item 6).
 *
 * The page lists the corpus AND the extended library (the staged draft pairs
 * from GET /api/scenarios/staged), every card shows its UC/TC, its detection
 * source (the doors its detections land on) and its MITRE techniques, and an
 * Unmapped filter lists the scenarios missing any of the three, with its count.
 * Every mapping comes from the API; a gap the API did not state is "Not
 * checked", never "Not mapped".
 */
import React from 'react'
import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import OperationsView from '../OperationsView.jsx'
import { mappingFor, unmappedCounts } from '../scenarioMapping.js'
import { installRoutes } from '../../../test/mockFetch.js'

void React

// Fully mapped: index refs, a technique, and doors in its readiness.
const EDR = {
  scenario_id: 'SIM-EDR-001', name: 'Credential Dumping', plane: 'EDR',
  mitre_technique: 'T1003', uc_ref: 'UCS-EDR-01', tc_ref: 'TC-EDR-03', tc_refs: ['TC-EDR-03'],
  steps: [{ id: 'step-01', mitre_technique: 'T1003.001', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] }],
}
// Mapped on UC/TC and MITRE, but its detections derive no door.
const CSPM = {
  scenario_id: 'SIM-CSPM-002', name: 'Posture Sweep', plane: 'CSPM',
  mitre_technique: 'T1580', uc_ref: 'UCS-CSPM-01', tc_ref: 'TC-CSPM-02', tc_refs: ['TC-CSPM-02'],
  steps: [{ id: 'step-01', expected_detections: [{ plane: 'CSPM', type: 'XQL' }] }],
}
// Its readiness was not evaluated (registry absent): source NOT CHECKED, not unmapped.
const NDR = {
  scenario_id: 'SIM-NDR-001', name: 'C2 Beacon', plane: 'NDR',
  mitre_technique: 'T1071', uc_ref: 'UCS-NDR-02', tc_ref: 'TC-NDR-01', tc_refs: ['TC-NDR-01'],
  steps: [{ id: 'step-01', expected_detections: [{ plane: 'NDR', type: 'XQL' }] }],
}
// A staged pair: no UC/TC refs (staging writes none), its own doors.
const DRAFT = {
  scenario_id: 'SIM-DRAFT-015', name: 'Masquerading Malware', plane: 'EDR',
  mitre_technique: 'T1036.005', uc_ref: null, tc_ref: null, tc_refs: [],
  steps: [{ id: 'step-01', mitre_technique: 'T1036.005', expected_detections: [{ plane: 'EDR', type: 'BIOC' }] }],
  doors: ['xdr_agent'], doors_code: null,
  staged: { card: 'TTP-0000-0015', platform: 'windows', source: null, findings: ['source not recorded'], launchable: false },
}

const DOORS = [
  { door_id: 'xdr_agent', key: 'xdr_agent', label: 'XDR agent', scope: 'agent', state: 'unknown' },
  { door_id: 'ngfw_traffic', key: 'ngfw_traffic', label: 'NGFW traffic logs', scope: 'tenant', state: 'unknown' },
]
const READINESS = {
  'SIM-EDR-001': { scenario_id: 'SIM-EDR-001', state: 'not_evaluated', not_open: [], doors: ['ngfw_traffic', 'xdr_agent'] },
  'SIM-CSPM-002': { scenario_id: 'SIM-CSPM-002', state: 'not_evaluated', not_open: [], doors: [] },
  'SIM-NDR-001': { scenario_id: 'SIM-NDR-001', state: 'not_evaluated', reason: 'REGISTRY_ABSENT' },
}

function mount({ staged = { loaded: true, code: null, scenarios: [DRAFT], total: 1 }, ...props } = {}) {
  installRoutes({
    'GET /api/scenarios': { scenarios: [EDR, CSPM, NDR] },
    'GET /api/scenarios/staged': staged,
    'GET /api/runs': { runs: [] },
    'GET /api/agents': { agents: [] },
    'GET /api/doors': { doors: DOORS },
    'GET /api/doors/readiness': { registry_loaded: true, scenarios: READINESS },
  })
  return render(<OperationsView {...props} />)
}
const shown = () => screen.queryAllByTestId(/^scenario-card-SIM-/).map((c) => c.getAttribute('data-testid').replace('scenario-card-', ''))
const mapOf = (id) => within(screen.getByTestId(`scenario-card-${id}`)).getByTestId('scenario-card-map')
const axis = (id, name) => mapOf(id).querySelector(`[data-map="${name}"]`)

describe('Scenarios — every card shows UC/TC, detection source and MITRE', () => {
  it('reads each mapping from the API: index refs, the readiness doors by label, the techniques', async () => {
    mount()
    await waitFor(() => expect(axis('SIM-EDR-001', 'source')).toHaveTextContent('NGFW traffic logs · XDR agent'))
    expect(axis('SIM-EDR-001', 'uctc')).toHaveTextContent('UCS-EDR-01 · TC-EDR-03')
    expect(axis('SIM-EDR-001', 'mitre')).toHaveTextContent('T1003 · T1003.001')
    for (const a of ['uctc', 'source', 'mitre']) expect(axis('SIM-EDR-001', a)).toHaveAttribute('data-state', 'mapped')
  })

  it('a door list that came back empty is Not mapped; a readiness with no answer is Not checked', async () => {
    mount()
    await waitFor(() => expect(axis('SIM-CSPM-002', 'source')).toHaveAttribute('data-state', 'missing'))
    expect(axis('SIM-CSPM-002', 'source')).toHaveTextContent('Not mapped')
    expect(axis('SIM-NDR-001', 'source')).toHaveAttribute('data-state', 'unknown')
    expect(axis('SIM-NDR-001', 'source')).toHaveTextContent('Not checked')
  })

  it('before door readiness arrives no source is claimed either way', () => {
    const m = mappingFor(EDR, undefined)
    expect(m.source.state).toBe('unknown')
    expect(m.unmapped).toBe(false)
  })
})

describe('Scenarios — the extended library', () => {
  it('lists the staged pairs below the corpus, with their own mapping and no verbs', async () => {
    mount({ onContinueToLaunch: () => {} })
    const section = await screen.findByTestId('extended-library')
    await waitFor(() => expect(within(section).getByTestId('scenario-card-SIM-DRAFT-015')).toBeInTheDocument())
    expect(screen.getByTestId('library-staged-count')).toHaveTextContent('1')
    const card = within(section).getByTestId('scenario-card-SIM-DRAFT-015')
    expect(within(card).queryByRole('button')).not.toBeInTheDocument()
    expect(within(card).getByText('Staged draft')).toBeInTheDocument()
    expect(axis('SIM-DRAFT-015', 'uctc')).toHaveTextContent('Not mapped')
    expect(axis('SIM-DRAFT-015', 'source')).toHaveTextContent('XDR agent')
    expect(axis('SIM-DRAFT-015', 'mitre')).toHaveTextContent('T1036.005')
  })

  it('the Library select narrows the page to the corpus or to the extended library', async () => {
    mount()
    await waitFor(() => expect(shown()).toHaveLength(4))
    const lib = screen.getByRole('combobox', { name: 'Library' })
    fireEvent.change(lib, { target: { value: 'corpus' } })
    expect(shown().sort()).toEqual(['SIM-CSPM-002', 'SIM-EDR-001', 'SIM-NDR-001'])
    expect(screen.queryByTestId('extended-library')).not.toBeInTheDocument()
    fireEvent.change(lib, { target: { value: 'extended' } })
    expect(shown()).toEqual(['SIM-DRAFT-015'])
  })

  it('a staging directory that is not on this SimCore is said, not shown as an empty library', async () => {
    mount({ staged: { loaded: false, code: 'STAGED_LIBRARY_ABSENT', detail: 'not shipped', scenarios: [], total: 0 } })
    expect(await screen.findByTestId('extended-library-absent')).toHaveTextContent('STAGED_LIBRARY_ABSENT')
    expect(screen.queryByText('No staged pair matches the current filter.')).not.toBeInTheDocument()
  })
})

describe('Scenarios — the Unmapped filter', () => {
  it('carries its count and lists only the scenarios missing a mapping', async () => {
    mount()
    await waitFor(() => expect(shown()).toHaveLength(4))
    const toggle = screen.getByTestId('filter-unmapped')
    // CSPM (no door) and the staged pair (no UC/TC). NDR's source was not
    // checked, so it is not counted as unmapped.
    await waitFor(() => expect(screen.getByTestId('filter-unmapped-count')).toHaveTextContent('2'))
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    expect(shown().sort()).toEqual(['SIM-CSPM-002', 'SIM-DRAFT-015'])
    expect(screen.getByTestId('scenario-filter-count')).toHaveTextContent('2 of 4 shown')
    fireEvent.click(toggle)
    expect(shown()).toHaveLength(4)
  })

  it('the count follows the library in view', async () => {
    mount()
    await waitFor(() => expect(screen.getByTestId('filter-unmapped-count')).toHaveTextContent('2'))
    fireEvent.change(screen.getByRole('combobox', { name: 'Library' }), { target: { value: 'corpus' } })
    expect(screen.getByTestId('filter-unmapped-count')).toHaveTextContent('1')
  })

  it('counts a missing UC/TC, a missing technique and an empty door list; never an unanswered one', () => {
    const readiness = { A: { doors: ['xdr_agent'] }, B: { doors: ['xdr_agent'] }, C: { doors: [] }, D: { reason: 'REGISTRY_ABSENT' } }
    const rows = [
      { scenario_id: 'A', uc_ref: 'UC-1', tc_refs: ['TC-1'], mitre_technique: 'T1' },
      { scenario_id: 'B', uc_ref: 'UC-1', tc_refs: [], mitre_technique: 'T1' },
      { scenario_id: 'C', uc_ref: 'UC-1', tc_refs: ['TC-1'], mitre_technique: 'T1' },
      { scenario_id: 'D', uc_ref: 'UC-1', tc_refs: ['TC-1'], mitre_technique: 'T1' },
      { scenario_id: 'E', uc_ref: 'UC-1', tc_refs: ['TC-1'], steps: [] },
    ]
    expect(unmappedCounts(rows, readiness)).toEqual({ unmapped: 3, sourceUnknown: 1 })
  })
})
