/**
 * ComposerCanvas — the centre column of the Simulation Composer.
 *
 * It owns everything between the palette and the inspector: the canvas head
 * (scenario id/name + a Chain/YAML view toggle + a Design/Run LENS toggle), the
 * spine-constrained graph, the YAML projection, the first-run empty state and
 * the origin-error banner.
 *
 * It is a VIEW: it never fetches and never mutates the draft. Structure edits
 * arrive as callbacks (`onAddStep`, `onMoveStep`, …); selection and view/lens
 * changes arrive as callbacks too. The one piece of state it owns is the zoom
 * level, which is presentation, not data.
 *
 * TWO LENSES, ONE HONESTY RULE
 * ----------------------------
 *  - **Design** lays the DRAFT out with `layoutChain` — a spine-constrained
 *    tree (one root, no forward refs; the loader and `setCausalityParent`
 *    guarantee it), node cards tinted by PLANE, START/END anchors, curved SVG
 *    spine edges with IO ports.
 *  - **Run** renders the REAL `build_causality_graph` via `layoutCausalityGraph`
 *    — nodes and typed edges carrying their real `EXPECTED | CONFIRMED | BROKEN`
 *    state, the `chain_completeness_pct`, and the `broken_stitches` list. It
 *    NEVER invents a state and NEVER upgrades a BROKEN stitch to CONFIRMED; with
 *    no run yet it says so ("EXPECTED only"), rather than drawing a green chain
 *    the tenant never actually correlated.
 *
 * Geometry is imported from `composerLayout.js` so the on-screen spine and the
 * layout tests read from ONE source of truth.
 *
 * DESIGN LENS RENDERING (2026-09-08 direct-manipulation Task 8) — the step
 * cards render through React Flow (`@xyflow/react`) instead of a hand-rolled
 * absolutely-positioned SVG canvas. This pass is RENDER ONLY: node dragging
 * (Task 9) and connect-by-drag (Task 10) come later, which is why
 * `nodesDraggable`/`nodesConnectable` are false below and the `Handle`s on
 * `StepNode` currently do nothing but sit there for Task 10 to wire up.
 * START and END stay plain, non-draggable anchors OUTSIDE the React Flow
 * graph — they are not steps, so "one node per step" holds through React
 * Flow's own node count.
 */
import React, { useMemo, useState } from 'react'
import { ReactFlow, ReactFlowProvider, Background, Controls, Handle, Position } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  layoutChain,
  layoutCausalityGraph,
  stitchOverlayEdges,
  mergeStoredPositions,
} from './composerLayout.js'
import { effectiveChannel } from './composerDraft.js'

// Detection-type → chip tone. Identical mapping to the inspector's `detTone`
// (they are two new files; the mapping is duplicated deliberately rather than
// creating a shared import surface for one three-line function).
function detTone(type) {
  const t = String(type || '').toUpperCase()
  if (t === 'BIOC') return 'detected'
  if (t === 'XQL' || t === 'ANALYTICS') return 'signal'
  return 'pending'
}

// NICE-organized plane tint. The reference colours nodes by role; here a node's
// colour is its detection plane (the NICE analogue). This is a CATEGORICAL
// (qualitative) palette on purpose — 16 planes need 16 distinguishable hues, and
// there is no semantic token for "NDR blue vs ITDR amber". Neutral steel is the
// fallback so an unplaned draft still reads. (Semantic colours — run state and
// node kind, below — are theme tokens, not fixed hex, so they track the theme.)
const PLANE_TINT = {
  EDR: '#00C0E8', CDR: '#12b886', NDR: '#4c6ef5', ITDR: '#f59f00',
  CLOUD_APP: '#7048e8', ANALYTICS: '#e64980', AI_ACCESS: '#ae3ec9',
  AIRS: '#9c36b5', AI_SPM: '#845ef7', BROWSER: '#0ca678', KOI: '#e8590c',
  ASM: '#1098ad', CSPM: '#2f9e44', TIM: '#f76707', EMAIL: '#495057',
  DLP: '#c2255c',
}

// Run-lens edge state → colour, driven by the theme's semantic tokens so it
// tracks light/dark and the console's teal→green remap (a hardcoded #00C0E8
// would render cyan where the console now shows green). BROKEN is danger on
// purpose; it is the state a POV readout must not hide.
const STATE_TINT = {
  CONFIRMED: 'var(--cortex-success, #2f9e44)',
  BROKEN: 'var(--cortex-danger, #e03131)',
  EXPECTED: 'var(--cortex-steel, #6B7E8E)',
}

// Run-lens node kind → colour, mirroring the builder's column semantics, also
// token-driven for the same reason.
const KIND_TINT = {
  cgo: 'var(--ink, #003366)', process: 'var(--ac, #00C0E8)',
  wrapper: 'var(--cortex-steel, #6B7E8E)',
  exposure: 'var(--cortex-warning, #f59f00)',
  alert: 'var(--cortex-success, #2f9e44)',
}

/** Tint a design step by its first detection's plane, else the draft's plane. */
function stepPlane(step, draft) {
  return step?.detections?.[0]?.plane || draft?.plane || null
}

/** Curved connector between two resolved ports. Vertical spine or horizontal. */
function edgePath(from, to, orientation) {
  if (!from || !to) return ''
  if (orientation === 'horizontal') {
    const dx = Math.max(30, Math.abs(to.x - from.x) / 2)
    return `M ${from.x} ${from.y} C ${from.x + dx} ${from.y} ${to.x - dx} ${to.y} ${to.x} ${to.y}`
  }
  const dy = Math.max(20, Math.abs(to.y - from.y) / 2)
  return `M ${from.x} ${from.y} C ${from.x} ${from.y + dy} ${to.x} ${to.y - dy} ${to.x} ${to.y}`
}

// ─── Design lens ──────────────────────────────────────────────────────────────

/**
 * One STEP, as a React Flow node. START and END are NOT React Flow nodes (see
 * the file-header note) — this component only ever receives `data.kind ===
 * 'step'`. Reuses the existing `.chain-node` / `.chain-node__*` classes and
 * every existing testid/aria-label byte-for-byte, so the DOM the ComposerView
 * and ComposerCanvas tests already pin is unchanged apart from the React Flow
 * wrapper around it. `Handle`s are the connect affordance Task 10 wires up —
 * inert for now because `nodesConnectable={false}` on the parent `<ReactFlow>`.
 */
function StepNode({ data }) {
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
      <Handle type="target" position={Position.Top} />
      <button
        type="button"
        className="chain-node__body"
        onClick={data.onSelectStep}
        aria-pressed={data.selected}
        data-testid={`chain-step-${s.id}`}
      >
        <span className="chain-node__row">
          <span className="chain-node__kind">{s.authored ? 'new' : 'step'}</span>
          <span className="chain-node__id mono">{s.id}</span>
          {/* Channel badge — GATED so an agent-default node (no channel,
              no target) renders byte-identically to today. An eal step
              shows its emitter; an agent step with a second endpoint
              shows where it runs. Reuses existing chip tones (no hex). */}
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
          {data.runState && (
            <span
              className="chain-node__runbadge"
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: STATE_TINT[data.runState],
                letterSpacing: '0.04em',
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
            /* Not decoration — the on-canvas marker for the step that
               becomes a GAP in the POV readout. */
            <span className="chip chip--missed">no expected detection</span>
          )}
        </span>
      </button>
      <div className="chain-node__tools">
        <button type="button" title="Move earlier" aria-label={`Move ${s.id} earlier`}
          onClick={data.onMoveEarlier}>↑</button>
        <button type="button" title="Move later" aria-label={`Move ${s.id} later`}
          onClick={data.onMoveLater}>↓</button>
        <button type="button" title="Duplicate step" aria-label={`Duplicate ${s.id}`}
          onClick={data.onDuplicate}>⧉</button>
        <button type="button" title="Remove step" aria-label={`Remove ${s.id}`}
          onClick={data.onRemove}>×</button>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

const nodeTypes = { step: StepNode }

function DesignGraph({
  draft, steps, selectedId, onSelect, lens, causalityStates,
  tenantName, agentName, onNavigate,
  onMoveStep, onDuplicateStep, onRemoveStep, onAddStep,
  stitchModel = null, showStitch = false, storedLayout = null,
}) {
  const layout = useMemo(
    () => layoutChain({ ...draft, steps }),
    [draft, steps],
  )
  // Overlay any DC-dragged positions (Task 9) onto the computed layout. A
  // draft that has never been touched (`storedLayout` null/empty) round-trips
  // byte-identically — see `mergeStoredPositions`'s doc comment.
  const laidOut = useMemo(
    () => mergeStoredPositions(layout, storedLayout),
    [layout, storedLayout],
  )
  const { bounds } = laidOut
  const runLens = lens === 'run'

  const stepLaidOutNodes = useMemo(
    () => laidOut.nodes.filter((n) => n.kind === 'step'),
    [laidOut],
  )
  const stepIds = useMemo(
    () => new Set(stepLaidOutNodes.map((n) => n.id)),
    [stepLaidOutNodes],
  )

  // React Flow's node/edge shape. START and END are excluded on purpose (see
  // the file-header note) — "one node per step" is the contract Task 8's own
  // test pins.
  const rfNodes = useMemo(
    () => stepLaidOutNodes.map((n) => {
      const s = n.step
      const i = steps.indexOf(s)
      const plane = stepPlane(s, draft)
      const channel = effectiveChannel(s)
      return {
        id: n.id,
        type: 'step',
        position: { x: n.x, y: n.y },
        draggable: false,
        connectable: false,
        data: {
          step: s,
          order: (i < 0 ? 0 : i) + 1,
          selected: selectedId === s.id,
          tint: PLANE_TINT[plane] || 'var(--cortex-steel, #6B7E8E)',
          runState: runLens ? (causalityStates?.[s.id]?.state || 'EXPECTED') : null,
          channelKind: channel === 'eal' ? 'eal' : (s.target ? 'target' : null),
          onSelectStep: () => onSelect(s.id),
          onMoveEarlier: () => onMoveStep(i, -1),
          onMoveLater: () => onMoveStep(i, 1),
          onDuplicate: () => onDuplicateStep(i),
          onRemove: () => onRemoveStep(i),
        },
      }
    }),
    [
      stepLaidOutNodes, steps, draft, selectedId, runLens, causalityStates,
      onSelect, onMoveStep, onDuplicateStep, onRemoveStep,
    ],
  )

  // Root (start→step) and terminal (leaf→end) edges reference START/END,
  // which are not React Flow nodes here — an edge to a node React Flow
  // doesn't have is simply dropped, so only the real inter-step causality
  // edges are worth constructing.
  const rfEdges = useMemo(
    () => laidOut.edges
      .filter((e) => stepIds.has(e.source) && stepIds.has(e.target))
      .map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: 'default',
        style: {
          stroke: 'var(--ac, #00C0E8)',
          strokeWidth: 1.5,
        },
      })),
    [laidOut, stepIds],
  )

  // Design-lens entity-join overlay: the stitch edges the context IMPLIES,
  // EXPECTED-only (authored intent, never outcome). Off by default; drawn as a
  // distinct dashed secondary layer offset to the right of the spine.
  const stitchEdges = useMemo(
    () => (showStitch ? stitchOverlayEdges(steps, stitchModel) : []),
    [showStitch, steps, stitchModel],
  )

  return (
    <div className="chain composer-canvas__graph" data-testid="composer-chain">
      {/* START anchor — not a step, so it stays outside React Flow. */}
      <div className="chain-node chain-node--start" data-testid="chain-start">
        <div className="chain-node__kicker">Start</div>
        <div className="chain-node__title">On launch</div>
        <div className="chain-node__scope">
          <button type="button" className="scope-link" onClick={() => onNavigate('tenants')}>
            <span className="scope-link__label">Tenant</span>
            <span className="scope-link__value mono">{tenantName || 'none selected'}</span>
          </button>
          <button type="button" className="scope-link" onClick={() => onNavigate('agents')}>
            <span className="scope-link__label">Agent</span>
            <span className="scope-link__value mono">{agentName || 'none selected'}</span>
          </button>
        </div>
      </div>

      <div className="composer-canvas__flow">
        <ReactFlowProvider>
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            panOnDrag={false}
            fitView
            proOptions={{ hideAttribution: false }}
          >
            <Background />
            <Controls />
          </ReactFlow>
        </ReactFlowProvider>

        {/* Stitch overlay — additive, EXPECTED-only dashed entity-join edges the
            context implies. STATE_TINT.EXPECTED (never CONFIRMED/BROKEN; those
            belong to the Run lens). Off unless showStitch && a model. Drawn
            in the layout's own coordinate space, independent of React Flow's
            pan/zoom — an advisory annotation, not a pixel-locked overlay. */}
        {stitchEdges.length > 0 && (
          <svg
            width={bounds.width}
            height={bounds.height}
            data-testid="composer-stitch-overlay"
            style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}
            aria-hidden="true"
          >
            {stitchEdges.map((se) => {
              // Bulge to the right of the spine so the join reads as a second layer.
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
        )}
      </div>

      {/* END anchor — not a step, so it stays outside React Flow. */}
      <div className="chain-node chain-node--end" data-testid="chain-end">
        <div className="chain-node__kicker">End</div>
        <div className="chain-node__title">Teardown &amp; proof</div>
        <div className="chain-node__sub mono">
          {draft.teardown?.length
            ? `${draft.teardown.length} cleanup command${draft.teardown.length === 1 ? '' : 's'}`
            : 'no cleanup declared'}
        </div>
      </div>

      <button
        type="button"
        className="chain-node chain-node--add"
        onClick={onAddStep}
        data-testid="composer-add-step"
      >
        + Add step
      </button>
    </div>
  )
}

// ─── Run lens ─────────────────────────────────────────────────────────────────

function RunGraph({ causalityGraph, zoom, binding = null }) {
  const summary = causalityGraph?.causality_summary || {}
  // The REAL resolved 5-tuple/UPN/host/CI/cloud-resource the run used, persisted
  // on the run (runs.stitch_binding). Quoted verbatim — Gate A5: real values,
  // nothing invented. Absent for a run whose scenario had no stitch context.
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
    // Honesty: no run has produced observations. EXPECTED only — never a
    // fabricated CONFIRMED.
    return (
      <div
        className="composer-canvas__runempty"
        data-testid="composer-run-graph"
        style={{
          border: '1px dashed var(--bd, #c1ccd6)', borderRadius: 8, padding: '18px 20px',
          color: 'var(--cortex-steel, #6B7E8E)', fontSize: 13,
        }}
      >
        <strong style={{ color: 'var(--ink, #003366)' }}>No run yet — EXPECTED only.</strong>
        {' '}Launch this chain (or reconcile an observed run) and the real
        causality graph will render here with CONFIRMED / BROKEN edges. Nothing on
        this canvas is inferred before a run exists.
      </div>
    )
  }

  return (
    <div data-testid="composer-run-graph" className="composer-canvas__rungraph">
      <div
        className="composer-canvas__runbar"
        style={{
          display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap',
          marginBottom: 12, fontSize: 12,
        }}
      >
        <span style={{ fontWeight: 700, color: 'var(--ink, #003366)' }}>
          chain completeness:{' '}
          {typeof completeness === 'number' ? `${completeness}%` : 'not measured'}
        </span>
        {broken.length > 0 ? (
          <span style={{ color: 'var(--cortex-danger, #e03131)', fontWeight: 600 }} data-testid="composer-broken-stitches">
            {broken.length} broken stitch{broken.length === 1 ? '' : 'es'}: {broken.join(', ')}
          </span>
        ) : (
          <span style={{ color: 'var(--cortex-success, #2f9e44)' }}>no broken stitches</span>
        )}
      </div>

      {bindingEntries.length > 0 && (
        <div
          className="composer-canvas__binding"
          data-testid="composer-stitch-binding"
          style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12, fontSize: 11 }}
        >
          <span style={{ fontWeight: 700, color: 'var(--ink, #003366)' }}>
            resolved binding:
          </span>
          {bindingEntries.map(([k, v]) => (
            <span key={k} className="mono composer-canvas__binding-pair">
              {k}=<strong>{String(v)}</strong>
            </span>
          ))}
        </div>
      )}

      <div
        style={{
          position: 'relative',
          width: bounds.width * zoom,
          height: bounds.height * zoom,
          minHeight: 80,
        }}
      >
        <div
          style={{
            position: 'absolute', top: 0, left: 0,
            width: bounds.width, height: bounds.height,
            transform: `scale(${zoom})`, transformOrigin: 'top left',
          }}
        >
          <svg
            width={bounds.width}
            height={bounds.height}
            style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }}
            aria-hidden="true"
          >
            {edges.map((e) => {
              const stroke = STATE_TINT[e.state] || 'var(--cortex-steel, #6B7E8E)'
              return (
                <g key={e.id}>
                  <path
                    d={edgePath(e.from, e.to, 'horizontal')}
                    fill="none"
                    stroke={stroke}
                    strokeWidth={e.state === 'BROKEN' ? 2 : 1.5}
                    strokeDasharray={e.state === 'EXPECTED' ? '5 4' : undefined}
                  >
                    {/* Always a text equivalent: colour + stroke width alone are
                        a colour-blind failure for CONFIRMED vs BROKEN. */}
                    <title>
                      {`${e.kind || 'edge'} · ${e.state}`}
                      {e.rationale ? `: ${e.rationale}` : ''}
                    </title>
                  </path>
                </g>
              )
            })}
          </svg>

          {nodes.map((n) => {
            const tint = KIND_TINT[n.kind] || 'var(--cortex-steel, #6B7E8E)'
            const label = n.node?.label || n.node?.name || n.id
            return (
              <div
                key={n.id}
                className={`chain-node chain-node--run chain-node--kind-${n.kind}`}
                data-node-kind={n.kind}
                style={{
                  position: 'absolute', left: n.x, top: n.y, width: n.w,
                  minHeight: n.h, boxSizing: 'border-box',
                  borderLeft: `4px solid ${tint}`, background: 'var(--s1, #fff)',
                  border: '1px solid var(--bd, #dde3e8)', borderRadius: 6, padding: '8px 10px',
                  fontSize: 12,
                }}
              >
                <div style={{ textTransform: 'uppercase', fontSize: 9, color: tint, fontWeight: 700, letterSpacing: '0.05em' }}>
                  {n.kind}
                </div>
                <div className="mono" style={{ fontSize: 11, wordBreak: 'break-all' }}>{label}</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ─── Root ─────────────────────────────────────────────────────────────────────

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
  onStartLibrary = () => {},
  onStartTtp = () => {},
  onStartBlank = () => {},
  onNavigate = () => {},
  // ── Phase-2 Stitch Context (additive, defaulted — overlay OFF by default so
  //    every existing ComposerCanvas.test assertion stays green) ──
  stitchModel = null,
  showStitch = false,
  onToggleStitch = () => {},
  // Stored per-node canvas positions (`draft.layout`, Task 5/6) — additive
  // and defaulted so a draft that has never had a node dragged renders
  // byte-identically. Task 9 is what actually writes to this map; Task 8
  // only wires it through to the lens that reads it.
  storedLayout = null,
}) {
  const [zoom, setZoom] = useState(1)
  const runLens = lens === 'run'
  const hasSteps = steps.length > 0

  const zoomOut = () => setZoom((z) => Math.max(0.5, Math.round((z - 0.1) * 10) / 10))
  const zoomIn = () => setZoom((z) => Math.min(1.6, Math.round((z + 0.1) * 10) / 10))
  const zoomReset = () => setZoom(1)

  return (
    <section className="composer-canvas" aria-label="Chain canvas">
      <div className="composer-canvas__head">
        <span className="mono composer-canvas__id">{draft.originId || 'no scenario'}</span>
        <span className="composer-canvas__name">{draft.name || 'Open a scenario, or add a step'}</span>
        <span className="composer__spacer" />

        {/* Lens toggle — Design edits structure, Run renders the real graph. */}
        <div className="composer-canvas__lens" role="group" aria-label="Canvas lens">
          {[['design', 'Design'], ['run', 'Run']].map(([id, label]) => (
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

        {/* Stitch overlay toggle — a design-lens entity-join layer. Sits beside
            the lens toggle; EXPECTED-only intent, never a run outcome. */}
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

        {/* Zoom controls — presentation-only, act on the graph layers. */}
        {canvasView !== 'yaml' && (
          <div className="composer-canvas__zoom" role="group" aria-label="Zoom" style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
            <button type="button" className="canvas-view" aria-label="Zoom out" onClick={zoomOut}>−</button>
            <button type="button" className="canvas-view" aria-label="Reset zoom" onClick={zoomReset}>{Math.round(zoom * 100)}%</button>
            <button type="button" className="canvas-view" aria-label="Zoom in" onClick={zoomIn}>+</button>
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
              draft={draft}
              steps={steps}
              selectedId={selectedId}
              onSelect={onSelect}
              lens={lens}
              causalityStates={causalityStates}
              tenantName={tenantName}
              agentName={agentName}
              onNavigate={onNavigate}
              onMoveStep={onMoveStep}
              onDuplicateStep={onDuplicateStep}
              onRemoveStep={onRemoveStep}
              onAddStep={onAddStep}
              stitchModel={stitchModel}
              showStitch={showStitch}
              storedLayout={storedLayout}
            />
          )}

          {hasSteps && runLens && (
            <>
              <RunGraph causalityGraph={causalityGraph} zoom={zoom} binding={activeRun?.stitch_binding || null} />
              {/* The authored spine stays visible under the Run lens, each card
                  badged with its REAL run status (default EXPECTED before a run
                  produces observations) so the DC sees intent against outcome. */}
              <DesignGraph
                draft={draft}
                steps={steps}
                selectedId={selectedId}
                onSelect={onSelect}
                lens={lens}
                causalityStates={causalityStates}
                tenantName={tenantName}
                agentName={agentName}
                onNavigate={onNavigate}
                onMoveStep={onMoveStep}
                onDuplicateStep={onDuplicateStep}
                onRemoveStep={onRemoveStep}
                onAddStep={onAddStep}
                storedLayout={storedLayout}
              />
            </>
          )}
        </>
      )}
    </section>
  )
}
