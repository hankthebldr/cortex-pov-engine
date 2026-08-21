---
name: new-ttp-card
description: Scaffold and promote a new CortexSim TTP card (TTP-2026-NNNN) — computes the next free id, drafts a schema-valid card with detection_scanner/scripts/generate_card.py (into ttps/_drafts/), guides enrichment per the RUNBOOK, validates, promotes to ttps/, and regenerates the deterministic exports. Use when authoring a detection card, or wiring a scenario's expected_detections to a new card. Peer of new-scenario.
disable-model-invocation: true
---

# new-ttp-card

Author a new TTP card that passes `validate.py` and resolves every scenario
`detection_id` slug that points at it. Cards are the JSON detection catalog under
`detection_scanner/ttps/*.json`, contract-governed by
`detection_scanner/schema/ttp-entry.schema.json` (JSON Schema 2020-12, strict,
`additionalProperties: false`). This skill **wraps the existing generator and the
RUNBOOK promotion flow** — it does not hand-roll the JSON; `generate_card.py` already
lifts identity / MITRE / execution / detection-shell / panw_mapping from a scenario.

> Authoritative procedure: `detection_scanner/RUNBOOK.md` → "How to add a new TTP"
> (steps 1–11). This skill automates the mechanics around those steps; read the
> RUNBOOK for the enrichment judgement calls.

## Inputs to gather (ask only for what's missing)

- **Anchor scenario** (preferred) — path to the `scenarios/**/*.yml` whose
  `expected_detections` this card backs. `generate_card.py` seeds the draft from it.
  If there's no scenario yet, say so — you'll copy a template card instead.
- **Card intent** — the detection story (what fires, on which plane, which Cortex
  module), and the **primary source** (a Unit 42 / vendor report already in
  `detection_scanner/sources/source-registry.json`, or one to add).

## Steps

1. **Compute the next free id.** Never reuse, even a deprecated one:
   ```bash
   grep -rhoE '"id":\s*"TTP-2026-[0-9]+"' detection_scanner/ttps/*.json \
     | grep -oE 'TTP-2026-[0-9]+' | sort -V | tail -1
   ```
   Increment, zero-pad to 4 digits → `TTP-2026-NNNN`.

2. **Draft it.** From an anchor scenario, let the generator do the skeleton
   (output lands in `detection_scanner/ttps/_drafts/`, always `status: draft`):
   ```bash
   python3 detection_scanner/scripts/generate_card.py \
     scenarios/<plane>/<file>.yml TTP-2026-NNNN
   ```
   With no scenario, copy the nearest-shape existing card as a template (RUNBOOK:
   endpoint→`TTP-2026-0002`, cloud→`0003`, identity→`0001`) into `_drafts/` and
   rewrite every field. Do **not** edit `sources/**` (submodule/registry — add a
   source via its own entry, not in-tree edits) — the `guard-paths` hook will block
   protected paths.

3. **Enrich per the RUNBOOK** (the parts the generator can't infer):
   - Exactly one `references[]` has `primary: true`; its `publisher_id` also appears
     in `metadata.source_refs[]`; both resolve to `sources/source-registry.json`.
   - MITRE: every `technique_id` = `Txxxx`, `subtechnique_id` = `Txxxx.xxx` under its
     parent; tactic ids = `TAxxxx`.
   - `execution.payload` is what the engine literally runs (`${VAR}` placeholders
     declared in `input_variables[]`); `expected_artifacts[]` make the test
     deterministic; `cleanup` present unless `safety_class: safe-by-design`.
   - `detections`: write the real 2–4 BIOC/XQL/correlation/IOC/**ABIOC** objects (and
     `modeling_rules[]` if this card proves the XDM substrate). Each BIOC references a
     technique that's also in `mitre_attack.techniques[]`.
   - `panw_mapping`: per module `coverage_tier` / `rule_ids` / `license_required` /
     `evidence_query`; one use case + ≥1 test case with `success_criteria[]` and
     `expected_score_weight ∈ [0,1]` (weights per use case sum to ≤ 1.0).

4. **Match the scenario's slugs.** If a scenario references this card, its
   `detection_id` slugs must resolve to objects you just wrote (rules in
   `scenarios/_schema.yml` / `core/engine/ttp_catalog.py`): `bioc-<slug of name>`,
   `xql-<slug of name>`, correlation `rule_id` verbatim, `ioc-<type>-<value>`,
   `abioc-<slug of name>`, `modeling-<slug of name>`. Grep to confirm — don't assume.

5. **Validate, then promote.** The draft must pass before it leaves `_drafts/`:
   ```bash
   python3 detection_scanner/scripts/validate.py            # must exit 0
   ```
   Once green, move the file out of `_drafts/` into `detection_scanner/ttps/`
   (the engine globs `ttps/*.json` and skips `_drafts/`), flip `status` to `active`,
   and re-run `validate.py` on the promoted location.

6. **Regenerate deterministic exports** (the export-determinism CI gate compares
   bytes; the PostToolUse hook also nudges this on TTP edits):
   ```bash
   python3 detection_scanner/scripts/export_artifacts.py --clean
   git diff --stat detection_scanner/exports/
   ```
   Do **not** commit `manifest.json` — per the RUNBOOK it is gitignored, not an
   engine input, and never a CI artifact.

7. **Offer to review & commit.** Suggest the `detection-corpus-reviewer` subagent for
   a second pass, then commit the promoted card + regenerated exports on one boundary
   (named-file `git add`, no `-A`).

Report the assigned `TTP-2026-NNNN`, the promoted path, the `validate.py` verdict,
and which scenario slugs now resolve to it.
