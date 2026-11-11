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
 * Below the chain sits the TENANT-CONTENT rail: one `rule` node per correlation
 * rule on a card the chain cites, fed by inert `content` edges from the steps
 * whose detections it composes. Derived, never persisted, absent in the Run lens
 * (`composerGraph` / `composerContent`).
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
import { layoutLanes, laneAtY, STEP_LANES } from './composerLanes.js'
import { signalBadge } from './composerSignal.js'
import { COMPOSER_LANES } from './povdata/corpus.js'
import {
  buildGraph, SNAP, PLANE_TINT, STATE_TINT, CGO_ID, PROOF_ID, GRAPH_LAYOUT,
} from './composerGraph.js'
import {
  detectionChips, contentHeadline, ruleLines, OWNERSHIP_MEANING,
} from './composerContent.js'
import { LANE_COL_X, LANE_COL_PITCH } from './composerLanes.js'

/**
 * What the spine actually is, said in the caption. It used to read "single
 * process_lineage spine" for every draft with a CGO — untrue with a typed
 * pivot on screen, a legacy star, or a card flagged off-chain.
 */
function spinePhrase(steps, validation) {
  const parented = steps.filter((s) => s.causalityParent)
  if (!parented.length) return steps.length > 1 ? 'no declared spine' : 'single step'
  const pivots = [...new Set(parented.map((s) => s.causalityPivot || 'process_lineage'))]
  const kind = pivots.length === 1 && pivots[0] === 'process_lineage'
    ? 'process_lineage spine'
    : `spine (${pivots.join(', ')})`
  const broken = (validation?.offChain?.length || 0)
    + (validation?.danglingParents?.length || 0)
    + (validation?.forwardRefs?.length || 0)
  return `${broken ? 'broken' : 'single'} ${kind}`
}

/** Wrap a card tool's handler so its click does not also select the card. */
const stop = (fn) => (e) => { e.stopPropagation(); if (fn) fn() }

// Run-lens node kind → colour (real causality graph; token-driven).
const KIND_TINT = {
  cgo: 'var(--ink, #003366)', process: 'var(--ac, #00C0E8)',
  wrapper: 'var(--cortex-steel, #6B7E8E)',
  exposure: 'var(--cortex-warning, #f59f00)',
  alert: 'var(--cortex-success, #2f9e44)',
}

/** Lane key → the band label and sub-label ('WIRE' → 'LIVE TRAFFIC'). */
const LANE_LABEL = Object.fromEntries(COMPOSER_LANES.map(([key, label]) => [key, label]))
const LANE_SUB = Object.fromEntries(COMPOSER_LANES.map(([key, , sub]) => [key, sub]))

/** Why a lane cannot be changed by dragging. Shown when a card is dropped in
 *  another lane; the card snaps back. */
const LANE_REFUSAL = 'A step\'s lane follows what its command does. Change the command, or the channel in the Inspector.'

/** The door badge's title: the path in words, the plugin, where it runs; or
 *  why the path could not be read. */
function doorTitle(sig) {
  if (!sig) return ''
  if (sig.unplaced) return `signal path not read: ${sig.reason}`
  const where = sig.runsOn === 'SIM' ? 'runs in SimCore\'s own process' : 'runs on the beacon'
  const plugin = sig.plugins?.length ? ` · plugin ${sig.plugins.join(', ')}` : ''
  return `${LANE_LABEL[sig.path] || sig.path} · ${LANE_SUB[sig.path] || ''}${plugin} · ${where}`
}

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
          {data.signal && (
            <span
              className={'pov-chip-plane chain-node__doorbadge'
                + (data.signal.unplaced ? ' chain-node__doorbadge--unplaced' : '')}
              /* `chain-door-` NOT `chain-step-door-`: the door badge now shows on
                 every lens, and several tests scan `[data-testid^="chain-step-"]`
                 to pin step-card DOM order — reusing that prefix would make a
                 badge read as a spurious extra "step". */
              data-testid={`chain-door-${s.id}`}
              title={doorTitle(data.signal)}
            >
              {signalBadge(data.signal)}
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
              {data.runState === 'NOT_RUN' ? 'not in run' : data.runState}
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
        {/* The step's PROOF CONTRACT, grouped by who owns the work: what the DC
            runs, what must be deployed in the tenant, what ships built in. One
            chip per type with a count, so three queries never read as three
            tags on the TTP. */}
        <span className="chain-node__chips">
          {s.detections.length ? (
            <>
              <span className="chain-node__expects">expects</span>
              {detectionChips(s.detections).map((c) => (
                <span
                  key={c.label}
                  className={'chip ' + (c.error ? 'chip--missed' : `chip--own chip--own-${c.ownership}`)}
                  title={c.error
                    ? 'Not one of the seven detection types. This expectation cannot be scored.'
                    : OWNERSHIP_MEANING[c.ownership]}
                >
                  {c.label}
                </span>
              ))}
              {data.unbound > 0 && (
                <span
                  className="chip chip--missed"
                  data-testid={`chain-unbound-${s.id}`}
                  title="This step expects a correlation alert, and no cited card carries a rule for it."
                >
                  {data.unbound > 1 ? `${data.unbound} × ` : ''}no rule on any card
                </span>
              )}
            </>
          ) : (
            <span className="chip chip--missed">no expected detection</span>
          )}
        </span>
      </button>
      {!data.runLens && (
        <div className="chain-node__tools nodrag nopan">
          <button type="button" title="Move earlier" aria-label={`Move ${s.id} earlier`}
            onClick={stop(data.onMoveEarlier)}>↑</button>
          <button type="button" title="Move later" aria-label={`Move ${s.id} later`}
            onClick={stop(data.onMoveLater)}>↓</button>
          <button type="button" title="Duplicate step" aria-label={`Duplicate ${s.id}`}
            onClick={stop(data.onDuplicate)}>⧉</button>
          <button type="button" title="Remove step" aria-label={`Remove ${s.id}`}
            onClick={stop(data.onRemove)}>×</button>
        </div>
      )}
      {isConnectable && <Handle type="source" id="out" position={Position.Right} />}
      {/* The port a content edge (step → rule) attaches to. Mounted on EVERY
          step outside the Run lens, not only once a rule exists: the cited
          card loads after React Flow has measured this node, and a handle
          added later is invisible to the edge renderer, so the rule was drawn
          with no edge to it (seen in Chrome, never in jsdom). Inert and 1px. */}
      {!data.runLens && (
        <Handle
          type="source" id="content" position={Position.Bottom}
          isConnectable={false} isConnectableStart={false} isConnectableEnd={false}
          className="chain-node__contentport"
        />
      )}
    </div>
  )
}

/**
 * A correlation RULE the tenant must hold. Not a step: nothing runs here and
 * nothing is persisted. It prints its two statuses as words (`ruleLines`), so a
 * stub can never be mistaken for importable content by its colour.
 */
function RuleNode({ data }) {
  const r = data.rule
  const lines = ruleLines(r)
  return (
    <div
      className={
        `chain-node chain-node--rule chain-node--rule-${r.contentStatus}`
        + (data.selected ? ' chain-node--selected' : '')
      }
    >
      <Handle
        type="target" id="in" position={Position.Top}
        isConnectable={false} isConnectableStart={false} isConnectableEnd={false}
          className="chain-node__contentport"
      />
      <button
        type="button"
        className="chain-node__body nodrag nopan"
        onClick={data.onSelectRule}
        aria-pressed={!!data.selected}
        data-testid={`chain-rule-${r.ruleId}`}
      >
        <span className="chain-node__row">
          <span className="chain-node__kind">rule · correlation</span>
        </span>
        <span className="chain-rule__id mono">{r.ruleId}</span>
        <span className="chain-node__name">{r.name}</span>
        <span className="chain-rule__line chain-rule__line--content">{lines.content}</span>
        <span className="chain-rule__line">{lines.binding}</span>
        <span className="chain-node__sub mono">{lines.inputs}</span>
      </button>
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

const nodeTypes = { cgo: CgoNode, step: StepNode, proof: ProofNode, rule: RuleNode }

// ─── Edge components ────────────────────────────────────────────────────────

/**
 * A causality edge — the one PERSISTED relationship. Renders the pivot as an
 * interactive chip (click to cycle through the seven pivots) plus a delete
 * button (child becomes a root). Both live in an `EdgeLabelRenderer` portal and
 * are `nodrag nopan` with `pointer-events:all` so they stay clickable over the
 * pane. A derived edge (cgo→root, leaf→proof) is muted and inert.
 */
function CausalityEdge({
  id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition,
  markerEnd, style, data, selected,
}) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })
  const pivot = data?.pivot || 'process_lineage'
  const showChip = selected || pivot !== 'process_lineage'
  // The Run lens shows the spine that RAN: its pivot is a label there, never
  // a control that would rewrite the draft from a read-only view.
  const editable = !data?.runLens && typeof data?.onCyclePivot === 'function'
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={24} />
      <EdgeLabelRenderer>
        <div
          className={'composer-edge-label nodrag nopan' + (selected ? ' composer-edge-label--on' : '')}
          style={{ transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}
        >
          {showChip && (editable ? (
            <button
              type="button"
              className="composer-edge-pivot"
              title={`pivot: ${pivot} — click to change`}
              // The VALUE leads the name: a screen reader must hear which pivot
              // will be saved, and voice control can say the visible text.
              aria-label={`${pivot} pivot, ${source} to ${target} — activate to change`}
              onClick={() => data.onCyclePivot()}
            >
              {pivot}
            </button>
          ) : (
            <span className="composer-edge-pivot composer-edge-pivot--static" title={`pivot: ${pivot}`}>
              {pivot}
            </span>
          ))}
          {editable && selected && (
            <button
              type="button"
              className="composer-edge-del"
              title="Delete this link (the child becomes a root)"
              aria-label={`Delete the link from ${source} to ${target}`}
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

/**
 * A step → rule edge. Inert: it states which detections a rule composes. The
 * fallback `card` edge (nothing resolved; the step only cites the same card) is
 * fainter than a `feeds` edge.
 */
function ContentEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data,
}) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  })
  const faint = data?.kind === 'card'
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: 'var(--cortex-steel, #6B7E8E)',
          strokeWidth: faint ? 1 : 1.25,
          strokeDasharray: faint ? '1 5' : '3 4',
          opacity: faint ? 0.7 : 1,
        }}
      />
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className={'composer-content-label nodrag nopan' + (faint ? ' composer-content-label--faint' : '')}
            style={{ transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

const edgeTypes = { causality: CausalityEdge, derived: DerivedEdge, content: ContentEdge }

/**
 * Fit the viewport once per `fitKey`, and only after React Flow has MEASURED
 * every node. The scenario arrives asynchronously, so the pane mounts and
 * reports a size before any card has dimensions; a fit issued then is a no-op
 * and the graph sat at scale(1) in the corner on some loads (observed live).
 * Rendered as a child of <ReactFlow> so it can read the store. Keyed by lens,
 * so adding or dropping a node never yanks the viewport the DC has panned.
 */
function FitOnReady({ fitKey, onReady, softKey = null, canSoftFit = () => false }) {
  const ready = useNodesInitialized()
  const rf = useReactFlow()
  const done = useRef(null)
  const doneSoft = useRef(null)
  useEffect(() => {
    if (!ready) return undefined
    if (done.current !== fitKey) {
      const id = requestAnimationFrame(() => {
        done.current = fitKey
        doneSoft.current = softKey
        onReady(rf)
      })
      return () => cancelAnimationFrame(id)
    }
    // SOFT refit: the graph's extent changed without a lens change (the tenant-
    // content rail arrived with its card, or was toggled). Refit so it is not
    // left below the fold, but only while the DC has not panned or zoomed since
    // the last fit: their viewport is never yanked.
    if (doneSoft.current !== softKey) {
      doneSoft.current = softKey
      if (!canSoftFit()) return undefined
      const id = requestAnimationFrame(() => onReady(rf))
      return () => cancelAnimationFrame(id)
    }
    return undefined
  }, [ready, fitKey, softKey, rf, onReady, canSoftFit])
  return null
}

// ─── Design / Lanes / Run pane ───────────────────────────────────────────────

const DesignGraph = forwardRef(function DesignGraph({
  draft, steps, selectedId, onSelect, lens, causalityStates,
  tenantName, agentName, onNavigate,
  onMoveStep, onDuplicateStep, onRemoveStep, onNodeMoved = () => {},
  onConnectSteps = () => {},
  onDeleteStepId = () => {}, onEdgeDelete = () => {}, onReparent = () => {},
  onSetPivot = () => {}, onDropCreate = () => {}, onCanvasDrop = () => {},
  stitchModel = null, showStitch = false,
  content = null, unbound = null, selectedRuleKey = null, onSelectRule = () => {},
  storedLayout = null, onZoomChange = () => {},
  // Pixel insets {top,right,bottom,left} of the stage the drawers and dock
  // cover. Fits frame the graph in the VISIBLE region between them rather
  // than behind a panel. Read through a ref so toggling a drawer never yanks
  // the viewport the DC has panned — only an explicit fit uses it.
  viewInsets = null,
}, ref) {
  const insetsRef = useRef(viewInsets)
  insetsRef.current = viewInsets
  // Whether the rail is drawn, for `fitPadding` (set once the graph is built).
  const hasRailRef = useRef(false)
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
    const capV = (v, share = 0.18) => (h ? Math.max(8, Math.min(v, h * share)) : Math.min(v, 24))
    // With the tenant-content rail shown the graph has a second row, and its
    // bottom edge is where the floating "+ Add step" button sits. Keep that
    // strip clear (the button is ~34px tall, ~60px above the dock), with a
    // larger share of a short pane than the plain chain needs.
    const bottom = hasRailRef.current
      ? capV((i.bottom || 0) + 32 + 52, 0.3)
      : capV((i.bottom || 0) + 32)
    return {
      top: `${Math.round(capV((i.top || 0) + 32))}px`,
      right: `${(i.right || 0) + 32}px`,
      bottom: `${Math.round(bottom)}px`,
      left: `${(i.left || 0) + 32}px`,
    }
  }
  const runLens = lens === 'run'
  const lanesLens = lens === 'lanes'
  const rfRef = useRef(null)
  const [refusal, setRefusal] = useState(null)

  // Lane layout (Lanes lens only) — steps at x=order, y=door band.
  const laneLayout = useMemo(
    () => (lanesLens ? layoutLanes(steps) : null),
    [lanesLens, steps],
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

  // Measured step-card heights, fed back into the pure graph so the tenant-
  // content rail clears the real bottom of the lowest card. Only a CHANGE in a
  // height replaces the object, so a re-measure that agrees is a no-op.
  const [stepHeights, setStepHeights] = useState(null)

  // The pure graph, then callbacks injected into node/edge data.
  const built = useMemo(
    () => buildGraph(draft, steps, {
      // The door badge on Design/Run reads the step's own channel/plane: a
      // lane override is arrangement on the Lanes lens, and the plane lanes
      // (CC/BVM/ENG) do not change how the step launches.
      selectedId, runLens, causalityStates, layout: storedLayout,
      content, selectedRuleKey, stepHeights,
    }),
    [draft, steps, selectedId, runLens, causalityStates, storedLayout,
      content, selectedRuleKey, stepHeights],
  )

  hasRailRef.current = !!built.rail && !lanesLens

  // Correlation expectations with no rule behind them, per step. A property of
  // the STEP's chips, so it shows on every lens and whether or not the rail is.
  const unboundByStep = useMemo(() => {
    const m = new Map()
    for (const u of unbound || []) m.set(u.stepId, (m.get(u.stepId) || 0) + 1)
    return m
  }, [unbound])

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
            signal: ln ? ln.signal : null,
            channelKind: effectiveChannel(s) === 'eal' ? 'eal' : (s.target ? 'target' : null),
          },
        }
      }).concat(
        // Rules sit in the Cortex Engine band, in the columns AFTER the last
        // step, so a rule can never land on a step that is itself in that band.
        built.nodes.filter((n) => n.type === 'rule').map((n, k) => ({
          ...n,
          position: {
            x: LANE_COL_X + (steps.length + k) * LANE_COL_PITCH,
            y: laneLayout?.lanes.find((b) => b.key === 'ENG')?.nodeY ?? n.position.y,
          },
        })),
      )
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
        if (n.type === 'rule') {
          return { ...n, selected: false, data: { ...n.data, onSelectRule: () => onSelectRule(n.id) } }
        }
        return n
      }
      const i = steps.findIndex((s) => s.id === n.id)
      return {
        ...n,
        selected: selectedId === n.id && !runLens,
        data: {
          ...n.data,
          unbound: unboundByStep.get(n.id) || 0,
          onSelectStep: () => onSelectStep(n.id),
          onMoveEarlier: () => onMoveStep(i, -1),
          onMoveLater: () => onMoveStep(i, 1),
          onDuplicate: () => onDuplicateStep(i),
          onRemove: () => onRemoveStep(i),
        },
      }
    })
  }, [
    built, lanesLens, steps, lanePos, selectedId, runLens, draft,
    tenantName, agentName, onNavigate, onSelectStep, onMoveStep, onDuplicateStep, onRemoveStep,
    laneLayout, unboundByStep, onSelectRule,
  ])

  const rfEdges = useMemo(() => {
    const base = lanesLens
      ? built.edges.filter((e) => e.type === 'causality' || e.type === 'content')
      : built.edges
    return base.map((e) => {
      if (e.type === 'content') {
        return { ...e, markerEnd: { type: MarkerType.Arrow, color: 'var(--cortex-steel, #6B7E8E)' } }
      }
      if (e.type !== 'causality') {
        return { ...e, markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--bd2, #4A4A4A)' } }
      }
      const markerEnd = { type: MarkerType.ArrowClosed, color: e.style?.stroke || 'var(--tx2, #C7C7C7)' }
      // Read-only lens: no edit callbacks at all, so nothing on an edge can
      // rewrite the draft from the Run lens.
      if (runLens) return { ...e, markerEnd }
      return {
        ...e,
        markerEnd,
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
  }, [built, lanesLens, runLens, onEdgeDelete, onSetPivot])

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
          // A lane is read from what the step does, so a drop cannot change
          // it: the card snaps back to its slot, and a drop in ANOTHER lane
          // says why instead of silently retargeting (the old behaviour wrote
          // the channel or a presentation-only override).
          const before = lanePos.get(c.id)
          if (!before) continue
          setLiveNodes((nds) => nds.map((n) => (
            n.id === c.id ? { ...n, position: { x: before.x, y: before.y } } : n
          )))
          if (laneAtY(c.position.y) !== before.lane) setRefusal({ reason: LANE_REFUSAL })
          continue
        }
        if (c.id === CGO_ID || c.id === PROOF_ID) continue
        // Only a STEP has a stored position. A rule node is not draggable, and
        // this keeps it out of `composer_layout` whatever React Flow reports.
        if (!steps.some((st) => st.id === c.id)) continue
        onNodeMoved(
          c.id,
          Math.round(c.position.x / SNAP) * SNAP,
          Math.round(c.position.y / SNAP) * SNAP,
        )
      }
    }
  }, [onNodeMoved, lanesLens, lanePos, steps])

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
  useEffect(() => {
    const next = {}
    for (const n of liveNodes) {
      if (n.type === 'step' && n.measured?.height) next[n.id] = Math.round(n.measured.height)
    }
    setStepHeights((prev) => {
      const a = prev || {}
      const same = Object.keys(next).length === Object.keys(a).length
        && Object.keys(next).every((id) => a[id] === next[id])
      return same ? prev : next
    })
  }, [liveNodes])
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

  // Releasing a connection drag that React Flow did NOT accept. React Flow never
  // calls onConnect for a connection isValidConnection rejected, so this is
  // the only place a refusal can be explained. Three outcomes, each visible:
  //  - released over a card (its port, or anywhere on its body) → connect if
  //    canConnect allows it, otherwise show WHY not (it used to end silently,
  //    with only a red port as the cue);
  //  - released over empty canvas from a step's OUTPUT port → n8n
  //    create-on-drop: a new step there, parented to the source card;
  //  - anything else → nothing: a drag out of an INPUT port into space, or an
  //    edge reconnect being abandoned (React Flow routes those here too, and
  //    used to spawn a phantom step).
  const reconnectingRef = useRef(false)
  const onConnectEnd = useCallback((event, state) => {
    if (!state || state.isValid) return
    if (reconnectingRef.current) return
    const from = state.fromNode
    if (!from || from.type !== 'step') return
    const fromSource = (state.fromHandle?.type || 'source') === 'source'
    const pt = (event && 'changedTouches' in event && event.changedTouches?.[0]) || event
    const under = (typeof document.elementFromPoint === 'function'
      && document.elementFromPoint(pt.clientX, pt.clientY)) || event?.target || null
    const nodeEl = under && typeof under.closest === 'function' ? under.closest('.react-flow__node') : null
    const toId = state.toNode?.id || nodeEl?.getAttribute('data-id') || null
    if (toId) {
      if (toId === from.id) return
      if (toId === CGO_ID || toId === PROOF_ID) {
        setRefusal({ reason: 'The trigger and the proof are the fixed ends of the chain — connect a step to a step.' })
        return
      }
      const [parent, child] = fromSource ? [from.id, toId] : [toId, from.id]
      const verdict = canConnect(steps, parent, child)
      if (!verdict.ok) { setRefusal(verdict); return }
      setRefusal(null)
      onConnectSteps(parent, child)
      return
    }
    if (!fromSource) return
    const onPane = under && typeof under.closest === 'function' && under.closest('.react-flow__pane')
    if (!onPane) return
    const rf = rfRef.current
    if (!rf) return
    onDropCreate({
      position: rf.screenToFlowPosition({ x: pt.clientX, y: pt.clientY }),
      parentId: from.id,
    })
  }, [steps, onConnectSteps, onDropCreate])

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

  // Delete / Backspace — scoped to the CANVAS. React Flow's own deleteKeyCode
  // listens on the whole DOCUMENT and only spares inputs, so with a card
  // selected, Backspace on a button in the workflow bar, a drawer, the dock —
  // or behind the exit confirm — deleted the card, with no undo. This handler
  // sits on the canvas element: only a key pressed with focus inside the graph
  // can delete anything (`deleteKeyCode={null}` below turns the global one off).
  const liveNodesRef = useRef(liveNodes)
  liveNodesRef.current = liveNodes
  const liveEdgesRef = useRef(liveEdges)
  liveEdgesRef.current = liveEdges
  const onCanvasKeyDown = useCallback((e) => {
    if (runLens) return
    if (e.key !== 'Delete' && e.key !== 'Backspace') return
    const t = e.target
    if (t && typeof t.closest === 'function'
      && t.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""]')) return
    const nodes = liveNodesRef.current.filter((n) => n.selected && n.type === 'step')
    const edges = liveEdgesRef.current.filter((ed) => ed.selected && ed.type === 'causality')
    if (!nodes.length && !edges.length) return
    e.preventDefault()
    onDelete({ nodes, edges })
  }, [runLens, onDelete])

  // ── Reconnect (re-parent) ──
  // Edges reconnect at their TAIL only (composerGraph `reconnectable:
  // 'source'`): dragging it to another step changes THIS child's parent. The
  // old version took the head too, so dropping an arrowhead on a third step
  // kept the original edge and silently stole the new step from its parent.
  const onReconnect = useCallback((oldEdge, conn) => {
    if (conn.target !== oldEdge.target || conn.source === oldEdge.source) return
    if (conn.source === CGO_ID || conn.source === PROOF_ID) return
    const verdict = canConnect(steps, conn.source, conn.target)
    if (!verdict.ok) { setRefusal(verdict); return }
    setRefusal(null)
    onReparent(conn.target, conn.source)
  }, [steps, onReparent])
  const onReconnectStart = useCallback(() => { reconnectingRef.current = true }, [])
  // A reconnect React Flow refused (isValidConnection said no) never reaches
  // onReconnect — say why here, as for a refused new connection.
  const onReconnectEnd = useCallback((_e, edge, _handleType, cs) => {
    reconnectingRef.current = false
    if (!cs || cs.isValid || !cs.toNode) return
    const parent = cs.toNode.id
    if (parent === edge.source || parent === edge.target) return
    if (parent === CGO_ID || parent === PROOF_ID) {
      setRefusal({ reason: 'The trigger and the proof are the fixed ends of the chain — re-attach to a step.' })
      return
    }
    const verdict = canConnect(steps, parent, edge.target)
    if (!verdict.ok) setRefusal(verdict)
  }, [steps])

  if (typeof window !== 'undefined' && import.meta.env.MODE !== 'production') {
    window.__rfOnReconnect = onReconnect
  }

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
  // True once the DC pans or zooms by hand. React Flow passes the DOM event
  // for a user gesture and null for a programmatic move (a fit), so a fit
  // never counts as the DC's own movement.
  const userMovedRef = useRef(false)
  const canSoftFit = useCallback(() => !userMovedRef.current, [])
  const handleMoveStart = useCallback((event) => {
    if (event) userMovedRef.current = true
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
    fit: () => {
      userMovedRef.current = false
      rfRef.current?.fitView({ duration: 200, padding: fitPadding(), maxZoom: 1.2 })
    },
  }), [])

  // Lanes fits the whole band stage (all seven bands, not just the cards);
  // Design/Run fit the nodes into the region the drawers leave visible.
  const ruleCols = built.nodes.filter((n) => n.type === 'rule').length
  const laneStageW = laneLayout ? laneLayout.bounds.width + ruleCols * LANE_COL_PITCH : 0
  const laneStageH = laneLayout ? laneLayout.bounds.height : 0
  const fitForLens = useCallback((rf) => {
    userMovedRef.current = false
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
    if (n.type === 'cgo') return 'var(--tx2, #C7C7C7)'
    if (n.type === 'proof') return 'var(--cortex-success, #2f9e44)'
    if (n.type === 'rule') return 'var(--cortex-steel, #6B7E8E)'
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

      {/* Focusable (programmatically) so a selected edge can take Delete: an
          edge click moves focus here; the key handler is scoped to this box. */}
      <div className="composer-canvas__flow" ref={paneRef} tabIndex={-1} onKeyDown={onCanvasKeyDown}>
        <ReactFlowProvider>
          <ReactFlow
            nodes={liveNodes}
            edges={liveEdges}
            nodeTypes={nodeTypes}
            // The card's own <button> is the keyboard control; React Flow's
            // wrapper was a second, unnamed tab stop whose announced
            // "press Enter to select" did nothing here.
            nodesFocusable={false}
            edgeTypes={edgeTypes}
            nodesDraggable={!runLens}
            nodesConnectable={!runLens}
            elementsSelectable={!runLens}
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
            onNodeClick={(_e, n) => {
              if (n.type === 'step') onSelect(n.id)
              else if (n.type === 'rule') onSelectRule(n.id)
            }}
            onPaneClick={() => onSelect(null)}
            onEdgeClick={() => paneRef.current?.focus({ preventScroll: true })}
            snapToGrid
            snapGrid={[SNAP, SNAP]}
            onConnect={onConnect}
            onConnectEnd={onConnectEnd}
            isValidConnection={isValidConnection}
            onDelete={onDelete}
            onReconnect={runLens ? undefined : onReconnect}
            onReconnectStart={onReconnectStart}
            onReconnectEnd={onReconnectEnd}
            deleteKeyCode={null}
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
            <FitOnReady
              fitKey={fitKey}
              onReady={fitForLens}
              // Counted from what React Flow has MEASURED, not from the derived
              // graph: a refit issued the render the rule is derived runs before
              // the node exists in the store, and frames the chain without it.
              softKey={`${liveNodes.filter((n) => n.type === 'rule' && n.measured?.height).length}`}
              canSoftFit={canSoftFit}
            />
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--bd2, #4A4A4A)" />
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
                      style={{ top: b.top, height: b.h, width: Math.max(laneStageW, 1200), background: b.fill }}
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

            {built.rail && !lanesLens && (
              <ViewportPortal>
                <div
                  className="composer-rail"
                  data-testid="composer-content-rail"
                  style={{
                    position: 'absolute',
                    left: built.rail.x0,
                    top: built.rail.y,
                    width: Math.max(built.rail.x1 - built.rail.x0, 420),
                    pointerEvents: 'none',
                  }}
                >
                  <span className="composer-rail__label">Cortex Engine · tenant content</span>
                  <span className="composer-rail__sub">
                    must exist in the tenant · not part of the chain · nothing here is imported or verified
                  </span>
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
  viewInsets = null,
  // Tenant content (`composerContent.deriveTenantContent`), or null before the
  // first derivation. `contentLoading` is true while a cited card is in flight.
  content = null,
  contentLoading = false,
  showContent = true,
  onToggleContent = () => {},
  selectedRuleKey = null,
  onSelectRule = () => {},
  onExportContent = () => {},
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
    onConnectSteps,
    onDeleteStepId, onEdgeDelete, onReparent, onSetPivot, onDropCreate, onCanvasDrop,
    stitchModel, showStitch,
    storedLayout, viewInsets,
    // The rail is a toggle; the unbound marker on a step's chips is not.
    content: showContent ? content : null,
    unbound: content?.unbound || null,
    selectedRuleKey, onSelectRule,
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

        <div className="composer-canvas__content" role="group" aria-label="Tenant content">
          <button
            type="button"
            data-testid="composer-content-toggle"
            className={'canvas-view' + (showContent ? ' canvas-view--on' : '')}
            aria-pressed={showContent}
            title="Show the correlation rules this chain needs in the tenant. Derived from the cited TTP cards; nothing here is imported or verified."
            onClick={onToggleContent}
          >
            <span className="composer-canvas__content-kicker">Tenant content · </span>
            {contentHeadline(content, { loading: contentLoading || !content })}
          </button>
          <button
            type="button"
            data-testid="composer-content-export"
            className="canvas-view"
            title="Download what this chain needs in the tenant, as JSON. Read-only: nothing is sent to a tenant."
            disabled={!hasSteps}
            onClick={onExportContent}
          >
            Export
          </button>
        </div>

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
        {draft.cgo && <> · {spinePhrase(steps, validation)} · CGO {draft.cgo}</>}
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
                      Fastest path — the chains in the scenario library arrive complete with their
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
