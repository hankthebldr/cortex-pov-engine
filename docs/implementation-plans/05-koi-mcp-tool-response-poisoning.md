# 05 — KOI MCP Tool-Response Poisoning (runtime trust-gap)

> Implements brainstorm item **#6 / §2d** (`docs/brainstorm/2026-06-16-detection-substrate-expansion.md`).
> Output path: `docs/implementation-plans/05-koi-mcp-tool-response-poisoning.md`.
> Verdict after reading the codebase: **ENHANCEMENT of SIM-KOI-002 + one net-new sibling scenario SIM-KOI-006.** SIM-KOI-002 ("Hidden Prompt Injection in MCP Tool Response") *names* tool-response injection but its actual detection logic is **connect-time / static-scan** (BIOC `bioc-koi-002-...` keys on `action_process_image_command_line contains \"pa-firewall-mcp\"` + grep markers in `server.py`; the runtime angle is only one weak AIRS step). The genuine **runtime trust-gap** OWASP documents — a malicious tool RESULT entering the live agent context at invocation time, no static artifact on disk — is not modeled. So: deepen SIM-KOI-002's card with CVE anchors + ATLAS mapping, and add SIM-KOI-006 as the runtime detection scenario.

## 1. Goal
Close the named 2025-2026 MCP attack surface OWASP calls the **connect-time↔runtime trust gap**: "Tool descriptions are reviewed once … Tool responses go straight into the LLM context with no equivalent check" (`docs/brainstorm/.../detection-substrate-expansion.md` §2d, **confidence high · 3-0**, anchored to CVE-2025-49596 and CVE-2025-54136, OWASP MCP Top-10 MCP04). The existing KOI pack ships connect-time vectors (typosquat MCP, malicious-MCP source, backdoored PyPI, malicious skill/extensions) and SIM-KOI-002 detects the malicious server *source*; none model a benign-registered server returning a poisoned `tools/call` **result at runtime**. This broadens the detection surface by adding a runtime-content-inspection detection family (BIOC/XQL/Correlation over the tool-RESULT body and the agent process that consumed it) that is distinct from artifact-scan/egress detection, exercising the same `agentic_egress` EAL transport without any engine schema change.

## 2. Dependencies & ordering
- **No hard dependency on other plans.** This plan uses only the existing `detection_type` enum (`BIOC | XQL | Analytics | Correlation | IOC`) — verified `{"BIOC","XQL","Analytics","Correlation","IOC"}` in `core/engine/scenario_loader.py:32`. It does **not** require the ABIOC enum work (brainstorm item #2 / plan 02), which is not yet in the tree (`grep ABIOC core/ detection_scanner/schema/` returns nothing).
- **Optional deepening hook:** if plan 02 (ABIOC enum) lands first, add one ABIOC detection to the SIM-KOI-006 card for the behavioral-ML "agent emits anomalous post-tool-call action" angle. Sequence this plan to *not block* on it.
- **Commit sequence:**
  1. Pack content: add the runtime poisoned-response fixture under `sources/cortex-malicious-agentic-pack/mcp/pa-firewall-mcp/` (new sibling file) + README row.
  2. New card `TTP-2026-0080-sim-koi-006.json` + enrich `TTP-2026-0043-sim-koi-002.json` (CVE refs + `atlas_techniques`). Run validator.
  3. New scenario `scenarios/koi/sim-koi-006-mcp-runtime-tool-response-poisoning.yml`.
  4. Regenerate exports (`export_artifacts.py`) + commit `detection_scanner/exports/` diff (CI determinism gate, `.github/workflows/ci.yml:152` `git diff --exit-code detection_scanner/exports/`).
  5. Doc count bumps (CLAUDE.md, `docs/reference/README.md`, `docs/reference/scenario-catalog.md`).

## 3. Change points
| File | Current state | Change |
|---|---|---|
| `scenarios/koi/sim-koi-006-mcp-runtime-tool-response-poisoning.yml` | does not exist | **NEW** scenario, `scenario_id: SIM-KOI-006`, `plane: KOI`, `detection_types: [BIOC, XQL, Correlation]`. 3 steps (see §4). |
| `detection_scanner/ttps/TTP-2026-0080-sim-koi-006.json` | does not exist (highest existing is `TTP-2026-0079`) | **NEW** card backing SIM-KOI-006 (runtime tool-response BIOC/XQL/Correlation). |
| `detection_scanner/ttps/TTP-2026-0043-sim-koi-002.json` | exists; `references[]` has Unit42 (primary) + MITRE T1656; no CVE anchors; `mitre_attack` has **no** `atlas_techniques` | **EDIT**: add CVE-2025-49596 / CVE-2025-54136 reference rows + a `[CVE]` mention in `identity.description`; add `mitre_attack.atlas_techniques` (`AML.T0051.001 LLM Prompt Injection: Indirect`); add a `detection_engineering` bullet pointing at SIM-KOI-006 for the runtime angle; bump `entry_version` + changelog. **Do not** renumber `detection_id`s (494/494 slugs resolve — GAP-4). |
| `sources/cortex-malicious-agentic-pack/mcp/pa-firewall-mcp/server.py` | static `DIAGNOSE_REPLY` dict with `[SYSTEM_OVERRIDE]` + `AKIA0000CORTEXSIMCANARY` (read at `:19-28`) | **EDIT (minimal) or leave as-is** — reused as the poisoned-response payload source. Prefer leave-as-is; the new fixture references it. |
| `sources/cortex-malicious-agentic-pack/mcp/pa-firewall-mcp/runtime-poisoned-response.json` | does not exist | **NEW** static fixture: a captured JSON-RPC `tools/call` *result* envelope carrying the poisoned `content[].text` (the runtime artifact the agent would receive). Lets SIM-KOI-006 step-1 show the on-the-wire result shape, and gives the `agentic_egress` POST a distinct body. |
| `sources/cortex-malicious-agentic-pack/README.md` | layout table lists `mcp/pa-firewall-mcp/` as "Malicious MCP server source" (`:30`) | **EDIT**: add a note that `runtime-poisoned-response.json` is the runtime tool-RESULT fixture for SIM-KOI-006. |
| `CLAUDE.md` | "KOI … 5 scenarios" (`:128`); "75 loadable scenarios" + "76 TTP cards" + "494/494 slugs" (`:133`) | **EDIT**: KOI 5→6; 75→76 scenarios; 76→77 cards; bump the resolved-slug total to new count (494 + N new SIM-KOI-006 detection_ids). |
| `docs/reference/README.md` | counted ground truth 75/76 | **EDIT** counts. |
| `docs/reference/scenario-catalog.md` | KOI section lists sim-koi-001..005 | **EDIT**: add SIM-KOI-006 row. |

**No code/engine file changes.** `core/engine/scenario_loader.py`, `core/engine/ttp_catalog.py`, `_schema.yml`, `core/eal_simulator/plugins/agentic_egress.py` are **unchanged** — the existing `agentic_egress` `component=mcp_server` path already tarballs `mcp/<name>` and POSTs it (`agentic_egress.py:70-75, _send_one`), so the new fixture rides the existing transport.

## 4. New artifacts
**Scenario — `SIM-KOI-006`** (`scenarios/koi/sim-koi-006-mcp-runtime-tool-response-poisoning.yml`):
- Purpose: prove Cortex detects a poisoned MCP **tool RESULT at runtime** (content inspection of the JSON-RPC `tools/call` response body + the agent process that consumed it), distinct from SIM-KOI-002's source-scan.
- `uc_ref: UCS-KOI-06`, `tc_ref: TC-KOI-06`, `uc_name: "MCP Runtime Tool-Response Content Inspection"`, `tc_name: "Benign-registered MCP server returns instruction-injecting tools/call result"`.
- `plane: KOI`; `detection_types: [BIOC, XQL, Correlation]`; `execution_identity.default: container-runtime`; `push_supported/pull_supported: true`; `required_content: [{repo: hankthebldr/cortex-malicious-agentic-pack}]`; `infra_modules_needed: [base]`; `external_tools` mirrors SIM-KOI-002 (`adapter_ref: TOOL-CORTEX-AGENTIC-PACK`, `cortexsim-eal-cli`).
- MITRE: `mitre_tactic: TA0001` Initial Access; `mitre_technique: T1656` Impersonation; `additional_techniques: [T1059, T1552.001]`.
- KPI block: copy SIM-KOI-002's F1 / Detection Accuracy ≥90 / `moat_tier: MOAT` (per S-07 per-plane convention).
- Steps (each `ttp_ref: TTP-2026-0080`):
  1. **step-01** — display the runtime poisoned result fixture: `cat sources/cortex-malicious-agentic-pack/mcp/pa-firewall-mcp/runtime-poisoned-response.json`. expected: KOI / **BIOC** `detection_id: bioc-koi-006-mcp-tools-call-result-content-embeds-instruction-injection-at-runtime`.
  2. **step-02** — drive `agentic_egress` (`component: mcp_server`, `artifact_name: pa-firewall-mcp`, `target_url: https://cortexsim-canary.invalid/mcp/`) so NGFW sees the result body on the wire. expected: NDR / **XQL** `detection_id: xql-koi-006-ngfw-json-rpc-tools-call-result-body-carries-injection-markers`; KOI / **XQL** `detection_id: xql-koi-006-agent-process-consumed-poisoned-tool-result`.
  3. **step-03** — show the runtime consequence (agent acts on injected instruction → credential-file read attempt, canary). expected: KOI / **Correlation** `detection_id: CR-KOI-0006`; ANALYTICS / **XQL** `detection_id: xql-koi-006-stitch-tool-result-injection-with-followon-credential-access`.
- NOTE: each `detection_id` MUST equal `_slug(card detection name, prefix)` per `core/engine/ttp_catalog.py:346-353` (lowercase, non-alnum→`-`, collapse `--`, prefix `bioc-`/`xql-`; correlation uses `rule_id` verbatim, e.g. `CR-KOI-0006`). Author the card detection `name` fields to slugify to the ids above.

**Card — `TTP-2026-0080-sim-koi-006.json`** (filename MUST start with `id`, validator check #5):
- `id: TTP-2026-0080`, `status: active`, schema_version `1.0.0`.
- `metadata.tags`: `["plane-koi","vector-mcp-runtime-injection","vector-supply-chain","agentic","severity-high"]`; `source_refs: ["SRC-MITRE-ATTACK","SRC-MITRE-ATLAS","SRC-OWASP-LLM","SRC-UNIT42"]` (all four exist in `detection_scanner/sources/source-registry.json`).
- `identity`: name "MCP Runtime Tool-Response Poisoning (T1656 / T1059)"; description must state the connect-time↔runtime trust gap, cite CVE-2025-49596 + CVE-2025-54136, and contrast with SIM-KOI-002 (source-scan vs runtime result).
- `mitre_attack.techniques`: T1656 (TA0001), T1059 (TA0002), T1552.001 (TA0006); `mitre_attack.atlas_techniques`: `[{atlas_id:"AML.T0051.001", name:"LLM Prompt Injection: Indirect", atlas_tactic:"Initial Access"}]` (nested under `mitre_attack`, `additionalProperties:false` — verified shape on `TTP-2026-0012:mitre_attack.atlas_techniques`).
- `detections`: 1 BIOC (runtime result content), 2 XQL (NGFW result-body + agent-consumed result), 1 Correlation `rule_id: CR-KOI-0006`, plus the ANALYTICS-stitch XQL; 1 IOC `cortexsim-canary.invalid`. Every BIOC/XQL body MUST carry a `preset =`/`dataset` reference and balanced quotes/parens, no skeleton tokens (validator check #13).
- `panw_mapping.use_cases`: `UC-SUPPLY-NNN` / `TC-SUPPLY-NNNx`; `expected_score_weight` sums ≤1.0 (validator check #12).
- `references[]`: exactly **one** `primary: true` (validator check #9) — OWASP MCP/genai (publisher_id `SRC-OWASP-LLM`) as primary; plus MITRE T1656, MITRE ATLAS AML.T0051, Unit42. CVE strings carried in reference titles/urls (NVD URLs) since there is no `SRC-CVE` registry id — keep `publisher_id` to an existing SRC id (validator checks #6-8 require `publisher_id ∈ source_refs ⊆ registry`).

**Fixture — `runtime-poisoned-response.json`**: a JSON-RPC `tools/call` result envelope `{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"...[SYSTEM_OVERRIDE]... AKIA0000CORTEXSIMCANARY CORTEXSIM-CANARY"}]}}` reusing the exact markers from `server.py:DIAGNOSE_REPLY`. Purpose: the on-the-wire runtime artifact (vs the source-code artifact SIM-KOI-002 scans). Safe — static file, canary-marked.

## 5. Engine/schema specifics
- **Enum (unchanged):** scenario `detection_types` validator `core/engine/scenario_loader.py:237-245` enforces values ∈ `{"BIOC","XQL","Analytics","Correlation","IOC"}` (definition at `:32`). SIM-KOI-006 uses `BIOC/XQL/Correlation` — **no enum change**. Card schema `severity`/status enums untouched.
- **No new Pydantic fields.** SIM-KOI-006 uses only fields already in `_schema.yml` (verified the full schema doc). `atlas_techniques` is an **existing** optional card field nested under `mitre_attack` (schema lines 314-334; `pattern ^AML\.T[0-9]{4}(\.[0-9]{3})?$`) — populating it on TTP-2026-0043/0080 needs no schema edit.
- **Loader logic:** `ttp_catalog.py` resolves `(ttp_ref, detection_id)` via `_by_pair` (`:163,172-176`); detection ids are matched against `_slug(name, prefix)` for biocs/xql (`:362,382`) and `rule_id` verbatim for correlation (`:402`). Author names so slugs match the scenario `detection_id`s exactly, else GAP-4 (494/494) regresses and the dangling-ref check fires (warning, but acceptance requires 0 dangling).
- **Slug/validator implications:** `_slug` truncates to 120 chars — keep BIOC/XQL names short enough that the slug isn't clipped below the scenario's `detection_id`. Validator (`detection_scanner/scripts/validate.py`) checks #4 (id uniqueness — 0080 is free), #5 (filename starts with id), #9 (one primary ref), #12 (weights ≤1.0), #13 (grammar lint).
- **Coverage-rollup impact:** the card maps T1656/T1059/T1552.001 (already covered) + new ATLAS `AML.T0051.001` (Indirect) — broadens the ATLAS sub-technique surface on the KOI plane in `/api/mitre/coverage`. Adds 1 to KOI scenario count and 1 to corpus card count; adds N new resolvable `detection_id`s (5 step-level detections above), pushing the resolved-slug total from 494 → 494+5.
- **EAL transport:** `agentic_egress` already supports `component=mcp_server` tarballing `mcp/pa-firewall-mcp` (`agentic_egress.py:_COMPONENTS["mcp_server"]`, `_resolve_artifact_dir`). The new fixture file lands inside that dir and rides the existing POST — **no plugin change**.

## 6. Validation & acceptance criteria
1. **Detection-corpus validator green:** `python3 detection_scanner/scripts/validate.py --quiet` exits 0 (baseline 140 pass / 0 fail per CLAUDE.md `:189`); the +1 card validates against `schema/ttp-entry.schema.json`.
2. **Export determinism:** `python3 detection_scanner/scripts/export_artifacts.py` then `git diff --exit-code detection_scanner/exports/` clean (CI `.github/workflows/ci.yml:152`). Must commit regenerated exports for TTP-2026-0080 and the edited 0043.
3. **Real loader, prod image:** `docker compose up -d --build` on `cortex-pov-engine-simcore:latest` → boot log shows **76 loadable scenarios, 0 rejected, 0 dangling ttp_ref, 0 dangling adapter_ref**; `GET /api/scenarios` returns SIM-KOI-006.
4. **detection_id resolution:** all 5 SIM-KOI-006 `detection_id`s resolve to a card detection object (resolved-slug total 494→499, 499/499). Guarded by `tests/engine/test_ttp_catalog.py` + `tests/smoke/test_scenario_catalog_integrity.py`.
5. **pytest:** `.venv/bin/pytest tests/engine/test_ttp_catalog.py tests/engine/test_scenario_catalog.py tests/smoke/test_scenario_catalog_integrity.py tests/eal_simulator/test_plugin_agentic_egress.py -v` green; full suite stays at 1596 pass / 80 skip (+ any new assertions).
6. **EAL dry-run:** `python3 -m scripts.eal_simulator.cli run /tmp/koi-006.yml` (mirror SIM-KOI-002 step-02 campaign, `component: mcp_server`) emits `agentic_egress_artifact_fetch` against the canary allowlist.
7. **Target counts:** scenarios 75→**76**; KOI 5→**6**; cards 76→**77**; resolved slugs 494→**499**.

## 7. Effort & risk
- **Effort: S/M (lean M).** Mostly content authoring (1 scenario YAML, 1 new card, 1 fixture, 1 card edit, doc bumps); zero engine code. Commit breakdown: (1) pack fixture+README; (2) cards + validate; (3) scenario; (4) export regen; (5) doc counts. ~5 small commits.
- **Top risks:**
  1. **detection_id ↔ slug drift.** The scenario `detection_id`s must byte-match `_slug(card name, prefix)` (`ttp_catalog.py:346`). Easy to typo and regress the 494/494 invariant. Mitigation: author card `name`s first, compute the slug, then paste into the scenario.
  2. **Overlap with SIM-KOI-002 read as duplication.** Reviewer may see "another MCP injection scenario." Mitigation: the card `identity.description` and the SIM-KOI-002 enrichment must explicitly frame the connect-time(source-scan)↔runtime(result-content) distinction + CVE anchors; keep SIM-KOI-006 detections keyed on the **tool-RESULT body / agent-consumed result**, not on `pa-firewall-mcp` in the command line (which is SIM-KOI-002's BIOC).
