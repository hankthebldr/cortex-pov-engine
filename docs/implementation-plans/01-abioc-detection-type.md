# Implementation Plan 01 — ABIOC (Analytics Behavioral IOC) as a First-Class Detection-Content Type

> Output path: `docs/implementation-plans/01-abioc-detection-type.md`
> Brainstorm item #2 / §2b · Branch base: `ultracode/full-revamp` · Author: Henry Reed
> NOTE TO IMPLEMENTER: This is the FRAMEWORK unlock. Plan 03 (CDR/K8s lab port → sockshop ABIOC, SIM-CDR-009+) depends on this landing first. Land this whole plan before any ABIOC-bearing scenario port.

---

## 1. Goal

Add **ABIOC** (Analytics Behavioral IOC) as a sixth first-class Cortex `detection_type` across the engine, card schema, catalog, and coverage rollup, and ship the first ABIOC content card + scenario (the sockshop/microservices behavioral-anomaly case from `sources/xsiam-prisma-cdr-lab/2.0/sockshop-k8-ABIOC`). This broadens the detection surface because ABIOC is a **separately-documented, PANW-authored, auto-tuned behavioral-ML detection type** — "a single event of suspicious behavior with an identified chain of causality … leverag[ing] user, endpoint, and network profiles … Cortex tailors each ABIOC to your environment and continually tunes/delivers new ABIOCs with content updates" — distinct from hand-authored BIOC. Brainstorm confidence: **high · 3-0 adversarial vote** (PANW XSIAM BIOC+ABIOC pages, primary). Until now the corpus could only model BIOC|XQL|Analytics|Correlation|IOC; the whole `xsiam-prisma-cdr-lab` is built around ABIOC, so without this type those ~10 lab scenarios cannot be wrapped with faithful detection content. We treat ABIOC as a **causality-anchored, validated behavioral detector** in the coverage rollup — like Analytics but, unlike a bare named `analytics_module`, backed by deployable XQL-shaped logic so it counts as validated detection (not mapped-but-not-logic). UEBA is explicitly **deferred** (see §7) — the brainstorm flags it as "related-but-distinct identity/entity analytics — don't conflate," and it has no lab content to back a scenario; adding it now would create an empty enum value.

---

## 2. Dependencies & ordering

**Must land first:** none. This is the root framework plan; nothing else is a prerequisite.

**Blocks:** Plan 03 (CDR/K8s lab port). SIM-CDR-009 (sockshop ABIOC) and any future ABIOC card cannot validate `detection_type: ABIOC` until the enum + parser + rollup edits here are merged.

**Commit sequence (one PR, ordered commits so each is independently green):**
1. **Engine + schema enum** — add `ABIOC` to `DETECTION_TYPES` (scenario_loader), the doc enum (`scenarios/_schema.yml`), and `detections.abiocs[]` to `ttp-entry.schema.json`. Run pytest + `validate.py` — both must stay green with zero new content (additive enum, no card references it yet).
2. **Catalog parser + slug + rollup** — add `_parse_abioc_list` + `abioc-` slug to `ttp_catalog.py`, extend `card_detection_kind_counts` with `abioc`, extend `core/api/mitre.py` rollup `detection_kinds` dict + `with_abioc` summary key. Run pytest.
3. **Validator + exporters** — teach `validate.py` to lint `detections.abiocs[].logic` (require_dataset=True) and `export_artifacts.py` to render abiocs into the xql/sigma paste buffers. Regenerate `detection_scanner/exports/` and commit so `git diff --exit-code detection_scanner/exports/` stays clean.
4. **First ABIOC content card** — `TTP-2026-0080-sim-cdr-009.json` with an `abiocs[]` block. `validate.py` green.
5. **First ABIOC scenario** — `scenarios/cdr/cdr-009-sockshop-microservices-abioc.yml` referencing the card via `expected_detections[].type: ABIOC` + `ttp_ref`/`detection_id`. Boot the prod image; assert 0 rejected, 0 dangling refs, detection_id resolves. Update doc counts.

---

## 3. Change points

| File | Current state | Change |
|------|---------------|--------|
| `core/engine/scenario_loader.py` | `DETECTION_TYPES = frozenset({"BIOC", "XQL", "Analytics", "Correlation", "IOC"})` (lines 31-33). `StepExpectedDetection.validate_type` + `ScenarioSchema.validate_detection_types` both gate on this frozenset. | **EDIT** — add `"ABIOC"` to the frozenset and the docstring comment block (lines 24-33). No validator-body change needed (both validators read the frozenset). The S-09 drift check (`_warn_scenario_hygiene`) needs no change — it compares declared vs emitted sets generically. |
| `scenarios/_schema.yml` | `detection_types` enum doc (lines 41-54): `BIOC \| XQL \| Analytics \| Correlation \| IOC` with per-type gloss. Per-step `expected_detections[].type` doc (line 187): `One of: BIOC \| XQL \| Analytics \| Correlation \| IOC`. `detection_id` match-table (lines 197-204). | **EDIT** — add `ABIOC` to both enum lists + a gloss line: `ABIOC — Analytics Behavioral IOC: PANW-authored, auto-tuned behavioral-ML detection with an identified causality chain (distinct from hand-authored BIOC)`. Add to the detection_id match-table: `detections.abiocs[].name → slug "abioc-<slugified-name>"`. |
| `detection_scanner/schema/ttp-entry.schema.json` | `detections` object (lines 486-619) has `iocs`, `biocs`, `xql_queries`, `correlation_rules`, `analytics_modules`. `additionalProperties: false` (line 488). | **EDIT** — add a sibling `abiocs` array (shape mirrors `biocs`: required `name`, `description`, `logic`; optional `severity`, `mitre_technique_ids`, `suppression_window_seconds`; plus optional `behavioral_profile` enum `statistical\|machine-learning` and `causality_anchor` string to capture the documented ABIOC distinction). Because `additionalProperties:false`, the new key MUST be added explicitly or every card with abiocs is rejected. |
| `core/engine/ttp_catalog.py` | `_parse_entry` (lines 307-310) calls `_parse_bioc_list`/`_parse_xql_list`/`_parse_correlation_list`/`_parse_ioc_list`. `_slug(name, prefix)` (line 346). `card_detection_kind_counts` (lines 259-280) returns `{"bioc","xql","correlation","ioc","analytics"}`. `DetectionCard.kind` docstring lists `bioc\|xql\|correlation\|ioc\|analytics`. | **EDIT** — add `_parse_abioc_list(ttp_ref, abiocs)` (copy `_parse_bioc_list` body, `kind="abioc"`, slug prefix `"abioc"`, read `logic`/`description`/`severity`/`mitre_technique_ids`); call it in `_parse_entry` (extends `cards`). Add `"abioc": len(det.get("abiocs") or [])` to `card_detection_kind_counts`. `find()` needs NO change (it resolves by `(ttp_ref, detection_id)` over `_by_pair`, which the new cards populate). |
| `core/api/mitre.py` | `_ensure` seeds `detection_kinds` dict with 5 keys (lines 220-223). `/coverage` summary computes `with_correlation` (line 365). Module docstring describes the kinds. | **EDIT** — add `"abioc": 0` to the `_ensure` `detection_kinds` seed; the fold loop (lines 288-289) is generic over `kind_counts.items()` so it picks up `abioc` automatically once the catalog emits it. Add a `with_abioc` summary key (mirror `with_correlation`). ABIOC counts as validated-ML, so no `validated:false` discount applies (contrast: named `analytics_modules`). |
| `detection_scanner/scripts/validate.py` | GAP-12 lint loop (lines 426-438) lints `biocs[].logic`, `xql_queries[].query`, `correlation_rules[].logic`. | **EDIT** — add a loop linting `detections.get("abiocs", [])` `[].logic` with `require_dataset=True` (ABIOC logic is XQL-shaped against a real dataset, exactly like BIOC). |
| `detection_scanner/scripts/export_artifacts.py` | `render_xql` (line 152) reads `biocs`+`xql_queries`; `render_sigma` (line 82) reads `biocs`. CI runs `git diff --exit-code detection_scanner/exports/`. | **EDIT** — include `abiocs` in `render_xql` (emit one paste-buffer section per ABIOC, labeled `# ABIOC: <name> (behavioral-ML, causality-anchored)`) and `render_sigma` (one doc per ABIOC, `status: experimental`). Then regenerate exports and commit. Skipping this means abioc logic silently never reaches the DC paste buffers AND risks an export-determinism diff once a card carries abiocs. |
| `docs/reference/README.md` + `CLAUDE.md` + `docs/reference/scenario-catalog.md` | Counts: 75 scenarios / 76 cards / detection_types `BIOC\|XQL\|Analytics\|Correlation\|IOC`; CDR "8 scenarios". | **EDIT** — bump to 76 scenarios / 77 cards; add `ABIOC` to the detection_type vocabulary line; CDR → 9 scenarios (add SIM-CDR-009); detection-object count + the "494 detection_id slugs resolve" tally updated for the new card's objects. |

---

## 4. New artifacts

- **`detection_scanner/ttps/TTP-2026-0080-sim-cdr-009.json`** — First ABIOC content card. Backs SIM-CDR-009 (sockshop/microservices behavioral anomaly). Carries a `detections.abiocs[]` block (e.g. abioc "Sock-Shop Microservice Anomalous East-West Connection Spike" + "Anomalous Container Process-Tree Deviation from Learned Baseline"), each with XQL-shaped `logic` against `xdr_data`/`network_story`, `behavioral_profile: machine-learning`, `causality_anchor` describing the user/endpoint/network profile chain. Plus supporting `xql_queries[]` (validation) and a `correlation_rules[]` stitching the ABIOCs into one behavioral-anomaly incident. `panw_mapping.products[]` → `cortex-cloud` (cloud-posture/analytics) + `cortex-xsiam` (analytics). One `use_case`/`test_case` pair (UC-CLOUD-009 / TC-CLOUD-009A, weight ≤ 1.0). Exactly one primary reference; `source_refs` ⊇ all `publisher_id`s (validator checks 6-9).
- **`scenarios/cdr/cdr-009-sockshop-microservices-abioc.yml`** — Scenario id `SIM-CDR-009`, `plane: CDR`, `detection_types: [ABIOC, Correlation, XQL]`, `status: active`. Steps deploy/exercise the sockshop microservices workload (grounded in `sources/xsiam-prisma-cdr-lab/2.0/sockshop-k8-ABIOC/microservices-demo-weaveworks.yaml`) and trigger the behavioral anomaly; each step's `expected_detections[]` sets `type: ABIOC` + `ttp_ref: TTP-2026-0080` + `detection_id: abioc-<slug>` resolving to the card. `methodology_family: F1` (Signal Injection & Detection Accuracy) or `F2` if stitched. `infra_modules_needed: [base, cdr]`.
- **`docs/implementation-plans/01-abioc-detection-type.md`** — this plan (the output artifact).

Regenerated (not new, but committed): the abioc-derived files under `detection_scanner/exports/sigma/TTP-2026-0080.yml` and `detection_scanner/exports/xql/TTP-2026-0080.xql` (produced by step-3 exporter run).

---

## 5. Engine/schema specifics

**(a) Detection-type enum — exact current value being extended.** `core/engine/scenario_loader.py` lines 31-33:
```python
DETECTION_TYPES: frozenset[str] = frozenset(
    {"BIOC", "XQL", "Analytics", "Correlation", "IOC"}
)
```
→ becomes `{"BIOC", "XQL", "Analytics", "Correlation", "IOC", "ABIOC"}`. Both `StepExpectedDetection.validate_type` (lines 63-71) and `ScenarioSchema.validate_detection_types` (lines 237-248) gate on this single frozenset, so the one-line addition flows through both. No new Pydantic field on the scenario side — ABIOC is a `type` enum value, referenced exactly like BIOC via `expected_detections[].type` + `ttp_ref` + `detection_id`.

**(b) Card schema — ABIOC is a NEW `detections.abiocs[]` kind, NOT a label on `analytics_modules`.** Rationale grounded in the corpus: `analytics_modules` (schema lines 592-617) are explicitly "NAMED references, not testable BIOC/XQL logic … cannot be validated post-run," and its `validated` flag is **defined but consumed nowhere in `core/`** (verified: `grep -rn validated core/` returns no analytics consumer; `card_detection_kind_counts` counts `len(analytics_modules)` regardless). ABIOC, per the brainstorm, carries "an identified chain of causality" and deployable behavioral logic — so it belongs alongside `biocs[]` as a logic-bearing, lint-able, validated kind, not folded into the un-validated `analytics_modules` channel. New schema block (sibling of `biocs`, lines 533-555 shape):
```jsonc
"abiocs": {
  "type": "array",
  "description": "Analytics Behavioral IOCs — PANW-authored, auto-tuned behavioral-ML detections with an identified causality chain. Logic-bearing (XQL-shaped), validated like a BIOC; distinct from hand-authored biocs and from named analytics_modules.",
  "items": {
    "type": "object", "additionalProperties": false,
    "required": ["name", "description", "logic"],
    "properties": {
      "name": {"type":"string"}, "description": {"type":"string"},
      "logic": {"type":"string"},
      "severity": {"type":"string","enum":["informational","low","medium","high","critical"]},
      "mitre_technique_ids": {"type":"array","items":{"type":"string","pattern":"^T[0-9]{4}(\\.[0-9]{3})?$"}},
      "behavioral_profile": {"type":"string","enum":["statistical","machine-learning"]},
      "causality_anchor": {"type":"string","description":"The user/endpoint/network profile chain the ABIOC anchors to."},
      "suppression_window_seconds": {"type":"integer","minimum":0}
    }
  }
}
```
Because `detections.additionalProperties:false` (line 488), this key must be added explicitly.

**(c) Catalog parser + slug.** `DetectionCard.kind` gains `abioc` as a valid value (docstring line 41). New `_parse_abioc_list` mirrors `_parse_bioc_list` (lines 356-373) with `kind="abioc"` and `_slug(name, "abioc")` → slug prefix **`abioc-`** (collision-proof against bioc-/xql-/correlation-/ioc- by construction, per the `_slug` design comment lines 346-353). Wire into `_parse_entry` after the bioc line (line 307): `cards.extend(_parse_abioc_list(ttp_ref, detections_raw.get("abiocs") or []))`. `find()` is unchanged — it resolves over `_by_pair[(ttp_ref, detection_id)]` which the new cards populate; scenario `detection_id: abioc-<slug>` resolves automatically.

**(d) Coverage rollup.** `card_detection_kind_counts` (lines 271-280) gains `counts["abioc"] = len(det.get("abiocs") or [])`. `core/api/mitre.py` `_ensure` `detection_kinds` seed (lines 220-223) gains `"abioc": 0`; the generic fold loop (lines 288-289) requires no edit. ABIOC is treated as **validated** (causality-anchored ML with deployable logic) — it contributes to coverage exactly like bioc/xql/correlation, NOT discounted like a bare analytics_module. Add summary key `with_abioc = sum(1 for t in output if t["detection_kinds"]["abioc"] > 0)` (mirror `with_correlation`, line 365). Response stays backward-compatible (additive keys only).

**(e) Loader S-09 drift check.** `_warn_scenario_hygiene` (lines 434-474) compares `set(detection_types)` vs union of `expected_detections[].type` — generic, no edit. A scenario declaring `detection_types: [ABIOC, ...]` whose steps emit `type: ABIOC` produces no drift warning.

---

## 6. Validation & acceptance criteria

1. **Detection-corpus validator stays green:** `python3 detection_scanner/scripts/validate.py --quiet` exits 0 (was 140 pass / 0 fail). The new card must pass schema validation (the `abiocs` block + `additionalProperties:false`), the GAP-12 lint on `abiocs[].logic` (balanced quotes/parens, a `dataset =` anchor, known dataset, no placeholder tokens), and the UC/TC weight-sum check.
2. **Export determinism stays green:** after editing `export_artifacts.py` and adding the card, run `python3 detection_scanner/scripts/export_artifacts.py` then `git diff --exit-code detection_scanner/exports/` → clean. (This is the CI `detection` job's second half; commit the regenerated abioc exports.)
3. **Real loader in the prod image:** boot `cortex-pov-engine-simcore:latest` (`docker compose up -d --build`); loader log must show **76 scenarios loaded, 0 REJECTED, 0 dangling `ttp_ref`, 0 dangling `adapter_ref`**. `SIM-CDR-009` present.
4. **detection_id resolution:** every `expected_detections[].detection_id` on SIM-CDR-009 resolves via `catalog.find(ttp_ref, detection_id)` (no "unresolved TTP card" warning from `_warn_dangling_ttp_refs`). The repo-wide slug-resolution count goes from 494/494 to 494+N/494+N where N = abioc detections referenced.
5. **pytest:** `.venv/bin/pytest tests/ -v` (and the full backend suite inside the prod image, baseline 1596 pass / 80 skip) stays green. Add/extend a catalog test asserting `kind="abioc"` cards parse and `card_detection_kind_counts` returns the abioc tally; add a loader test asserting `detection_types: [ABIOC]` validates and an invalid `type: ABIOCX` still rejects.
6. **Coverage endpoint:** `GET /api/mitre/coverage` returns `detection_kinds.abioc` and `summary.with_abioc`; existing UI keys unchanged (additive-only contract preserved).
7. **Exact target counts after merge:** scenarios 75 → **76**; TTP cards 76 → **77**; detection_types vocabulary 5 → **6** (adds ABIOC); CDR plane 8 → **9** scenarios. Update `CLAUDE.md`, `docs/reference/README.md`, `docs/reference/scenario-catalog.md` to match.

---

## 7. Effort & risk

**Effort: M.** ~5 commits (see §2). Engine/schema/catalog/rollup edits are small and additive (~60 LOC across 6 files); the real work is authoring one lint-clean, validator-passing ABIOC card + one scenario grounded in the sockshop lab spec, plus regenerating exports and updating three count docs.

**Top 2 risks:**
1. **Export-determinism CI failure (high-likelihood, low-severity).** If `export_artifacts.py` is taught to render abiocs but the regenerated `detection_scanner/exports/` files are not committed (or the exporter is NOT extended while a card carries abiocs that some other export path picks up), `git diff --exit-code detection_scanner/exports/` fails CI. Mitigation: make the exporter edit + `export_artifacts.py` run + commit a single atomic commit (step 3), and run `make validate-detection` locally before pushing.
2. **Coverage-rollup double-counting / validated-semantics drift (medium-likelihood, medium-severity).** ABIOC is intentionally treated as validated (unlike named `analytics_modules`, whose `validated` flag is currently dead code). If a future contributor also lists the same detection as an `analytics_module` on the card, the technique's `detection_kinds` would count both `abioc` and `analytics`, inflating apparent depth. Mitigation: document on the card schema that an ABIOC is the logic-bearing form and should NOT be duplicated as a named analytics_module; add a validator WARN (not fail) if a card has both an abioc and an analytics_module with overlapping names. Secondary watch-item: do NOT add `UEBA` to the enum in this plan (deferred — no backing content; an empty enum value would let scenarios declare an unvalidatable type).
