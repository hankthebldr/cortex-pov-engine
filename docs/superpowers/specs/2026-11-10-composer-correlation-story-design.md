# Composer: correlation rules in the chain story (2026-11-10)

Status: **approved 2026-11-10; phase 1 built** on
`feature/ui-composer-correlation-story` (cut from `dev` @ eeafe9f). Phases 2 to
4 (§9) are not built. Builds on the 2026-10-21 composer-mode spec; its graph
model, gestures and honesty rules are unchanged. What phase 1 did differently
from this text is listed in §13.

## 1. Problem

A DC opens `SIM-AIACC-003` in the Composer and sees step-02 tagged
`XQL XQL XQL`. The chips read as labels on the TTP. They are the step's three
expected detections, and all three are queries a DC runs by hand. The rule that
would raise the alert, `CR-AIACC-0003`, is on the card and nowhere on the
canvas.

The requirement, verbatim: *"the scenario is supposed to be composing the
different TTPs to be injected at the endpoint runtime or scenarios that would
be fired in through the BVM -- there may be specific correlation rules that
need to be created with the scenarios and that should also be built into the
user story."*

Measured on `dev` @ eeafe9f with a throwaway script (193 scenarios, 191 cards).
Phase 1's pure modules become the counted truth and a test pins them.

| Fact | Count |
|---|---|
| Step-detections typed `XQL` | 539 of 1377 (39.1%); 11 scenarios are XQL-only |
| ...that resolve to a card query, by `purpose` | validation 469 · hunt 58 · metric 6 · investigation 1 (3 resolve to a modeling rule, 2 carry no card reference) |
| Cards carrying a correlation rule | 190 of 191, 209 rules |
| Rules with anything beyond `{name, rule_id, logic}` | **0**. The card schema forbids more (`additionalProperties: false`) |
| `logic` that is real XQL (has a `dataset=` / `preset=` anchor) | **0**. About 189 are compositions such as `XQL(a) OR XQL(b)`, about 20 are prose |
| (scenario, rule) pairs a canvas would draw | 212, in 191 scenarios. At most 4 per scenario; 175 scenarios get exactly one |
| ...declared by a `Correlation` step-detection | 137 |
| ...on the card only | 75 (72 distinct rules; 61 cards where no scenario declares any) |
| `Correlation` step-detections that are scored | 139. None is backed by a rule a DC can import |
| On-disk `exports/correlation/*.json` | 190 files, 209 rules, every one stamped `severity: "high"` that no card authored. Nothing serves them |
| Steps declaring `channel: eal` | **0 of 796**. Every corpus door badge is plane-derived |

A composition names the card's validation queries. `XQL(name) OR XQL(name)`
is not XQL: a correlation rule is an XQL query the tenant runs on a schedule or
in real time, and this notation is nothing a tenant can run. The `logic` line
is an authoring note. **No correlation rule in the corpus is deployable.**

## 2. What changes on the canvas

```
  ◖ Trigger·CGO ─▶ [step-01] ┄shared_entity┄▶ [step-02] ┄shared_entity┄▶ [step-03] ─▶ Proof ◗
                    expects                    expects                    expects
                    run · 1 query              run · 3 queries            run · 1 query
                                                    ┆ feeds · 3 inputs
  ─ Cortex Engine · tenant content ────────────────────────────────────────────────────────
                                          ┌┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┐
                                          ┆ RULE · Correlation        CR-AIACC-0003   ┆
                                          ┆ Anomalous Gemini Usage Volume Single User ┆
                                          ┆ INTENT ONLY · nothing to import           ┆
                                          ┆ not declared by this scenario             ┆
                                          └┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┘
```

## 3. Tenant content as derived nodes

The recommendation holds: correlation rules are **derived nodes**, built in
`composerGraph.js` beside the CGO and Proof nodes and never persisted.

| Node | Source | Persisted? | Selectable | Deletable |
|---|---|---|---|---|
| `rule` | `detections.correlation_rules[]` of every card the chain's steps cite | never | yes, opens the Inspector | no |

| Edge | Meaning | Persisted? |
|---|---|---|
| `feeds` step → rule | the step declares a detection the rule's logic names (the label carries the input count), or carries the rule's own `Correlation` expectation | never |
| `card` step → rule | fallback when no input resolves: the step cites the card that carries the rule. Fainter, labelled "same card" | never |

* **Placement.** Design lens: a labelled *Cortex Engine · tenant content* rail
  one row below the lowest step, each rule under its right-most feeding step.
  Lanes lens: the existing ENG band. Positions are recomputed every build, the
  same as the CGO and Proof nodes; `composer_layout` keeps pruning non-step
  keys, so there is no new schema field and no drag.
* **Never a step.** A rule node is absent from `steps[]`, `draftToApi`,
  `draftSnapshot` and the emitted YAML. It cannot make a draft `edited`, and
  `validateDraft` / `spineProblems` never see it, so they keep matching
  `validate_causality_spine`. A fork gains and loses no field.
* **Selection is view state.** Clicking a rule opens the Inspector on the rule:
  both statuses in words, the `logic` line verbatim under the heading
  *authoring note, not XQL*, each input with where it resolved, and a link to
  the card.
* **Inputs are resolved, never assumed.** Each `KIND(name)` term resolves to a
  detection on the same card, then to the steps that declare it. A term is
  `step`, `card-only` (on the card, no step expects it) or `unknown` (not on
  the card; 59 term-uses today). The node prints the tally ("3 of 3 inputs on
  steps"). A prose rule has no terms: it gets a `card` edge and says "inputs
  not machine-readable". Nothing is dropped silently.
* **Run lens.** Unchanged in phase 1. It renders the server's
  `build_causality_graph`; lending a rule node a verdict there is a later
  phase.

**Do BIOC and ABIOC follow the same model?** One rule decides: *a node when the
content composes more than one detection, a chip when it is bound to one step.*

| Type | What it is | On the canvas | In the export |
|---|---|---|---|
| Correlation | tenant content spanning steps | **node** on the rail | yes |
| BIOC, IOC | tenant content the DC loads, bound to one step | chip on its step (a node each would add 484 one-edge nodes) | yes, same status model |
| ABIOC, Analytics, Prevention | ships with Cortex; nothing to import | chip on its step | no (named as prerequisites only) |
| XQL | a query the DC runs | chip on its step | yes, as the queries to run |

## 4. Status: two axes, never one colour

**Content** (what the card holds):

| Status | Test | Today | Rendering |
|---|---|---|---|
| `deployable` | the rule carries a complete `deploy` block that lints clean (§6) | 0 | solid border; still reads "authored, not imported into any tenant" |
| `intent-only` | only `name` / `rule_id` / `logic` | 209 | dashed border, steel; "INTENT ONLY · nothing to import" |
| `unreadable` | any other shape: unknown keys, non-string logic, half a `deploy` block | 0 | error tone, "card shape not recognised" with the reason |

The classifier **raises** on an unrecognised shape. The graph turns that into
an `unreadable` node; it is never drawn as intent-only and never dropped.

**Binding** (what the scenario asks for):

| Status | Test | Today | Rendering |
|---|---|---|---|
| `declared` | a step carries a `Correlation` expectation that resolves to the rule | 137 pairs | "scored by this run" |
| `card-only` | the rule is on a cited card and no step declares it | 75 pairs | "not declared by this scenario · not scored" |
| `unbound` | a step's `Correlation` expectation has no rule behind it | 2 | on the step's chip: "no rule on any card" |

The two axes are printed as two lines of words. Colour alone never carries
either. The cell that matters most is **declared and intent-only** (137): the
run seeds a result for an alert that no CortexSim content can raise, so it
confirms only when the tenant already raises a matching one. That node says so.

There is **no "declare" action in phase 1.** Declaring an intent-only rule adds
a scored expectation nothing can satisfy, and a missed row in a POV report
reads as "Cortex missed it".

## 5. What the chips say

One chip per (ownership, type) with a count, under a small `expects` label, so
the row reads as the step's proof contract and not as tags.

| Ownership | Types | Chip | Meaning to a DC |
|---|---|---|---|
| `run` | XQL | `run · 3 queries` | you run these after the step to confirm the telemetry landed |
| `deploy` | BIOC, IOC, Correlation | `deploy · 1 BIOC` | must exist in the tenant before the run; this is what raises the alert |
| `built-in` | ABIOC, Analytics, Prevention | `built-in · 1 ABIOC` | ships with Cortex; needs the module licensed and enabled, nothing to import |

* `SIM-AIACC-003` step-02 becomes `expects  run · 3 queries`, with one `feeds`
  edge down to `CR-AIACC-0003`.
* The title text and the Inspector row carry the detail: the query's `purpose`
  from the card, and for a Correlation chip both statuses from §4.
* The vocabulary is the seven `detection_type` values and it is closed. An
  unknown type raises in the pure module and renders `? <type>` in the error
  tone. It does not fall into a default class.
* Ownership follows the **declared** type. Where the card object disagrees (43
  `Analytics` expectations resolve to a card query), the Inspector states both.
  Phase 1 does not rewrite the corpus.

## 6. The deployable rule: content model, validator, coverage

**Where it lives.** A new optional object,
`detections.correlation_rules[].deploy`. `name`, `rule_id` and `logic` stay, so
every existing `detection_id` keeps resolving and `logic` remains the human
statement of intent.

```yaml
deploy:                       # field names are CANDIDATES, see the gate below
  xql: |                      # real XQL with a dataset/preset anchor
  execution: scheduled        # scheduled | realtime
  schedule: "*/10 * * * *"    # scheduled only
  search_window: 30m          # scheduled only; no shorter than the schedule period
  severity: high              # the five-value enum, or a named query field
  alert:
    name: ""
    description: ""
    fields: {}                # alert-field mapping; must carry the scenario's stitching_key
  suppression: {enabled: true, duration: 1h, fields: []}
  mitre: {tactics: [], techniques: []}
  provenance: {authored_by: "", authored: "", tenant_accepted: []}
```

**Gate before any authoring.** The names and enums above must be pinned to what
a tenant imports: one rule exported from a real tenant, or the live reference
for `POST /public_api/v1/correlations/insert`. The doc link the repo's own
operation pack carries for that endpoint returned 404 on 2026-11-10 and I could
not confirm the field list from public documentation. Until that anchor exists
the block above is the shape of the decision, not a format claim.

"Deployable" means *structurally complete and lint-clean*. It does not mean a
tenant accepted it. `provenance.tenant_accepted` stays empty until a DC saves
the rule in a real tenant, and `tenant-verified` stays 0.

**`validate.py`.**

| Check | Rule | Level |
|---|---|---|
| V1 | no `deploy` block: intent-only. Counted and printed (`correlation rules: 209 intent-only · 0 deployable`). Exit code unchanged | info |
| V2 | a `deploy` block must be complete: XQL present, anchored and lint-clean like a BIOC body; a scheduled rule has a schedule and a window; severity in the enum; a non-empty alert name; no placeholder tokens. Half a block never downgrades to intent-only | ERROR |
| V3 | every `KIND(name)` term in `logic` resolves to a detection on the same card | WARN for existing cards, ERROR from a sequence cut-off (the `IOC_WARN_FROM_SEQ` precedent) |
| V4 | from the same cut-off, a rule that a scenario declares must carry `deploy` | ERROR, phase 3 |

Existing stubs are grandfathered by card sequence, in the open, the way IOC
provenance was. The schema gains `deploy` with `additionalProperties: false`.

**`coverage_report.py`.** `correlation_share` is `Correlation` step-detections
over all step-detections: 139 of 1377, 10.1% against the 10% floor. It counts
expectations, so a stub counts in full.

* Report two numbers: `correlation_declared_share` (today's figure; the old key
  stays as an alias for one release) and `correlation_deployable_share`, the
  share whose resolved rule is `deployable`. Printed together:
  `Correlation 10.1% declared · 0.0% deployable (0 of 139)`.
* `make coverage-strict` keeps gating on the declared share through phases 1
  and 2. Gating on the deployable share now would turn the detection job red
  with no way to green it.
* Phase 3 adds `correlation_deployable_floor` as a measured ratchet, the
  `hq_signal_floor` pattern: it starts at the measured value and only rises.
  From the V4 cut-off, a new `Correlation` step-detection counts toward the 10%
  floor only when its rule is deployable.

## 7. Getting the content out (read-only)

| Route | Input | Output |
|---|---|---|
| `GET /api/scenarios/{id}/tenant-content` | a corpus scenario or a saved draft row | the manifest |
| `POST /api/scenarios/drafts/tenant-content` | an unsaved draft, the same body the save route takes | the manifest; persists nothing |

One resolver, `core/engine/tenant_content.py`, backs both routes. The canvas
derives its nodes in the browser from the raw cards (`GET /api/ttps/{id}`,
already served) so it follows every edit without a round trip. That leaves two
implementations of the status logic, one per language, so a shared fixture
(`spec/tenant_content_cases.json`) is asserted by both pytest and vitest. It
is the same guard `spec/identity_harness.json` gives the Python and Go sides
of the identity harness.

```json
{
  "scenario_id": "SIM-AIACC-003", "catalog_loaded": true,
  "tenant_verified": 0, "write_path": false,
  "summary": {"correlation": {"total": 1, "deployable": 0, "intent_only": 1,
                              "unreadable": 0, "declared": 0, "card_only": 1, "unbound": 0}},
  "rules": [{"rule_id": "CR-AIACC-0003", "ttp_ref": "TTP-2026-0009",
             "content_status": "intent-only", "binding": "card-only",
             "logic": "XQL(...) OR XQL(...) OR XQL(...)", "logic_kind": "composition",
             "inputs": [{"kind": "xql", "name": "...", "state": "step", "steps": ["step-02"]}],
             "deploy": null}],
  "biocs": [], "iocs": [], "queries": [], "notes": []
}
```

* **Nothing is invented.** A stub is exported as `deploy: null` with its
  status. No default severity.
* **A zero is degraded.** A scenario that cites no card returns
  `NO_CARD_BINDING` in `notes`. A deploy without the card corpus returns 200
  with `catalog_loaded: false`, the `/api/uctc` convention; a caller must
  render that as degraded and never as "no content needed".
* **No write path.** Neither route contacts a tenant.
  `OP-CORRELATIONS-INSERT` stays behind `CORTEXSIM_XSIAM_ALLOW_WRITE` and this
  spec never calls it. Creating the rule is the DC's own action in the XSIAM
  console.
* **The on-disk exporter stops inventing.** `render_correlation` drops the
  `severity or "high"` default and adds `content_status`. The 190 committed
  files are regenerated in the same commit, because the export-determinism
  gate requires them to match the cards.
* **Composer affordance.** A *Tenant content* control in the canvas chrome
  shows the count and the worst status (`1 rule · intent only`), toggles the
  rail and downloads the manifest.

The app gains two routes. `docs/reference/api-and-agent-surface.md`, the
route count in `CLAUDE.md` and the CHANGELOG are updated in phase 1.

## 8. Doors

The badge conflates two facts: **where the step runs** and **how its signal
reaches Cortex**. Today only the first has a data field (`channel`), and no
corpus step sets it.

| Measured | Count |
|---|---|
| Steps, all `channel: agent` | 796 |
| ...that shell out to the EAL CLI from the beacon | 165, in 70 scenarios |
| ......naming a collector-POST plugin (log injection) | 89. Badged BVM 28 · AGT 26 · CC 25 · DC 10 |
| ......naming a live-traffic plugin | 49 |
| ......with no plugin parseable from the command | 27 |

**An agent step that shells out to the EAL CLI.** The badge shows both facts:
it *runs on* the agent host, and its *signal goes via* the EAL plugin. The
`SIM-AIACC-003` burst would read `AGT → WIRE · llm_provider_egress`. It runs
on the endpoint; a firewall observes the egress. Reading the plugin out of a
heredoc is brittle, so it becomes data: an optional per-step `emitter` (plugin
name), validated against the plugin registry, with a lint that the command
really names that plugin. 138 steps backfill mechanically and 27 by hand. Until
a step is backfilled its badge reads `AGT → EAL ?`; it does not claim a door.

**Is Broker VM delivery a real step property?** No, and it should not stay
plane-derived. It is a property of the **collector destination**. A
collector-class plugin POSTs records to the URL the operator supplies, and
that URL is a Broker VM applet, the tenant's HTTP collector or a forwarder.
The step's real property is "emits through a collector-class plugin". Which
collector is known only once `collector_url` is set; then the badge can name
it. So:

* Plugins declare `Meta.delivery: collector | wire`. Today it is derivable
  (the params model has `collector_url`: 15 collector, 12 wire). A test pins
  the declaration to that derivation, and `/api/eal/plugins` serves it.
* The door follows the emitter, never the plane. The plane says which product
  observes the signal. `NDR → BVM` and `CDR → CC` stop being door claims.
* The ENG lane holds tenant content (§3). It stops holding steps.

That gives three signal paths: **endpoint runtime**, **log injection through a
collector (the Broker VM path)** and **live traffic past a sensor**. The third
is real in the corpus and is not one of the two named in the requirement; see
§11.

This is phase 2. It touches `LANE_CATALOG`, which the Runs topology shares, so
it lands as its own change and gets its own short spec.

## 9. Phasing

| Phase | Builds | Proves | Does not prove |
|---|---|---|---|
| **1 · Surface** | rule nodes and edges (§3), both statuses (§4), chip wording (§5), the two export routes and the exporter fix (§7) | every rule on a chain's cards is visible with its true status; the export serves exactly what the cards hold | that any rule is deployable (0), or exists in any tenant; nothing about doors |
| **2 · Doors** | plugin `Meta.delivery`, step `emitter` with backfill, the two-fact badge, lane relabel (§8) | every badge is backed by declared data | that a collector ingests (the existing EAL preflight answers that) |
| **3 · Deployable rules** | the format anchor, the `deploy` block, V2 to V4, the second coverage share and its ratchet, rules authored for the 137 declared pairs first, a Markdown runbook export, a "declare" action for deployable rules | complete, lint-clean rule content exists on the card | that a tenant accepts it, or that it fires |
| **4 · Present in tenant** | a read-only check through `OP-CORRELATIONS-GET` marking each required rule present, absent or unknown before launch; a Readiness rung | the rule exists in that tenant by name | that it fires. Only a reconciled run moves `tenant-verified` |

## 10. Guards for phase 1

* **vitest, new pure module `composerContent.js`.** The classifier (intent-only,
  deployable, and the raise on an unrecognised shape), the composition parser,
  binding, input resolution with all three term states, and chip ownership
  including the raise on an unknown type.
* **vitest, `composerGraph.test.js`.** Rule nodes and edges are derived; they
  are absent from `draftToApi`, `draftSnapshot` and the emitted YAML; adding
  them leaves `edited` false; `spineProblems` returns the same result with and
  without them.
* **Shared fixture.** `spec/tenant_content_cases.json`, asserted by vitest and
  pytest.
* **pytest.** The resolver and both routes: no outbound call, no invented
  severity, the two degraded shapes, a 404 for an unknown scenario. A corpus
  tripwire asserts `deployable == 0` across all 209 rules, so the day that
  changes it is a deliberate edit.
* **Existing fork guard.** Every loaded scenario's fork body still validates
  against `DraftScenarioSchema`.
* **Real browser.** `ui/tests/e2e/12-composer-correlation-story.spec.ts`
  through installed Chrome: `SIM-AIACC-003` shows `CR-AIACC-0003` marked
  intent-only and not declared, and step-02 reads `run · 3 queries`. Screenshot
  attached to the report.

Every new guard is observed failing with its change reverted.

## 11. Decisions

**One question.** The requirement names two injection paths: endpoint runtime,
or fired in through the Broker VM. The corpus has a third. 49 steps, the
`SIM-AIACC-003` burst among them, send live traffic that a firewall or SaaS
sensor observes; nothing reaches a collector. Name it as a third path
(recommended), or fold it under one of the two? Phase 1 does not depend on the
answer; phase 2 does.

**Defaults I take unless struck.**

1. No "declare" action for an intent-only rule (§4).
2. `coverage-strict` keeps gating on the declared share until phase 3 (§6).
3. Phase 1 fixes the on-disk exporter's invented severity, regenerating 190
   files (§7).
4. BIOC and IOC stay chips; only correlation rules become nodes (§3).
5. The Run lens is untouched in phase 1 (§3).

## 12. Deliberately not done

* **Writing XQL for the 209 rules.** Phase 3, behind the format anchor.
* **Editing a rule in the Composer.** The Inspector is read-only for rules.
* **Draggable rule nodes.** No schema home; `composer_layout` prunes non-step
  keys.
* **Corpus hygiene the new view exposes.** 43 `Analytics` expectations resolve
  to a card query, 16 step-detections carry no card reference, and 4 resolve to
  nothing. Phase 1 shows them truthfully and changes none.
* **BIOC proof.** BIOC bodies are real and lint-clean, and none has been
  imported into a tenant. The same ladder applies; this spec does not climb it.


## 13. Phase 1 as built

Verified: `cd ui && npx vitest run` (138 files, 1758 tests), the Python suites
for the resolver, routes and exporter, and
`ui/tests/e2e/12-composer-correlation-story.spec.ts` through installed Chrome
against a host-run SimCore and the Vite dev server. **Not verified:** the
bundle baked into a Docker image (no image was built), and anything against a
tenant.

Where the build differs from the text above:

* **Rail placement (§3).** "One row below the lowest step" drew the rail
  through the cards: a step card is taller than the row pitch. The rail now
  clears the measured bottom of the lowest card (`opts.stepHeights`), and the
  canvas refits once when a rule node is first measured, unless the DC has
  already panned or zoomed.
* **Content port (§3).** Every step outside the Run lens mounts one inert bottom
  port. Mounting it only when a rule exists left the rule with no edge, because
  the card loads after React Flow measures the node.
* **`declared` (§4).** A step declares a rule when ANY of its expectations
  resolves to the rule's id, not only one typed `Correlation`. On today's
  corpus the two readings give the same answer.
* **`unbound` (§4).** Three reasons are reported: `no-reference`, `no-rule` and
  `not-a-rule` (the id resolves to a query or BIOC, not a rule). A step citing
  a card that has not loaded is not judged.
* **Inputs (§3).** A `KIND(name)` term is looked up under its own kind, then
  under every other kind on the card, and reports the kind it resolved to. The
  cards write `Analytics(name)` for detections they hold as queries.
* **Logic (§3).** A fourth `logic_kind`, `unparsed`, covers a term that never
  closes. The corpus has none (a test pins that).
* **Exporter (§7).** The on-disk exporter is strict: it renders the intent-only
  shape and raises on any other, rather than classifying. A test pins its
  status to the engine classifier for every corpus rule.
* **Lanes lens (§3).** Rules sit in the ENG band in the columns after the last
  step, so a rule cannot land on a step that is itself in that band.
* **Not done from §5.** The Inspector's per-detection rows still show the bare
  type. The query `purpose` from the card and both statuses for a Correlation
  expectation are shown on the rule node and the rule Inspector, not on the
  step's detection rows.
* **Not done from §6.** `validate.py` V1 (the info count line) and the second
  coverage share. Both report numbers the manifest and the tripwire test
  already carry; they move with V2 to V4 in phase 3.
* **Route count (§7).** The count was 133 in the docs and is 150 measured (on `dev` @ e964501
  plus this branch); the
  documented counting one-liner returns 4 on FastAPI 0.141. Both are corrected
  in `docs/reference/api-and-agent-surface.md` §0.

**Counted by the phase-1 resolver** (`engine.tenant_content.build_manifest`
over all 193 scenarios and 191 cards, 2026-11-10). The §1 table came from a
throwaway script; these replace it.

| Fact | Count |
|---|---|
| (scenario, rule) pairs | 212, from 209 card rules. 191 scenarios carry at least one; 175 carry exactly one, 12 two, 3 three, 1 four |
| Content status | **0 deployable** · 212 intent-only · 0 unreadable |
| Binding | 137 declared · 75 card-only |
| Declared and intent-only | **137**: scored expectations with no importable rule behind them |
| Logic line | 192 composition · 20 prose · 0 unparsed |
| Edge into the rule | 204 `feeds` · 8 `card` |
| Inputs named by a logic line | 627 on a step · 12 on the card only · 34 not on the card under that name |
| `Correlation` expectations with no rule | 2 (1 `no-rule`, 1 `no-reference`) |
| Other content the manifests list | 529 queries · 358 BIOCs · 117 IOCs · 215 built-in detectors (per scenario, de-duplicated) |

The 34 unresolved inputs are down from the 59 the script reported: names now
resolve across kinds and may carry their own parentheses.
