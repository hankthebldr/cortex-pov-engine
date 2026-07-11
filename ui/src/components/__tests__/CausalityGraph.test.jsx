/**
 * Tests for <CausalityGraph /> — the left-rooted Causality-View look-alike
 * (the third projection of a run's seeded-Result denominator). fetch is
 * mocked; jsdom has no EventSource so the component falls back to interval
 * polling (guarded internally).
 *
 * The fixture is the EXACT shape core/engine/causality_graph.py →
 * build_causality_graph is contracted to emit, wrapped in the
 * { causality_graph } envelope core/api/causality.py returns. getCausality()
 * normalizes this into the layout view-model the component reads — so this
 * fixture exercises the real client↔server seam.
 */
import React from 'react'
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import CausalityGraph, { layoutGraph } from '../CausalityGraph.jsx'
import { normalizeCausality } from '../../api/causality.js'

void React

// SIM-MP-007 dual-control graph: an exposure (ASM) node wired by an
// exposure_exploit edge to the runtime exploit chain, endpoint + network lanes
// joined by the hero endpoint_network_stitch edge, a broken temporal stitch.
const GRAPH_RAW = {
  causality_graph: {
    run_id: 'run-mp7',
    scenario_id: 'SIM-MP-007',
    run_status: 'complete',
    nodes: [
      { id: 'cgo:run-mp7', kind: 'cgo', label: 'Causality Group Owner', sublabel: 'cortexsim-agent' },
      {
        id: 'exp:asm', kind: 'exposure', lane: 'exposure', stage: 'stage', plane: 'ASM',
        control_layer: 'exposure', label: 'Exposed Redis :6379',
        detection_id: 'analytics-mp-007-asm-exposed-redis-surface',
        status: 'detected', detection_type: 'Analytics', result_id: 10,
      },
      { id: 'proc:s2', kind: 'process', lane: 'endpoint', stage: 'exploit', label: 'sh', sublabel: 'www-data', mitre_technique: 'T1190' },
      {
        id: 'alert:1', kind: 'alert', lane: 'endpoint', stage: 'exploit', plane: 'EDR',
        label: 'Shell from www-data', status: 'detected', detection_type: 'BIOC',
        mttd_seconds: 12, detection_id: 'bioc-mp-007-shell-from-www-data', result_id: 1,
        control_layer: 'prevention',
      },
      {
        id: 'alert:2', kind: 'alert', lane: 'endpoint', stage: 'exploit', plane: 'EDR',
        label: 'Anomalous credential access', status: 'pending', detection_type: 'ABIOC',
        detection_id: 'abioc-mp-007-cred-access-behavioral', result_id: 2, control_layer: 'prevention',
      },
      { id: 'proc:s4', kind: 'process', lane: 'endpoint', stage: 'exploit', label: 'redis-cli', sublabel: 'www-data' },
      {
        id: 'alert:3', kind: 'alert', lane: 'network', stage: 'exploit', plane: 'NDR',
        label: 'Unauth Redis access', status: 'detected', detection_type: 'XQL',
        mttd_seconds: 20, detection_id: 'xql-mp-007-ndr-unauth-redis', result_id: 3, control_layer: 'prevention',
      },
      {
        id: 'alert:4', kind: 'alert', lane: 'network', stage: 'impact', plane: 'NDR',
        label: 'C2 beacon', status: 'missed', detection_type: 'XQL',
        detection_id: 'xql-mp-007-ndr-beacon', result_id: 4, control_layer: 'prevention',
      },
      {
        id: 'alert:5', kind: 'alert', lane: 'correlation', stage: 'impact', plane: 'ANALYTICS',
        label: 'Incident spans 3 planes', status: 'pending', detection_type: 'Correlation',
        detection_id: 'CR-MP-0007', result_id: 5, control_layer: 'prevention',
      },
    ],
    edges: [
      { id: 'e1', source: 'cgo:run-mp7', target: 'proc:s2', kind: 'process_lineage', state: 'confirmed' },
      { id: 'e2', source: 'proc:s2', target: 'alert:1', kind: 'process_lineage', state: 'confirmed' },
      { id: 'e3', source: 'proc:s2', target: 'alert:2', kind: 'process_lineage', state: 'expected' },
      { id: 'e4', source: 'proc:s4', target: 'alert:3', kind: 'network_session', state: 'confirmed' },
      { id: 'e5', source: 'proc:s4', target: 'alert:3', kind: 'endpoint_network_stitch', state: 'confirmed' },
      { id: 'e6', source: 'exp:asm', target: 'proc:s4', kind: 'exposure_exploit', state: 'confirmed' },
      { id: 'e7', source: 'alert:3', target: 'alert:4', kind: 'temporal', state: 'broken' },
      { id: 'e8', source: 'proc:s2', target: 'proc:s4', kind: 'sequence', state: 'expected' },
    ],
    summary: {
      total: 5, detected: 3, pending: 1, missed: 1, coverage_pct: 60,
      mttd: { count: 3, median: 16, p90: 20 },
    },
    causality_summary: {
      nodes: 9, edges: 8, stitched_incidents: 1, chain_completeness_pct: 66,
      broken_stitches: [{ source: 'alert:3', target: 'alert:4' }],
      edges_by_type: { process_lineage: 3, network_session: 1, endpoint_network_stitch: 1, exposure_exploit: 1, temporal: 1, sequence: 1 },
    },
  },
}

function mockFetchOk(payload) {
  return vi.fn(async () => ({
    ok: true, status: 200, statusText: 'OK', json: async () => payload,
  }))
}
function mockFetchErr() {
  return vi.fn(async () => ({
    ok: false, status: 503, statusText: 'Service Unavailable',
    json: async () => ({ detail: { error: 'engine module absent', code: 'CAUSALITY_ENGINE_UNAVAILABLE' } }),
  }))
}

describe('normalizeCausality + layoutGraph', () => {
  it('normalizes the envelope shape and infers lane/stage defaults', () => {
    const g = normalizeCausality(GRAPH_RAW.causality_graph)
    expect(g.run_id).toBe('run-mp7')
    expect(g.nodes).toHaveLength(9)
    expect(g.edges).toHaveLength(8)
    // exposure node keeps its explicit lane/stage
    const exp = g.nodes.find((n) => n.id === 'exp:asm')
    expect(exp.lane).toBe('exposure')
    expect(exp.stage).toBe('stage')
    // causality summary threaded through
    expect(g.causality.chain_completeness_pct).toBe(66)
    expect(g.causality.broken_stitches).toHaveLength(1)
  })

  it('lays nodes onto a deterministic grid with a spanning CGO and present lanes', () => {
    const g = normalizeCausality(GRAPH_RAW.causality_graph)
    const layout = layoutGraph(g)
    // Endpoint, network, exposure and correlation lanes are all present.
    expect(layout.lanes).toEqual(expect.arrayContaining(['exposure', 'endpoint', 'network', 'correlation']))
    // Every node got a numeric position and the id map resolves edges.
    layout.nodes.forEach((n) => {
      expect(Number.isFinite(n.cx)).toBe(true)
      expect(Number.isFinite(n.cy)).toBe(true)
    })
    expect(layout.byId.get('proc:s2')).toBeDefined()
    // The CGO sits at column 0, left of the exploit-stage process nodes.
    const cgo = layout.byId.get('cgo:run-mp7')
    const proc = layout.byId.get('proc:s2')
    expect(cgo.col).toBe(0)
    expect(cgo.cx).toBeLessThan(proc.cx)
  })
})

describe('<CausalityGraph />', () => {
  beforeEach(() => { vi.useRealTimers() })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders an empty state when no runId is supplied', () => {
    render(<CausalityGraph runId={null} />)
    expect(screen.getByText(/No run selected/i)).toBeInTheDocument()
  })

  it('fetches the graph and renders the header KPIs', async () => {
    vi.stubGlobal('fetch', mockFetchOk(GRAPH_RAW))
    render(<CausalityGraph runId="run-mp7" scenarioId="SIM-MP-007" />)

    expect(await screen.findByText('Causality Graph')).toBeInTheDocument()
    // Coverage KPI (delegated to build_summary server-side)
    expect(screen.getByText('60%')).toBeInTheDocument()
    expect(screen.getByText('3/5')).toBeInTheDocument()
    // Chain completeness — the causality-native KPI
    expect(screen.getByText('66%')).toBeInTheDocument()
  })

  it('renders nodes and colours alert nodes by status', async () => {
    vi.stubGlobal('fetch', mockFetchOk(GRAPH_RAW))
    const { container } = render(<CausalityGraph runId="run-mp7" />)

    expect(await screen.findByText('Exposed Redis :6379')).toBeInTheDocument()
    expect(screen.getByText('Shell from www-data')).toBeInTheDocument()
    expect(screen.getByText('Anomalous credential access')).toBeInTheDocument()
    // The flagship ABIOC engine badge is surfaced on its alert node.
    expect(screen.getByText('ABIOC')).toBeInTheDocument()
    // A detected node, a pending node and a missed node all render.
    expect(container.querySelector('.cg-node--detected')).toBeTruthy()
    expect(container.querySelector('.cg-node--pending')).toBeTruthy()
    expect(container.querySelector('.cg-node--missed')).toBeTruthy()
  })

  it('draws every edge and highlights the exposure→exploit + stitch critical path', async () => {
    vi.stubGlobal('fetch', mockFetchOk(GRAPH_RAW))
    const { container } = render(<CausalityGraph runId="run-mp7" />)

    await screen.findByText('Causality Graph')
    const edges = container.querySelectorAll('[data-testid="cg-edge"]')
    expect(edges.length).toBe(8)
    // exposure_exploit + endpoint_network_stitch are the two hero edges.
    expect(container.querySelectorAll('.cg-edge--hero').length).toBe(2)
    // The broken temporal stitch is rendered in its broken state.
    expect(container.querySelector('.cg-edge--broken')).toBeTruthy()
    // Hero edge is labelled with its kind for the presenter.
    expect(screen.getByText('exposure→exploit')).toBeInTheDocument()
  })

  it('fires onOpenEvidence when an alert node is activated', async () => {
    vi.stubGlobal('fetch', mockFetchOk(GRAPH_RAW))
    const onOpenEvidence = vi.fn()
    render(<CausalityGraph runId="run-mp7" onOpenEvidence={onOpenEvidence} />)

    const node = await screen.findByText('Shell from www-data')
    // Click the node chip (button role on alert nodes).
    fireEvent.click(node.closest('[data-testid="cg-node"]'))
    expect(onOpenEvidence).toHaveBeenCalledTimes(1)
    expect(onOpenEvidence.mock.calls[0][0].detection_id).toBe('bioc-mp-007-shell-from-www-data')
  })

  it('surfaces an error state and calls onError when the fetch fails', async () => {
    vi.stubGlobal('fetch', mockFetchErr())
    const onError = vi.fn()
    render(<CausalityGraph runId="run-mp7" onError={onError} />)

    expect(await screen.findByText(/Could not load causality graph/i)).toBeInTheDocument()
    await waitFor(() => expect(onError).toHaveBeenCalled())
    expect(onError.mock.calls[0][0]).toMatch(/engine module absent/)
  })
})
