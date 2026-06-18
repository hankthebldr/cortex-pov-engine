# Implementation Plan 02 — XDM Parsing + Modeling Rules as a Detection Substrate

> Output path: `docs/implementation-plans/02-xdm-parsing-modeling-rules.md`
> Brainstorm item #1 / §2a · Branch base: `ultracode/full-revamp` · Author: Henry Reed
> NOTE TO IMPLEMENTER: This is a SUBSTRATE plan, not a detection-type plan. It adds the
> normalization layer *beneath* detections. It is INDEPENDENT of Plan 01 (ABIOC) — they
> touch the same files (`ttp-entry.schema.json`, `validate.py`, `ttp_catalog.py`,
> `export_artifacts.py`) but at disjoint keys; if both land, merge the schema/catalog
> edits, do NOT collide on the new card/scenario IDs (this plan uses TTP-2026-0081 +
> SIM-CDR-XDM, Plan 01 uses TTP-2026-0080 + SIM-CDR-009). BOUNDED: model + surface the
> substrate so a DC can paste a `[MODEL: dataset=…]` block into XSIAM and prove the chain.
> Do NOT build a real XSIAM parser/normalizer engine — CortexSim generates signal IN, it
> does not normalize logs itself.

---

## 1. Goal

Model **XDM Parsing Rules + Modeling Rules** as a first-class detection *substrate* — the
normalization layer beneath every detection — so a POV can prove the full
**raw `<source>` log → XDM-normalized field → BIOC/Analytics fires** chain instead of
starting from "assume data is already normalized." Modeling rules "map your logs into a
single, unified data model … regardless of source or dataset," written in XQL under a
`[MODEL: dataset=…]` declaration that maps raw event JSON → XDM (Cortex Data Model)
fields; normalization is **opt-in per source / schema-on-read**, so un-modeled logs stay
searchable but are *not* analytics-grade. Modeling rules are therefore the **gating
substrate** for analytics-grade detection. Brainstorm confidence: **high · 3-0 adversarial
vote** (PANW Developer Guide — Data modeling rules, primary). Until now the corpus jumps
straight to BIOC/XQL/Analytics logic against canonical datasets (`xdr_data`,
`panw_ngfw_traffic_raw`, …) and never models the onboarding/normalization step a real DC
must perform when a customer's log source is not pre-parsed. The lab already ships
modeling-shaped content (`sources/xsiam-prisma-cdr-lab/2.0/tests-toolkits/csa-xql` +
`container_info.sql`: `dataset = xdr_data | … | alter privileged = json_extract_scalar(actor_container_info, "$.privileged")`) — raw JSON field extraction normalized into addressable XDM-ish fields — but it lives as loose SQL outside the card/scenario corpus. This plan brings the substrate into the card schema as `detections.modeling_rules[]` and proves the chain in one scenario.

**Substrate, not a detection type.** A modeling rule does not *fire*; it *enables*
firing. So we deliberately do **not** add a `detection_type` enum value (contrast Plan
01's ABIOC). The `DETECTION_TYPES` frozenset stays at five. Modeling rules are surfaced as
a logic-bearing, paste-able card kind that the coverage rollup counts **informationally
only** (substrate present, not "validated detection") — they must not inflate detection
depth.

---

## 2. Dependencies & ordering

**Must land first:** none. Independent of Plan 01. (If Plan 01 lands first, the
`ttp-entry.schema.json` `detections` object and `ttp_catalog._parse_entry` already have an
extra sibling key/parser — add `modeling_rules` alongside, no rework.)

**Blocks:** nothing hard. Enables (soft) the CDR/K8s lab port (Plan 03) and the Email
plane (brainstorm §2c) to ship faithful "raw source → modeled → detection" stories rather
than assuming normalized data.

**Commit sequence (one PR, ordered commits so each is independently green):**
1. **Card schema** — add optional `detections.modeling_rules[]` to `ttp-entry.schema.json`
   (sibling of `biocs`; `additionalProperties:false` makes this mandatory or every card
   carrying modeling rules is rejected). Run `validate.py` — green with zero new content
   (additive optional array, no card uses it yet).
2. **Validator** — teach `validate.py` to (a) recognize the `[MODEL: dataset=…]` opener +
   raw dataset names without false WARNs, and (b) lint `detections.modeling_rules[].logic`
   bodies. Run `validate.py` — still green.
3. **Catalog parser + rollup** — add `_parse_modeling_list` (`kind="modeling"`,
   slug prefix `modeling-`) to `ttp_catalog.py`; add a `modeling` key to
   `card_detection_kind_counts` (informational — NOT folded into validated-detection
   depth). Run pytest.
4. **Exporter** — add `render_modeling()` to `export_artifacts.py` writing
   `exports/modeling/<TTP>.xql` (a paste-ready `[MODEL: …]` buffer for the DC). Extend
   `clean_exports()` to cover the new `modeling/` subdir. Regenerate + commit so
   `git diff --exit-code detection_scanner/exports/` stays clean.
5. **First content card** — `TTP-2026-0081-sim-cdr-xdm.json` with a `modeling_rules[]`
   block (derived from the real `container_info.sql`) + a BIOC/XQL that fires on the
   normalized field. `validate.py` green.
6. **First substrate scenario** — `scenarios/cdr/cdr-xdm-modeling-proof.yml`
   (`SIM-CDR-XDM`) proving raw container telemetry → XDM-normalized
   `container.privileged` → BIOC fires. Boot the prod image; assert 0 rejected, 0 dangling
   refs, `detection_id` resolves (incl. the `modeling-` slug). Update doc counts.

---

## 3. Change points

| File | Current state | Change |
|------|---------------|--------|
| `detection_scanner/schema/ttp-entry.schema.json` | `detections` object (lines 486-619) has `iocs`, `biocs`, `xql_queries`, `correlation_rules`, `analytics_modules`. `additionalProperties: false` (line 488). | **EDIT** — add a sibling `modeling_rules` array (shape below, §5b): required `name`, `dataset`, `logic`; optional `description`, `vendor`, `product`, `content_id`, `xdm_fields[]` (the XDM fields the rule populates). Because `detections.additionalProperties:false` (line 488), the key MUST be added explicitly or every card with modeling rules is schema-rejected. |
| `detection_scanner/scripts/validate.py` | `KNOWN_DATASETS` frozenset (lines 117-125) lists canonical Cortex datasets. `DATASET_ANCHORS = ("dataset =", "dataset=", "preset =", "preset=")` (line 100). GAP-12 lint loops (lines 426-438) lint `biocs[].logic`, `xql_queries[].query`, `correlation_rules[].logic`. `_lint_xql_grammar` (lines 228-274) WARNs on unrecognised datasets + unknown stage verbs. | **EDIT (3 parts)** — (a) add the modeling-rule *raw* datasets this corpus introduces to `KNOWN_DATASETS` (e.g. `cortexsim_container_raw`) so the modeling-rule source name does not WARN; (b) add a modeling-rule lint loop in `main()` that calls `lint_detection_body(..., require_dataset=True)` on each `modeling_rules[].logic` — the `[MODEL: dataset=…]` opener already satisfies the `DATASET_ANCHORS` substring check (`"dataset="` appears inside `[MODEL: dataset=…]`) and `_DATASET_SOURCE_RE` (lines 140-142) extracts the dataset name from it, so the registry check works unchanged once the raw dataset is registered; (c) NO new stage-verb is needed — `_split_xql_stages` treats the `[MODEL: …]` header as the source segment (segment 0, skipped by the verb loop at line 264) exactly like a bare `dataset =` source. |
| `core/engine/ttp_catalog.py` | `_parse_entry` (lines 307-310) parses bioc/xql/correlation/ioc. `DetectionCard.kind` docstring (line 48): `bioc \| xql \| correlation \| ioc \| analytics`. `card_detection_kind_counts` (lines 271-280) returns a 5-key dict. `_slug(name, prefix)` (lines 346-353). | **EDIT** — add `_parse_modeling_list(ttp_ref, modeling_rules)` (mirror `_parse_bioc_list`, `kind="modeling"`, slug prefix `"modeling"`, `logic=m.get("logic")`, `description=m.get("description")`); call it in `_parse_entry` after the ioc line. Add `"modeling": len(det.get("modeling_rules") or [])` to `card_detection_kind_counts`. Add `modeling` to the `DetectionCard.kind` docstring. `find()` needs NO change — it resolves over `_by_pair[(ttp_ref, detection_id)]`, which the new cards populate, so scenario `detection_id: modeling-<slug>` resolves automatically. |
| `detection_scanner/scripts/export_artifacts.py` | Renderers for sigma/xql/correlation/xsoar (lines 82-285). `clean_exports()` (lines 309-319) iterates `("sigma","xql","correlation","xsoar_playbook")`. `main()` (lines 357-388) calls the four renderers. CI runs `git diff --exit-code detection_scanner/exports/`. | **EDIT** — add `render_modeling(ttp)` (emits a `[MODEL: dataset=…]` paste-buffer, one section per modeling rule, header `# MODELING RULE — <name> (XDM normalization substrate)`, plus a `# XDM fields populated: …` comment from `xdm_fields[]`). Wire into `main()` writing `modeling/<TTP>.xql`; add `"modeling"` to the `clean_exports()` tuple (line 313). Then regenerate exports + commit. Skipping the exporter means the substrate never reaches the DC paste buffer AND risks an export-determinism diff once a card carries modeling rules that another path picks up. |
| `detection_scanner/exports/README.md` (generated) | Index header (lines in `write_index`, export_artifacts.py 322-342) lists the four artifact subdirs. | **REGEN** — re-running the exporter rewrites this index; the modeling artifacts appear in the per-TTP row. No manual edit; commit the regenerated file. |
| `docs/reference/README.md` + `CLAUDE.md` + `docs/reference/scenario-catalog.md` | Counts (verified 2026-06-15): **75 loadable scenarios · 76 TTP cards · 676 deployable detection objects · 14 detection planes**; detection vocabulary `BIOC \| XQL \| Analytics \| Correlation \| IOC`. CDR "8 scenarios". | **EDIT** — bump to 76 scenarios / 77 cards; add a one-line note that the card corpus now models the **XDM normalization substrate** via `detections.modeling_rules[]` (NOT a 6th detection_type — substrate, surfaced/exported but not counted as validated detection); CDR → 9 scenarios (add SIM-CDR-XDM). Detection-object tally updated for the new card's objects. Detection-vocabulary line is UNCHANGED (still five types). |

---

## 4. New artifacts

- **`detection_scanner/ttps/TTP-2026-0081-sim-cdr-xdm.json`** — First XDM-substrate card.
  Carries a `detections.modeling_rules[]` block grounded verbatim in the lab's
  `container_info.sql` / `csa-xql`: a modeling rule
  `[MODEL: dataset = cortexsim_container_raw] | alter pod_namespace = json_extract_scalar(...) | … | alter privileged = json_extract_scalar(actor_container_info, "$.privileged")`
  that normalizes raw container daemonset JSON into addressable fields, with
  `xdm_fields: ["xdm.source.container.pod_namespace", "xdm.source.container.image_name", "xdm.source.container.privileged", …]`. Plus a `biocs[]` that fires on the *normalized*
  field (e.g. a privileged-container BIOC: `dataset = xdr_data | filter … privileged = "true"`)
  and an `xql_queries[]` validation that proves the modeled field is populated. This is the
  on-card proof that the normalization substrate feeds detection.
  `panw_mapping.products[]` → `cortex-cloud` (cloud / CDR) + `cortex-xsiam` (analytics).
  One `use_case`/`test_case` pair (UC-CLOUD-NNN / TC-CLOUD-NNNA, weight ≤ 1.0). Exactly
  one primary reference (PANW Developer Guide — Data modeling rules); `source_refs` ⊇ all
  `publisher_id`s (validator checks 6-9). NOTE: pick the lowest free TTP id (current max is
  `TTP-2026-0079`; `TTP-2026-0080` is reserved by Plan 01 — use `0081`).
- **`scenarios/cdr/cdr-xdm-modeling-proof.yml`** — Scenario id `SIM-CDR-XDM`, `plane: CDR`,
  `detection_types: [BIOC, XQL]`, `status: active`. Steps deploy/exercise a privileged
  container workload (grounded in the lab's CDR/K8s content) so raw container telemetry is
  produced; each step's `expected_detections[]` references the card via `ttp_ref: TTP-2026-0081`
  + `detection_id` (one entry pointing at the modeling rule via `detection_id: modeling-<slug>`
  to make the substrate explicit, plus the BIOC entry it enables). The scenario *narrates*
  the chain in its `description`: raw `cortexsim_container_raw` → modeling rule normalizes →
  `privileged` field populated → BIOC fires. `methodology_family: F1` (Signal Injection &
  Detection Accuracy). `infra_modules_needed: [base, cdr]`. (Avoids the `SIM-CDR-009` id —
  reserved by Plan 01's ABIOC scenario.)
- **`docs/implementation-plans/02-xdm-parsing-modeling-rules.md`** — this plan (the output
  artifact).

Regenerated (not new, but committed): `detection_scanner/exports/modeling/TTP-2026-0081.xql`
(+ the BIOC/XQL exports under `sigma/`, `xql/`) and the rewritten
`detection_scanner/exports/README.md` index (produced by the step-4 exporter run).

---

## 5. Engine/schema specifics

**(a) Where parsing/modeling rules live — a `detections.modeling_rules[]` card kind, NOT
a sibling content tree.** Rationale (decided, with grounding):
- A **sibling content tree** (e.g. a new `modeling_scanner/`) would duplicate the whole
  load/validate/export/catalog machinery (`validate.py`, `export_artifacts.py`,
  `ttp_catalog.py`, the CI `detection` job) for one substrate kind, and would sever the
  scenario join — a scenario references detections by `(ttp_ref, detection_id)` over
  `catalog._by_pair` (ttp_catalog.py lines 162-176), so a separate tree could not be
  referenced by `expected_detections[]` without a parallel resolver. Rejected.
- A **6th `detection_type` enum value** would mis-model the substrate as a thing that
  *fires* and would force scenarios to declare an unvalidatable detection type (the
  problem Plan 01 §7 calls out for UEBA). Rejected — modeling is substrate, not detection.
- **Chosen:** a new optional `detections.modeling_rules[]` array on the existing card,
  living next to `biocs[]`. It rides every existing pipeline (schema validation, GAP-12
  lint, catalog parse, export, scenario `detection_id` resolution) with the smallest
  surface, and keeps the modeling rule *physically co-located* with the BIOC/XQL it
  enables — so one card is the complete "normalize → detect" proof.

**(b) Card schema — new `detections.modeling_rules[]` block.** Sibling of `biocs`
(schema lines 533-555 shape), `additionalProperties:false` per item:
```jsonc
"modeling_rules": {
  "type": "array",
  "description": "XDM Modeling/Parsing rules — the normalization substrate. Each maps a raw log source into Cortex Data Model (XDM) fields via XQL under a `[MODEL: dataset=…]` declaration. A modeling rule does NOT fire a detection; it ENABLES one (it makes a raw source analytics-grade). Surfaced + exported for the DC to paste into XSIAM, counted informationally by the coverage rollup, NOT as a validated detection.",
  "items": {
    "type": "object", "additionalProperties": false,
    "required": ["name", "dataset", "logic"],
    "properties": {
      "name": {"type": "string"},
      "description": {"type": "string"},
      "dataset": {"type": "string", "description": "The RAW source dataset this rule normalizes (e.g. cortexsim_container_raw, proofpoint_tap_raw)."},
      "vendor": {"type": "string", "description": "Source vendor, e.g. PaloAltoNetworks, Proofpoint."},
      "product": {"type": "string", "description": "Source product, e.g. Cortex, TAP."},
      "content_id": {"type": "string", "description": "Modeling-rule content id mirrored into the [MODEL: …] header, e.g. ProofpointTAP."},
      "logic": {"type": "string", "description": "Modeling-rule body. XQL under a `[MODEL: dataset=…]` opener mapping raw event JSON → XDM fields (alter / json_extract_scalar). Multi-line allowed."},
      "xdm_fields": {"type": "array", "items": {"type": "string"}, "description": "XDM fields this rule populates, e.g. xdm.source.container.privileged."}
    }
  }
}
```
Because `detections.additionalProperties:false` (line 488), this key must be added
explicitly.

**(c) The `[MODEL: dataset=…]` body format.** The `logic` body is XQL with a model
declaration header, modeled on the lab content and the cited Developer-Guide shape:
```
[MODEL: dataset = cortexsim_container_raw]
| alter xdm.source.container.pod_namespace = json_extract_scalar(actor_container_info, "$.pod_namespace")
| alter xdm.source.container.image_name    = json_extract_scalar(actor_container_info, "$.image_name")
| alter xdm.source.container.privileged    = json_extract_scalar(actor_container_info, "$.privileged")
```
Header forms accepted: `[MODEL: dataset = <raw_dataset>]` and (Proofpoint-shape)
`[MODEL: dataset=<raw_dataset>, content_id="<ContentId>"]`. The substring `dataset=` /
`dataset =` inside the header satisfies the existing `DATASET_ANCHORS` check (validate.py
line 100, 220) and `_DATASET_SOURCE_RE` (lines 140-142) extracts `<raw_dataset>` for the
`KNOWN_DATASETS` membership check — so no new anchor/parser is needed, only registering
the raw dataset name.

**(d) Proving the chain (raw → normalized → fires) in one scenario.** The chain is proven
*on the card* (modeling rule normalizes a field that the same card's BIOC filters on) and
*narrated by the scenario*. SIM-CDR-XDM's steps generate raw container telemetry; its
`expected_detections[]` carries two entries against `TTP-2026-0081`: one
`detection_id: modeling-<slug>` (substrate present) and one BIOC `detection_id` that
fires on the normalized `privileged` field. Acceptance is structural — both `detection_id`s
resolve via `catalog.find(...)` and the scenario loads with 0 dangling refs. CortexSim does
NOT execute the modeling rule (no real normalizer) — it surfaces it for the DC to deploy.

**(e) Validator changes — recognize MODEL declarations + raw datasets without false
WARNs.** Three minimal edits (no new grammar machinery):
1. Add the corpus's raw modeling datasets to `KNOWN_DATASETS` (validate.py lines 117-125),
   e.g. `"cortexsim_container_raw"` (and, when the Email plane lands, `"proofpoint_tap_raw"`).
   Without this the modeling-rule source would WARN as "unrecognised dataset."
2. Add a lint loop in `main()` (after the correlation loop, line 438):
   ```python
   for i, m in enumerate(detections.get("modeling_rules", [])):
       lint_detection_body(report, rel, f"detections.modeling_rules[{i}].logic",
                           m.get("logic"), require_dataset=True)
   ```
   `lint_detection_body` already handles the `[MODEL: …]` header: the `dataset=` anchor
   passes (check 4), `_DATASET_SOURCE_RE` extracts the raw dataset for the registry check,
   and the stage-verb loop (`_split_xql_stages`, line 263-274) treats the `[MODEL: …]`
   header as segment 0 (skipped), so the `alter` stages lint as known verbs. No false WARN.
3. NO change to `XQL_STAGE_VERBS` — modeling rules use `alter` / `filter` / `fields`,
   all already in the frozenset (lines 107-111). NO change to the SaaS-SSO coherence check
   (the container/raw datasets are not in `SAAS_SSO_DATASETS`).

**(f) Coverage rollup — substrate counts informationally, NOT as validated detection.**
`card_detection_kind_counts` (ttp_catalog.py 271-280) gains `counts["modeling"] = len(det.get("modeling_rules") or [])`.
This is exposed so the heatmap can show "this technique has a modeling-rule substrate," but
`core/api/mitre.py`'s validated-detection depth (the `with_correlation`-style summary keys)
is **not** extended for `modeling` — a modeling rule on its own does not constitute a
detection. (If the rollup's generic fold loop picks up the `modeling` key from
`kind_counts.items()`, gate it out of any "validated" summary; mirror how named
`analytics_modules` are kept out of validated depth, GAP-11.) Response stays
backward-compatible (additive key only).

**(g) `DETECTION_TYPES` frozenset is UNCHANGED.** `core/engine/scenario_loader.py` lines
31-33 stay `{"BIOC","XQL","Analytics","Correlation","IOC"}`. SIM-CDR-XDM declares
`detection_types: [BIOC, XQL]` (the things that fire); the modeling rule is referenced by
`detection_id` on an `expected_detections[]` entry whose `type` is one of the five (use
the BIOC/XQL the modeling rule enables), so the loader's S-09 drift check
(`_warn_scenario_hygiene`) and `validate_detection_types` (lines 242-248) need no edit.

---

## 6. Validation & acceptance criteria

1. **Detection-corpus validator stays green:** `python3 detection_scanner/scripts/validate.py --quiet`
   exits 0 (was 140 pass / 0 fail). The new card passes schema validation (the
   `modeling_rules` block + `additionalProperties:false`), the modeling-rule lint
   (balanced quotes/parens, the `[MODEL: dataset=…]` anchor present, `cortexsim_container_raw`
   in `KNOWN_DATASETS` so no WARN, no placeholder tokens), AND the BIOC/XQL it enables
   still lint clean. No new WARN lines (verify the modeling dataset does not trip the
   unrecognised-dataset WARN).
2. **Export determinism stays green:** after editing `export_artifacts.py` (+ `clean_exports`
   tuple) and adding the card, run `python3 detection_scanner/scripts/export_artifacts.py`
   then `git diff --exit-code detection_scanner/exports/` → clean. Commit the regenerated
   `exports/modeling/TTP-2026-0081.xql` + rewritten `exports/README.md`.
3. **Real loader in the prod image:** boot `cortex-pov-engine-simcore:latest`
   (`docker compose up -d --build`); loader log must show **76 scenarios loaded, 0
   REJECTED, 0 dangling `ttp_ref`, 0 dangling `adapter_ref`**. `SIM-CDR-XDM` present.
4. **detection_id resolution (incl. the modeling slug):** every `expected_detections[].detection_id`
   on SIM-CDR-XDM resolves via `catalog.find(ttp_ref, detection_id)` — including the
   `modeling-<slug>` entry (no "unresolved TTP card" warning from `_warn_dangling_ttp_refs`,
   scenario_loader lines 394-414). The repo-wide slug-resolution tally increases by the
   number of detection objects (incl. modeling rules) the new card carries, all resolving.
5. **pytest:** `.venv/bin/pytest tests/ -v` (and the full backend suite inside the prod
   image, baseline 1596 pass / 80 skip) stays green. Extend the catalog test
   (`tests/.../test_ttp_catalog*`) asserting a `kind="modeling"` card parses, its
   `modeling-<slug>` detection_id resolves via `find()`, and `card_detection_kind_counts`
   returns the `modeling` tally. Add a validator test asserting a `[MODEL: dataset=cortexsim_container_raw]`
   body lints clean and an unregistered raw dataset WARNs.
6. **Coverage endpoint:** `GET /api/mitre/coverage` exposes `detection_kinds.modeling`
   informationally; the validated-depth summary keys are UNCHANGED (modeling not counted as
   validated detection); existing UI keys unchanged (additive-only contract preserved).
7. **Exact target counts after merge:** scenarios 75 → **76**; TTP cards 76 → **77**;
   detection_types vocabulary **UNCHANGED at 5** (modeling is substrate, not a type); CDR
   plane 8 → **9** scenarios. Update `CLAUDE.md`, `docs/reference/README.md`,
   `docs/reference/scenario-catalog.md` to match, with the one-line substrate note.

---

## 7. Effort & risk

**Effort: M.** ~6 commits (see §2). Schema/validator/catalog/exporter edits are small and
additive (~70 LOC across 4 files); the real work is authoring one lint-clean,
validator-passing XDM-substrate card (modeling rule + the BIOC/XQL it enables, grounded in
the real `container_info.sql`) + one scenario that narrates and structurally proves the
chain, plus regenerating exports and updating three count docs. No new infra, no normalizer
engine (explicitly out of scope).

**Top 2 risks:**
1. **Export-determinism CI failure (high-likelihood, low-severity).** Two ways to trip the
   `git diff --exit-code detection_scanner/exports/` guard: (a) adding the modeling card but
   not running/committing the exporter, or (b) adding `render_modeling()` but forgetting to
   extend `clean_exports()` so a stale/missing `modeling/` artifact survives a `--clean`
   regen and produces a diff. Mitigation: make the `export_artifacts.py` edit (renderer +
   `clean_exports` tuple) + exporter run + commit a single atomic commit (step 4), and run
   `make validate-detection` (or the exporter + `git diff`) locally before pushing.
2. **Substrate mis-modeled as a detection / coverage inflation (medium-likelihood,
   medium-severity).** A modeling rule is substrate, not a detection — but it is XQL-shaped
   and logic-bearing, so the temptation (and the easy bug) is to fold the `modeling` count
   into validated-detection depth in `core/api/mitre.py`, inflating apparent coverage, or
   to add a `MODELING` detection_type and let scenarios declare an unvalidatable type.
   Mitigation: keep `DETECTION_TYPES` at five (§5g); expose `detection_kinds.modeling`
   informationally only and explicitly gate it out of any validated-depth summary (§5f),
   exactly as named `analytics_modules` are kept out (GAP-11); document on the schema that a
   modeling rule ENABLES detection and must be paired on-card with the BIOC/XQL it feeds.
   Secondary watch-item: do NOT let this plan grow into a real parser/normalizer — CortexSim
   surfaces the `[MODEL: …]` block for the DC to deploy in XSIAM; it does not execute it.
