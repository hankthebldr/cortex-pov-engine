# Composer Direct Manipulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Composer canvas directly manipulable — drag step nodes, draw causality edges by pointer, persist positions — without weakening the spine-constrained topology invariant.

**Architecture:** Node positions become draft state in a new nullable `composer_layout` JSON column on `Scenario`, keyed by `step_id`, following the `cgo_anchor` / `stitch_context` precedent exactly. `composerLayout.js` demotes from authority to fallback, so every existing draft opens unchanged. The canvas render moves to React Flow, whose `isValidConnection` refuses an illegal edge *before it exists* — which is how D2's one-root acyclic spine survives becoming draggable.

**Tech Stack:** React 18, Vite, `@xyflow/react` v12 (MIT), vitest + @testing-library/react, FastAPI, SQLAlchemy async, Pydantic v2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-composer-direct-manipulation-design.md`

## Global Constraints

- **O1 is settled as React Flow (`@xyflow/react` v12) for this plan.** Choosing Drawflow instead replaces **Tasks 7–11 only**; Tasks 1–6 and 12 are library-independent. Do not silently switch.
- **No CDN.** Every dependency is an npm package bundled by Vite and served same-origin by SimCore. CDN-loading reintroduces the air-gap failure measured on 2026-09-08 (all webfonts fetched from `fonts.googleapis.com`; default-deny customer jumpboxes degrade silently).
- **The spine invariant is non-negotiable.** Exactly one root step; no cycles; no forward or self references. An illegal connection is **refused with its reason rendered** — never silently dropped, never accepted-then-reverted.
- **Presentation state never enters `steps[]`.** It lives only in `composer_layout`. `emitDraftYaml` output must stay loader-clean, because that YAML is meant for `scenarios/<plane>/`.
- **Corpus `StepSchema` (`core/engine/scenario_loader.py:192`) must not change.** A shipped scenario cannot carry canvas coordinates.
- **A zero is degraded, not ok** and **tolerance hides bugs** (Gate A5). An unrecognised shape raises; it does not read as empty.
- Backend tests: `make test-backend` (pytest inside the built prod image). UI tests: `cd ui && npm test` (`vitest run`).
- Commit messages: one line ≤72 chars, imperative, `type(area): subject`. Stage by name — never `git add -A`/`.`/`-a`.
- Do **not** open a PR. Commits land on the topic branch.

---

## File Structure

| file | responsibility |
|---|---|
| `core/models.py` | `Scenario.composer_layout` nullable JSON column + `to_dict()` exposure |
| `core/database.py` | `_migrate_scenarios_columns` gains one `("composer_layout", "JSON")` entry |
| `core/engine/composer_draft_schema.py` | `DraftScenarioSchema.composer_layout` field, its validator, and ORM conversion |
| `core/api/drafts.py` | carries the field through create / read / replace (mostly free via the schema) |
| `tests/api/test_drafts_api.py` | backend round-trip, replace-preservation, orphan pruning |
| `tests/engine/test_composer_draft_schema.py` | schema validation + corpus-`StepSchema`-unchanged guard |
| `ui/src/components/console/composerDraft.js` | `layout` in the draft object across its 5 existing touch points; orphan pruning; `emitDraftYaml` stays clean |
| `ui/src/components/console/composerLayout.js` | `mergeStoredPositions()` — stored position wins, computed is fallback |
| `ui/src/components/console/ComposerCanvas.jsx` | React Flow render, drag, connect, read-only Run lens, resize handling |
| `ui/src/components/console/composerSpine.js` | **new** — pure connection-legality predicate, shared by canvas and tests |
| `ui/src/components/console/ComposerView.jsx` | layout state, save wiring, `Re-layout` action |
| `ui/src/components/__tests__/composerSpine.test.js` | **new** — legality rules |
| `ui/src/components/__tests__/ComposerCanvas.test.jsx` | drag/connect/read-only behaviour |
| `ui/src/components/__tests__/composerDraft.test.js` | layout round-trip + YAML cleanliness |
| `CLAUDE.md` | record the prod `ALTER TABLE`, as the other two columns are |

---

### Task 1: Diagnose why the Run lens renders its empty state

**This is a debugging task, not an implementation task.** Its deliverable is a *written cause*. Do not write a fix until the cause is established — the spec (§5.7) is explicit that designing a fix for an unestablished cause ships a change that fixes nothing.

**REQUIRED SUB-SKILL:** `superpowers:systematic-debugging`.

**Files:**
- Read: `ui/src/components/console/ComposerView.jsx:260-300`, `:700-740`
- Read: `ui/src/api/causality.js`, `ui/src/api/client.js:400-425`
- Read: `core/api/causality.py`, `core/engine/causality_graph.py`
- Write: `docs/superpowers/plans/2026-09-08-run-lens-diagnosis.md`

**Interfaces:**
- Produces: a written diagnosis naming the cause, plus a decision recorded in that file — either (a) a bounded fix, appended to this plan as Task 13, or (b) a separate gap with its own spec.

- [ ] **Step 1: Establish the baseline — does the endpoint return a graph at all?**

The container on :8888 is current as of 2026-09-08 (image `cortex-pov-engine-simcore:1.0.2`).

```bash
curl -s localhost:8888/api/runs | python3 -m json.tool | head -40
```

Note the most recent `run_id` and its `status`. The console header showed `SIM-EDR-001 · failed`.

- [ ] **Step 2: Ask the causality endpoint directly for that run**

```bash
RUN=<run_id from step 1>
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8888/api/runs/$RUN/causality"
curl -s "localhost:8888/api/runs/$RUN/causality" | head -c 1200
```

Record which of these it is:
- **200 with a populated `causality_graph`** → the backend is fine; the bug is in the frontend fetch or its trigger condition. Go to Step 3.
- **200 with an empty/degenerate graph** → the run produced no observations. Establish whether that is correct for a `failed` run before calling it a bug.
- **404 / 5xx** → a backend gap. Read `core/api/causality.py` for the precondition it enforces.

- [ ] **Step 3: Find the frontend trigger condition**

```bash
sed -n '255,305p' ui/src/components/console/ComposerView.jsx
grep -n "getCausality\|setCausalityGraph" ui/src/components/console/ComposerView.jsx
```

Determine exactly what must be true for `getCausality` to be called. The suspicion to confirm or kill: it fires only when a run is *bound to the open draft*, which is never true for a corpus scenario opened from the Library.

- [ ] **Step 4: Confirm the cause in the live console**

```
Browser pane → localhost:8888/#/composer → open a scenario → switch to the Run lens
```

Then read the network log for a `causality` request. Its presence or absence distinguishes "not fetched" from "fetched empty" — the two causes need opposite fixes, and guessing between them is the failure this task exists to prevent.

- [ ] **Step 5: Write the diagnosis**

Create `docs/superpowers/plans/2026-09-08-run-lens-diagnosis.md` stating: the observed behaviour, the four probe results verbatim, the established cause, and the recommendation (bounded fix vs separate gap). **State plainly if the cause was not established** — an unresolved diagnosis is a legitimate outcome and must not be dressed up as one.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-09-08-run-lens-diagnosis.md
git commit -m "docs(composer): diagnose Run-lens empty state"
```

---

### Task 2: Persist `composer_layout` — the fail-without-fix proof

**Files:**
- Modify: `core/models.py` (Scenario column + `to_dict()`)
- Modify: `core/database.py:211-250` (`_migrate_scenarios_columns`)
- Modify: `core/engine/composer_draft_schema.py` (`DraftScenarioSchema`, `draft_to_orm_kwargs`)
- Test: `tests/api/test_drafts_api.py`

**Interfaces:**
- Produces: `Scenario.composer_layout: Optional[dict[str, dict[str, int]]]`, shaped `{"<step_id>": {"x": int, "y": int}}`; present in `Scenario.to_dict()` under key `composer_layout`; accepted by `POST`/`PUT /api/scenarios/drafts`.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_drafts_api.py`:

```python
def test_composer_layout_round_trips(client):
    """A canvas position POSTed with a draft survives GET.

    Fails before the fix: DraftStepSchema/StepSchema declare no model_config,
    so Pydantic v2 defaults to extra='ignore' and the field is silently
    DISCARDED — the drag would appear to work until reload.
    """
    body = _draft_body(steps=[_step("s1"), _step("s2", causality={"parent_step": "s1"})])
    body["composer_layout"] = {"s1": {"x": 240, "y": 80}, "s2": {"x": 240, "y": 260}}

    created = client.post("/api/scenarios/drafts", json=body)
    assert created.status_code == 201, created.text
    sid = created.json()["scenario_id"]

    got = client.get(f"/api/scenarios/drafts/{sid}")
    assert got.status_code == 200, got.text
    assert got.json()["composer_layout"] == {
        "s1": {"x": 240, "y": 80},
        "s2": {"x": 240, "y": 260},
    }


def test_draft_without_layout_stores_null(client):
    """A layout-less draft is stored byte-identically to a pre-feature draft."""
    body = _draft_body(steps=[_step("s1")])
    sid = client.post("/api/scenarios/drafts", json=body).json()["scenario_id"]
    assert client.get(f"/api/scenarios/drafts/{sid}").json()["composer_layout"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
make test-backend 2>&1 | grep -A 12 "test_composer_layout_round_trips"
```

Expected: `test_composer_layout_round_trips` **FAILS** — `KeyError: 'composer_layout'` or `assert None == {...}`. This failure *is* the Gate A evidence; capture the output for the commit body. `test_draft_without_layout_stores_null` also fails on the missing key.

- [ ] **Step 3: Add the ORM column**

In `core/models.py`, immediately after the `stitch_context` column on `Scenario`:

```python
    # ── Composer canvas layout (2026-09-08) ─────────────────────────────────
    # Presentation-only: {"<step_id>": {"x": int, "y": int}} for the Composer
    # canvas. Deliberately NOT inside steps[] — steps are content and are
    # emitted into scenarios/<plane>/ YAML, which the strict loader validates;
    # coordinates must never reach the shipped corpus. Nullable, so a draft
    # with no dragged node stores NULL and is byte-identical to a pre-feature
    # draft. NOTE: prod needs
    #   ALTER TABLE scenarios ADD COLUMN composer_layout JSON
    composer_layout: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
```

Add to `Scenario.to_dict()` alongside `"stitch_context"`:

```python
            "composer_layout": self.composer_layout,
```

- [ ] **Step 4: Add the migration entry**

In `core/database.py`, inside `_migrate_scenarios_columns`'s `additions` list, after the `stitch_context` line:

```python
        ("composer_layout", "JSON"),     # Composer canvas node positions
```

- [ ] **Step 5: Add the schema field and its validator**

In `core/engine/composer_draft_schema.py`, add to `DraftScenarioSchema` after `stitch_context`:

```python
    # ── Optional Composer canvas layout (2026-09-08) ────────────────────────
    # Presentation-only, keyed by step id. Declared EXPLICITLY rather than
    # relying on the free-form steps JSON: DraftStepSchema/StepSchema declare
    # no model_config, so Pydantic v2's extra='ignore' would silently discard
    # a position and green every test (Gate A5, "tolerance hides bugs").
    composer_layout: Optional[dict[str, NodePosition]] = None
```

And above `DraftScenarioSchema`, the position model — strict, so a malformed
coordinate raises instead of being dropped:

```python
class NodePosition(BaseModel):
    """One node's canvas position. ``extra='forbid'`` on purpose: an
    unrecognised key here means the caller and the canvas disagree about the
    shape, which must surface as 422 rather than a silently half-stored
    position."""

    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
```

Confirm `ConfigDict` is imported: `from pydantic import BaseModel, ConfigDict, Field, field_validator`.

- [ ] **Step 6: Add the ORM conversion**

In `draft_to_orm_kwargs`, after the `stitch_context` entry:

```python
        # Canvas layout: normalise an empty map to None so a layout-less draft
        # stores NULL, exactly as stitch_context does.
        "composer_layout": (
            {k: v.model_dump() for k, v in draft.composer_layout.items()} or None
            if draft.composer_layout
            else None
        ),
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
make test-backend 2>&1 | tail -20
```

Expected: both new tests PASS, and the pre-existing suite stays green (baseline was 5321 passed / 0 failed / 57 skipped).

- [ ] **Step 8: Record the prod migration in CLAUDE.md**

Find the existing `cgo_anchor` `ALTER TABLE` note and add alongside it:
`prod needs ALTER TABLE scenarios ADD COLUMN composer_layout JSON`.

- [ ] **Step 9: Commit**

```bash
git add core/models.py core/database.py core/engine/composer_draft_schema.py \
        tests/api/test_drafts_api.py CLAUDE.md
git commit -m "feat(api): persist composer canvas node positions"
```

Include the Step 2 failure output in the commit body — Gate A requires proof the guard fails without the fix.

---

### Task 3: Preserve positions across full-replace, prune orphans

`PUT /api/scenarios/drafts/{id}` is full-replace. A step the DC did not touch must keep its position, and a position whose step was deleted must not linger.

**Files:**
- Modify: `core/engine/composer_draft_schema.py`
- Test: `tests/api/test_drafts_api.py`

**Interfaces:**
- Consumes: `NodePosition`, `DraftScenarioSchema.composer_layout` (Task 2).
- Produces: pruning behaviour — `composer_layout` keys are restricted to ids present in `steps[]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_replace_preserves_untouched_step_positions(client):
    body = _draft_body(steps=[_step("s1"), _step("s2", causality={"parent_step": "s1"})])
    body["composer_layout"] = {"s1": {"x": 10, "y": 20}, "s2": {"x": 10, "y": 200}}
    sid = client.post("/api/scenarios/drafts", json=body).json()["scenario_id"]

    body["steps"][1]["name"] = "renamed"          # edit s2, touch no positions
    resp = client.put(f"/api/scenarios/drafts/{sid}", json=body)
    assert resp.status_code == 200, resp.text
    assert client.get(f"/api/scenarios/drafts/{sid}").json()["composer_layout"] == {
        "s1": {"x": 10, "y": 20},
        "s2": {"x": 10, "y": 200},
    }


def test_orphan_layout_key_is_pruned_not_an_error(client):
    """A position for a removed step is presentation debris, not a broken
    reference — dropped silently, never a 422."""
    body = _draft_body(steps=[_step("s1")])
    body["composer_layout"] = {"s1": {"x": 1, "y": 2}, "ghost": {"x": 9, "y": 9}}
    resp = client.post("/api/scenarios/drafts", json=body)
    assert resp.status_code == 201, resp.text
    sid = resp.json()["scenario_id"]
    assert client.get(f"/api/scenarios/drafts/{sid}").json()["composer_layout"] == {
        "s1": {"x": 1, "y": 2}
    }


def test_malformed_position_is_422_not_dropped(client):
    """Gate A5: an unrecognised shape raises. extra='forbid' on NodePosition."""
    body = _draft_body(steps=[_step("s1")])
    body["composer_layout"] = {"s1": {"x": 1, "y": 2, "z": 3}}
    resp = client.post("/api/scenarios/drafts", json=body)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "DRAFT_SCHEMA_INVALID"
```

- [ ] **Step 2: Run to verify they fail**

```bash
make test-backend 2>&1 | grep -E "orphan_layout|malformed_position|preserves_untouched"
```

Expected: `test_orphan_layout_key_is_pruned_not_an_error` FAILS (`ghost` survives). The other two should already pass from Task 2 — if `test_malformed_position_is_422_not_dropped` fails, `extra="forbid"` was omitted; fix Task 2 Step 5.

- [ ] **Step 3: Implement pruning**

Add a model validator to `DraftScenarioSchema`, after the existing field validators:

```python
    @model_validator(mode="after")
    def _prune_orphan_layout_keys(self):
        """Drop layout entries whose step no longer exists.

        Steps are freely removable and a stale coordinate is presentation
        debris, not a dangling reference — so this prunes rather than raises.
        Contrast NodePosition's extra='forbid': a MALFORMED position is a
        contract disagreement and does raise.
        """
        if self.composer_layout:
            live = {s.id for s in self.steps}
            self.composer_layout = {
                k: v for k, v in self.composer_layout.items() if k in live
            } or None
        return self
```

Confirm `model_validator` is imported from `pydantic`.

- [ ] **Step 4: Run to verify they pass**

```bash
make test-backend 2>&1 | tail -20
```

- [ ] **Step 5: Commit**

```bash
git add core/engine/composer_draft_schema.py tests/api/test_drafts_api.py
git commit -m "fix(api): prune orphan composer layout keys on save"
```

---

### Task 4: Guard the corpus schema against presentation data

**Files:**
- Test: `tests/engine/test_composer_draft_schema.py`

**Interfaces:**
- Consumes: `StepSchema`, `DraftScenarioSchema` (Task 2).
- Produces: nothing consumed downstream; a standing drift guard.

- [ ] **Step 1: Write the guard test**

```python
def test_corpus_step_schema_carries_no_coordinates():
    """A shipped scenario must never carry canvas coordinates.

    composer_layout is draft-level presentation state. If it ever appears on
    StepSchema, positions can reach scenarios/<plane>/ YAML and the shipped
    corpus. Structural guard, in the spirit of this repo's AST drift guards.
    """
    from engine.scenario_loader import StepSchema

    banned = {"x", "y", "position", "coords", "composer_layout", "layout"}
    assert not (banned & set(StepSchema.model_fields)), (
        "StepSchema gained a presentation field — see the 2026-09-08 composer "
        "direct-manipulation spec §5.2"
    )


def test_draft_layout_is_scenario_level_not_step_level():
    from engine.composer_draft_schema import DraftScenarioSchema, DraftStepSchema

    assert "composer_layout" in DraftScenarioSchema.model_fields
    assert "composer_layout" not in DraftStepSchema.model_fields
```

- [ ] **Step 2: Run to verify they pass immediately**

```bash
make test-backend 2>&1 | grep -E "no_coordinates|scenario_level"
```

Expected: PASS. These are **regression guards**, not TDD drivers — they document an invariant Task 2 already satisfies. If either fails now, Task 2 put the field in the wrong place; go fix Task 2 before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/engine/test_composer_draft_schema.py
git commit -m "test(engine): guard corpus schema against canvas coordinates"
```

---

### Task 5: Carry layout through the frontend draft module

`composerDraft.js` is a pure-reducer module. `stitch_context` already threads through five touch points; `layout` follows the same path.

**Files:**
- Modify: `ui/src/components/console/composerDraft.js` (lines ~101, ~117, ~675, ~734, plus `draftFromApi`)
- Test: `ui/src/components/__tests__/composerDraft.test.js`

**Interfaces:**
- Consumes: the API contract from Task 2 (`composer_layout` as `{stepId: {x, y}}`).
- Produces: `draft.layout` — a plain object `{[stepId]: {x, y}}` or `null`; `setNodePosition(draft, stepId, x, y) -> draft`; `clearLayout(draft) -> draft`.

- [ ] **Step 1: Write the failing tests**

```javascript
import {
  emptyDraft, draftToApi, draftFromApi, emitDraftYaml,
  setNodePosition, clearLayout,
} from '../console/composerDraft.js'

describe('composer layout', () => {
  it('starts null so a fresh draft is byte-identical to a pre-feature draft', () => {
    expect(emptyDraft().layout).toBeNull()
  })

  it('setNodePosition stores a position without mutating the input', () => {
    const d0 = { ...emptyDraft(), steps: [{ id: 's1' }] }
    const d1 = setNodePosition(d0, 's1', 120, 40)
    expect(d1.layout).toEqual({ s1: { x: 120, y: 40 } })
    expect(d0.layout).toBeNull()          // no mutation
  })

  it('round-trips through the API shape', () => {
    const d = setNodePosition({ ...emptyDraft(), steps: [{ id: 's1' }] }, 's1', 8, 16)
    expect(draftToApi(d).composer_layout).toEqual({ s1: { x: 8, y: 16 } })
    expect(draftFromApi({ ...draftToApi(d), composer_layout: { s1: { x: 8, y: 16 } } }).layout)
      .toEqual({ s1: { x: 8, y: 16 } })
  })

  it('omits composer_layout entirely when there is no layout', () => {
    expect('composer_layout' in draftToApi(emptyDraft())).toBe(false)
  })

  it('clearLayout resets to null for Re-layout', () => {
    const d = setNodePosition({ ...emptyDraft(), steps: [{ id: 's1' }] }, 's1', 1, 2)
    expect(clearLayout(d).layout).toBeNull()
  })

  it('NEVER leaks coordinates into emitted scenario YAML', () => {
    // That YAML is dropped into scenarios/<plane>/ and validated by the strict
    // loader. Coordinates there would reach the shipped corpus.
    const d = setNodePosition(
      { ...emptyDraft(), name: 'x', plane: 'EDR', steps: [{ id: 's1', name: 'a', command: 'id', detections: [] }] },
      's1', 999, 777,
    )
    const yaml = emitDraftYaml(d)
    expect(yaml).not.toMatch(/composer_layout|\bx:\s*999|\by:\s*777|position/)
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ui && npm test -- composerDraft 2>&1 | tail -25
```

Expected: FAIL — `setNodePosition is not a function`.

- [ ] **Step 3: Implement**

In `composerDraft.js`:

```javascript
/**
 * Canvas node positions, keyed by step id. Presentation-only — deliberately a
 * draft-level map rather than a field on each step, so it structurally cannot
 * reach emitDraftYaml (and therefore the shipped corpus). See the 2026-09-08
 * direct-manipulation spec §5.2.
 */
export function setNodePosition(draft, stepId, x, y) {
  return {
    ...draft,
    layout: { ...(draft.layout || {}), [stepId]: { x: Math.round(x), y: Math.round(y) } },
  }
}

/** Drop every stored position — the Re-layout action's reducer. */
export function clearLayout(draft) {
  return { ...draft, layout: null }
}
```

Then the five threading points, mirroring `stitchContext` exactly:

1. `emptyDraft()` (~line 117): add `layout: null,`
2. `draftFromScenario()` (~line 101): add `layout: scenario.composer_layout || null,`
3. `draftFromApi()` (~line 701): add `layout: row.composer_layout || null,`
4. `draftToApi()` (~line 675, beside the `stitch_context` block):

```javascript
  // Only send a layout that exists, so a layout-less draft POSTs the exact
  // body it did before this feature (backend stores NULL).
  if (draft.layout && Object.keys(draft.layout).length > 0) {
    body.composer_layout = draft.layout
  }
```

5. `draftSnapshot()` (~line 734): add `layout: draft.layout || null,` so dragging a node marks the draft dirty and `isDraftDirty` offers a save.

**Do not touch `emitDraftYaml`.** Its cleanliness is asserted above.

- [ ] **Step 4: Run to verify they pass**

```bash
cd ui && npm test -- composerDraft 2>&1 | tail -12
```

- [ ] **Step 5: Run the whole UI suite for regressions**

```bash
cd ui && npm test 2>&1 | tail -12
```

Expected: no regressions (baseline 1127 passed / 91 files).

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/console/composerDraft.js \
        ui/src/components/__tests__/composerDraft.test.js
git commit -m "feat(ui): carry canvas node positions through the draft model"
```

---

### Task 6: Demote computed layout to a fallback

Every existing draft must open exactly as it does today. A stored position wins; a step without one uses its computed position.

**Files:**
- Modify: `ui/src/components/console/composerLayout.js`
- Test: `ui/src/components/__tests__/composerLayout.test.js`

**Interfaces:**
- Consumes: `layoutChain(draft, opts) -> {nodes, edges, bounds}` (existing).
- Produces: `mergeStoredPositions(layout, stored) -> {nodes, edges, bounds}` — same shape, `nodes[].x`/`.y` overridden where `stored[node.id]` exists.

- [ ] **Step 1: Write the failing tests**

```javascript
import { layoutChain, mergeStoredPositions } from '../console/composerLayout.js'

describe('mergeStoredPositions', () => {
  const draft = { steps: [
    { id: 's1', name: 'a', detections: [] },
    { id: 's2', name: 'b', detections: [], causalityParent: 's1' },
  ] }

  it('is a no-op with no stored positions — the back-compat guard', () => {
    const base = layoutChain(draft)
    expect(mergeStoredPositions(base, null)).toEqual(base)
    expect(mergeStoredPositions(base, {})).toEqual(base)
  })

  it('overrides only the nodes that have a stored position', () => {
    const base = layoutChain(draft)
    const merged = mergeStoredPositions(base, { s2: { x: 500, y: 900 } })
    const s1b = base.nodes.find(n => n.id === 's1')
    const s1m = merged.nodes.find(n => n.id === 's1')
    const s2m = merged.nodes.find(n => n.id === 's2')
    expect({ x: s1m.x, y: s1m.y }).toEqual({ x: s1b.x, y: s1b.y })  // untouched
    expect({ x: s2m.x, y: s2m.y }).toEqual({ x: 500, y: 900 })
  })

  it('ignores a stored position for a node that is not on the canvas', () => {
    const base = layoutChain(draft)
    expect(() => mergeStoredPositions(base, { ghost: { x: 1, y: 1 } })).not.toThrow()
    expect(mergeStoredPositions(base, { ghost: { x: 1, y: 1 } }).nodes)
      .toHaveLength(base.nodes.length)
  })

  it('grows bounds to contain a dragged-out node so the canvas can scroll to it', () => {
    const base = layoutChain(draft)
    const merged = mergeStoredPositions(base, { s2: { x: 5000, y: 4000 } })
    expect(merged.bounds.width).toBeGreaterThan(base.bounds.width)
    expect(merged.bounds.height).toBeGreaterThan(base.bounds.height)
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ui && npm test -- composerLayout 2>&1 | tail -20
```

Expected: FAIL — `mergeStoredPositions is not a function`.

- [ ] **Step 3: Implement**

Append to `composerLayout.js`:

```javascript
/**
 * Overlay stored canvas positions on a computed layout.
 *
 * The computed layout stays authoritative for any node the DC has never
 * dragged, which is what makes adopting a position opt-in per node and keeps
 * every pre-existing draft opening unchanged. Unknown keys are ignored rather
 * than raising: a position whose step was deleted is presentation debris.
 *
 * Bounds are recomputed so a node dragged outside the computed extent stays
 * reachable — without this the canvas cannot scroll to it.
 */
export function mergeStoredPositions(layout, stored) {
  if (!stored || Object.keys(stored).length === 0) return layout

  const nodes = layout.nodes.map((n) => {
    const p = stored[n.id]
    if (!p || typeof p.x !== 'number' || typeof p.y !== 'number') return n
    return { ...n, x: p.x, y: p.y }
  })

  const bounds = nodes.reduce(
    (acc, n) => ({
      width: Math.max(acc.width, n.x + (n.w || 0) + LAYOUT.padX),
      height: Math.max(acc.height, n.y + (n.h || LAYOUT.nodeH || 0) + LAYOUT.padY),
    }),
    { width: layout.bounds.width, height: layout.bounds.height },
  )

  return { ...layout, nodes, bounds }
}
```

Before running, confirm the real key names on `LAYOUT` and on a node:

```bash
sed -n '23,52p' ui/src/components/console/composerLayout.js
```

Use whatever `LAYOUT` actually exports for padding and node height; the names above are the expected ones, not assumed ones.

- [ ] **Step 4: Run to verify they pass**

```bash
cd ui && npm test -- composerLayout 2>&1 | tail -12
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/console/composerLayout.js \
        ui/src/components/__tests__/composerLayout.test.js
git commit -m "feat(ui): let stored node positions override computed layout"
```

---

### Task 7: Extract the spine-legality predicate

This is where D2 survives. Pure module, no React — so the rules are testable without a canvas, and the canvas cannot diverge from them.

**Files:**
- Create: `ui/src/components/console/composerSpine.js`
- Test: `ui/src/components/__tests__/composerSpine.test.js`

**Interfaces:**
- Produces: `canConnect(steps, fromId, toId) -> {ok: true} | {ok: false, code, reason}` where `code` is one of `SELF_REF` · `CYCLE` · `SECOND_ROOT` · `ALREADY_PARENTED` and `reason` is a DC-readable sentence.

- [ ] **Step 1: Write the failing tests**

```javascript
import { canConnect } from '../console/composerSpine.js'

const chain = [
  { id: 's1', causalityParent: null },   // the single root
  { id: 's2', causalityParent: 's1' },
  { id: 's3', causalityParent: 's2' },
]

describe('canConnect — the spine invariant (spec D2)', () => {
  it('allows re-parenting a step onto another branch', () => {
    expect(canConnect(chain, 's1', 's3')).toEqual({ ok: true })
  })

  it('refuses a self reference', () => {
    const r = canConnect(chain, 's2', 's2')
    expect(r.ok).toBe(false)
    expect(r.code).toBe('SELF_REF')
    expect(r.reason).toMatch(/itself/i)
  })

  it('refuses a cycle', () => {
    // s3 descends from s1; making s1 a child of s3 closes the loop.
    const r = canConnect(chain, 's3', 's1')
    expect(r.ok).toBe(false)
    expect(r.code).toBe('CYCLE')
    expect(r.reason).toMatch(/cycle|loop/i)
  })

  it('refuses an edge that would orphan the only root', () => {
    // Giving the root a parent leaves no step linking from the CGO anchor.
    const twoRoots = [{ id: 'a', causalityParent: null }, { id: 'b', causalityParent: null }]
    const r = canConnect(twoRoots, 'a', 'a')
    expect(r.ok).toBe(false)
  })

  it('refuses a second parent for an already-parented step', () => {
    const r = canConnect(chain, 's1', 's2')   // s2 already has s1... via s1
    expect(r).toEqual({ ok: true })            // same parent is idempotent
    const r2 = canConnect(chain, 's3', 's2')   // s2 -> parent s3 would cycle
    expect(r2.ok).toBe(false)
  })

  it('always returns a reason when it refuses — a silent no-op reads as a broken canvas', () => {
    for (const [f, t] of [['s2', 's2'], ['s3', 's1']]) {
      const r = canConnect(chain, f, t)
      expect(r.ok).toBe(false)
      expect(typeof r.reason).toBe('string')
      expect(r.reason.length).toBeGreaterThan(10)
    }
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ui && npm test -- composerSpine 2>&1 | tail -15
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```javascript
/**
 * composerSpine — the connection-legality predicate for the Composer canvas.
 *
 * WHY THIS IS A SEPARATE PURE MODULE
 * ----------------------------------
 * The 2026-09-04 spec's D2 fixed the topology as a spine-constrained causality
 * tree (one root, acyclic) and enforced it by withholding the drag affordance.
 * The 2026-09-08 amendment adds the affordance and moves enforcement HERE, to
 * connect time. Keeping it pure means the rules are provable without mounting a
 * canvas, and the canvas cannot drift from them.
 *
 * The rules mirror what core/engine/scenario_loader.py enforces on YAML: no
 * forward refs, no self refs, at most one root step. A refusal ALWAYS carries a
 * reason — a silently dropped drag is indistinguishable from a broken canvas,
 * and the DC has to be able to learn the constraint from the tool.
 */

/** Walk ancestors of `id`, returning the set of step ids above it. */
function ancestorsOf(steps, id) {
  const byId = new Map(steps.map((s) => [s.id, s]))
  const seen = new Set()
  let cur = byId.get(id)
  while (cur && cur.causalityParent && !seen.has(cur.causalityParent)) {
    seen.add(cur.causalityParent)
    cur = byId.get(cur.causalityParent)
  }
  return seen
}

/**
 * @returns {{ok: true} | {ok: false, code: string, reason: string}}
 */
export function canConnect(steps, fromId, toId) {
  if (fromId === toId) {
    return {
      ok: false,
      code: 'SELF_REF',
      reason: 'A step cannot descend from itself — the causality spine is acyclic.',
    }
  }

  // A step's existing parent being re-asserted is a no-op, not a violation.
  const to = steps.find((s) => s.id === toId)
  if (to && to.causalityParent === fromId) return { ok: true }

  if (ancestorsOf(steps, fromId).has(toId)) {
    return {
      ok: false,
      code: 'CYCLE',
      reason: `${toId} already descends from ${fromId}; this edge would close a cycle.`,
    }
  }

  // Parenting the only root leaves nothing linking from the CGO anchor.
  const roots = steps.filter((s) => !s.causalityParent)
  if (roots.length === 1 && roots[0].id === toId) {
    return {
      ok: false,
      code: 'SECOND_ROOT',
      reason: `${toId} is the chain's only root step — it links from the CGO anchor and cannot take a parent.`,
    }
  }

  return { ok: true }
}
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd ui && npm test -- composerSpine 2>&1 | tail -12
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/console/composerSpine.js \
        ui/src/components/__tests__/composerSpine.test.js
git commit -m "feat(ui): extract spine-legality predicate for canvas edges"
```

---

### Task 8: Add React Flow and render the Design lens through it

Render only — no drag, no connect yet. This isolates "the graph still looks right" from "the graph can now be edited", so a reviewer can reject one without the other.

**Files:**
- Modify: `ui/package.json`
- Modify: `ui/src/components/console/ComposerCanvas.jsx` (`DesignGraph`, lines 100-357)
- Modify: `ui/src/styles/destinations/composer.css`
- Test: `ui/src/components/__tests__/ComposerCanvas.test.jsx`

**Interfaces:**
- Consumes: `mergeStoredPositions` (Task 6); `layoutChain` (existing).
- Produces: a `StepNode` custom node type registered as `nodeTypes.step`; `DesignGraph` accepting the existing props plus `storedLayout`.

- [ ] **Step 1: Install the dependency**

```bash
cd ui && npm install @xyflow/react@^12
```

Confirm it landed in `dependencies`, not `devDependencies` — the Docker `ui-builder` stage runs `npm ci` and builds for production:

```bash
python3 -c "import json;print(json.load(open('ui/package.json'))['dependencies'])"
```

- [ ] **Step 2: Measure the bundle cost before writing code**

```bash
cd ui && npm run build 2>&1 | tail -20
```

Record the largest chunk sizes. The Composer is a lazily-loaded destination, so React Flow must land in the Composer's chunk and **not** in the entry chunk — `ui/src/app/__tests__/entryCodeSplit.test.js` exists to guard exactly this class of regression. If it lands in the entry chunk, stop and fix the import boundary before continuing.

- [ ] **Step 3: Write the failing test**

```javascript
it('renders one node per step through React Flow, at stored positions', () => {
  const draft = { steps: [
    { id: 's1', name: 'drop', detections: [] },
    { id: 's2', name: 'dump', detections: [], causalityParent: 's1' },
  ] }
  const { container } = render(
    <ComposerCanvas {...baseProps({ draft, lens: 'design',
                                    storedLayout: { s2: { x: 400, y: 300 } } })} />
  )
  expect(container.querySelectorAll('.react-flow__node')).toHaveLength(2)
  const s2 = container.querySelector('[data-id="s2"]')
  expect(s2.style.transform).toContain('400')
  expect(s2.style.transform).toContain('300')
})

it('falls back to computed positions for a step with no stored position', () => {
  const draft = { steps: [{ id: 's1', name: 'drop', detections: [] }] }
  const { container } = render(
    <ComposerCanvas {...baseProps({ draft, lens: 'design', storedLayout: null })} />
  )
  expect(container.querySelector('[data-id="s1"]')).toBeTruthy()
})
```

React Flow needs a sized container in jsdom. Add to the file's setup, above the tests:

```javascript
// React Flow measures its container; jsdom reports 0x0, which makes it refuse
// to render nodes. Stub the observer and the box.
beforeAll(() => {
  global.ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
  }
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 1200 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 800 })
})
```

- [ ] **Step 4: Run to verify they fail**

```bash
cd ui && npm test -- ComposerCanvas 2>&1 | tail -25
```

Expected: FAIL — no `.react-flow__node` elements.

- [ ] **Step 5: Implement the render**

At the top of `ComposerCanvas.jsx`:

```javascript
import { ReactFlow, ReactFlowProvider, Background, Controls, Handle, Position } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { mergeStoredPositions } from './composerLayout.js'
```

The custom node — keep the existing `.chain-node` classes so the Cortex visual
language and its plane tints survive the swap:

```javascript
/**
 * One step, as a React Flow node. Reuses the existing .chain-node classes so
 * the plane tinting and Cortex type treatment are unchanged by the move off the
 * hand-rolled SVG canvas. Handles are the connect affordance the amendment adds.
 */
function StepNode({ id, data }) {
  return (
    <div className={`chain-node chain-node--${data.plane || 'unknown'}`} data-testid={`chain-node-${id}`}>
      <Handle type="target" position={Position.Top} />
      <div className="chain-node__kicker">{data.kicker}</div>
      <div className="chain-node__title">{data.name}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

const nodeTypes = { step: StepNode }
```

Inside `DesignGraph`, convert the existing computed layout into React Flow's shape:

```javascript
  const laidOut = useMemo(
    () => mergeStoredPositions(layout, storedLayout),
    [layout, storedLayout],
  )

  const rfNodes = useMemo(
    () => laidOut.nodes.map((n) => ({
      id: n.id,
      type: 'step',
      position: { x: n.x, y: n.y },
      data: { name: n.name, kicker: n.kicker, plane: n.plane },
    })),
    [laidOut],
  )

  const rfEdges = useMemo(
    () => laidOut.edges.map((e) => ({
      id: `${e.from.id}-${e.to.id}`,
      source: e.from.id,
      target: e.to.id,
      type: 'default',                 // bezier, matching the previous edgePath
      label: e.pivot !== 'process_lineage' ? e.pivot : undefined,
    })),
    [laidOut],
  )
```

Then the element, read-only for now:

```javascript
  <ReactFlowProvider>
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      fitView
      proOptions={{ hideAttribution: false }}
    >
      <Background />
      <Controls />
    </ReactFlow>
  </ReactFlowProvider>
```

Inspect the real `layout.nodes` / `layout.edges` field names first — the names above follow `composerLayout.js`'s existing shape and must be confirmed, not assumed:

```bash
sed -n '99,166p' ui/src/components/console/composerLayout.js
```

- [ ] **Step 6: Run to verify they pass**

```bash
cd ui && npm test -- ComposerCanvas 2>&1 | tail -15
```

- [ ] **Step 7: Run the whole UI suite**

```bash
cd ui && npm test 2>&1 | tail -12
```

Existing `ComposerCanvas.test.jsx` assertions that query the old SVG/`.chain-node` absolute-position DOM will break. Update them to the React Flow DOM — **do not delete an assertion to make the suite pass.** If an old assertion no longer has a meaning, say so explicitly in the commit body.

- [ ] **Step 8: Commit**

```bash
git add ui/package.json ui/package-lock.json \
        ui/src/components/console/ComposerCanvas.jsx \
        ui/src/styles/destinations/composer.css \
        ui/src/components/__tests__/ComposerCanvas.test.jsx
git commit -m "feat(ui): render the Composer design lens through React Flow"
```

---

### Task 9: Make nodes draggable and persist the position

**Files:**
- Modify: `ui/src/components/console/ComposerCanvas.jsx`
- Modify: `ui/src/components/console/ComposerView.jsx`
- Test: `ui/src/components/__tests__/ComposerCanvas.test.jsx`

**Interfaces:**
- Consumes: `setNodePosition` (Task 5), `canConnect` (Task 7, next task uses it).
- Produces: `DesignGraph` prop `onNodeMoved(stepId, x, y)`; `ComposerView` wires it to `setNodePosition`.

- [ ] **Step 1: Write the failing tests**

```javascript
it('reports a moved node in FLOW coordinates, not screen pixels', () => {
  // React Flow already divides screen delta by zoom before emitting a position
  // change. The bug this guards is someone "helpfully" re-applying zoom on top,
  // which double-scales every drag. See spec §5.4.
  const onNodeMoved = vi.fn()
  const draft = { steps: [{ id: 's1', name: 'a', detections: [] }] }
  render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onNodeMoved })} />)

  // Simulate the change React Flow emits after a drag at zoom 2.
  fireNodeDrag('s1', { x: 300, y: 150 })
  expect(onNodeMoved).toHaveBeenCalledWith('s1', 300, 150)   // NOT 600/300
})

it('snaps a stored position to the 8px grid', () => {
  const onNodeMoved = vi.fn()
  const draft = { steps: [{ id: 's1', name: 'a', detections: [] }] }
  render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onNodeMoved })} />)
  fireNodeDrag('s1', { x: 301, y: 149 })
  expect(onNodeMoved).toHaveBeenCalledWith('s1', 304, 152)
})

it('exposes no drag affordance in the run lens', () => {
  const { container } = render(
    <ComposerCanvas {...baseProps({ lens: 'run', causalityGraph: GRAPH_FIXTURE })} />
  )
  container.querySelectorAll('.react-flow__node').forEach((n) => {
    expect(n.classList.contains('draggable')).toBe(false)
  })
})
```

Add the drag helper beside the other test helpers in the file:

```javascript
/** Emit the node-position change React Flow produces after a drag. */
function fireNodeDrag(id, position) {
  // React Flow's onNodesChange receives {id, type:'position', position,
  // dragging:false} on drag end. Driving the handler directly is the stable
  // seam — synthesising pointer events against a transformed pane is not.
  const handler = window.__rfOnNodesChange
  handler([{ id, type: 'position', position, dragging: false }])
}
```

Expose that seam from the component under test only (guard it so it never ships behaviour):

```javascript
  // Test seam: the pane's transform makes synthetic pointer events unreliable
  // in jsdom, so tests drive onNodesChange directly. Assignment only.
  if (typeof window !== 'undefined') window.__rfOnNodesChange = onNodesChange
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ui && npm test -- ComposerCanvas 2>&1 | tail -25
```

Expected: FAIL — `onNodeMoved` never called.

- [ ] **Step 3: Implement**

In `DesignGraph`:

```javascript
  const SNAP = 8

  const onNodesChange = useCallback((changes) => {
    for (const c of changes) {
      // Only commit on drag END: committing every intermediate frame would
      // write a draft-dirty state per animation frame.
      if (c.type === 'position' && c.dragging === false && c.position) {
        onNodeMoved(
          c.id,
          Math.round(c.position.x / SNAP) * SNAP,
          Math.round(c.position.y / SNAP) * SNAP,
        )
      }
    }
  }, [onNodeMoved])
```

Set on the element: `nodesDraggable`, `onNodesChange={onNodesChange}`, `snapToGrid`, `snapGrid={[SNAP, SNAP]}`.

In `ComposerView.jsx`, wire it:

```javascript
  const handleNodeMoved = useCallback((stepId, x, y) => {
    setDraft((d) => setNodePosition(d, stepId, x, y))
  }, [])
```

and pass `onNodeMoved={handleNodeMoved}` to `ComposerCanvas`. Import `setNodePosition` from `./composerDraft.js`.

- [ ] **Step 4: Run to verify they pass**

```bash
cd ui && npm test -- ComposerCanvas 2>&1 | tail -15
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/console/ComposerCanvas.jsx \
        ui/src/components/console/ComposerView.jsx \
        ui/src/components/__tests__/ComposerCanvas.test.jsx
git commit -m "feat(ui): drag Composer step nodes and persist positions"
```

---

### Task 10: Draw connections, refusing illegal ones visibly

**Files:**
- Modify: `ui/src/components/console/ComposerCanvas.jsx`
- Modify: `ui/src/components/console/ComposerView.jsx`
- Test: `ui/src/components/__tests__/ComposerCanvas.test.jsx`

**Interfaces:**
- Consumes: `canConnect` (Task 7), `setCausalityParent` (existing).
- Produces: `DesignGraph` prop `onConnectSteps(fromId, toId)`; a refusal banner with `data-testid="canvas-refusal"`.

- [ ] **Step 1: Write the failing tests**

```javascript
it('re-parents a step when a legal edge is drawn', () => {
  const onConnectSteps = vi.fn()
  const draft = { steps: [
    { id: 's1', name: 'a', detections: [] },
    { id: 's2', name: 'b', detections: [], causalityParent: 's1' },
    { id: 's3', name: 'c', detections: [], causalityParent: 's2' },
  ] }
  render(<ComposerCanvas {...baseProps({ draft, lens: 'design', onConnectSteps })} />)
  window.__rfOnConnect({ source: 's1', target: 's3' })
  expect(onConnectSteps).toHaveBeenCalledWith('s1', 's3')
})

it('REFUSES a cycle and renders the reason — a silent no-op reads as a broken canvas', () => {
  const onConnectSteps = vi.fn()
  const draft = { steps: [
    { id: 's1', name: 'a', detections: [] },
    { id: 's2', name: 'b', detections: [], causalityParent: 's1' },
  ] }
  const { getByTestId } = render(
    <ComposerCanvas {...baseProps({ draft, lens: 'design', onConnectSteps })} />
  )
  window.__rfOnConnect({ source: 's2', target: 's1' })
  expect(onConnectSteps).not.toHaveBeenCalled()
  expect(getByTestId('canvas-refusal').textContent).toMatch(/cycle|loop/i)
})

it('exposes no connect affordance in the run lens', () => {
  const { container } = render(
    <ComposerCanvas {...baseProps({ lens: 'run', causalityGraph: GRAPH_FIXTURE })} />
  )
  expect(container.querySelectorAll('.react-flow__handle')).toHaveLength(0)
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ui && npm test -- ComposerCanvas 2>&1 | tail -25
```

- [ ] **Step 3: Implement**

In `DesignGraph`:

```javascript
  const [refusal, setRefusal] = useState(null)

  // isValidConnection runs while the DC is still dragging the edge, so an
  // illegal target simply never accepts — the edge is refused BEFORE it
  // exists rather than created and reverted. This is where D2's spine
  // constraint lives now that the affordance exists.
  const isValidConnection = useCallback(
    ({ source, target }) => canConnect(steps, source, target).ok,
    [steps],
  )

  const onConnect = useCallback(({ source, target }) => {
    const verdict = canConnect(steps, source, target)
    if (!verdict.ok) {
      setRefusal(verdict)      // visible, with its reason
      return
    }
    setRefusal(null)
    onConnectSteps(source, target)
  }, [steps, onConnectSteps])

  if (typeof window !== 'undefined') window.__rfOnConnect = onConnect
```

Render the refusal above the canvas:

```javascript
  {refusal && (
    <div className="canvas-refusal" data-testid="canvas-refusal" role="status" aria-live="polite">
      <strong>Edge refused</strong> — {refusal.reason}
      <button type="button" onClick={() => setRefusal(null)} aria-label="Dismiss">×</button>
    </div>
  )}
```

Set `nodesConnectable`, `onConnect`, `isValidConnection` on the element. Import `canConnect` from `./composerSpine.js`.

In `ComposerView.jsx`:

```javascript
  const handleConnectSteps = useCallback((fromId, toId) => {
    setDraft((d) => ({ ...d, steps: setCausalityParent(d.steps, toId, fromId) }))
  }, [])
```

Verify `setCausalityParent`'s real argument order before wiring — it is
`setCausalityParent(steps, id, parentId, pivot = 'process_lineage')`, i.e. the
CHILD id comes first:

```bash
sed -n '470,480p' ui/src/components/console/composerDraft.js
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd ui && npm test -- ComposerCanvas 2>&1 | tail -15
```

- [ ] **Step 5: Style the refusal banner**

Add to `ui/src/styles/destinations/composer.css`, using existing tokens only — no new colour literals:

```css
.canvas-refusal {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; margin-bottom: 8px;
  border: 1px solid var(--crit); border-radius: 6px;
  font: 500 12px/1.4 var(--font-ui);
  color: var(--ink);
}
.canvas-refusal button { margin-left: auto; background: none; border: 0; cursor: pointer; }
```

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/console/ComposerCanvas.jsx \
        ui/src/components/console/ComposerView.jsx \
        ui/src/styles/destinations/composer.css \
        ui/src/components/__tests__/ComposerCanvas.test.jsx
git commit -m "feat(ui): draw causality edges, refuse illegal ones visibly"
```

---

### Task 11: Re-layout control and mount-hidden hygiene

**Files:**
- Modify: `ui/src/components/console/ComposerView.jsx`
- Modify: `ui/src/components/console/ComposerCanvas.jsx`
- Test: `ui/src/components/__tests__/ComposerCanvas.test.jsx`, `ComposerView.test.jsx`

**Interfaces:**
- Consumes: `clearLayout` (Task 5).
- Produces: a `Re-layout` button, `data-testid="composer-relayout"`.

- [ ] **Step 1: Write the failing tests**

```javascript
it('Re-layout clears stored positions back to computed', async () => {
  const { getByTestId } = render(<ComposerView {...viewProps({ draft: draftWithLayout })} />)
  await userEvent.click(getByTestId('composer-relayout'))
  expect(getByTestId('composer-canvas').dataset.storedLayout).toBe('none')
})

it('does not fitView against a zero-sized container', () => {
  // The Composer is a lazily-mounted destination and can mount hidden at 0x0.
  // The reference implementation hit exactly this (duplicated DOM on re-init).
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, value: 0 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 0 })
  expect(() => render(<ComposerCanvas {...baseProps({ lens: 'design' })} />)).not.toThrow()
})
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd ui && npm test -- Composer 2>&1 | tail -25
```

- [ ] **Step 3: Implement**

In `ComposerView.jsx`, beside the other header actions from spec §6.7:

```javascript
  <button
    type="button"
    data-testid="composer-relayout"
    onClick={() => setDraft((d) => clearLayout(d))}
    title="Discard dragged positions and re-run the automatic layout"
    disabled={!draft.layout}
  >
    Re-layout
  </button>
```

In `DesignGraph`, guard the fit:

```javascript
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
```

Pass `fitView={measured}` rather than a bare `fitView`.

- [ ] **Step 4: Run to verify they pass**

```bash
cd ui && npm test -- Composer 2>&1 | tail -15
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/console/ComposerView.jsx \
        ui/src/components/console/ComposerCanvas.jsx \
        ui/src/components/__tests__/ComposerCanvas.test.jsx \
        ui/src/components/__tests__/ComposerView.test.jsx
git commit -m "feat(ui): add Re-layout and guard zero-sized canvas mount"
```

---

### Task 12: Full-suite green, rebuild, and live verification

jsdom cannot prove a canvas. This task is the evidence standard used for the 2026-09-08 header verification: rebuild the image and observe the running container.

**Files:**
- Modify: none (verification only, plus a fix cycle if it finds something)

- [ ] **Step 1: Run every suite**

```bash
cd ui && npm test 2>&1 | tail -6
cd ui && npm run build 2>&1 | tail -6
make test-backend 2>&1 | tail -6
make check-refs 2>&1 | tail -4
```

Expected, against the 2026-09-06 baselines: UI ≥1127 passed; backend 5321+ passed / 0 failed; `check-refs` 6 passed; build clean. **Report the real numbers, not the baselines.**

- [ ] **Step 2: Confirm React Flow did not land in the entry chunk**

```bash
cd ui && npm test -- entryCodeSplit 2>&1 | tail -8
```

- [ ] **Step 3: Rebuild the image and bring it up**

```bash
docker compose build simcore && docker compose up -d simcore
```

- [ ] **Step 4: Confirm the served bundle is the new one**

```bash
for i in $(seq 1 30); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' localhost:8888/api/health)" = "200" ] && break
  sleep 2
done
curl -s localhost:8888/api/health | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["status"],d["version"])'
curl -s localhost:8888/ | grep -o '/assets/index-[A-Za-z0-9_-]*\.js'
```

The asset hash must differ from the pre-change one. An unchanged hash means the image did not pick up the UI change — the exact failure mode that made the stale PANW header persist.

- [ ] **Step 5: Verify the drag end-to-end in the browser pane**

1. Open `localhost:8888/#/composer`, load a multi-step scenario.
2. Drag a node. Save the draft.
3. Reload. **The node must still be where it was dropped.**
4. Draw an edge that would create a cycle. **The refusal banner must appear with its reason.**
5. Switch to the Run lens. **No node may be draggable and no handle may be present.**

- [ ] **Step 6: Confirm the emitted YAML is still loader-clean**

```bash
curl -s "localhost:8888/api/scenarios/drafts/<draft_id>" | python3 -c 'import sys,json;print(json.load(sys.stdin)["composer_layout"])'
```

Then `Download draft YAML` from the console and confirm it contains no coordinates:

```bash
grep -nE "composer_layout|position|^\s+x:|^\s+y:" <downloaded>.yml || echo "clean"
```

- [ ] **Step 7: Commit any fixes, then report**

Report: the four real suite numbers, the old and new asset hashes, and a screenshot of a dragged-and-reloaded canvas. If any step failed, say which and what the output was — do not report completion on a partially verified change.

---

## Self-Review

**Spec coverage**

| spec section | task |
|---|---|
| §2 amendment / D2 reversal scoped | Task 7 (predicate), Task 10 (enforcement) |
| §5.1 node = step | Tasks 8-10 |
| §5.2 `composer_layout` column + `extra='ignore'` trap | Tasks 2, 3, 4 |
| §5.3 auto-layout as fallback + Re-layout | Tasks 6, 11 |
| §5.4 drag, zoom correctness, 8px snap | Task 9 |
| §5.5 connect-time validation, visible refusal | Tasks 7, 10 |
| §5.6 palette | **GAP — deliberately deferred.** The palette already exists per the 2026-09-04 spec §6.6; only its drop mechanism changes, and that is a separate reviewable change. Not in this plan. |
| §5.7 Run lens read-only + diagnosis first | Tasks 1, 9, 10 |
| §5.8 mount-hidden hygiene | Task 11 |
| §7 all 14 tests | Tasks 2-12 |
| §9 O1 | Global Constraints |

**Placeholder scan:** every code step carries real code. Three steps deliberately
instruct the implementer to *verify a name before using it* (`LAYOUT` keys in
Task 6, `layout.nodes`/`edges` field names in Task 8, `setCausalityParent`
argument order in Task 10) with the exact command to run — that is a
verification instruction, not a placeholder, and it exists because those names
were not confirmed while writing the plan.

**Type consistency:** `composer_layout` (backend, snake) ↔ `draft.layout`
(frontend, camel) is the same convention `stitch_context` ↔ `stitchContext`
already uses. `canConnect` returns `{ok}` / `{ok, code, reason}` in Tasks 7 and
10 identically. `setNodePosition(draft, stepId, x, y)` and `clearLayout(draft)`
are defined in Task 5 and consumed unchanged in Tasks 9 and 11.
`mergeStoredPositions(layout, stored)` is defined in Task 6 and consumed in
Task 8.

---

### Task 13: Scope the Run lens to the open draft's own scenario

Added 2026-09-11 from Task 1's diagnosis (`docs/superpowers/plans/2026-09-08-run-lens-diagnosis.md`).
Fixes TWO defects with one change.

**Defect A — the lens is blind to terminal runs.** `ComposerView.jsx:259` keys the
causality fetch on `env.activeRun`, and `EnvironmentContext.jsx:333` derives that as
`runs.find(r => r.status === 'running')` — app-wide and running-only. A completed or
failed run therefore never triggers the fetch, so the canvas renders "No run yet —
EXPECTED only. … Nothing on this canvas is inferred before a run exists" while the
backend serves a fully populated causality graph for that very run. The code contradicts
its own comment three lines above, which claims it renders "an in-flight **or terminal**
run for this draft".

**Defect B — cross-scenario evidence contamination.** Nothing guards `env.activeRun` by
scenario id, so an in-flight run of scenario B paints scenario A's Run lens while A's
draft is open. That renders one scenario's detection evidence as another's.

**Files:**
- Modify: `ui/src/components/console/ComposerView.jsx:255-275`
- Modify: `ui/src/components/console/ComposerCanvas.jsx` (`RunGraph` empty-state copy, ~line 374)
- Test: `ui/src/components/__tests__/ComposerView.test.jsx`

**Interfaces:**
- Consumes: `env.runs` (already used in this component at `ComposerView.jsx:818` for `HistoryPane`), `env.activeRun`, `draft.originId`.
- Produces: `scopedRun` — the run this lens renders. No new exports.

- [ ] **Step 1: Confirm the field names before writing code**

Do not assume the run row's shape. Run:

```bash
grep -n "scenario_id\|started_at\|run_id\|status" ui/src/components/console/ComposerView.jsx | sed -n '1,20p'
curl -s localhost:8888/api/runs | python3 -c 'import sys,json;d=json.load(sys.stdin);r=(d.get("runs") or d)[0];print(sorted(r.keys()))'
```

Use the names that come back, not the names in this task text.

- [ ] **Step 2: Write the failing tests**

```javascript
const SCENARIO = 'SIM-EDR-001'

function runRow(over = {}) {
  return { run_id: 'r1', scenario_id: SCENARIO, status: 'completed',
           started_at: '2026-09-08T12:00:00Z', ...over }
}

it('fetches causality for a TERMINAL run of the open scenario', async () => {
  // Fails today: env.activeRun is running-only, so a completed/failed run
  // never triggers the fetch and the canvas claims no run exists.
  const getRunCausality = vi.fn().mockResolvedValue({ nodes: [], edges: [] })
  renderComposer({ draft: { originId: SCENARIO }, env: { runs: [runRow()], activeRun: null }, getRunCausality })
  await waitFor(() => expect(getRunCausality).toHaveBeenCalledWith('r1'))
})

it('does NOT paint this canvas with another scenario\'s in-flight run', async () => {
  // Defect B: evidence contamination across scenarios.
  const getRunCausality = vi.fn()
  renderComposer({
    draft: { originId: SCENARIO },
    env: { runs: [], activeRun: { run_id: 'other', scenario_id: 'SIM-CDR-009', status: 'running', step: 1, detected: 0 } },
    getRunCausality,
  })
  await waitFor(() => expect(getRunCausality).not.toHaveBeenCalled())
})

it('prefers the in-flight run when it belongs to THIS scenario', async () => {
  const getRunCausality = vi.fn().mockResolvedValue({ nodes: [], edges: [] })
  renderComposer({
    draft: { originId: SCENARIO },
    env: { runs: [runRow({ run_id: 'old' })],
           activeRun: { run_id: 'live', scenario_id: SCENARIO, status: 'running', step: 2, detected: 1 } },
    getRunCausality,
  })
  await waitFor(() => expect(getRunCausality).toHaveBeenCalledWith('live'))
})

it('shows "no run yet" only when NO run exists for this scenario', () => {
  const { getByTestId } = renderComposer({
    draft: { originId: SCENARIO },
    env: { runs: [runRow({ scenario_id: 'SIM-CDR-009' })], activeRun: null },
    lens: 'run',
  })
  expect(getByTestId('composer-run-graph').textContent).toMatch(/no run yet/i)
})
```

- [ ] **Step 3: Run to verify they fail**

```bash
cd ui && npm test -- ComposerView 2>&1 | tail -25
```

Expected: the terminal-run test FAILS (`getRunCausality` never called). That failure is
the Gate A proof this fix is load-bearing.

- [ ] **Step 4: Implement the scoped selection**

Replace the `activeRunId` / `runTick` derivation in `ComposerView.jsx`:

```javascript
  // The run this lens renders: the most recent run OF THE OPEN DRAFT'S OWN
  // scenario, in-flight or terminal.
  //
  // Previously this keyed on env.activeRun alone, which EnvironmentContext
  // derives as `runs.find(r => r.status === 'running')` — app-wide and
  // running-only. Two defects fell out of that: a terminal run never triggered
  // the fetch (so the canvas claimed no run existed while the backend served a
  // populated graph), and an in-flight run of a DIFFERENT scenario painted this
  // canvas with another scenario's evidence.
  const scenarioId = draft.originId || null

  const scopedRun = useMemo(() => {
    if (!scenarioId) return null
    // Prefer the live run, but only when it is THIS scenario's.
    if (env.activeRun && env.activeRun.scenario_id === scenarioId) return env.activeRun
    const mine = (env.runs || []).filter((r) => r && r.scenario_id === scenarioId)
    if (mine.length === 0) return null
    return mine.reduce((best, r) =>
      Date.parse(r.started_at || 0) > Date.parse(best.started_at || 0) ? r : best)
  }, [scenarioId, env.activeRun, env.runs])

  const activeRunId = scopedRun ? runIdOf(scopedRun) : null

  // Poll only while non-terminal; a terminal run's graph is settled, so keying
  // on its mutable fields would refetch forever for no new data.
  const isLive = scopedRun ? scopedRun.status === 'running' : false
  const runTick = isLive
    ? `${scopedRun.step}:${scopedRun.detected}:${scopedRun.status}`
    : null
```

- [ ] **Step 5: Correct the empty-state copy**

In `ComposerCanvas.jsx`'s `RunGraph`, the empty state must distinguish "no run for this
scenario" from "a run exists but its graph has not loaded". Keep the existing honest
wording for the former; do NOT claim "nothing is inferred before a run exists" when a run
does exist.

- [ ] **Step 6: Run to verify they pass**

```bash
cd ui && npm test -- ComposerView 2>&1 | tail -15
cd ui && npm test 2>&1 | tail -8
```

- [ ] **Step 7: Commit**

```bash
git add ui/src/components/console/ComposerView.jsx \
        ui/src/components/console/ComposerCanvas.jsx \
        ui/src/components/__tests__/ComposerView.test.jsx
git commit -m "fix(ui): scope the Run lens to the open draft's own scenario"
```
