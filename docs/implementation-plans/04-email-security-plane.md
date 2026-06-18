# Implementation Plan 04 — EMAIL Security Line of Defense (Proofpoint TAP + Microsoft 365) as a New Detection Plane

> Output path: `docs/implementation-plans/04-email-security-plane.md`
> Brainstorm item #5 / §2c · Branch base: `ultracode/full-revamp` · Author: Henry Reed
> NOTE TO IMPLEMENTER: This is a self-contained NET-NEW plane. It does NOT depend on Plan 01 (ABIOC) or Plan 02 (XDM modeling). Plan 02 is the natural predecessor for the `[MODEL: dataset=…]` artifact kind but is **not a blocker** (see §2) — modeling-rule XQL ships inline in the BIOC/XQL `logic` bodies the catalog already resolves. **UNRESOLVED (research pass 2):** whether a *first-party PANW Email Security product* exists vs. raw third-party log ingestion. This plan assumes **raw ingestion only** and asserts no product — see §1 and §7.

---

## 1. Goal

Add **EMAIL** as a new active Cortex detection plane — the email line of defense — modeling phishing / BEC / impersonation as third-party log ingestion of **Proofpoint TAP** (`proofpoint_tap_raw`) and **Microsoft 365** (`msft_o365`) into XSIAM/NG-SIEM, plus the correlation that stitches the email signal to the endpoint/identity follow-on. This broadens the detection surface because email is **a genuinely missing first-class line of defense** — the corpus models 14 planes but none of the #1 initial-access vector (phishing/BEC). Per the brainstorm: "XSIAM (3.x/NG-SIEM) ingests Proofpoint TAP into `proofpoint_tap_raw` with shipped `[MODEL: dataset=proofpoint_tap_raw, content_id=\"ProofpointTAP\"]` parsing+modeling rules; a parallel M365 connector exists." Brainstorm confidence: **high · 3-0 adversarial vote** (PANW Proofpoint TAP docs primary + `Palo-Cortex/soc-proofpoint-tap` GitHub).

The plane is modeled as **third-party log ingestion + correlation**, NOT as a first-party PANW product surface. The brainstorm §6.3 explicitly leaves open: "is there a first-party Email Security *product* vs. raw log ingestion? The marketing page conflated them." We therefore do **not** assert a product exists; the data sources are the customer's own Proofpoint TAP / M365 feeds wired into XSIAM, and detection = parsing/modeling of those datasets + correlation to the endpoint/identity stitch. The PANW "~10K detectors / 2.6K ML models across … email" figures are round unaudited vendor numbers — cite descriptively only, never as efficacy benchmarks (brainstorm §2c ⚠️).

This mirrors the existing **ITDR** plane exactly: synthetic, shape-true audit events POSTed to an operator-supplied log collector that the customer has already wired into XSIAM — never a real Proofpoint/M365 tenant call (forbidden by the standalone no-Cortex-API design). The `idp_signin_emulator` plugin (`core/eal_simulator/plugins/idp_signin_emulator.py`) is the direct template.

---

## 2. Dependencies & ordering

**Must land first:** none. Self-contained net-new plane. `msft_o365` is **already** a known dataset (`detection_scanner/scripts/validate.py` line 120) — only `proofpoint_tap_raw` is new.

**Soft predecessor (NOT a blocker): Plan 02 (XDM Parsing + Modeling Rules).** The `[MODEL: dataset=proofpoint_tap_raw, content_id="ProofpointTAP"]` modeling shape is plan 02's first-class artifact kind. Until plan 02 lands, the modeling-rule XQL ships **inline in the BIOC/XQL `logic`/`query` body** of each EMAIL card — the only fields `core/engine/ttp_catalog.py` resolvers read and `validate.py` lints. Migrate to the plan-02 artifact kind later; no rework of scenarios needed (they reference cards by `detection_id`, not by artifact kind).

**No relation to Plan 01 (ABIOC).** EMAIL cards use the existing `BIOC | XQL | Analytics | Correlation | IOC` vocabulary; no new `detection_type`.

**Commit sequence (one PR, ordered commits so each is independently green):**
1. **Plane enum + descriptor** — add `"EMAIL"` to `scenario_loader.validate_plane` allowed-set; add `core/planes/email.py` (real `PlaneDescriptor`) + wire into `core/planes/__init__.py`; add the EMAIL gloss to `scenarios/_schema.yml` plane doc block. Run pytest — green with zero scenarios (additive enum + descriptor).
2. **Data source registry + validator dataset** — add `SRC-PROOFPOINT-TAP-DOCS` + `SRC-MICROSOFT-365-DOCS` to `detection_scanner/sources/source-registry.json` (bump `registry_version` 1.3.0→1.4.0); add `"proofpoint_tap_raw"` to `KNOWN_DATASETS` in `validate.py`. Run `validate.py --quiet` (still 0 fail — additive).
3. **EAL email_emitter plugin** — `core/eal_simulator/plugins/email_emitter.py` + `tests/eal/test_email_emitter.py`. Auto-registered by the package registry (no wiring). Run pytest.
4. **TTP card family** — `TTP-2026-0081..0084` (phishing cred-link, malicious attachment, BEC/impersonation, thread-hijack). `validate.py` green; regenerate `detection_scanner/exports/` and commit so `git diff --exit-code detection_scanner/exports/` stays clean.
5. **EMAIL scenarios** — `scenarios/email/sim-email-001..004.yml`. Boot the prod image; assert 79 scenarios loaded, 0 REJECTED, 0 dangling `ttp_ref`/`adapter_ref`, every `detection_id` resolves. Update doc counts.

---

## 3. Change points

| File | Current state | Change |
|------|---------------|--------|
| `core/engine/scenario_loader.py` | `validate_plane` allowed-set (lines 220-232) is `{EDR, CDR, NDR, ITDR, CLOUD_APP, ANALYTICS, AI_ACCESS, AIRS, AI_SPM, BROWSER, KOI, ASM, CSPM, TIM}` (14 planes). `DETECTION_TYPES` frozenset (lines 31-33) is `{BIOC, XQL, Analytics, Correlation, IOC}`. | **EDIT** — add `"EMAIL"` to the `allowed` set in `validate_plane` (one line, with a comment `# Email line of defense — Proofpoint TAP / M365 ingestion + phishing/BEC correlation`). `DETECTION_TYPES` is **unchanged** (EMAIL reuses the existing vocabulary). This is the ONLY enforcing change — `validate_plane` is the single gate. |
| `scenarios/_schema.yml` | Plane doc block (lines 30-39): `One of: EDR \| CDR \| NDR \| ITDR \| CLOUD_APP \| ANALYTICS \| AI_ACCESS \| AIRS \| AI_SPM \| BROWSER \| KOI` — already **stale** (omits ASM/CSPM/TIM that the loader allows). | **EDIT (doc-only)** — append `\| EMAIL` to the enum and add a gloss line: `EMAIL — Email security: Proofpoint TAP (proofpoint_tap_raw) / Microsoft 365 (msft_o365) log ingestion; phishing / BEC / impersonation + endpoint/identity stitch`. Doc-only — the loader does not read `_schema.yml`. (Optionally also backfill the missing ASM/CSPM/TIM gloss while here.) |
| `core/planes/__init__.py` | Imports + `ALL_PLANES` tuple (lines 23-54) + `PLANE_REGISTRY` + `__all__` (lines 72-92) cover 14 active planes; every active plane ships a descriptor. | **EDIT** — add `from planes.email import EMAIL_PLANE`; insert `EMAIL_PLANE` into `ALL_PLANES` (after `ITDR_PLANE` keeps it near the other ingestion/identity planes, or at end — order only drives UI listing); add `"EMAIL_PLANE"` to `__all__`. `PLANE_REGISTRY` / `get_plane` / `list_planes` rebuild automatically from `ALL_PLANES`. |
| `detection_scanner/sources/source-registry.json` | `registry_version: "1.3.0"` (line 3). Has `SRC-PROOFPOINT-TR` (Proofpoint *Threat Research* blog, tier 2, lines 402-415) but **no** Proofpoint-TAP-ingestion-docs or M365-connector-docs source. `validate.py` check #6 requires every `metadata.source_refs[]` + `references[].publisher_id` to resolve to a registered `SRC-` id (lines 368-381). | **EDIT** — add `SRC-PROOFPOINT-TAP-DOCS` (PANW Proofpoint TAP ingestion docs + `Palo-Cortex/soc-proofpoint-tap` GitHub, `publisher_class: vendor-docs`, `primary: true`) and `SRC-MICROSOFT-365-DOCS` (M365 / Defender-for-Office-365 connector docs, `vendor-docs`). Bump `registry_version` → `"1.4.0"`, `updated_at`. Without these, every EMAIL card's `source_refs` is an unknown id → validate.py FAIL (check #6). |
| `detection_scanner/scripts/validate.py` | `KNOWN_DATASETS` (lines 117-125) **already contains `msft_o365`** (line 120) but **NOT `proofpoint_tap_raw`**. A `dataset =` source not in the set is WARN (lines 243-245). GAP-12 lint loop (lines 425-438) lints `biocs[].logic` / `xql_queries[].query` (`require_dataset=True`) + `correlation_rules[].logic`. | **EDIT** — add `"proofpoint_tap_raw"` to `KNOWN_DATASETS` (one token). `msft_o365` needs no change. No lint-loop change — EMAIL cards use the existing bioc/xql/correlation kinds the loop already handles. |
| `detection_scanner/scripts/export_artifacts.py` + `detection_scanner/exports/` | CI regenerates exports then runs `git diff --exit-code detection_scanner/exports/` (`.github/workflows/ci.yml` lines 146-152). | **NO code edit** — the exporter already renders biocs/xql/correlation. **MUST** run `export_artifacts.py` after adding the 4 cards and **commit** the regenerated `exports/sigma|xql|correlation/TTP-2026-008{1..4}.*` in the same commit, or the determinism gate fails. (NOTE: the CI gate is `git diff --exit-code`, NOT `sha256sum -c` — the earlier stub mislabeled it.) |
| `CLAUDE.md` + `docs/reference/README.md` + `docs/reference/scenario-catalog.md` + `docs/reference/eal-plugin-catalog.md` | Counts: 75 scenarios; detection-plane table lists 14 active planes; EAL plugin catalog lists 13 plugins (and states "no plugins for EDR, CDR, CSPM, ASM, TIM, Analytics"). | **EDIT** — bump to 79 scenarios; add EMAIL row to the plane table (15 planes); add `email_emitter` to the EAL catalog (14 plugins); add 4 cards to the corpus count; add SIM-EMAIL-001..004 to the scenario catalog; update the "all N detection_id slugs resolve" tally for the new cards' detection objects. |

---

## 4. New artifacts

- **`core/planes/email.py`** — real `PlaneDescriptor` (NOT a stub — every active plane ships one; `core/planes/itdr.py` is the template):
  ```python
  EMAIL_PLANE = PlaneDescriptor(
      id="EMAIL",
      name="Email Security",
      cortex_engine="Cortex XSIAM / NG-SIEM (Proofpoint TAP + M365 ingestion)",
      detection_types=["XQL", "Analytics", "Correlation", "IOC"],
      primary_sources=[
          "EAL: email_emitter",
          "Proofpoint TAP (proofpoint_tap_raw)",
          "Microsoft 365 / Defender for Office 365 (msft_o365)",
      ],
      key_techniques=[
          "T1566.002",  # Phishing: Spearphishing Link
          "T1566.001",  # Phishing: Spearphishing Attachment
          "T1656",      # Impersonation (BEC)
          "T1534",      # Internal Spearphishing (thread hijack lateral)
          "T1598",      # Phishing for Information
      ],
      default_identity="container-runtime",
      summary=(
          "Validates phishing / BEC / impersonation / malicious-link+attachment / "
          "thread-hijack detection via Proofpoint TAP + M365 log ingestion, stitched "
          "to the endpoint/identity follow-on."
      ),
  )
  ```
- **`core/eal_simulator/plugins/email_emitter.py`** — synthetic email-security event emitter. Direct clone of `idp_signin_emulator.py` structure: a `BaseSimulation` subclass with `Meta.name = "email_emitter"`, `Meta.params_model`, `async def run()`; dry-run + live paths; ECS audit events; per-event `cortexsim_run_id` + `X-Simulation-Run-ID` header; `ctx.authorise(host)` against the campaign allowlist; injectable `httpx.AsyncClient`. **Provider builders:** `_proofpoint_tap_event()` (TAP SIEM-API `messagesBlocked` / `messagesDelivered` / `clicksPermitted` shape → `proofpoint_tap_raw`) and `_m365_event()` (Defender-for-O365 / message-trace + `EmailEvents` shape → `msft_o365`). **Event patterns:** `phishing_link`, `malicious_attachment`, `bec_impersonation`, `thread_hijack`. Auto-registered by `PluginRegistry` (drop-in; `core/eal_simulator/plugins/__init__.py` documents zero-wiring). Never POSTs to a real tenant — only to an operator-supplied collector URL.
- **`tests/eal/test_email_emitter.py`** — mirrors the existing idp_signin_emulator tests: stub transport (no network), assert each pattern emits the right event count + shape, `proofpoint_tap_raw`/`msft_o365` markers present, dry-run emits exactly one info event, allowlist denial raises.
- **4 TTP cards** `detection_scanner/ttps/TTP-2026-008{1..4}-*.json` (next free is 0080, **reserved by Plan 01**, so EMAIL starts **0081**):
  - **TTP-2026-0081** — phishing credential-harvest link · `T1566.002` + `T1598`
  - **TTP-2026-0082** — malicious attachment (macro / ISO / HTML-smuggling) · `T1566.001`
  - **TTP-2026-0083** — BEC / executive impersonation · `T1656` + `T1534`
  - **TTP-2026-0084** — thread-hijack reply-chain phishing · `T1566.002` + `T1534`
  Each card carries `detections.xql_queries[]` (with `[MODEL: dataset=proofpoint_tap_raw …]`-shaped logic inline in the body), `detections.biocs[]`, `detections.correlation_rules[]` (`CR-EMAIL-0001..4`, stitching `proofpoint_tap_raw` → `xdr_data` endpoint/process + `msft_o365`/ITDR sign-in on recipient+time-window), `detections.iocs[]` (sender domain / payload URL / attachment hash), `panw_mapping.products[]` → `cortex-xsiam`, one `use_case`/`test_case` pair per card (weight ≤ 1.0). `metadata.source_refs` ⊇ all `references[].publisher_id` and every id resolves to a registered `SRC-` (the two new ones). Exactly one `primary: true` reference per validator checks 6-9.
- **4 scenarios** `scenarios/email/sim-email-001..004.yml` — ids `SIM-EMAIL-001..004`, `plane: EMAIL`, `status: active`, each mirroring the `sim-itdr-001` pattern: step-01 pre-flight (`cli describe email_emitter | jq -e '.eal_targets | length > 0'`), step-02 emit the pattern via `email_emitter` against the collector, step-03 the endpoint/identity stitch follow-on. `expected_detections[]` set `type` + `ttp_ref: TTP-2026-008N` + `detection_id` resolving to the card. `methodology_family: F1` (Signal Injection & Detection Accuracy) for single-plane, `F2` for the stitch step. `infra_modules_needed: [base]` (no IaC mail target — see §5e).
- **`docs/implementation-plans/04-email-security-plane.md`** — this plan.

---

## 5. Engine/schema specifics

**(a) The ONLY enforcing change is `scenario_loader.validate_plane`.** Lines 220-232 hold the single authoritative plane allow-set. `_schema.yml` is a documentation artifact the loader never reads (header line: "It is a documentation artifact — NOT a runnable scenario"). The `PlaneDescriptor` registry (`core/planes/`) is declarative API/UI metadata — it does **not** gate scenario loading. So a scenario with `plane: EMAIL` loads the instant `"EMAIL"` is in `validate_plane`'s set; the descriptor + schema doc are for completeness/UX, not validation. (We still ship all three — every active plane has a descriptor by convention.)

**(b) No new Pydantic fields, no new detection_type.** EMAIL reuses `XQL | Analytics | Correlation | IOC` (note: no `BIOC`-only requirement — email detection is log-analytics-shaped). `StepExpectedDetection.validate_type` and `ScenarioSchema.validate_detection_types` already accept these.

**(c) detection_id resolution invariant (494/494 must stay whole).** Every `expected_detections[].detection_id` on an EMAIL scenario MUST resolve via `core/engine/ttp_catalog.py` to a card detection object, or `_warn_dangling_ttp_refs` logs a warning and the "all slugs resolve" invariant breaks. Resolution rules (from `_schema.yml` lines 197-204): `biocs[].name → bioc-<slug>`, `xql_queries[].name → xql-<slug>`, `correlation_rules[].rule_id` verbatim (e.g. `CR-EMAIL-0001`), `iocs[] → ioc-<type>-<value>`. Author an **explicit, slug-matching `detection_id`** on each card detection and reference exactly that string. (This is the discipline the ITDR cards followed — see SIM-ITDR-001 referencing `xql-itdr-001-impossible-travel-…`.)

**(d) Loader auto-discovery.** `_find_yaml_files` (lines 283-297) walks `scenarios/` recursively, skipping only `{probes, packages, campaigns}` and `_schema.yml`. A new `scenarios/email/` directory is picked up with zero loader change. The S-09 drift check (`_warn_scenario_hygiene`) is generic — a scenario whose declared `detection_types` matches its steps' emitted `type`s produces no warning.

**(e) MITRE coverage rollup fuses automatically.** `core/api/mitre.py` aggregates `card_techniques` over the corpus; the new `T1566.001/.002`, `T1656`, `T1534`, `T1598` techniques (some net-new to the corpus) flow into `/api/mitre/coverage` and the heatmap with no rollup edit — the fold is generic over technique ids.

**(f) Modeling-rule shape (plan-02 forward-compat).** XSIAM's shipped Proofpoint modeling rule is declared `[MODEL: dataset=proofpoint_tap_raw, content_id="ProofpointTAP"]`. Until Plan 02 introduces a first-class `modeling` artifact kind, embed this declaration **inside the `xql_queries[].query` body** (it is valid XQL and the only field the catalog + lint read). The validator's `_DATASET_SOURCE_RE` (line 140) extracts `dataset = proofpoint_tap_raw` from `MODEL`/`dataset` clauses and checks it against `KNOWN_DATASETS` — hence the step-2 addition of `proofpoint_tap_raw`.

---

## 6. Validation & acceptance criteria

1. **Detection-corpus validator stays green:** `python3 detection_scanner/scripts/validate.py --quiet` exits 0 (was 140 pass / 0 fail). The 4 new cards pass schema validation, the GAP-12 lint on `xql_queries[].query` + `biocs[].logic` (balanced quotes/parens, a `dataset =` anchor resolving to a `KNOWN_DATASET` — `proofpoint_tap_raw` now registered, `msft_o365` already was, no placeholder tokens), check #6 (`source_refs` resolve — the two new `SRC-` ids registered), checks 7-9 (publisher_id ⊆ source_refs, exactly one primary), and the UC/TC weight-sum check.
2. **Export determinism stays green:** after adding the 4 cards, run `python3 detection_scanner/scripts/export_artifacts.py` then `git diff --exit-code detection_scanner/exports/` → clean (CI `detection` job lines 146-152). Commit the regenerated `TTP-2026-008{1..4}` export files.
3. **Real loader in the prod image:** boot `cortex-pov-engine-simcore:latest` (`docker compose up -d --build`); loader log must show **79 scenarios loaded, 0 REJECTED, 0 dangling `ttp_ref`, 0 dangling `adapter_ref`**. `SIM-EMAIL-001..004` present.
4. **detection_id resolution:** every `expected_detections[].detection_id` on the 4 EMAIL scenarios resolves via `catalog.find(ttp_ref, detection_id)` — no "unresolved TTP card" warning. The repo-wide slug-resolution tally goes from N/N to N+M/N+M (M = EMAIL detections referenced) with zero unresolved.
5. **pytest:** `.venv/bin/pytest tests/ -v` (and the full backend suite inside the prod image, baseline 1596 pass / 80 skip) stays green. New `tests/eal/test_email_emitter.py` passes. Add a loader test asserting `plane: EMAIL` validates and `plane: EMAILX` rejects; add a planes-registry test asserting `get_plane("EMAIL")` returns the descriptor.
6. **Plane API:** `GET /api/planes` (or the plane catalog feed) includes EMAIL with its `to_dict()` payload; existing plane keys unchanged (additive).
7. **EAL CLI:** `python3 -m scripts.eal_simulator.cli describe email_emitter` returns non-empty `eal_targets` (the pre-flight step asserts this).
8. **Exact target counts after merge:** scenarios 75 → **79**; TTP cards 76 → **80** (0080 from Plan 01 + 0081-0084 here — if Plan 01 has not merged, EMAIL alone takes 76 → **80** only once both land; EMAIL's own delta is +4 to **79 cards / 79 scenarios** if landed solo before Plan 01); active planes 14 → **15**; EAL plugins 13 → **14**. Update `CLAUDE.md`, `docs/reference/{README,scenario-catalog,eal-plugin-catalog}.md` to match. (NOTE: card-count baseline — `ls detection_scanner/ttps/*.json` is 76; CLAUDE.md's "72 TTP cards" line is stale and should be reconciled to the on-disk count while editing.)

---

## 7. Effort & risk

**Effort: M.** ~5 commits (see §2). Plane enum + descriptor + schema doc are trivial additive edits (~30 LOC across 4 files). The `email_emitter` plugin is a structural clone of `idp_signin_emulator.py` (~400 LOC, mostly the four provider/pattern builders) plus its test. The real work is authoring 4 lint-clean, validator-passing TTP cards with faithful `proofpoint_tap_raw`/`msft_o365` field shapes + 4 scenarios, regenerating exports, and reconciling counts across 4 docs.

**Top 2 risks:**
1. **`proofpoint_tap_raw` / `msft_o365` XDM field-name fidelity (medium-likelihood, high-severity).** `validate.py` lints XQL *syntax* and dataset *names* — it does NOT verify that the field names inside the query bodies (e.g. TAP's `messagesBlocked.threatsInfoMap[].classification`, M365's `EmailEvents.ThreatTypes`) actually match the shipped XSIAM modeling-rule output. A card that lints clean can still be wrong against the real XDM schema, producing a POV detection that never fires. Mitigation: ground every field name in the PANW Proofpoint TAP docs + `Palo-Cortex/soc-proofpoint-tap` modeling rules (the new `SRC-PROOFPOINT-TAP-DOCS` source); manual review gate before promotion; **close brainstorm §6.3 (product-vs-ingestion) in research pass 2 first** — if a first-party product exists it may add/rename datasets and change the modeling story.
2. **Export-determinism CI failure (high-likelihood, low-severity).** If the 4 cards are added but `export_artifacts.py` is not re-run and the regenerated `detection_scanner/exports/` files not committed, `git diff --exit-code detection_scanner/exports/` fails CI. Mitigation: make card-add + exporter-run + export-commit a single atomic commit (step 4); run `make -n ci` / `validate.py` locally before pushing.

**Secondary watch-items:** (a) Do NOT build an IaC mail-server target — an SMTP server produces no `proofpoint_tap_raw`/`msft_o365` records without a live tenant (forbidden by the standalone-no-Cortex-API design); the `email_emitter` EAL plugin posting shape-true events to a collector is the correct, ITDR-consistent signal source. (b) Do NOT assert a first-party PANW Email Security product — UNRESOLVED per brainstorm §6.3; the plane is third-party ingestion + correlation until research pass 2 confirms otherwise. (c) Cite the "~10K detectors / 2.6K ML models" vendor figures descriptively only, never as benchmarks.
