/**
 * ComposerCanvas — the full-screen node graph at the heart of Composer MODE.
 *
 * It owns the canvas: the chrome bar (lens + stitch + view + zoom), the
 * spine-constrained graph, the YAML projection, the first-run empty state and
 * the origin-error banner. It is a VIEW — it never fetches and never mutates the
 * draft; structure edits, selection and view changes all arrive as callbacks.
 *
 * THE NODE GRAPH (rewritten for Composer mode)
 * --------------------------------------------
 * Unlike the windowed Composer (START/END were DOM cards outside React Flow and
 * only steps were nodes, laid out top-to-bottom in a 620px box), every piece is
 * now a real React Flow node, laid out LEFT-TO-RIGHT like the node-automation
 * tools this is modelled on:
 *   - a `cgo` TRIGGER node (the Causality Group Owner) that owns the chain,
 *   - one `step` node per draft step,
 *   - a `proof` TERMINAL node (teardown + the POV readout).
 * Edges are typed: a solid `process_lineage` spine, dashed labelled edges for
 * the six non-lineage pivots, and muted derived edges from the CGO to each root
 * and from each leaf to Proof. The graph builder (`composerGraph.buildGraph`) is
 * pure and tested; this file renders it and wires the gestures.
 *
 * GESTURES
 *   - drag a card by its grip → `onNodeMoved` on drop (8px grid, persisted),
 *   - drag from a card's right port to another card → `onConnectSteps` (a new
 *     `process_lineage` edge), refused with a visible reason by `canConnect`,
 *   - drop that connection on empty canvas → `onDropCreate` (n8n create-on-drop),
 *   - drag a palette item onto the canvas → `onCanvasDrop(key, position)`,
 *   - Delete/Backspace a selected step → `onDeleteStepId` (children re-parent
 *     onto its parent — the reparent-on-delete rule that fixes the old
 *     dangling-ref bug), a causality edge → `onEdgeDelete` (child becomes a root),
 *   - reconnect a causality edge's target end → `onReparent`,
 *   - the pivot chip on an edge → `onSetPivot`.
 *
 * TWO OTHER LENSES, ONE HONESTY RULE
 *   - Lanes: swimlanes by ingestion door (`composerLanes.layoutLanes`), drag to
 *     retarget. Steps only; the LAUNCH/PROOF bands stand in for the anchors.
 *   - Run: the REAL `build_causality_graph` (`RunGraph`) — EXPECTED | CONFIRMED |
 *     BROKEN straight through, never synthesized — over a read-only spine whose
 *     cards carry each step's real run state. With no run it says so.
 */
import React, {
  useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, forwardRef,
} from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, BackgroundVariant, Controls, ControlButton, MiniMap,
  Handle, Position, ViewportPortal, BaseEdge, EdgeLabelRenderer,
  getBezierPath, MarkerType, applyNodeChanges, applyEdgeChanges,
  useNodesInitialized, useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { layoutCausalityGraph } from './composerLayout.js'
import { effectiveChannel, PIVOTS } from './composerDraft.js'
import { plantedKeys, stitchPlaceholdersIn } from './stitchContext.js'
import { canConnect } from './composerSpine.js'
import { laneOf, layoutLanes, laneAtY, STEP_LANES } from './composerLanes.js'
import { COMPOSER_LANES } from './povdata/corpus.js'
import {
  buildGraph, SNAP, PLANE_TINT, STATE_TINT, CGO_ID, PROOF_ID, GRAPH_LAYOUT,
} from './composerGraph.js'

// Detection-type → chip tone. Identical mapping to the inspector's `detTone`.
function detTone(type) {
  const t = String(type || '').toUpperCase()
  if (t === 'BIOC') return 'detected'
  if (t === 'XQL' || t === 'ANALYTICS') return 'signal'
  return 'pending'
}

// Run-lens node kind → colour (real causality graph; token-driven).
const KIND_TINT = {
  cgo: 'var(--ink, #003366)', process: 'var(--ac, #00C0E8)',
  wrapper: 'var(--cortex-steel, #6B7E8E)',
  exposure: 'var(--cortex-warning, #f59f00)',
  alert: 'var(--cortex-success, #2f9e44)',
}

/** Lane key → the band label the canvas prints ('AGT' → 'ENDPOINT'). */
const LANE_LABEL = Object.fromEntries(COMPOSER_LANES.map(([key, label]) => [key, label]))

/** Curved connector between two resolved ports (Run-lens SVG only). */
function edgePath(from, to) {
  if (!from || !to) return ''
  const dx = Math.max(30, Math.abs(to.x - from.x) / 2)
  return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y} ${to.x - dx} ${to.y} ${to.x} ${to.y}`
}

// ─── Node components ────────────────────────────────────────────────────────

/**
 * The CGO trigger node — what OWNS the chain. Styled like an automation tool's
 * trigger (the chain starts here). One source port on the right. Not a step, so
 * it keeps the `chain-start` testid the suite pins, moved onto this real node.
 */
function CgoNode({ data }) {
  return (
    <div className="chain-node chain-node--cgo" data-testid="chain-start">
      <div className="chain-node__kicker">Trigger · CGO</div>
      <div className="chain-node__title mono">{data.cgo || 'cortexsim-agent'}</div>
      <div className="chain-node__scope">
        <button type="button" className="scope-link" onClick={data.onTenant}>
          <span className="scope-link__label">Tenant</span>
          <span className="scope-link__value mono">{data.tenantName || 'none selected'}</span>
        </button>
        <button type="button" className="scope-link" onClick={data.onAgent}>
          <span className="scope-link__label">Agent</span>
          <span className="scope-link__value mono">{data.agentName || 'none selected'}</span>
        </button>
      </div>
      {!data.runLens && <Handle type="source" id="out" position={Position.Right} isConnectable={false} />}
    </div>
  )
}

/**
 * One STEP. The card body, badges, detection chips and tool buttons are the same
 * DOM the suite pins (`chain-step-{id}`, `chain-node-grip-{id}`, the door/channel/
 * target badges, the move/duplicate/remove aria-labels) — what changed from the
 * windowed canvas is the flow direction: the target port is on the LEFT and the
 * source on the RIGHT, matching the left-to-right layout. `Handle`s mount only
 * when connectable (Run lens renders none, so it reads strictly read-only).
 */
function StepNode({ data, isConnectable }) {
  const s = data.step
  return (
    <div
      className={
        'chain-node chain-node--step'
        + (data.selected ? ' chain-node--selected' : '')
        + (s.detections.length ? '' : ' chain-node--nodetect')
      }
      style={{ borderLeft: `3px solid ${data.tint}` }}
    >
      {isConnectable && <Handle type="target" id="in" position={Position.Left} />}
      {!data.runLens && (
        <div
          className="chain-node__grip"
          title="Drag to reposition"
          aria-label="Drag to reposition"
          data-testid={`chain-node-grip-${s.id}`}
        >
          ⠿
        </div>
      )}
      <button
        type="button"
        className="chain-node__body nodrag nopan"
        onClick={data.onSelectStep}
        aria-pressed={data.selected}
        data-testid={`chain-step-${s.id}`}
      >
        <span className="chain-node__row">
          <span className="chain-node__kind">{s.authored ? 'new' : 'step'}</span>
          <span className="chain-node__id mono">{s.id}</span>
          {data.door && (
            <span
              className="pov-chip-plane chain-node__doorbadge"
              /* `chain-door-` NOT `chain-step-door-`: the door badge now shows on
                 every lens, and several tests scan `[data-testid^="chain-step-"]`
                 to pin step-card DOM order — reusing that prefix would make a
                 badge read as a spurious extra "step". */
              data-testid={`chain-door-${s.id}`}
              title={`launches through ${LANE_LABEL[data.door] || data.door} — drag into another lane to retarget`}
            >
              {data.door}
            </span>
          )}
          {data.channelKind === 'eal' ? (
            <span
              className="chip chip--signal chain-node__chanbadge"
              data-testid={`chain-step-channel-${s.id}`}
              title={`channel: eal · emitter ${s.eal?.plugin || '(unset)'}`}
            >
              EAL
            </span>
          ) : data.channelKind === 'target' ? (
            <span
              className="chip chip--pending chain-node__chanbadge"
              data-testid={`chain-step-target-${s.id}`}
              title={`runs on second endpoint ${s.target}`}
            >
              {`→ ${s.target}`}
            </span>
          ) : null}
          <span className="composer__spacer" />
          {data.offChain && (
            <span
              className="chip chip--missed chain-node__offchain"
              data-testid={`chain-offchain-${s.id}`}
              title="Not connected to the chain — drag from a step's right port to this card, or the draft will not save"
            >
              off-chain
            </span>
          )}
          {data.runState && (
            <span
              className="chain-node__runbadge"
              style={{
                fontSize: 10, fontWeight: 700,
                color: STATE_TINT[data.runState], letterSpacing: '0.04em',
              }}
            >
              {data.runState}
            </span>
          )}
          <span className="chain-node__order mono">
            {String(data.order).padStart(2, '0')}
          </span>
        </span>
        <span className="chain-node__name">{s.name}</span>
        <span className="chain-node__sub mono">
          {s.technique || 'no technique'} · {s.identity || 'no identity'}
        </span>
        <span className="chain-node__chips">
          {s.detections.length ? (
            s.detections.map((d, k) => (
              <span key={k} className={`chip chip--${detTone(d.type)}`}>
                {d.type || '?'}
              </span>
            ))
          ) : (
            <span className="chip chip--missed">no expected detection</span>
          )}
        </span>
      </button>
      {!data.runLens && (
        <div className="chain-node__tools nodrag nopan">
          <button type="button" title="Move earlier" aria-label={`Move ${s.id} earlier`}
            onClick={data.onMoveEarlier}>↑</button>
          <button type="button" title="Move later" aria-label={`Move ${s.id} later`}
            onClick={data.onMoveLater}>↓</button>
          <button type="button" title="Duplicate step" aria-label={`Duplicate ${s.id}`}
            onClick={data.onDuplicate}>⧉</button>
          <button type="button" title="Remove step" aria-label={`Remove ${s.id}`}
            onClick={data.onRemove}>×</button>
        </div>
      )}
      {isConnectable && <Handle type="source" id="out" position={Position.Right} />}
    </div>
  )
}

/**
 * The Proof terminal node — where a run is measured. One target port on the
 * left. Keeps the `chain-end` testid, moved onto this real node.
 */
function ProofNode({ data }) {
  const n = data.teardown?.length || 0
  return (
    <div className="chain-node chain-node--proof" data-testid="chain-end">
      {!data.runLens && <Handle type="target" id="in" position={Position.Left} isConnectable={false} />}
      <div className="chain-node__kicker">Proof · teardown</div>
      <div className="chain-node__title">Teardown &amp; proof</div>
      <div className="chain-node__sub mono">
        {n ? `${n} cleanup command${n === 1 ? '' : 's'}` : 'no cleanup declared'}
      </div>
    </div>
  )
}

const nodeTypes = { cgo: CgoNode, step: StepNode, proof: ProofNode }

// ─── Edge components ────────────────────────────────────────────────────────

/**
 * A causality edge — the one PERSISTED relationship. Renders the pivot as an
 * interactive chip (click to cycle through the seven pivots) plus a delete
 * button (child becomes a root). Both live in an `EdgeLabelRenderer` portal and
 * are `nodrag nopan` with `pointer-events:all` so they stay clickable over the
 * pane. A derived edge (cgo→root, leaf→proof) is muted and inert.
 */
function CausalityEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  markerEnd, style, data, selected,
}) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })
  const pivot = data?.pivot || 'process_lineage'
  const showChip = selected || pivot !== 'process_lineage'
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={24} />
      <EdgeLabelRenderer>
        <div
          className={'composer-edge-label nodrag nopan' + (selected ? ' composer-edge-label--on' : '')}
          style={{ transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}
        >
          {showChip && (
            <button
              type="button"
              className="composer-edge-pivot"
              title={`pivot: ${pivot} — click to change`}
              aria-label={`Pivot for edge ${id}`}
              onClick={() => data?.onCyclePivot?.()}
            >
              {pivot}
            </button>
          )}
          {!data?.runLens && selected && (
            <button
              type="button"
              className="composer-edge-del"
              title="Delete this link (the child becomes a root)"
              aria-label={`Delete edge ${id}`}
              onClick={() => data?.onDelete?.()}
            >
              ×
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

function DerivedEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data,
}) {
  const [path] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })
  // A trigger edge into an OFF-CHAIN step is drawn as a warning: the loader
  // allows one root, so this link is a statement the draft cannot save.
  const off = !!data?.offChain
  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      style={{
        stroke: off ? 'var(--warn-str, #FFCB06)' : 'var(--bd2, #4A4A4A)',
        strokeWidth: off ? 1.5 : 1.25,
        strokeDasharray: off ? '6 4' : '2 5',
      }}
    />
  )
}

const edgeTypes = { causality: CausalityEdge, derived: DerivedEdge }

/**
 * Fit the viewport once per `fitKey`, and only after React Flow has MEASURED
 * every node. The scenario arrives asynchronously, so the pane mounts and
 * reports a size before any card has dimensions; a fit issued then is a no-op
 * and the graph sat at scale(1) in the corner on some loads (observed live).
 * Rendered as a child of <ReactFlow> so it can read the store. Keyed by lens,
 * so adding or dropping a node never yanks the viewport the DC has panned.
 */
function FitOnReady({ fitKey, onReady }) {
  const ready = useNodesInitialized()
  const rf = useReactFlow()
  const done = useRef(null)
  useEffect(() => {
    if (!ready || done.current === fitKey) return undefined
    const id = requestAnimationFrame(() => { done.current = fitKey; onReady(rf) })
    return () => cancelAnimationFrame(id)
  }, [ready, fitKey, rf, onReady])
  return null
}

// ─── Design / Lanes / Run pane ───────────────────────────────────────────────

const DesignGraph = forwardRef(function DesignGraph({
  draft, steps, selectedId, onSelect, lens, causalityStates,
  tenantName, agentName, onNavigate,
  onMoveStep, onDuplicateStep, onRemoveStep, onNodeMoved = () => {},
  laneOverrides = null, onLaneChange = () => {},
  onConnectSteps = () => {},
  onDeleteStepId = () => {}, onEdgeDelete = () => {}, onReparent = () => {},
  onSetPivot = () => {}, onDropCreate = () => {}, onCanvasDrop = () => {},
  stitchModel = null, showStitch = false,
  storedLayout = null, onZoomChange = () => {},
  // Pixel insets {top,right,bottom,left} of the stage the drawers and dock
  // cover. Fits frame the graph in the VISIBLE region between them rather
  // than behind a panel. Read through a ref so toggling a drawer never yanks
  // the viewport the DC has panned — only an explicit fit uses it.
  viewInsets = null,
}, ref) {
  const insetsRef = useRef(viewInsets)
  insetsRef.current = viewInsets
  const fitPadding = () => {
    const i = insetsRef.current
    if (!i) return 0.2
    // Drawer insets are real occlusion and stay exact. The VERTICAL margins
    // (caption pill, dock, breathing room) are capped at 18% of the pane each:
    // on a short stage fixed margins ate nearly all of it and the chain fitted
    // at 20%.
    // Before the pane is measured (React Flow's own prop-driven fit reads this
    // during render), fall back to a small fixed margin, never the uncapped
    // one — the uncapped margins clamped the initial fit to minZoom.
    const h = paneRef.current?.clientHeight || 0
    const capV = (v) => (h ? Math.max(8, Math.min(v, h * 0.18)) : Math.min(v, 24))
    return {
      top: `${Math.round(capV((i.top || 0) + 32))}px`,
      right: `${(i.right || 0) + 32}px`,
      bottom: `${Math.round(capV((i.bottom || 0) + 32))}px`,
      left: `${(i.left || 0) + 32}px`,
    }
  }
  const runLens = lens === 'run'
  const lanesLens = lens === 'lanes'
  const rfRef = useRef(null)
  const [refusal, setRefusal] = useState(null)

  // Lane layout (Lanes lens only) — steps at x=order, y=door band.
  const laneLayout = useMemo(
    () => (lanesLens ? layoutLanes(steps, laneOverrides, draft.plane) : null),
    [lanesLens, steps, laneOverrides, draft.plane],
  )
  const lanePos = useMemo(() => {
    const m = new Map()
    if (laneLayout) for (const n of laneLayout.nodes) m.set(n.id, n)
    return m
  }, [laneLayout])

  // Stable per-step callbacks, keyed by index, so the node memo below does not
  // rebuild its data closures every render (which would clobber React Flow's
  // in-flight drag / selection state on each keystroke elsewhere).
  const onSelectStep = useCallback((id) => onSelect(id), [onSelect])

  // The pure graph, then callbacks injected into node/edge data.
  const built = useMemo(
    () => buildGraph(draft, steps, {
      selectedId, runLens, causalityStates, laneOverrides, layout: storedLayout,
    }),
    [draft, steps, selectedId, runLens, causalityStates, laneOverrides, storedLayout],
  )

  const rfNodes = useMemo(() => {
    // Lanes lens: steps only, positioned by band; no cgo/proof nodes (the
    // LAUNCH/PROOF bands say the same thing).
    const base = lanesLens
      ? steps.map((s, i) => {
        const ln = lanePos.get(s.id)
        const plane = s.detections?.[0]?.plane || draft.plane || null
        return {
          id: s.id, type: 'step',
          position: ln ? { x: ln.x, y: ln.y } : { x: 40, y: 40 },
          dragHandle: '.chain-node__grip',
          data: {
            kind: 'step', step: s, order: i + 1, selected: selectedId === s.id,
            tint: PLANE_TINT[plane] || 'var(--cortex-steel, #6B7E8E)',
            runState: null, runLens: false,
            door: ln ? ln.lane : laneOf(s, laneOverrides, draft.plane),
            channelKind: effectiveChannel(s) === 'eal' ? 'eal' : (s.target ? 'target' : null),
          },
        }
      })
      : built.nodes

    return base.map((n) => {
      if (n.type !== 'step') {
        // cgo/proof: inject the scope-link navigation.
        if (n.type === 'cgo') {
          return { ...n, selected: false, data: {
            ...n.data, tenantName, agentName,
            onTenant: () => onNavigate('tenants'),
            onAgent: () => onNavigate('agents'),
          } }
        }
        return n
      }
      const i = steps.findIndex((s) => s.id === n.id)
      return {
        ...n,
        selected: selectedId === n.id && !runLens,
        data: {
          ...n.data,
          onSelectStep: () => onSelectStep(n.id),
          onMoveEarlier: () => onMoveStep(i, -1),
          onMoveLater: () => onMoveStep(i, 1),
          onDuplicate: () => onDuplicateStep(i),
          onRemove: () => onRemoveStep(i),
        },
      }
    })
  }, [
    built, lanesLens, steps, lanePos, selectedId, runLens, draft, laneOverrides,
    tenantName, agentName, onNavigate, onSelectStep, onMoveStep, onDuplicateStep, onRemoveStep,
  ])

  const rfEdges = useMemo(() => {
    const base = lanesLens
      ? built.edges.filter((e) => e.type === 'causality')
      : built.edges
    return base.map((e) => {
      if (e.type !== 'causality') {
        return { ...e, markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--bd2, #4A4A4A)' } }
      }
      return {
        ...e,
        markerEnd: { type: MarkerType.ArrowClosed, color: e.style?.stroke || 'var(--tx2, #C7C7C7)' },
        data: {
          ...e.data,
          onDelete: () => onEdgeDelete(e.target),
          onCyclePivot: () => {
            const cur = e.data?.pivot || 'process_lineage'
            const next = PIVOTS[(PIVOTS.indexOf(cur) + 1) % PIVOTS.length]
            onSetPivot(e.target, next)
          },
        },
      }
    })
  }, [built, lanesLens, onEdgeDelete, onSetPivot])

  // Design-lens stitch overlay: the entity-join edges the context IMPLIES,
  // EXPECTED-only (authored intent, never outcome). Computed from the LTR node
  // positions this canvas actually draws, so the join is pixel-locked to the
  // cards (it rides the pane transform via ViewportPortal) rather than the old
  // vertical-spine coordinates. A key nothing consumes, or a single consumer,
  // draws no edge; consecutive consumers in step order draw one hop each.
  const stitchEdges = useMemo(() => {
    if (runLens || lanesLens || !showStitch) return []
    const planted = plantedKeys(stitchModel)
    if (!planted.length) return []
    const posById = new Map(built.nodes.filter((n) => n.type === 'step').map((n) => [n.id, n.position]))
    const CARD_H = 92
    const port = (p) => ({ x: p.x + GRAPH_LAYOUT.nodeW, y: p.y + CARD_H / 2 })
    const out = []
    for (const key of planted) {
      const consumers = steps.filter((s) => stitchPlaceholdersIn(s.command).includes(key))
      for (let i = 0; i < consumers.length - 1; i += 1) {
        const a = posById.get(consumers[i].id)
        const b = posById.get(consumers[i + 1].id)
        if (!a || !b) continue
        out.push({ id: `stitch:${key}:${consumers[i].id}->${consumers[i + 1].id}`, key, from: port(a), to: port(b) })
      }
    }
    return out
  }, [runLens, lanesLens, showStitch, stitchModel, steps, built.nodes])

  // ── Node change handling (position commit on drop + lane retarget) ──
  const onNodesChange = useCallback((changes) => {
    for (const c of changes) {
      if (c.type === 'position' && c.dragging === false && c.position) {
        if (lanesLens) {
          const before = lanePos.get(c.id)
          const lane = laneAtY(c.position.y)
          if (!before || before.lane !== lane) onLaneChange(c.id, lane)
          continue
        }
        if (c.id === CGO_ID || c.id === PROOF_ID) continue
        onNodeMoved(
          c.id,
          Math.round(c.position.x / SNAP) * SNAP,
          Math.round(c.position.y / SNAP) * SNAP,
        )
      }
    }
  }, [onNodeMoved, onLaneChange, lanesLens, lanePos])

  if (typeof window !== 'undefined' && import.meta.env.MODE !== 'production') {
    window.__rfOnNodesChange = onNodesChange
  }

  // ── Semi-controlled graph ──
  // The draft stays the source of truth (`rfNodes`/`rfEdges` are derived from
  // it), but React Flow's own changes — dimensions, in-flight drag positions,
  // selection — are applied to LOCAL state so the library can do its job: a
  // dragged card follows the pointer (not a jump on drop), nodes carry their
  // `measured` size (the MiniMap renders only measured nodes, and edges attach
  // to measured handles), and edges can be selected (which is what reveals the
  // pivot chip and the delete button). Whenever the derived graph changes, the
  // local copy is re-synced from it, carrying over `measured` and any drag in
  // flight. `remove` changes are NOT applied locally — deletion goes through
  // onNodesDelete / onEdgesDelete into the draft, and the re-sync drops the node.
  const [liveNodes, setLiveNodes] = useState(rfNodes)
  const [liveEdges, setLiveEdges] = useState(rfEdges)
  useEffect(() => {
    setLiveNodes((prev) => {
      const byId = new Map(prev.map((n) => [n.id, n]))
      return rfNodes.map((n) => {
        const p = byId.get(n.id)
        if (!p) return n
        const carried = p.measured ? { ...n, measured: p.measured } : n
        return p.dragging ? { ...carried, position: p.position, dragging: true } : carried
      })
    })
  }, [rfNodes])
  useEffect(() => {
    setLiveEdges((prev) => {
      const selected = new Set(prev.filter((e) => e.selected).map((e) => e.id))
      return rfEdges.map((e) => (selected.has(e.id) ? { ...e, selected: true } : e))
    })
  }, [rfEdges])
  const handleNodesChange = useCallback((changes) => {
    setLiveNodes((nds) => applyNodeChanges(changes.filter((c) => c.type !== 'remove'), nds))
    onNodesChange(changes)
  }, [onNodesChange])
  const handleEdgesChange = useCallback((changes) => {
    setLiveEdges((eds) => applyEdgeChanges(changes.filter((c) => c.type !== 'remove'), eds))
  }, [])

  // ── Connecting ──
  const isValidConnection = useCallback(
    ({ source, target }) => {
      if (source === CGO_ID || target === PROOF_ID) return false
      return canConnect(steps, source, target).ok
    },
    [steps],
  )

  const onConnect = useCallback(({ source, target }) => {
    if (source === CGO_ID || target === PROOF_ID) return
    const verdict = canConnect(steps, source, target)
    if (!verdict.ok) { setRefusal(verdict); return }
    setRefusal(null)
    onConnectSteps(source, target)
  }, [steps, onConnectSteps])

  if (typeof window !== 'undefined' && import.meta.env.MODE !== 'production') {
    window.__rfOnConnect = onConnect
  }

  // n8n create-on-drop: releasing a connection on empty canvas spawns a step
  // there, parented to the source card when the drag began on one.
  const onConnectEnd = useCallback((event, state) => {
    if (!state || state.isValid || state.toNode) return
    const rf = rfRef.current
    if (!rf) return
    const pt = ('changedTouches' in event && event.changedTouches[0]) || event
    const position = rf.screenToFlowPosition({ x: pt.clientX, y: pt.clientY })
    const from = state.fromNode
    const parentId = from && from.type === 'step' ? from.id : null
    onDropCreate({ position, parentId })
  }, [onDropCreate])

  if (typeof window !== 'undefined' && import.meta.env.MODE !== 'production') {
    window.__rfOnConnectEnd = onConnectEnd
  }

  // ── Delete (reparent) ──
  // ONE handler for the whole deletion. React Flow removes the edges attached
  // to a deleted node too, and its pipeline reports them (onEdgesDelete) BEFORE
  // the node (onNodesDelete): handling edges separately cleared the children's
  // parents first, so reparent-on-delete had nothing left to re-attach and the
  // children fell off the chain (observed live: deleting step-03 left step-04
  // as a second root). A deleted node owns its attached edges; only an edge
  // whose two ends both survive is an edge deletion in its own right.
  const onDelete = useCallback(({ nodes = [], edges = [] }) => {
    const goneSteps = new Set(nodes.filter((n) => n.type === 'step').map((n) => n.id))
    for (const e of edges) {
      if (e.type !== 'causality') continue
      if (goneSteps.has(e.source) || goneSteps.has(e.target)) continue
      onEdgeDelete(e.target)
    }
    for (const id of goneSteps) onDeleteStepId(id)
  }, [onDeleteStepId, onEdgeDelete])

  if (typeof window !== 'undefined' && import.meta.env.MODE !== 'production') {
    window.__rfOnDelete = onDelete
  }

  // ── Reconnect (re-parent) ──
  const onReconnect = useCallback((oldEdge, conn) => {
    if (conn.source === CGO_ID || conn.target === PROOF_ID) return
    const verdict = canConnect(steps, conn.source, conn.target)
    if (!verdict.ok) { setRefusal(verdict); return }
    setRefusal(null)
    onReparent(conn.target, conn.source)
  }, [steps, onReparent])

  // ── Palette drag-and-drop ──
  const onDragOver = useCallback((e) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }, [])
  const onDrop = useCallback((e) => {
    e.preventDefault()
    const key = e.dataTransfer.getData('application/cortexsim-node')
    if (!key) return
    const rf = rfRef.current
    const position = rf
      ? rf.screenToFlowPosition({ x: e.clientX, y: e.clientY })
      : { x: 0, y: 0 }
    onCanvasDrop(key, position)
  }, [onCanvasDrop])

  // ── Zero-size mount guard + live zoom ──
  const paneRef = useRef(null)
  const [measured, setMeasured] = useState(false)
  useEffect(() => {
    const el = paneRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setMeasured(width > 0 && height > 0)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const handleInit = useCallback((instance) => {
    rfRef.current = instance
    onZoomChange(instance.getZoom())
  }, [onZoomChange])
  const handleMove = useCallback((_e, viewport) => onZoomChange(viewport.zoom), [onZoomChange])

  // The overview map appears while the DC pans or zooms (when it answers
  // "where am I?") and fades shortly after, unless pinned from the controls.
  // A permanent map sat on top of the chain's right end on short stages —
  // the Proof node was hidden under it at 1024x768.
  const [miniPinned, setMiniPinned] = useState(false)
  const [moving, setMoving] = useState(false)
  const moveTimer = useRef(null)
  const handleMoveStart = useCallback(() => {
    clearTimeout(moveTimer.current)
    setMoving(true)
  }, [])
  const handleMoveEnd = useCallback(() => {
    clearTimeout(moveTimer.current)
    moveTimer.current = setTimeout(() => setMoving(false), 1400)
  }, [])
  useEffect(() => () => clearTimeout(moveTimer.current), [])

  useImperativeHandle(ref, () => ({
    zoomIn: () => rfRef.current?.zoomIn(),
    zoomOut: () => rfRef.current?.zoomOut(),
    zoomReset: () => rfRef.current?.zoomTo(1),
    fit: () => rfRef.current?.fitView({ duration: 200, padding: fitPadding(), maxZoom: 1.2 }),
  }), [])

  // Lanes fits the whole band stage (all seven bands, not just the cards);
  // Design/Run fit the nodes into the region the drawers leave visible.
  const laneStageW = laneLayout ? laneLayout.bounds.width : 0
  const laneStageH = laneLayout ? laneLayout.bounds.height : 0
  const fitForLens = useCallback((rf) => {
    if (lanesLens) {
      rf.fitBounds({ x: 0, y: 0, width: laneStageW, height: laneStageH }, { padding: 0.04, duration: 0 })
    } else {
      rf.fitView({ duration: 0, padding: fitPadding(), maxZoom: 1.2 })
    }
    // fitPadding reads a ref; deliberately not a dep (see viewInsets above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lanesLens, laneStageW, laneStageH])
  const fitKey = lanesLens ? `lanes:${laneStageW}x${laneStageH}` : lens

  const miniColor = useCallback((n) => {
    if (n.type === 'cgo') return 'var(--ink, #003366)'
    if (n.type === 'proof') return 'var(--cortex-success, #2f9e44)'
    return n.data?.tint || 'var(--cortex-steel, #6B7E8E)'
  }, [])

  return (
    <div className="chain composer-canvas__graph" data-testid="composer-chain">
      {refusal && (
        <div className="canvas-refusal" data-testid="canvas-refusal" role="status" aria-live="polite">
          <strong>Edge refused</strong> — {refusal.reason}
          <button type="button" onClick={() => setRefusal(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <div className="composer-canvas__flow" ref={paneRef}>
        <ReactFlowProvider>
          <ReactFlow
            nodes={liveNodes}
            edges={liveEdges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            nodesDraggable={!runLens}
            nodesConnectable={!runLens}
            elementsSelectable={!runLens}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onNodeClick={(_e, n) => { if (n.type === 'step') onSelect(n.id) }}
            onPaneClick={() => onSelect(null)}
            snapToGrid
            snapGrid={[SNAP, SNAP]}
            onConnect={onConnect}
            onConnectEnd={onConnectEnd}
            isValidConnection={isValidConnection}
            onDelete={onDelete}
            onReconnect={runLens ? undefined : onReconnect}
            deleteKeyCode={runLens ? null : ['Backspace', 'Delete']}
            onDragOver={onDragOver}
            onDrop={onDrop}
            fitView={measured}
            fitViewOptions={{ padding: fitPadding(), maxZoom: 1.2 }}
            minZoom={0.2}
            maxZoom={2}
            onInit={handleInit}
            onMove={handleMove}
            onMoveStart={handleMoveStart}
            onMoveEnd={handleMoveEnd}
            proOptions={{ hideAttribution: false }}
          >
            <FitOnReady fitKey={fitKey} onReady={fitForLens} />
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} />
            <Controls showInteractive={false}>
              {!lanesLens && (
                <ControlButton
                  onClick={() => setMiniPinned((v) => !v)}
                  title={miniPinned ? 'Unpin the overview map' : 'Pin the overview map'}
                  aria-label={miniPinned ? 'Unpin the overview map' : 'Pin the overview map'}
                  aria-pressed={miniPinned}
                  className={'composer-minimap-toggle' + (miniPinned ? ' composer-minimap-toggle--on' : '')}
                >
                  ▣
                </ControlButton>
              )}
            </Controls>
            {!lanesLens && (miniPinned || moving) && (
              <MiniMap
                pannable
                zoomable
                nodeColor={miniColor}
                nodeStrokeWidth={2}
                ariaLabel="Chain overview"
                className="composer-minimap"
              />
            )}

            {laneLayout && (
              <ViewportPortal>
                <div
                  data-testid="composer-lane-bands"
                  aria-hidden="true"
                  style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: -1 }}
                >
                  {laneLayout.lanes.map((b) => (
                    <div
                      key={b.key}
                      className={'composer-lane' + (STEP_LANES.includes(b.key) ? '' : ' composer-lane--terminal')}
                      data-lane={b.key}
                      style={{ top: b.top, height: b.h, width: Math.max(laneLayout.bounds.width, 1200), background: b.fill }}
                    >
                      <span className="composer-lane__label">{b.label}</span>
                      <span className="composer-lane__sub">
                        {b.sub}{STEP_LANES.includes(b.key) ? ` · ${b.count}` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </ViewportPortal>
            )}

            {stitchEdges.length > 0 && (
              <ViewportPortal>
                <svg
                  data-testid="composer-stitch-overlay"
                  style={{ position: 'absolute', top: 0, left: 0, overflow: 'visible', pointerEvents: 'none' }}
                  aria-hidden="true"
                >
                  {stitchEdges.map((se) => {
                    const bulge = 28 + Math.abs(se.to.y - se.from.y) / 3
                    const d = `M ${se.from.x} ${se.from.y} `
                      + `C ${se.from.x + bulge} ${se.from.y} `
                      + `${se.to.x + bulge} ${se.to.y} ${se.to.x} ${se.to.y}`
                    return (
                      <path
                        key={se.id}
                        d={d}
                        fill="none"
                        stroke={STATE_TINT.EXPECTED}
                        strokeWidth={1.5}
                        strokeDasharray="4 4"
                        data-stitch-key={se.key}
                      >
                        <title>{`stitch ${se.key} · EXPECTED (authored intent)`}</title>
                      </path>
                    )
                  })}
                </svg>
              </ViewportPortal>
            )}
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </div>
  )
})

// ─── Run lens (the REAL causality graph) ─────────────────────────────────────

function RunGraph({ causalityGraph, zoom, binding = null, hasRun = false }) {
  const summary = causalityGraph?.causality_summary || {}
  const bindingEntries = binding && typeof binding === 'object'
    ? Object.entries(binding).filter(([, v]) => v != null && v !== '')
    : []
  const completeness = summary.chain_completeness_pct
  const broken = Array.isArray(summary.broken_stitches) ? summary.broken_stitches : []

  const { nodes, edges, bounds } = useMemo(
    () => layoutCausalityGraph(causalityGraph),
    [causalityGraph],
  )

  if (!causalityGraph) {
    if (hasRun) {
      return (
        <div className="composer-canvas__runempty" data-testid="composer-run-graph">
          <strong>Run found — causality graph not loaded yet.</strong>{' '}
          A run exists for this scenario; its causality graph has not finished
          loading (or the fetch failed). This is not "no run" — it will render here
          with CONFIRMED / BROKEN edges once the graph arrives.
        </div>
      )
    }
    return (
      <div className="composer-canvas__runempty" data-testid="composer-run-graph">
        <strong>No run yet — EXPECTED only.</strong>{' '}
        Launch this chain (or reconcile an observed run) and the real causality
        graph will render here with CONFIRMED / BROKEN edges. Nothing on this
        canvas is inferred before a run exists.
      </div>
    )
  }

  return (
    <div data-testid="composer-run-graph" className="composer-canvas__rungraph">
      <div className="composer-canvas__runbar">
        <span className="composer-canvas__runpct">
          chain completeness:{' '}
          {typeof completeness === 'number' ? `${completeness}%` : 'not measured'}
        </span>
        {broken.length > 0 ? (
          <span className="composer-canvas__runbroken" data-testid="composer-broken-stitches">
            {broken.length} broken stitch{broken.length === 1 ? '' : 'es'}: {broken.join(', ')}
          </span>
        ) : (
          <span className="composer-canvas__runok">no broken stitches</span>
        )}
      </div>

      {bindingEntries.length > 0 && (
        <div className="composer-canvas__binding" data-testid="composer-stitch-binding">
          <span className="composer-canvas__bindlabel">resolved binding:</span>
          {bindingEntries.map(([k, v]) => (
            <span key={k} className="mono composer-canvas__binding-pair">
              {k}=<strong>{String(v)}</strong>
            </span>
          ))}
        </div>
      )}

      <div style={{ position: 'relative', width: bounds.width * zoom, height: bounds.height * zoom, minHeight: 80 }}>
        <div style={{
          position: 'absolute', top: 0, left: 0, width: bounds.width, height: bounds.height,
          transform: `scale(${zoom})`, transformOrigin: 'top left',
        }}>
          <svg width={bounds.width} height={bounds.height}
            style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }} aria-hidden="true">
            {edges.map((e) => {
              const stroke = STATE_TINT[e.state] || 'var(--cortex-steel, #6B7E8E)'
              return (
                <g key={e.id}>
                  <path d={edgePath(e.from, e.to)} fill="none" stroke={stroke}
                    strokeWidth={e.state === 'BROKEN' ? 2 : 1.5}
                    strokeDasharray={e.state === 'EXPECTED' ? '5 4' : undefined}>
                    <title>{`${e.kind || 'edge'} · ${e.state}`}{e.rationale ? `: ${e.rationale}` : ''}</title>
                  </path>
                </g>
              )
            })}
          </svg>
          {nodes.map((n) => {
            const tint = KIND_TINT[n.kind] || 'var(--cortex-steel, #6B7E8E)'
            const label = n.node?.label || n.node?.name || n.id
            return (
              <div key={n.id} className={`chain-node chain-node--run chain-node--kind-${n.kind}`}
                data-node-kind={n.kind}
                style={{
                  position: 'absolute', left: n.x, top: n.y, width: n.w, minHeight: n.h,
                  boxSizing: 'border-box', borderLeft: `4px solid ${tint}`,
                }}>
                <div className="chain-node--run__kind" style={{ color: tint }}>{n.kind}</div>
                <div className="mono chain-node--run__label">{label}</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ─── Root ────────────────────────────────────────────────────────────────────

export default function ComposerCanvas({
  draft = {},
  steps = [],
  lens = 'design',
  onLens = () => {},
  canvasView = 'chain',
  onCanvasView = () => {},
  selectedId = null,
  onSelect = () => {},
  validation = { counts: { steps: 0, detections: 0 } },
  causalityGraph = null,
  causalityStates = {},
  activeRun = null,
  hasRun = false,
  originError = null,
  loadingOrigin = false,
  fromId = null,
  tenantName = null,
  agentName = null,
  yamlText = '',
  onMoveStep = () => {},
  onDuplicateStep = () => {},
  onRemoveStep = () => {},
  onAddStep = () => {},
  onNodeMoved = () => {},
  onConnectSteps = () => {},
  onDeleteStepId = () => {},
  onEdgeDelete = () => {},
  onReparent = () => {},
  onSetPivot = () => {},
  onDropCreate = () => {},
  onCanvasDrop = () => {},
  onStartLibrary = () => {},
  onStartTtp = () => {},
  onStartBlank = () => {},
  onNavigate = () => {},
  stitchModel = null,
  showStitch = false,
  onToggleStitch = () => {},
  storedLayout = null,
  laneOverrides = null,
  onLaneChange = () => {},
  viewInsets = null,
}) {
  const [zoom, setZoom] = useState(1)
  const runLens = lens === 'run'
  const hasSteps = steps.length > 0

  const zoomOut = () => setZoom((z) => Math.max(0.5, Math.round((z - 0.1) * 10) / 10))
  const zoomIn = () => setZoom((z) => Math.min(1.6, Math.round((z + 0.1) * 10) / 10))
  const zoomReset = () => setZoom(1)

  const designGraphRef = useRef(null)
  const [designZoom, setDesignZoom] = useState(1)

  const hasStoredLayout = !!(storedLayout && Object.keys(storedLayout).length)

  const graphProps = {
    draft, steps, selectedId, onSelect, lens, causalityStates,
    tenantName, agentName, onNavigate,
    onMoveStep, onDuplicateStep, onRemoveStep, onNodeMoved,
    laneOverrides, onLaneChange, onConnectSteps,
    onDeleteStepId, onEdgeDelete, onReparent, onSetPivot, onDropCreate, onCanvasDrop,
    stitchModel, showStitch,
    storedLayout, viewInsets,
  }

  return (
    <section
      className="composer-canvas"
      aria-label="Chain canvas"
      data-testid="composer-canvas"
      data-stored-layout={hasStoredLayout ? 'set' : 'none'}
    >
      <div className="composer-canvas__head">
        <span className="mono composer-canvas__id">{draft.originId || 'no scenario'}</span>
        <span className="composer-canvas__name">{draft.name || 'Open a scenario, or add a step'}</span>
        <span className="composer__spacer" />

        <div className="composer-canvas__lens" role="group" aria-label="Canvas lens">
          {[['design', 'Design'], ['lanes', 'Lanes'], ['run', 'Run']].map(([id, label]) => (
            <button
              type="button"
              key={id}
              data-testid={`composer-lens-${id}`}
              className={'canvas-view' + (lens === id ? ' canvas-view--on' : '')}
              aria-pressed={lens === id}
              onClick={() => onLens(id)}
            >
              {label}
            </button>
          ))}
        </div>

        <button
          type="button"
          data-testid="composer-stitch-toggle"
          className={'canvas-view' + (showStitch ? ' canvas-view--on' : '')}
          aria-pressed={showStitch}
          title="Overlay the entity-join edges the stitch context implies (design intent)"
          onClick={onToggleStitch}
        >
          Stitch overlay
        </button>

        <div className="composer-canvas__views" role="group" aria-label="Canvas view">
          {[['chain', 'Chain'], ['yaml', 'YAML']].map(([id, label]) => (
            <button
              type="button"
              key={id}
              className={'canvas-view' + (canvasView === id ? ' canvas-view--on' : '')}
              aria-pressed={canvasView === id}
              onClick={() => onCanvasView(id)}
            >
              {label}
            </button>
          ))}
        </div>

        {canvasView !== 'yaml' && (
          <div className="composer-canvas__zoom" role="group" aria-label="Zoom">
            {runLens ? (
              <>
                <button type="button" className="canvas-view" aria-label="Zoom out" onClick={zoomOut}>−</button>
                <button type="button" className="canvas-view" aria-label="Reset zoom" onClick={zoomReset}>{Math.round(zoom * 100)}%</button>
                <button type="button" className="canvas-view" aria-label="Zoom in" onClick={zoomIn}>+</button>
              </>
            ) : (
              <>
                <button type="button" className="canvas-view" aria-label="Zoom out" onClick={() => designGraphRef.current?.zoomOut()}>−</button>
                <button type="button" className="canvas-view" aria-label="Reset zoom" onClick={() => designGraphRef.current?.zoomReset()}>{Math.round(designZoom * 100)}%</button>
                <button type="button" className="canvas-view" aria-label="Zoom in" onClick={() => designGraphRef.current?.zoomIn()}>+</button>
                <button type="button" className="canvas-view" aria-label="Fit view" onClick={() => designGraphRef.current?.fit()}>Fit</button>
              </>
            )}
          </div>
        )}
      </div>

      <div className="composer-canvas__meta">
        {validation.counts.steps} steps
        {draft.cgo && <> · single process_lineage spine · CGO {draft.cgo}</>}
        {runLens && activeRun && <> · run {activeRun.run_id || activeRun.id || ''} {activeRun.status || ''}</>}
      </div>

      {originError && (
        <div className="composer-canvas__error" role="alert" data-testid="composer-origin-error">
          <strong>{fromId} could not be loaded.</strong> {originError} — the canvas below is
          empty because nothing could be read, not because the scenario has no steps.
        </div>
      )}

      {canvasView === 'yaml' ? (
        <pre className="composer-yaml mono" data-testid="composer-yaml">
          {yamlText}
        </pre>
      ) : (
        <>
          {!hasSteps && !loadingOrigin && !originError && (
            <div className="composer-firstrun" data-testid="composer-firstrun">
              <div className="composer-firstrun__title">Start a simulation three ways</div>
              <p className="composer-firstrun__body">
                A simulation is an ordered chain of steps run against one agent. Each step
                declares the detection you expect Cortex to raise — that pairing is what a
                POV proves.
              </p>
              <div className="composer-firstrun__options">
                <button type="button" className="firstrun-option" onClick={onStartLibrary}>
                  <span className="firstrun-option__num">1</span>
                  <span className="firstrun-option__text">
                    <span className="firstrun-option__title">Start from a library scenario</span>
                    <span className="firstrun-option__note">
                      Fastest path — the chains in the Library arrive complete with their
                      expected detections.
                    </span>
                  </span>
                  <span className="firstrun-option__arrow" aria-hidden="true">→</span>
                </button>
                <button type="button" className="firstrun-option" onClick={onStartTtp}>
                  <span className="firstrun-option__num">2</span>
                  <span className="firstrun-option__text">
                    <span className="firstrun-option__title">Start from a TTP card</span>
                    <span className="firstrun-option__note">
                      Pick the detection you need to prove; the card supplies the technique
                      and its detector.
                    </span>
                  </span>
                  <span className="firstrun-option__arrow" aria-hidden="true">→</span>
                </button>
                <button type="button" className="firstrun-option" onClick={onStartBlank}>
                  <span className="firstrun-option__num">3</span>
                  <span className="firstrun-option__text">
                    <span className="firstrun-option__title">Start from a blank step</span>
                    <span className="firstrun-option__note">
                      Author the command yourself, then declare what Cortex should raise.
                    </span>
                  </span>
                  <span className="firstrun-option__arrow" aria-hidden="true">→</span>
                </button>
              </div>
            </div>
          )}

          {loadingOrigin && <div className="destination-loading">loading {fromId}…</div>}

          {hasSteps && !runLens && (
            <DesignGraph
              ref={designGraphRef}
              onZoomChange={setDesignZoom}
              {...graphProps}
            />
          )}

          {hasSteps && runLens && (
            <>
              <RunGraph
                causalityGraph={causalityGraph}
                zoom={zoom}
                binding={activeRun?.stitch_binding || null}
                hasRun={hasRun}
              />
              <DesignGraph {...graphProps} />
            </>
          )}

          {hasSteps && !runLens && (
            <button
              type="button"
              className="composer-add-step"
              onClick={onAddStep}
              data-testid="composer-add-step"
            >
              + Add step
            </button>
          )}
        </>
      )}
    </section>
  )
}
