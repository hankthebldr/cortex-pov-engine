# Composer mode — full-screen node-graph editor (2026-10-21)

Status: **built and verified** on `claude/composer-mode-node-graph-35dba8`
(cut from `main` @ e6f4f5e). Supersedes the *layout* of the 2026-09-08
direct-manipulation spec. Its data rules (one spine, honesty, what persists)
are unchanged; this spec tightens two of them (§6).

## 1. Problem

The Composer was a window inside a window. Opening it kept the 202px nav rail,
the Library tab strip and five stacked rows of its own (title, Proves bar,
palette | canvas | inspector grid, workstream tabs, execution timeline) around
a fixed 620px React Flow box in an `overflow:auto` column. At 1024×768 the
visible canvas was 822×299. Measured on the served build: cards overlapped by
44–61px (a 136px layout stride under 180–197px cards), drags had no live
preview (a fully controlled pane dropped every intermediate change), selection
and the Delete key were inert, START/END were DOM cards outside the graph, and
every new edge was written as `process_lineage` with no way to change it.

## 2. What composer mode is

```
┌ ConsoleHeader (+ consent / readiness banners — never hidden) ─────────────┐
├ ← Exit │ Simulation Composer · from SIM-… · unsaved edits │ Load Save … Launch ┤ workflow bar
├ Proves: TC · UC · plane · techniques ……………………………… ● Chain valid / incomplete ┤
├ SIM-… name ………………………… Design Lanes Run · Stitch overlay · Chain YAML · − % + Fit ┤ canvas chrome
│ [＋ Add]                     ( n steps · spine · CGO … )              [Inspector] │
│                                                                                 │
│  ◖ Trigger·CGO ─▶ [step-01] ─▶ [step-02] ┄network_session┄▶ [step-03] ─▶ Proof ◗ │ the stage:
│                                   ┆ (warning dash)                              │ React Flow,
│                              [step-06  OFF-CHAIN]                     ▢ minimap │ edge to edge
│ ⊕⊖⛶                          ( + Add step )                                     │
├ Payload plan · Preflight · Active run · History (dock, collapsible) ────────────┤
```

* **Entering.** The `composer` destination declares `immersive: true`. While
  it is open the shell (`AppShell`) keeps the header and its banners but does
  not mount the nav rail, AppConsole skips the Library tab strip, and the view
  loses its padding. Immersive is derived from the route and never persisted,
  so no other page can inherit it.
* **The stage is the graph.** The palette (node creator), the inspector and the
  workstream are overlay drawers on top of the canvas, not grid tracks beside
  it. Both drawers start **closed**; `＋ Add` opens the palette, selecting a
  step (or opening the workflow meta) opens the inspector.
* **Leaving.** `← Exit` in the workflow bar, or **Esc twice**. The Esc cascade
  peels one layer per press — inspector, then selection, then palette — and
  only then arms "Press Esc again to exit". A single stray Esc never exits.
  With unsaved edits either path asks first ("Leaving the Composer discards
  them"). Exit returns to the last ordinary page, falling back to `library`.

## 3. The graph model (`composerGraph.js`, pure)

| Node | Source | Persisted? | Deletable |
|---|---|---|---|
| `cgo` — trigger | `draft.cgo` (cgo_anchor) + launch tenant/agent | position: derived | no |
| `step` | one per `steps[]` | position: `composer_layout` | yes |
| `proof` — terminal | `draft.teardown` | position: derived | no |

| Edge | Meaning | Persisted? |
|---|---|---|
| `causality` parent → child | `steps[child].causality {parent_step, pivot}` | **yes** — the only one |
| `root` cgo → step | the step has no parent | derived |
| `terminal` leaf → proof | nothing parents the step | derived |

Layout runs left to right in topological columns (`GRAPH_LAYOUT`: 220px cards,
280px pitch), so cards never overlap. A stored position overrides a step's
slot; the trigger and terminal are recomputed every build, which is why the
n8n look needs no new schema field (`composer_layout` prunes non-step keys).

## 4. Gestures

| Gesture | Effect |
|---|---|
| drag a card's grip | live preview; position committed on drop (8px grid) |
| drag right port → card | new `process_lineage` edge, refused with a visible reason by `canConnect` |
| release a connection on empty canvas | from an OUTPUT port only: create a step there, parented to the source card |
| release a connection on a card | connect if `canConnect` allows (no port needed), else show its reason |
| drag a palette item onto the canvas | step kinds / TTP cards become steps at the drop point; others reuse their click action |
| click an edge | select it: pivot chip + delete button |
| click the pivot chip | cycle the seven backend pivots (non-lineage pivots are dashed, signal-coloured) |
| Delete / Backspace on a step, or its card `×` | remove it; its children re-attach to its parent, pivots kept. One delete for every gesture; the key acts only with focus inside the graph |
| Delete on an edge / its × | the child loses its parent (becomes a root — flagged if off-chain) |
| drag an edge's TAIL | re-parent the same child, pivot kept (the head is fixed, so a drop cannot take another step's child) |
| card `↑` / `↓` | reorder siblings; a move that puts a step before its parent is refused with the reason |
| `+ Add step` | append after the selected step, else after the last leaf |

The canvas is **semi-controlled**: the draft stays the source of truth, but
React Flow's own changes (dimensions, in-flight drag, selection) are applied to
local state and re-synced from the derived graph. That is what makes drags
follow the pointer, the MiniMap render (it only draws measured nodes) and
edges selectable. The viewport fits once per lens, after every node is
measured (`FitOnReady`), framed into the region the open drawers leave visible.

## 5. Lenses

Design (above), **Lanes** (swimlanes by ingestion door; steps only, LAUNCH /
PROOF bands stand in for the anchors; drag between lanes retargets) and
**Run** (the real `build_causality_graph`, EXPECTED | CONFIRMED | BROKEN passed
through, over a read-only spine whose cards show each step's real run state;
no handles, no drag, no delete).

## 6. Honesty rules this tightens

1. **An off-chain step is said out loud.** Once any step declares a parent the
   loader allows exactly one root. A node canvas makes a second root one drag
   away, and it used to read "Chain valid" until a prose 422 at save.
   `spineProblems()` now feeds both `validateDraft` (named problem, shown
   first) and the graph (an **off-chain** chip and a warning-dashed trigger
   edge). A legacy star with no declared causality may keep several roots.
2. **An in-place edit is an edit.** `edited` decides whether Launch runs the
   ORIGIN corpus row or the SAVED draft, and it compared step ids only — a
   pivot, command or re-parent left it false, so Launch would have run
   something other than the canvas. It now compares the launch-relevant
   snapshot (minus `layout` / `laneOverrides`; a drag never forces a save).
3. **Delete never strands children.** React Flow reports a deleted node's
   attached edges before the node; handling them as edge deletions cleared the
   children's parents first. One `onDelete` lets the node own its edges.
4. **A step before its parent is named.** The loader rejects a parent at a
   later index; `spineProblems().forwardRefs` names it, and the spine repair
   (`topologicallySortSteps`) leaves a valid array untouched and moves only
   the steps it must, so adding a step never reorders a branching chain.
5. **Launch runs only what validates.** On the saved-row path Launch also
   requires `validateDraft().ok`: the server's chain check accepts the
   `# configure the command for this step` placeholder, which would run a shell comment and
   report its detection as missed.
6. **The Run lens never lends a verdict.** A step that changed since the run
   reads "not in run"; Run-lens edges carry no edit callbacks.
7. **A fork carries what it read.** `external_tools` drives the launch consent
   gate and shelf staging; a fork that dropped it still ran the gated tool
   (its `{adapter:…}` command resolves from the catalog) without either. Every
   scenario, step and detection field now round-trips; all 176 loaded
   scenarios' fork bodies validate against `DraftScenarioSchema`.
8. **Leaving says only what is true.** An untouched chain exits at once;
   unsaved edits ask first with focus on Stay; opening a library scenario over
   edits asks before replacing them.

## 7. Guards

* Unit (vitest): `composerGraph.test.js` (node/edge set, layout, off-chain,
  reparent), `composerDraft.test.js` (spine problems + legacy-star control),
  `ComposerCanvas.test.jsx` (anchors as nodes, LTR ports, one delete handler),
  `ComposerView.test.jsx` (drawers, drag source, in-place edit forces a save
  with a layout-drag negative control, add-step parenting, delete re-parents),
  shell: `AppShellImmersive`, `immersiveReturn`, `immersive-shell` (cascade).
  `ComposerCanvas.gestures.test.jsx` drives the real handlers React Flow would
  call (refused connect/reconnect, create-on-drop, tail-only reconnect).
  Each new guard was shown failing with its fix reverted (review fix pass: 20
  component tests against the previous canvas/view, plus the model's own).
* Real browser (Playwright): `tests/e2e/09-composer-mode.spec.ts` — six widths
  1920 → 845×620: immersive, no rail/tabs, stage reaches the window bottom,
  no document scroll, graph fitted; tab-in / Exit round trip with a negative
  control. Red against main's bundle, green here. The review fix pass added 13
  layout/a11y checks (drawers not empty at ≤1150px, Run band clear of the
  floating tabs, open dock stops the drawers above it, translucent overview
  mask, `+ Add step` clear of the inspector, ≤1100px offsets, dot-grid token,
  visible focus on cards and edges), each red against the previous stylesheet.
  09 is 20 tests; 08 viewport matrix unchanged.

## 8. Deliberately not done (follow-ups)

* **Every component as a free node** (tools, payloads, detections, targets, EAL
  plugins with their own positions and edges) needs a backend change:
  `composer_layout` prunes non-step keys and `composer_lanes` is already
  silently dropped by `DraftScenarioSchema`. In this pass they attach to a step
  (badges, inspector, palette actions).
* **Navigation guard.** Exit and Esc confirm on unsaved edits, but header chips,
  ⌘K and cross-surface links still unmount the Composer silently (pre-existing).
* **Skip link** writes `#cortexsim-main` into the hash router and unmounts the
  current page (pre-existing; affects composer mode like every page).
* **Undo/redo, copy/paste, sticky notes** — no schema home yet.
* **Backend `_chain_valid` accepts the placeholder command.** The client now
  refuses Launch; the server should reject `# configure the command for this step` too.
* **Carried fields that go stale.** `platform_variants` is carried as-is, so
  editing a step's main command leaves the old per-OS variant in the `.ps1`
  bundle; a re-saved draft keeps the `mitre_technique` the server derived
  instead of re-deriving it. The Inspector should flag both.
* **`tc_refs` is sent only while it still contains the draft's `tc_ref`**, so
  rebinding a fork does not credit the run to the origin's test cases.
