import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getCausality, causalityEventsUrl } from '../api/causality.js'
import './CausalityGraph.css'

/**
 * CausalityGraph — left-rooted Cortex Causality-View look-alike.
 *
 * The THIRD projection of a run's seeded-Result denominator (peer to the
 * linear DetectionStoryline and the efficacy scorecard). Renders the attack as
 * a graph rather than a checklist: a synthetic Causality-Group-Owner (CGO)
 * root spines out through identity-harness process nodes to the alert nodes
 * each step's expected detection promotes verbatim, with the seven typed edge
 * kinds coloured by their EXPECTED → CONFIRMED | BROKEN state.
 *
 * Two lanes carry the hero cross-plane proof — ENDPOINT on top, NETWORK on the
 * bottom, joined by the `endpoint_network_stitch` edge only XSIAM can draw.
 * Exposure (ASM/CSPM/AI-SPM posture findings) form a left rail wired to the
 * runtime exploit nodes by the dotted `exposure_exploit` edge — the
 * code-to-cloud dual-control story the linear storyline structurally cannot.
 * A STAGE · EXPLOIT · IMPACT backdrop runs left→right behind the lanes.
 *
 * Live-updating: polls GET /api/runs/:id/causality every ~2s and (where the
 * browser supports it) folds the /api/runs/:id/events SSE stream so a
 * reconcile re-derives the graph immediately.
 *
 * Pure SVG — one <svg> with <foreignObject> node chips + <path> edges. No new
 * deps.
 *
 * Props:
 *   runId          — string (required)
 *   scenarioId     — string (optional; header label only)
 *   pollIntervalMs — number (default 2000)
 *   onOpenEvidence — (node) => void  fired when an alert node is activated
 *   onError        — (message: string) => void
 */

// ── Layout constants ────────────────────────────────────────────────────────
const COL_W = 236
const NODE_W = 168
const NODE_H = 66
const V_GAP = 14
const LANE_MIN_H = 132
const PAD_LEFT = 128 // room for lane labels
const PAD_TOP = 56 // room for the STAGE/EXPLOIT/IMPACT backdrop caption
const PAD_RIGHT = 48
const PAD_BOTTOM = 24

const STAGE_COL = { stage: 1, exploit: 2, impact: 3 }
const STAGE_LABEL = { stage: 'STAGE', exploit: 'EXPLOIT', impact: 'IMPACT' }

const LANE_ORDER = ['exposure', 'endpoint', 'identity', 'cloud', 'network', 'correlation']
const LANE_LABEL = {
  exposure: 'EXPOSURE',
  endpoint: 'ENDPOINT',
  identity: 'IDENTITY',
  cloud: 'CLOUD',
  network: 'NETWORK',
  correlation: 'CORRELATION',
}

// Edges the whole platform argument hangs on — drawn thicker + labelled.
const HERO_EDGE_KINDS = new Set(['endpoint_network_stitch', 'exposure_exploit'])

const EDGE_LABEL = {
  process_lineage: 'lineage',
  network_session: 'session',
  endpoint_network_stitch: 'stitch',
  temporal: 'temporal',
  shared_entity: 'entity',
  sequence: 'seq',
  exposure_exploit: 'exposure→exploit',
}

// ── Formatting helpers ──────────────────────────────────────────────────────
function formatMttd(seconds) {
  if (seconds == null) return null
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function engineLabel(type) {
  const t = (type || '').toLowerCase().trim()
  const labels = {
    bioc: 'BIOC', xql: 'XQL', analytics: 'Analytics',
    correlation: 'Correlation', ioc: 'IOC', abioc: 'ABIOC',
  }
  return labels[t] || (type || '').toString().toUpperCase() || ''
}

function nodeCol(n) {
  if (n.kind === 'cgo') return 0
  if (n.column != null) return n.column
  return STAGE_COL[n.stage] || 2
}

// ── Pure layout — deterministic (col, lane, slot) → pixel grid ──────────────
// Exported for unit testing; consumes the normalized graph, returns positioned
// nodes + an id→node map + the SVG canvas size.
export function layoutGraph(graph) {
  const nodes = graph.nodes || []
  const edges = graph.edges || []

  // Which lanes are actually present (a cgo node spans lanes, excluded here).
  const present = new Set()
  nodes.forEach((n) => { if (n.kind !== 'cgo') present.add(n.lane) })
  const lanes = LANE_ORDER.filter((l) => present.has(l))
  if (lanes.length === 0) lanes.push('endpoint')
  const laneIndex = new Map(lanes.map((l, i) => [l, i]))

  // Slot assignment within each (lane, col) bucket so co-located nodes stack.
  const bucketSlots = new Map() // key `${lane}:${col}` -> next slot
  const laneMaxSlots = new Map() // lane -> max slot count
  let maxCol = 0

  const placed = nodes.map((n) => {
    const col = nodeCol(n)
    maxCol = Math.max(maxCol, col)
    if (n.kind === 'cgo') return { node: n, col, lane: null, slot: 0 }
    const key = `${n.lane}:${col}`
    const slot = bucketSlots.get(key) || 0
    bucketSlots.set(key, slot + 1)
    const prevMax = laneMaxSlots.get(n.lane) || 0
    laneMaxSlots.set(n.lane, Math.max(prevMax, slot + 1))
    return { node: n, col, lane: n.lane, slot }
  })

  // Per-lane heights grow to fit the tallest stacked bucket.
  const laneHeight = lanes.map((l) =>
    Math.max(LANE_MIN_H, (laneMaxSlots.get(l) || 1) * (NODE_H + V_GAP) + V_GAP),
  )
  const laneTop = []
  let acc = PAD_TOP
  lanes.forEach((l, i) => { laneTop[i] = acc; acc += laneHeight[i] })
  const contentBottom = acc

  const positioned = placed.map(({ node, col, lane, slot }) => {
    const x = PAD_LEFT + col * COL_W
    let y
    if (node.kind === 'cgo') {
      // Vertically centre the CGO across the whole lane stack.
      y = PAD_TOP + (contentBottom - PAD_TOP) / 2 - NODE_H / 2
    } else {
      const li = laneIndex.get(lane) ?? 0
      y = laneTop[li] + V_GAP + slot * (NODE_H + V_GAP)
    }
    return {
      ...node,
      x, y, col,
      cx: x + NODE_W / 2,
      cy: y + NODE_H / 2,
    }
  })

  const byId = new Map(positioned.map((n) => [n.id, n]))
  const width = PAD_LEFT + (maxCol + 1) * COL_W + PAD_RIGHT - (COL_W - NODE_W)
  const height = contentBottom + PAD_BOTTOM

  // Stage backdrop columns actually used (1..3), for the STAGE/EXPLOIT/IMPACT rects.
  const usedStageCols = [1, 2, 3].filter((c) => c <= maxCol)

  return {
    nodes: positioned,
    edges,
    byId,
    lanes,
    laneTop,
    laneHeight,
    width,
    height,
    usedStageCols,
    contentBottom,
  }
}

function edgePath(a, b) {
  const dx = Math.max(38, Math.abs(b.cx - a.cx) * 0.42)
  return `M ${a.cx} ${a.cy} C ${a.cx + dx} ${a.cy}, ${b.cx - dx} ${b.cy}, ${b.cx} ${b.cy}`
}

// ── Node chip (HTML inside <foreignObject>) ─────────────────────────────────
function NodeChip({ node, onOpenEvidence, flashing }) {
  const clickable = node.kind === 'alert' && !!onOpenEvidence
  const engine = engineLabel(node.detection_type)
  const mttd = formatMttd(node.mttd_seconds)
  const status = node.status || (node.kind === 'alert' ? 'pending' : null)

  const cls =
    'cg-node' +
    ` cg-node--${node.kind}` +
    (status ? ` cg-node--${status}` : '') +
    (clickable ? ' cg-node--clickable' : '') +
    (flashing ? ' cg-node--flash' : '')

  const activate = () => {
    if (clickable) onOpenEvidence(node)
  }

  return (
    <div
      className={cls}
      data-testid="cg-node"
      data-kind={node.kind}
      data-status={status || ''}
      role={clickable ? 'button' : 'group'}
      tabIndex={clickable ? 0 : undefined}
      onClick={activate}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                activate()
              }
            }
          : undefined
      }
      title={node.detection_id || node.label}
    >
      <div className="cg-node__top">
        {node.plane && <span className="cg-node__plane">{node.plane}</span>}
        {engine && <span className="cg-node__engine">{engine}</span>}
        {node.kind === 'cgo' && <span className="cg-node__engine cg-node__engine--cgo">CGO</span>}
        {node.kind === 'process' && <span className="cg-node__engine cg-node__engine--proc">proc</span>}
        {node.kind === 'exposure' && <span className="cg-node__engine cg-node__engine--exp">exposure</span>}
      </div>
      <div className="cg-node__label">{node.label}</div>
      <div className="cg-node__foot">
        {node.sublabel && <span className="cg-node__sub text-mono">{node.sublabel}</span>}
        {mttd && status === 'detected' && (
          <span className="cg-node__mttd">MTTD {mttd}</span>
        )}
      </div>
    </div>
  )
}

// ── KPI header ──────────────────────────────────────────────────────────────
function GraphHeader({ graph, live, lastUpdated }) {
  const summary = graph.summary || {}
  const causality = graph.causality || {}
  const pct = summary.coverage_pct != null ? Math.round(summary.coverage_pct) : 0
  const chain =
    causality.chain_completeness_pct != null
      ? Math.round(causality.chain_completeness_pct)
      : null
  const broken = (causality.broken_stitches || []).length

  return (
    <header className="cg-header">
      <div className="cg-header__title">
        <h3>Causality Graph</h3>
        <span className="cg-header__scenario text-mono">
          {graph.scenario_id || graph.run_id}
        </span>
        {live && (
          <span className="cg-live" aria-live="polite">
            <span className="cg-live__dot" aria-hidden="true" />
            LIVE
          </span>
        )}
      </div>
      <div className="cg-kpis">
        <div className="cg-kpi">
          <span className="cg-kpi__label">Coverage</span>
          <span className="cg-kpi__value">
            {pct}%
            <span className="cg-kpi__sub">
              {summary.detected || 0}/{summary.total || 0}
            </span>
          </span>
        </div>
        <div className="cg-kpi">
          <span className="cg-kpi__label">Chain completeness</span>
          <span className="cg-kpi__value">{chain != null ? `${chain}%` : '—'}</span>
        </div>
        <div className="cg-kpi">
          <span className="cg-kpi__label">Stitched incidents</span>
          <span className="cg-kpi__value">{causality.stitched_incidents || 0}</span>
        </div>
        <div className={'cg-kpi' + (broken ? ' cg-kpi--warn' : '')}>
          <span className="cg-kpi__label">Broken stitches</span>
          <span className="cg-kpi__value">{broken}</span>
        </div>
      </div>
      {lastUpdated && (
        <div className="cg-header__updated text-mono">updated {lastUpdated}</div>
      )}
    </header>
  )
}

function Legend() {
  return (
    <div className="cg-legend" aria-hidden="true">
      <span className="cg-legend__item"><i className="cg-legend__swatch cg-legend__swatch--confirmed" />Confirmed</span>
      <span className="cg-legend__item"><i className="cg-legend__swatch cg-legend__swatch--expected" />Expected</span>
      <span className="cg-legend__item"><i className="cg-legend__swatch cg-legend__swatch--broken" />Broken stitch</span>
      <span className="cg-legend__item cg-legend__item--hero"><i className="cg-legend__swatch cg-legend__swatch--hero" />Critical path</span>
    </div>
  )
}

// ── Main component ──────────────────────────────────────────────────────────
export default function CausalityGraph({
  runId,
  scenarioId,
  pollIntervalMs = 2000,
  onOpenEvidence,
  onError,
}) {
  const [graph, setGraph] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [live, setLive] = useState(false)
  const [lastUpdated, setLastUpdated] = useState(null)

  const [flashing, setFlashing] = useState(() => new Set())
  const prevStatuses = useRef(new Map())
  const flashTimers = useRef([])

  const applyGraph = useCallback((next) => {
    if (!next) return
    // Diff alert-node statuses to flash fresh catches.
    const fresh = []
    const nextStatuses = new Map()
    for (const n of next.nodes || []) {
      if (n.kind !== 'alert') continue
      const status = n.status || 'pending'
      nextStatuses.set(n.id, status)
      const before = prevStatuses.current.get(n.id)
      if (status === 'detected' && before && before !== 'detected') fresh.push(n.id)
    }
    prevStatuses.current = nextStatuses
    if (fresh.length) {
      setFlashing((prev) => {
        const merged = new Set(prev)
        fresh.forEach((k) => merged.add(k))
        return merged
      })
      const t = setTimeout(() => {
        setFlashing((prev) => {
          const merged = new Set(prev)
          fresh.forEach((k) => merged.delete(k))
          return merged
        })
      }, 1800)
      flashTimers.current.push(t)
    }
    setGraph(next)
    try {
      setLastUpdated(new Date().toLocaleTimeString(undefined, {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      }))
    } catch {
      setLastUpdated(new Date().toISOString())
    }
  }, [])

  useEffect(() => {
    if (!runId) {
      setGraph(null)
      return undefined
    }
    let cancelled = false
    let pollTimer = null
    let eventSource = null

    const fetchOnce = async () => {
      try {
        const data = await getCausality(runId)
        if (cancelled) return
        applyGraph(data)
        setError(null)
      } catch (err) {
        if (cancelled) return
        const msg = err.message || String(err)
        setError(msg)
        if (onError) onError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    setLoading(true)
    fetchOnce()
    pollTimer = setInterval(fetchOnce, pollIntervalMs)

    if (typeof EventSource !== 'undefined') {
      try {
        eventSource = new EventSource(causalityEventsUrl(runId))
        eventSource.onopen = () => { if (!cancelled) setLive(true) }
        eventSource.onmessage = () => { if (!cancelled) fetchOnce() }
        eventSource.onerror = () => {
          if (!cancelled) setLive(false)
          if (eventSource) { eventSource.close(); eventSource = null }
        }
      } catch {
        /* fall back to polling only */
      }
    }

    return () => {
      cancelled = true
      if (pollTimer) clearInterval(pollTimer)
      if (eventSource) eventSource.close()
      flashTimers.current.forEach(clearTimeout)
      flashTimers.current = []
      prevStatuses.current = new Map()
    }
  }, [runId, pollIntervalMs, applyGraph, onError])

  const layout = useMemo(() => (graph ? layoutGraph(graph) : null), [graph])

  // ── Render states ─────────────────────────────────────────────────────────
  if (!runId) {
    return (
      <div className="cg cg--empty">
        <div className="cg-empty__icon" aria-hidden="true">⤳</div>
        <p>No run selected. Launch a scenario to build its Causality Graph.</p>
      </div>
    )
  }
  if (loading && !graph) {
    return (
      <div className="cg cg--loading">
        <span className="spinner" />
        <span className="text-muted">Assembling causality graph…</span>
      </div>
    )
  }
  if (error && !graph) {
    return (
      <div className="cg cg--error">
        <p className="cg-error__msg">Could not load causality graph: {error}</p>
      </div>
    )
  }
  if (!graph || !layout) return null

  const { nodes, edges, byId, lanes, laneTop, laneHeight, width, height, usedStageCols } = layout

  return (
    <section className="cg" aria-label="Causality graph">
      <GraphHeader graph={graph} live={live} lastUpdated={lastUpdated} />
      <Legend />

      {nodes.length === 0 ? (
        <div className="cg--empty">
          <p>This run has no causality nodes to graph yet.</p>
        </div>
      ) : (
        <div className="cg-canvas" role="img" aria-label="Attack causality graph">
          <svg
            className="cg-svg"
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            data-testid="cg-svg"
          >
            <defs>
              {['confirmed', 'expected', 'broken', 'hero'].map((state) => (
                <marker
                  key={state}
                  id={`cg-arrow-${state}`}
                  className={`cg-arrow cg-arrow--${state}`}
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M0,0 L8,4 L0,8 z" />
                </marker>
              ))}
            </defs>

            {/* STAGE · EXPLOIT · IMPACT backdrop */}
            {usedStageCols.map((c) => {
              const x = PAD_LEFT + c * COL_W - (COL_W - NODE_W) / 2 - 18
              const stage = Object.keys(STAGE_COL).find((k) => STAGE_COL[k] === c)
              return (
                <g key={`stage-${c}`} className={`cg-stage cg-stage--${stage}`}>
                  <rect
                    className="cg-stage__band"
                    x={x}
                    y={PAD_TOP - 8}
                    width={NODE_W + 36}
                    height={height - PAD_TOP - PAD_BOTTOM + 8}
                    rx="10"
                  />
                  <text className="cg-stage__label" x={x + (NODE_W + 36) / 2} y={PAD_TOP - 18}>
                    {STAGE_LABEL[stage]}
                  </text>
                </g>
              )
            })}

            {/* Lane guides + labels */}
            {lanes.map((lane, i) => (
              <g key={`lane-${lane}`} className={`cg-lane cg-lane--${lane}`}>
                <line
                  className="cg-lane__rule"
                  x1={PAD_LEFT - 24}
                  y1={laneTop[i] + laneHeight[i]}
                  x2={width - 8}
                  y2={laneTop[i] + laneHeight[i]}
                />
                <text
                  className="cg-lane__label"
                  x={12}
                  y={laneTop[i] + laneHeight[i] / 2}
                >
                  {LANE_LABEL[lane] || lane.toUpperCase()}
                </text>
              </g>
            ))}

            {/* Edges */}
            <g className="cg-edges">
              {edges.map((e) => {
                const a = byId.get(e.source)
                const b = byId.get(e.target)
                if (!a || !b) return null
                const hero = HERO_EDGE_KINDS.has(e.kind)
                const markerState = hero ? 'hero' : e.state
                const cls =
                  'cg-edge' +
                  ` cg-edge--${e.state}` +
                  ` cg-edge--kind-${e.kind}` +
                  (hero ? ' cg-edge--hero' : '')
                const midX = (a.cx + b.cx) / 2
                const midY = (a.cy + b.cy) / 2 - 6
                const label = e.label || EDGE_LABEL[e.kind] || e.kind
                return (
                  <g key={e.id} data-testid="cg-edge" data-kind={e.kind} data-state={e.state}>
                    <path
                      className={cls}
                      d={edgePath(a, b)}
                      markerEnd={`url(#cg-arrow-${markerState})`}
                    />
                    {(hero || e.state === 'broken') && (
                      <text className={`cg-edge__label cg-edge__label--${e.state}`} x={midX} y={midY}>
                        {label}
                      </text>
                    )}
                  </g>
                )
              })}
            </g>

            {/* Nodes */}
            <g className="cg-nodes">
              {nodes.map((n) => (
                <foreignObject
                  key={n.id}
                  x={n.x}
                  y={n.y}
                  width={NODE_W}
                  height={NODE_H}
                  className="cg-node-fo"
                >
                  <NodeChip
                    node={n}
                    onOpenEvidence={onOpenEvidence}
                    flashing={flashing.has(n.id)}
                  />
                </foreignObject>
              ))}
            </g>
          </svg>
        </div>
      )}
    </section>
  )
}
