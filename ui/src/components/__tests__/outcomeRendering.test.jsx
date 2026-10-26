/**
 * Prevention-efficacy outcome rendering: storyline normalisation, the status
 * glyph/label contract, and the severed-chain graph. The console renders what
 * the server sent; nothing here infers an outcome.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { getStoryline } from '../../api/storyline.js'
import { normalizeCausality } from '../../api/causality.js'
import DetectionStoryline from '../DetectionStoryline.jsx'
import CausalityGraph from '../CausalityGraph.jsx'

void React

function entry(step_id, idx, status, extra = {}) {
  return {
    step_id,
    step_index: idx,
    ttp_ref: 'TTP-1',
    expected_detection: { type: 'BIOC', plane: 'EDR', description: `desc ${step_id}`, detection_id: `det-${step_id}` },
    observed: null,
    status,
    mttd_seconds: null,
    evidence_refs: [],
    ...extra,
  }
}

const RAW = {
  detection_storyline: {
    run_id: 'run-p',
    scenario_id: 'SIM-EDR-001',
    run_status: 'complete',
    steps: [
      { step_id: 's1', step_index: 0, step_name: 'one', detections: [
        entry('s1', 0, 'prevented', {
          observed: { alert_id: 'alert-77', observed_at: '2026-10-26T10:00:00' },
          mttd_seconds: 4, outcome: 'prevented', outcome_reason: 'blocked by module',
          prevention_module: 'Behavioral Threat Protection', alert_action: 'BLOCKED',
        }),
        entry('s1', 0, 'prevented', { observed: { observed_at: '2026-10-26T10:00:01' }, outcome: 'prevented' }),
      ] },
      { step_id: 's2', step_index: 1, step_name: 'two', detections: [
        entry('s2', 1, 'not_reached', { outcome: 'not_reached', outcome_reason: 'chain severed at s1' }),
        entry('s2', 1, 'not_executed', { outcome: 'not_executed' }),
        entry('s2', 1, 'pending', { outcome: 'pending', outcome_reason: 'no alert window elapsed' }),
        // An engine-decided miss: only `outcome === 'missed'` is a gap (R4).
        entry('s2', 1, 'missed', { outcome: 'missed' }),
      ] },
    ],
    summary: {
      total: 6, detected: 0, prevented: 2, not_reached: 1, not_executed: 1,
      pending: 1, missed: 1, denominator: 4, coverage_pct: 50, mttd: { count: 0 },
    },
  },
}

function mockFetchOk(payload) {
  return vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => payload }))
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('normalizeStoryline (via getStoryline)', () => {
  it('keeps outcome fields, counts prevented as machine-verified only with an alert id', async () => {
    vi.stubGlobal('fetch', mockFetchOk(RAW))
    const s = await getStoryline('run-p')
    const [d0, d1] = s.steps[0].detections
    expect(d0.status).toBe('prevented')
    expect(d0.outcome_reason).toBe('blocked by module')
    expect(d0.prevention_module).toBe('Behavioral Threat Protection')
    expect(d0.alert_action).toBe('BLOCKED')
    // alert id present -> verified; absent -> attested
    expect(s.provenance.machine_verified).toBe(1)
    expect(s.provenance.attested).toBe(1)
    expect(d1.alert_external_id).toBeNull()
  })

  it('counts not_reached / not_executed / pending in neither provenance bucket', async () => {
    vi.stubGlobal('fetch', mockFetchOk(RAW))
    const s = await getStoryline('run-p')
    expect(s.provenance.machine_verified + s.provenance.attested).toBe(2)
    const statuses = s.steps[1].detections.map((d) => d.status)
    expect(statuses).toEqual(['not_reached', 'not_executed', 'pending', 'missed'])
  })

  it('passes the summary denominators through untouched', async () => {
    vi.stubGlobal('fetch', mockFetchOk(RAW))
    const s = await getStoryline('run-p')
    expect(s.coverage).toMatchObject({
      prevented: 2, not_reached: 1, not_executed: 1, denominator: 4, pct: 50,
    })
  })
})

describe('<DetectionStoryline /> outcome glyphs', () => {
  it('renders the six statuses with their glyph and label, and the pending reason', async () => {
    vi.stubGlobal('fetch', mockFetchOk(RAW))
    render(<DetectionStoryline runId="run-p" />)
    await screen.findByText('Detection Storyline')
    const pills = Array.from(document.querySelectorAll('.storyline-pill')).map((p) => p.textContent)
    expect(pills.some((t) => t.includes('🛡') && t.includes('Prevented'))).toBe(true)
    expect(pills.some((t) => t.includes('⏭') && t.includes('Not reached'))).toBe(true)
    expect(pills.some((t) => t.includes('⚠') && t.includes('Not executed'))).toBe(true)
    expect(pills.some((t) => t.includes('⏳') && t.includes('Pending'))).toBe(true)
    expect(pills.some((t) => t.includes('❌') && t.includes('Missed'))).toBe(true)
    expect(screen.getByText(/no alert window elapsed/)).toBeInTheDocument()
    expect(screen.getByText(/Behavioral Threat Protection/)).toBeInTheDocument()
  })
})

const SEVERED_GRAPH = {
  causality_graph: {
    run_id: 'run-p', scenario_id: 'SIM-EDR-001', run_status: 'complete',
    nodes: [
      { id: 'cgo:run-p', kind: 'cgo', label: 'apache2', lane: 'endpoint', stage: 'exploit' },
      { id: 'proc:s1', kind: 'process', label: 'bash', lane: 'endpoint', stage: 'exploit', step_id: 's1', control_layer: 'runtime_control' },
      { id: 'proc:s2', kind: 'process', label: 'curl', lane: 'endpoint', stage: 'exploit', step_id: 's2', status: 'not_reached' },
      {
        id: 'alert:1', kind: 'alert', label: 'shell blocked', lane: 'endpoint', stage: 'exploit',
        status: 'prevented', detection_type: 'BIOC', step_id: 's1',
        outcome: 'prevented', outcome_reason: 'blocked', prevention_module: 'BTP', alert_action: 'BLOCKED', alert_id: 'a-1',
      },
    ],
    edges: [
      { id: 'e1', source: 'cgo:run-p', target: 'proc:s1', kind: 'process_lineage', state: 'confirmed' },
      { id: 'e2', source: 'proc:s1', target: 'proc:s2', kind: 'process_lineage', state: 'SEVERED' },
      { id: 'e3', source: 'proc:s1', target: 'alert:1', kind: 'process_lineage', state: 'confirmed' },
    ],
    summary: { total: 1, detected: 0, prevented: 1, pending: 0, missed: 0, coverage_pct: 100, mttd: {}, severed_at_step: 's1' },
    causality_summary: { nodes: 4, edges: 3, stitched_incidents: 0, chain_completeness_pct: 50, broken_stitches: [], edges_by_type: {} },
  },
}

describe('normalizeCausality outcome fields', () => {
  it('carries step_id, outcome fields and summary.severed_at_step', () => {
    const g = normalizeCausality(SEVERED_GRAPH.causality_graph)
    const a = g.nodes.find((n) => n.id === 'alert:1')
    expect(a).toMatchObject({
      step_id: 's1', outcome: 'prevented', outcome_reason: 'blocked',
      prevention_module: 'BTP', alert_action: 'BLOCKED', alert_id: 'a-1',
    })
    expect(g.summary.severed_at_step).toBe('s1')
    expect(g.edges.find((e) => e.id === 'e2').state).toBe('severed')
  })
})

describe('<CausalityGraph /> severed chain', () => {
  it('draws the SEVERED edge with its own class, dims the not_reached node, marks the severed step', async () => {
    vi.stubGlobal('fetch', mockFetchOk(SEVERED_GRAPH))
    const { container } = render(<CausalityGraph runId="run-p" />)
    await screen.findByText('Causality Graph')
    expect(container.querySelector('.edge--severed')).toBeTruthy()
    expect(container.querySelector('.node--not-reached')).toBeTruthy()
    expect(screen.getAllByText(/chain severed here/i).length).toBeGreaterThan(0)
    const prevented = container.querySelector('.cg-node--prevented')
    expect(prevented.textContent).toMatch(/🛡/)
    expect(prevented.textContent).toMatch(/Prevented/)
  })
})

describe('fix round 1', () => {
  it('graph header counts prevented in the numerator over the denominator', async () => {
    const g = JSON.parse(JSON.stringify(SEVERED_GRAPH))
    g.causality_graph.summary = { total: 1, detected: 0, prevented: 1, denominator: 1, pending: 0, missed: 0, coverage_pct: 100, mttd: {} }
    vi.stubGlobal('fetch', mockFetchOk(g))
    render(<CausalityGraph runId="run-p" />)
    await screen.findByText('Causality Graph')
    expect(screen.getByText('1/1')).toBeInTheDocument()
  })

  it('a server-sent not_reached stands on a failed run', async () => {
    const raw = JSON.parse(JSON.stringify(RAW))
    raw.detection_storyline.run_status = 'failed'
    vi.stubGlobal('fetch', mockFetchOk(raw))
    render(<DetectionStoryline runId="run-p" />)
    await screen.findByText('Detection Storyline')
    const pills = Array.from(document.querySelectorAll('.storyline-pill')).map((p) => p.textContent)
    expect(pills.some((t) => t.includes('Not reached'))).toBe(true)
  })
})
