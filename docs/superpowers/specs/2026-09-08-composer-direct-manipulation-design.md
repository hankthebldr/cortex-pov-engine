# Composer direct manipulation — draggable nodes, drawn connections, run-true graph

**Date:** 2026-09-08
**Status:** Draft — awaiting approval
**Author:** Henry Reed + Claude
**Amends:** `2026-09-04-composer-workflow-design.md` (see §2 — this document
reverses **D2** and one line of that spec's §11, and narrows its §6.5)
**Reference implementation reviewed:** `local-ai-platform` @
`api/static/index.html` (Drawflow + dagre composer)

---

## 1. Why

The Composer canvas today renders a chain but cannot be manipulated. Measured
on this tree, not asserted:

| observation | evidence |
|---|---|
| No direct manipulation anywhere in the canvas | zero `onPointerDown` / `onMouseDown` / `onDrag` / `draggable` in `ui/src/components/console/ComposerCanvas.jsx` |
| Nothing to persist a drag *into* | `grep` for `x:` / `y:` / `position` / `coord` / `layout` in `composerDraft.js` → **no matches** |
| Node positions are computed every render | nodes read `n.x` / `n.y` from `composerLayout.js::layoutChain` / `layoutCausalityGraph` |
| Chaining exists as *data*, editable only from the inspector | `composerDraft.js::setCausalityParent(steps, id, parentId, pivot)` |

So the gap is narrower than "the canvas does not chain" — the chain is modelled
and drawn, in two lenses, one of which already reads real execution truth. What
is missing is the ability to move a node and to draw an edge.

## 2. What this amends, and why the reversal is coherent

`2026-09-04-composer-workflow-design.md` chose the opposite deliberately:

- **D2** — "spine-constrained (valid causality tree, one root; never a free-form DAG)"
- **§6.5** — "the DC adds/links/reorders, **never drags** into an invalid topology"
- **§11** — "Free-form DAG topologies (rejected; spine-constrained)"

That decision conflated two separate things: **free-form topology** and
**direct manipulation**. Only the first was actually the risk. The spine
constraint is a property of the *graph*, not of the *input method* — a drag that
cannot produce an invalid graph does not threaten it.

**This amendment therefore:**

- **Reverses D2's parenthetical** as it applies to input: nodes become draggable
  and edges become drawable.
- **Keeps D2's substance intact**: the topology stays a spine-constrained
  causality tree with exactly one root. It is now enforced *at connect time*
  rather than by withholding the affordance.
- **Leaves §11's rejection of free-form DAGs standing.** A drawn edge that would
  create a cycle, a forward reference, or a second root is **refused with its
  reason displayed** — never silently dropped, and never accepted.

Everything else in the 2026-09-04 spec — D1, D3–D10, the two lenses, the launch
gate, NICE, the stitch context, the three-phase sequence — is unchanged.

## 3. The reference implementation, as actually built

`local-ai-platform` implements this with **Drawflow** (~10 KB) for the canvas and
**dagre** (~30 KB) for auto-layout, both vendored under `api/static/vendor/` and
driven imperatively from one 25 362-line `index.html`.

Architecture worth copying:

- `dfInitEditor()` → `new Drawflow(container)`; `reroute = true`;
  `reroute_fix_curvature = true`; `.start()`
- Events: `nodeSelected`, `nodeUnselected`, `nodeRemoved`,
  `connectionCreated`, `connectionRemoved`
- **Separation of ownership**: Drawflow owns geometry and connections; a side
  map `dfNodeData[id]` owns the domain payload. START/END anchor nodes are
  deliberately kept *out* of `dfNodeData`, so YAML export skips them.
- `dagre.graphlib.Graph` → `dagre.layout(g)` seeds positions on import, followed
  by a centring pass into the visible viewport.
- A **second, read-only** Drawflow instance renders the Runs DAG.
- `connectionCreated` translates an edge into domain data — `to.inputs.push(`
  `` `${from.id}.${out}` `` `)` — rather than leaving it as pure geometry.

### 3.1 Three field-proven traps, carried into this design

These are the most valuable part of the review. All three are recorded in that
file's own comments, and all three apply to CortexSim.

1. **`editor_mode = 'fixed'` silently no-ops `addConnection` in some Drawflow
   builds.** The read-only Runs graph therefore stays in `'edit'` mode and
   disables interaction through a `.readonly-canvas` CSS class that routes
   pointer-events around Drawflow's own `mousedown→pan` handler. *The obvious
   read-only API breaks edge rendering, without an error.*
2. **HTML5 drag-and-drop from the palette silently dropped events** across
   browser zoom and macOS trackpad gestures — observed in a live demo, not in
   theory. The fix shipped as `composerAddAgentAtCenter`, a click-to-add
   fallback that spawns at the visible centre of the canvas.
3. **A container initialised at 0×0 while its tab was hidden** produces
   duplicated DOM on re-init; the guard is `container.innerHTML = ''` plus
   recreate. CortexSim's Composer is a **lazily-mounted destination**, so it can
   mount hidden — this trap is live here.

### 3.2 What does *not* port

The reference is vanilla JS, CDN-loaded, with no build step. CortexSim's console
is React 18 + Vite, built by the `ui-builder` stage in `core/Dockerfile` via
`npm ci`. So:

- **The interaction model ports. The delivery does not.** CDN-loading a graph
  library would reintroduce exactly the air-gap failure the 2026-09-08 font
  survey measured (all webfonts fetched from `fonts.googleapis.com`; on a
  default-deny customer jumpbox the console silently degrades). Any library
  here must be an npm dependency bundled by Vite and served same-origin by
  SimCore.
- **Imperative mutation does not port cleanly.** `composerDraft.js` is already a
  pure-reducer module: `moveStep`, `editStep`, `setCausalityParent`,
  `addDetection` all take state and return new arrays. A canvas library holding
  its own mutable internal graph becomes a *second source of truth* fighting
  that module — which is precisely the bookkeeping the reference pays for with
  `dfNodeData`, `_idMap`, anchor re-bracketing and re-init hygiene.

## 4. Approach decision

| | A · React Flow (`@xyflow/react`) | B · Drawflow, as the reference does it | C · Extend the hand-rolled SVG canvas |
|---|---|---|---|
| state model | **controlled** — `nodes`/`edges` props + `onNodesChange`; single source of truth in React | imperative; internal mutable graph mirrored into React | controlled (already is) |
| drag / pan / zoom | built in | built in | **hand-rolled** |
| port hit-testing, edge preview while dragging | built in | built in | **hand-rolled** |
| connection validation | `isValidConnection` — native fit for the spine constraint | manual, in `connectionCreated`, *after* the edge exists | hand-rolled |
| read-only mode | `nodesDraggable` / `nodesConnectable` / `elementsSelectable` = false | the `'fixed'` trap (§3.1.1) | trivially free |
| bundle cost | largest of the three — **measure at build, do not quote from memory** | ~10 KB + ~30 KB dagre | zero |
| fidelity to what Henry has already used | same interaction model, different library | **identical** | approximate |

**Recommendation: A — React Flow (`@xyflow/react`, v12, MIT).**

Because the console is React with a controlled-state idiom throughout, and
because `isValidConnection` expresses D2's spine constraint *natively* — it
refuses the connection before it exists, which is the difference between
"validated" and "reverted". Option B's dual source of truth is not a
one-time cost; it is permanent, and the reference's own code shows its size.

**Honest counterweight:** B is the option Henry has actually operated, and it is
roughly five times smaller. If fidelity to the working demo outranks state
hygiene, B is a defensible choice and this spec's §5 survives it with `useRef` +
`useEffect` bridging instead of props. **This is the one open decision — see §9.**

Option C is rejected: it re-derives pan, zoom, port hit-testing, drag preview
and reroute curvature, all of which both libraries already solved.

## 5. Design

### 5.1 Node granularity — a step, not a simulation

**Decided 2026-09-08.** One node = one step inside one draft. Edges are that
draft's causality spine. Chaining *across* scenarios/executions — one node per
simulation, a campaign altitude — is **out of scope** (§8).

### 5.2 Where a position lives, and the trap in the way

`Scenario.steps` is a free-form `JSON` column, so the temptation is to write
`x`/`y` onto each step and ship. **That silently loses data.**

`DraftStepSchema` (`core/engine/composer_draft_schema.py:89`) extends
`StepSchema` (`core/engine/scenario_loader.py:192`), and **neither declares
`model_config`** — so Pydantic v2 defaults to `extra='ignore'`. An `x`/`y`
POSTed to `/api/scenarios/drafts` is *silently discarded*. Drag would feel
correct until reload, then quietly snap back, and every existing test would stay
green. That is this repo's own **"tolerance hides bugs"** failure (Gate A5): the
tolerant path keeps the bug.

**Design:** a draft-level `composer_layout` nullable JSON column on `Scenario`,
shaped `{ "<step_id>": {"x": <int>, "y": <int>} }`, declared explicitly on
`DraftScenarioSchema`.

Rationale:

- Follows the exact precedent of `cgo_anchor` and `stitch_context` — nullable
  JSON, added by a `_migrate_scenarios_columns`-style helper in `database.py`,
  with the prod `ALTER TABLE scenarios ADD COLUMN composer_layout JSON` noted in
  CLAUDE.md as those two are.
- Presentation state never enters `steps[]`, so it **structurally cannot** leak
  into `emitDraftYaml`. That matters: emitted YAML is meant to be dropped into
  `scenarios/<plane>/` and run through the strict loader, which would reject
  unknown step keys or, worse, carry presentation into the shipped corpus.
- Keyed by `step_id`, so reordering, duplicating and removing steps compose with
  the existing reducers without positional index bookkeeping.

**Orphan policy:** a `composer_layout` key whose `step_id` no longer exists is
dropped on load, not an error — steps are removable and a stale key is
presentation debris, not a broken reference.

### 5.3 Auto-layout stays, as the fallback and the reset

`composerLayout.js` is **kept, not replaced** — it is CortexSim's equivalent of
the reference's dagre pass, and it already encodes the spine's shape.

- A step **with** a stored position renders there.
- A step **without** one renders at its computed position.
- Therefore **every existing draft opens exactly as it does today**, and adopting
  a position is opt-in per node, per drag.
- A **`Re-layout`** control clears `composer_layout` back to computed — the
  reference's `Auto-Layout` button, and the escape hatch from a graph a DC has
  dragged into illegibility.

### 5.4 The drag itself

Pointer Events with `setPointerCapture`, never HTML5 drag-and-drop (§3.1.2).

**The zoom trap, called out because it is the likeliest bug here.** The canvas
wraps nodes in `transform: scale(${zoom})` with `transformOrigin: 'top left'`
(`ComposerCanvas.jsx:139`, `:445`). A raw pointer delta applied to a node's
position **over-moves by a factor of `zoom`** — at 200 % the node travels twice
as far as the cursor. Every position delta must be divided by the live zoom.
This gets its own test (§7).

Snap to an 8 px grid, so chains stay visually aligned and stored positions are
stable rather than sub-pixel noise.

### 5.5 Drawing a connection — where D2 is actually enforced

Dragging from a node's output handle to another's input handle calls the
**existing** `setCausalityParent(steps, id, parentId, pivot)`. No new model
action; the IO ports are already drawn by `DesignGraph`.

Validation runs **before** the edge exists, via `isValidConnection`. A
connection is refused, with its reason surfaced on the canvas, when it would:

| refusal | rule it protects |
|---|---|
| create a cycle | loader: no forward refs; a spine is acyclic |
| make a step its own parent | loader: no self refs |
| leave the draft with a second root | loader: at most one root step (the step that omits `causality`, which links from the CGO anchor) |

`pivot` defaults to `process_lineage` and is editable in the inspector, matching
`composerDraft.js::PIVOTS`. A non-`process_lineage` pivot emits its own typed
edge and leaves the step rooted at the CGO — the canvas must render that
difference, not flatten it into a process chain.

**Refusal is visible.** A silently ignored drag is indistinguishable from a
broken canvas; the DC must learn the constraint from the tool.

### 5.6 The palette

Pointer-based drag from palette to canvas, **plus** a click-to-add that spawns at
the visible centre — both, because the reference shipped the fallback only after
losing events in a live demo (§3.1.2). Palette groups are the ones the
2026-09-04 spec §6.6 already specifies; this amendment changes only how an item
reaches the canvas.

### 5.7 The Run lens stays read-only, and its emptiness is a separate question

The Run lens is **not** made editable. It renders observed execution truth;
dragging its nodes would imply authorship over a record.

Read-only is expressed declaratively (`nodesDraggable={false}`,
`nodesConnectable={false}`) rather than through a mode flag, specifically to
avoid the reference's `'fixed'` trap (§3.1.1).

**Why it currently shows the empty state is not designed here, deliberately.**
`RunGraph` is already wired end-to-end — `core/engine/causality_graph.py::`
`build_causality_graph` → `GET /api/…/causality` → `api/causality.js::`
`normalizeCausality` → `ComposerView.jsx:267` state → `:723` prop — and it
renders typed EXPECTED/CONFIRMED/BROKEN edges, `chain_completeness_pct`,
`broken_stitches[]` and the run's real persisted `stitch_binding`. Candidate
causes include fetching only for a bound run, or the observed run
(`SIM-EDR-001`) having failed without producing a graph.

**This is a debugging task, not a design task**, and it is sequenced first in the
implementation plan. Designing a fix for an unestablished cause ships a change
that fixes nothing. Its outcome may be one line or a real gap; the plan says so
rather than pretending to know.

### 5.8 Mount-hidden hygiene

The Composer is a lazily-mounted destination and can mount at 0×0 (§3.1.3). The
canvas must measure on mount and on resize (`ResizeObserver`), and must not
compute a `fitView` against a zero-sized container.

## 6. Files touched

| file | change |
|---|---|
| `ui/package.json` | + graph library (per §9 decision) |
| `ui/src/components/console/ComposerCanvas.jsx` | drag, connect, read-only Run lens, resize handling |
| `ui/src/components/console/composerDraft.js` | `composer_layout` in `draftToApi` / `draftFromApi` / `draftSnapshot` / `isDraftDirty`; orphan pruning; **no change to `emitDraftYaml`** |
| `ui/src/components/console/composerLayout.js` | positions become fallback, not authority |
| `ui/src/components/console/ComposerView.jsx` | position state + save wiring, `Re-layout` action |
| `core/engine/composer_draft_schema.py` | explicit `composer_layout` on `DraftScenarioSchema` |
| `core/models.py` | `composer_layout` nullable JSON column + `to_dict()` |
| `core/database.py` | migration helper, per the `cgo_anchor` / `stitch_context` precedent |
| `core/api/drafts.py` | carry the field through create / read / replace |
| `CLAUDE.md` | record the prod `ALTER TABLE`, as the other two columns are |

## 7. Testing

Gate A requires **proof the new guard fails without the fix**. The first test
below does, by construction — `extra='ignore'` discards the position today.

**Backend**
1. A position POSTed to `/api/scenarios/drafts` survives `GET`. *Fails on
   today's tree — this is the fail-without-fix proof.*
2. `PUT` full-replace preserves positions for unchanged steps.
3. A `composer_layout` key for a removed step is pruned, not an error.
4. Corpus `StepSchema` is **unchanged** — a shipped scenario cannot carry
   presentation data.

**Frontend**
5. A pointer delta of 100 px at `zoom = 2` moves the node **50** units (§5.4).
6. Positions snap to the 8 px grid.
7. A step with no stored position renders at its `composerLayout.js` position —
   the back-compat guard for every existing draft.
8. `Re-layout` clears stored positions.
9. `emitDraftYaml` output contains **no** layout keys (regex guard).
10. A cycle-forming connection is refused **and** its reason is rendered.
11. A connection producing a second root is refused with its reason.
12. A non-`process_lineage` pivot renders a typed edge, not a process-chain edge.
13. The Run lens renders nodes but exposes no drag or connect affordance.

**Live — because jsdom cannot prove a canvas**
14. Rebuild the image, then in the browser pane: drag a node, reload, position
    holds. Same evidence standard as the 2026-09-08 header-mark verification.

## 8. Out of scope

- **The campaign altitude** — one node per simulation, edges across executions.
  Explicitly considered and deferred on 2026-09-08; it needs a new persisted
  entity, a new API surface, and a definition of what "feeds" means between two
  scenarios (ordering? shared entity? an observed causality join?).
- **Free-form DAG topologies** — still rejected, per the 2026-09-04 §11 line
  this amendment leaves standing.
- **Making the Run lens editable.**
- **Replacing `composerLayout.js` with dagre.** The existing layout encodes the
  spine; swapping it is a separate change with its own visual diff.
- Everything the 2026-09-04 spec §11 already excludes.

## 9. Open decisions

| # | decision | recommendation |
|---|---|---|
| O1 | **React Flow (`@xyflow/react`) or Drawflow?** State hygiene and native connection validation vs. fidelity to the demo Henry has operated and ~5× smaller bundle. | React Flow — but this is a genuine call, and §5 survives either. |
| O2 | Does `Re-layout` need an undo, or is re-dragging enough? | Re-dragging; add undo only if asked for. |
| O3 | Should a DC be able to drag the START/END anchors? | No — they are derived, not authored. |
