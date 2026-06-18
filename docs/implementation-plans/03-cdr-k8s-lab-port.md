# 03 — CDR/K8s Lab Port (brainstorm items #3 + #4 + #7)

> **Scope:** Wrap the unported `sources/xsiam-prisma-cdr-lab` content into CortexSim scenarios + TTP cards.
> Delivers **SIM-CDR-009..014** (CDR plane 8 → 14) + **SIM-MP-006** (ANALYTICS NGFW↔endpoint stitch).
> **Depends on Plan 01** (ABIOC detection type) for the ABIOC scenarios (SIM-CDR-009 + SIM-CDR-014).
> Output: `docs/implementation-plans/03-cdr-k8s-lab-port.md`

---

## 1. Goal

Port the already-built, runnable `xsiam-prisma-cdr-lab` (16 folders under `sources/xsiam-prisma-cdr-lab/2.0` + `1.0`) into CortexSim's scenario/card form, taking the CDR plane from 8 → 14 scenarios and adding one cross-plane ANALYTICS stitch scenario (SIM-MP-006). This is the **lowest-effort-per-scenario** expansion in the brainstorm because the attack content already exists and runs — we wrap, not author. It broadens the detection surface in three brainstorm-cited ways: (a) item #3 `[PORT]` "~10 ready-built scenarios; CDR depth goes 8 → ~16. Lowest effort per scenario — the attack content already exists and runs"; (b) item #4 `[PORT · NET-NEW]` the NGFW 5-tuple+10s causality stitch that "directly strengthens the ANALYTICS plane's headline stitching wedge (3-0: stitching is the architectural reality to simulate)"; (c) item #7 `[PORT]` the `actor_container_info` JSON-extraction XQL library that is "reusable detection content for every CDR card." Confidence: items #3/#7 are `[PORT]` (already built, no research risk); item #4's stitching premise is **high · 3-0** (PANW Cyberpedia AI-SOC, primary). Per §5 of the brainstorm, do **not** assert any CrowdStrike-comparison framing — the `crwd-detection-container` WIP folder is explicitly handled as a competitive demo artifact, **not** a scenario.

---

## 2. Dependencies & ordering

**Hard dependency — Plan 01 (ABIOC type) must land first** for the two ABIOC scenarios. Verified blocker: `core/engine/scenario_loader.py:31-33` defines
```python
DETECTION_TYPES: frozenset[str] = frozenset({"BIOC", "XQL", "Analytics", "Correlation", "IOC"})
```
and `StepExpectedDetection.validate_type` (line 63-71) + `validate_detection_types` (line 237-247) reject any other value. A scenario carrying `type: ABIOC` is rejected at boot until Plan 01 adds `ABIOC` to that frozenset (and updates `scenarios/_schema.yml` lines 45-51 / 187 the enum doc, plus `ttp_catalog` if Plan 01 adds an `abioc[]` parser — see §5).

**Commit sequence (7 commits):**
1. **C1 — non-ABIOC CDR cards + scenarios** (SIM-CDR-010, 011, 012, 013): 4 TTP cards (0080-0083) + 4 scenario YAMLs. Independent of Plan 01 (all use BIOC/XQL/Correlation/IOC only). **Can land before Plan 01.**
2. **C2 — NGFW stitch** (SIM-MP-006): 1 TTP card (0084) + 1 scenario YAML. Independent of Plan 01 (ANALYTICS/Correlation). **Can land before Plan 01.**
3. **C3 — ABIOC scenarios** (SIM-CDR-009, SIM-CDR-014): 2 TTP cards (0085, 0086) + 2 scenario YAMLs. **MUST land after Plan 01** (uses `type: ABIOC`).
4. **C4 — depth add to existing CDR-002** (cryptominer replicaSet, item from §3 row `1.0/cryptominers`): edit-only, optional, can be folded into C1.
5. **C5 — docs/count updates**: `CLAUDE.md`, `docs/reference/scenario-catalog.md`, `docs/reference/README.md`, `docs/reference/eal-plugin-catalog.md` (CDR has no EAL plugins — note unchanged).
6. **C6 — deterministic export regen**: `python3 detection_scanner/scripts/export_artifacts.py` (regenerates `detection_scanner/exports/*` so CI's `sha256sum -c` stays green).
7. **C7 — adapter wiring tally update** in CLAUDE.md (new scenarios add `adapter_ref` to TOOL-TRIVY/TOOL-SLIVER/TOOL-DEEPCE etc.).

If Plan 01 is not yet merged, ship C1+C2+C5(partial)+C6 first (CDR 8→12, MP 5→6), then C3 when Plan 01 lands (CDR 12→14).

---

## 3. Change points

| File | Current state | Change |
|------|---------------|--------|
| `scenarios/cdr/cdr-009-microservices-abioc.yml` | does not exist | **NEW** scenario SIM-CDR-009 (ABIOC, needs Plan 01) |
| `scenarios/cdr/cdr-010-ransomware-in-container.yml` | does not exist | **NEW** scenario SIM-CDR-010 |
| `scenarios/cdr/cdr-011-k8s-goat-escape-chain.yml` | does not exist | **NEW** scenario SIM-CDR-011 |
| `scenarios/cdr/cdr-012-wildfire-in-container-misconfig.yml` | does not exist | **NEW** scenario SIM-CDR-012 |
| `scenarios/cdr/cdr-013-insecure-deployment.yml` | does not exist | **NEW** scenario SIM-CDR-013 |
| `scenarios/cdr/cdr-014-malicious-k8s-deployment-ttp.yml` | does not exist | **NEW** scenario SIM-CDR-014 (ABIOC, needs Plan 01) |
| `scenarios/multi_plane/mp-006-ngfw-container-causality-stitch.yml` | does not exist | **NEW** scenario SIM-MP-006 (plane ANALYTICS) |
| `detection_scanner/ttps/TTP-2026-0080-sim-cdr-010.json` | does not exist | **NEW** card (ransomware-in-container) |
| `detection_scanner/ttps/TTP-2026-0081-sim-cdr-011.json` | does not exist | **NEW** card (k8s-goat escape chain) |
| `detection_scanner/ttps/TTP-2026-0082-sim-cdr-012.json` | does not exist | **NEW** card (WildFire-in-container + misconfig) |
| `detection_scanner/ttps/TTP-2026-0083-sim-cdr-013.json` | does not exist | **NEW** card (insecure-deployment) |
| `detection_scanner/ttps/TTP-2026-0084-sim-mp-006.json` | does not exist | **NEW** card (NGFW↔endpoint stitch) |
| `detection_scanner/ttps/TTP-2026-0085-sim-cdr-009.json` | does not exist | **NEW** card (sockshop ABIOC, needs Plan 01) |
| `detection_scanner/ttps/TTP-2026-0086-sim-cdr-014.json` | does not exist | **NEW** card (malicious-k8s-deployment ABIOC chain, needs Plan 01) |
| `scenarios/cdr/cdr-002-cryptominer.yml` | exists (8th line `SIM-CDR-002`) | **EDIT (optional, C4)**: add a replicaSet-persistence step from `1.0/cryptominers-containers/cryto-deployment.yaml` per §3 brainstorm row; or skip if out of budget |
| `CLAUDE.md` | line 116 "CDR … 8 scenarios"; line 124 "Analytics … 5 multi-plane"; line 133 "75 loadable scenarios … 76 TTP cards"; line 280 "(5 scenarios)" | **EDIT**: CDR 8→14, multi-plane 5→6, scenarios 75→82, cards 76→83, add SIM-MP-006 to the mp-* list |
| `docs/reference/scenario-catalog.md` | authoritative inventory | **EDIT**: add the 7 new scenarios |
| `docs/reference/README.md` | "counted ground truth … 75 loadable scenarios" | **EDIT**: 75→82, 76→83 cards, recompute `detection_id` resolution total |
| `detection_scanner/exports/*` | generated, checked by CI `sha256sum -c` | **REGEN (C6)** via `export_artifacts.py` |

**No engine code changes in this plan** beyond what Plan 01 already lands. The loader, `ttp_catalog`, schema, and validators are reused as-is. (If Plan 01 chose to author ABIOC content under `biocs[]` rather than add an `abioc[]` parser, this plan inherits that decision — see §5.)

---

## 4. New artifacts

**Scenario YAMLs** (filename → scenario_id, plane, primary MITRE, lab artifact wrapped, ABIOC?):

- `cdr-009-microservices-abioc.yml` → **SIM-CDR-009**, plane CDR, **T1610** (Deploy Container) / behavioral-anomaly on **T1611** (Escape to Host). Wraps `2.0/sockshop-k8-ABIOC/sockshop-weaveworks-spec.yaml` (12-microservice sock-shop demo — the canonical ABIOC baseline-then-deviation workload). **NEEDS ABIOC (Plan 01).** Purpose: first ABIOC card — deploy sock-shop, let UEBA/ABIOC learn the microservice baseline, then a single anomalous `carts`-pod process deviates → ABIOC fires on identified causality chain.
- `cdr-010-ransomware-in-container.yml` → **SIM-CDR-010**, plane CDR, **T1486** (Data Encrypted for Impact) + **T1611** (Escape to Host). Wraps `2.0/ransomware-in-container/ransomware-poc.sh` (chroot host-FS escape from `/proc/self/mountinfo` → clone Ransomware-PoC). Purpose: container-native ransomware distinct from EDR-008 host ransomware.
- `cdr-011-k8s-goat-escape-chain.yml` → **SIM-CDR-011**, plane CDR, **T1611** (Escape to Host) + **T1610**. Wraps `2.0/panw-goat/access-k8-goat.sh` (kubernetes-goat: DIND, SSRF, system-monitor escape, poor-registry). Purpose: full container-escape→host kill chain complementing CDR-003.
- `cdr-012-wildfire-in-container-misconfig.yml` → **SIM-CDR-012**, plane CDR, **T1610** + **T1204** (User Execution). Wraps `2.0/malicious-container-2.0/wildfire-samples.sh` + `container-escape.sh` + `K8-container-misconfig.yaml` (9 insecure-pod scenarios). Purpose: WildFire verdict path (PE/APK/macOS/ELF test samples) + posture misconfig in one fixture.
- `cdr-013-insecure-deployment.yml` → **SIM-CDR-013**, plane CDR, **T1610** + **T1496** (Resource Hijacking). Wraps `2.0/insecure-deployment/panw-insecure-deployment.yml` + `k8-busybox-attack.yml` (multi-container busybox pod fetching DeimosC2/Conti/BPFDoor ELFs + 8 attack Jobs). Purpose: posture→runtime — an insecure deployment that then detonates.
- `cdr-014-malicious-k8s-deployment-ttp.yml` → **SIM-CDR-014**, plane CDR, **T1078** (Valid Accounts) → **T1071** → **T1560** → **T1496** → **T1574** multi-step. Wraps `2.0/attk-ttp/attk-ttp.sh` + `attk-k8.yml` (9-technique in-cluster chain). **NEEDS ABIOC (Plan 01)** — the multi-step causality chain is the ABIOC sweet spot. Purpose: multi-step in-cluster TTP chain detected as one ABIOC causality story.
- `mp-006-ngfw-container-causality-stitch.yml` → **SIM-MP-006**, plane **ANALYTICS**, **T1071.001** (Web Protocols) + App-ID/URL/DGA. Wraps `2.0/ngfw/container-ttp-netgen.sh` (the `generate_network_traffic()` 5-tuple loop) + `fw-analysis.yaml`. Purpose: NGFW App-ID/URL/DGA net-gen ↔ endpoint process causality stitch — **5-tuple (src_ip, src_port, dst_ip, dst_port, protocol) + 10s window**. Mirror SIM-MP-001 F2 shape: `correlation_window_seconds: 10`, `required_planes_in_incident: [CDR, NDR]`, `stitching_key: container_id`.

**TTP cards** (id → backs scenario, one-line purpose). Use the next free ids ≥ 0080 (highest existing = `TTP-2026-0079`):

- `TTP-2026-0080` → SIM-CDR-010 — ransomware-in-container detection content (BIOC chroot/host-FS-escape + XQL mass-encrypt rate + IOC Ransomware-PoC repo).
- `TTP-2026-0081` → SIM-CDR-011 — k8s-goat escape-chain (BIOC DIND/nsenter/host-mount + correlation escape→host-process).
- `TTP-2026-0082` → SIM-CDR-012 — WildFire-in-container + misconfig (XQL WildFire verdict in container + BIOC privileged/hostPath misconfig).
- `TTP-2026-0083` → SIM-CDR-013 — insecure-deployment runtime detonation (BIOC busybox-wget-ELF + IOC DeimosC2/Conti/BPFDoor hashes).
- `TTP-2026-0084` → SIM-MP-006 — NGFW↔container causality stitch (XQL App-ID/URL/DGA + correlation CR-MP-0006 5-tuple+10s).
- `TTP-2026-0085` → SIM-CDR-009 — sock-shop microservices ABIOC (ABIOC behavioral-anomaly + supporting XQL). **Needs Plan 01.**
- `TTP-2026-0086` → SIM-CDR-014 — malicious-k8s-deployment ABIOC chain (ABIOC multi-step causality + XQL per-stage). **Needs Plan 01.**

**Shared reusable detection content (item #7):** every CDR card above embeds, as an `xql_queries[]` entry (`purpose: "hunt"`, `dataset: "xdr_data"`), the lab's container-workload XQL **verbatim** from `2.0/tests-toolkits/container_info.sql`:
```
dataset = xdr_data
| fields actor_container_info as container_info
| filter container_info != null
| alter pod_namespace = json_extract_scalar(container_info, "$.pod_namespace")
| alter pod_name      = json_extract_scalar(container_info, "$.pod_name")
| alter image_name    = json_extract_scalar(container_info, "$.image_name")
| alter image_id      = json_extract_scalar(container_info, "$.image_id")
| alter container_id  = json_extract_scalar(container_info, "$.id")
| alter privileged    = json_extract_scalar(container_info, "$.privileged")
```
This is the base projection; each card extends it with a `| filter` clause specific to its TTP (e.g. CDR-010 adds `| filter process_command_line contains "chroot"`). The `container_id` projection is also the `stitching_key` for SIM-MP-006.

---

## 5. Engine/schema specifics

**Enum being extended (by Plan 01, consumed here).** Exact current value, `core/engine/scenario_loader.py:31-33`:
```python
DETECTION_TYPES: frozenset[str] = frozenset(
    {"BIOC", "XQL", "Analytics", "Correlation", "IOC"}
)
```
SIM-CDR-009 and SIM-CDR-014 set `detection_types: [ABIOC, XQL]` and at least one step `expected_detections[].type: ABIOC`. This loads **only after** Plan 01 adds `"ABIOC"` to that frozenset. The mirror enum doc lives in `scenarios/_schema.yml:45-51` and the inline example at line 187.

**detection_id resolution for ABIOC (critical contingency).** The resolver `core/engine/ttp_catalog.py` `_parse_entry` (lines 305-310) only aggregates four arrays into resolvable cards:
```python
cards.extend(_parse_bioc_list(...detections_raw.get("biocs")...))      # slug prefix "bioc"
cards.extend(_parse_xql_list(...detections_raw.get("xql_queries")...)) # slug prefix "xql"
cards.extend(_parse_correlation_list(...detections_raw.get("correlation_rules")...))
cards.extend(_parse_ioc_list(...detections_raw.get("iocs")...))
```
There is **no `abioc[]` parser**. The scenario `type` field (BIOC/XQL/ABIOC) is independent of which array a `detection_id` resolves against — resolution is by `(ttp_ref, detection_id)` pair only (`find()`, line 172-176). So two valid authoring paths for ABIOC, **whichever Plan 01 chose**:
- **(A) Plan 01 adds an `abioc[]` array + `_parse_abioc_list` with slug prefix `abioc-`** → SIM-CDR-009/014 use `detection_id: abioc-<slug>` resolving against the new array. Card schema `detection_scanner/schema/ttp-entry.schema.json` gains an `abioc` property (mirror the `biocs` block at line 533-555, add `tuning: "auto"` to mark it PANW-authored/auto-tuned per brainstorm §2b). Slug rule from `_slug` (line 346-353) applies unchanged.
- **(B) Plan 01 did NOT add an `abioc[]` parser** → author ABIOC detection content under the existing `biocs[]` array (resolves as `bioc-<slug>`), and let the scenario-level `type: ABIOC` + tags carry the ABIOC semantics. Zero ttp_catalog/schema change in this plan.

This plan is written to **follow Plan 01's choice** — read Plan 01 before authoring TTP-2026-0085/0086. If Plan 01 is path (B), the two ABIOC scenarios' `detection_id`s use `bioc-` slugs.

**Slug/validator implications.** Detection ids must match `_slug(name, prefix)` output: lowercase, non-alnum→`-`, collapsed `--`, truncated to 120 chars. e.g. card 0080 BIOC named "Container chroot to host filesystem mount" → `detection_id: bioc-container-chroot-to-host-filesystem-mount`. The corpus validator (`detection_scanner/scripts/validate.py`) enforces: filename starts with id (so `TTP-2026-0080-sim-cdr-010.json` must carry `"id": "TTP-2026-0080"`); ID uniqueness; `metadata.source_refs[]` resolve to the source registry; exactly one `references[].primary: true`; technique-id `^T\d{4}(\.\d{3})?$`; UC ids `^UC-[A-Z0-9]+-\d{3}$` / TC ids `^TC-[A-Z0-9]+-\d{3}[A-Z]?$`; per-UC `expected_score_weight` sum ≤ 1.0; and the GAP-12 XQL/BIOC grammar lint (**balanced quotes/parens + every BIOC/XQL body must reference a `dataset`/preset + no skeleton tokens**). The container XQL above satisfies the dataset requirement (`dataset = xdr_data`).

**Coverage-rollup impact.** ABIOC is validated-ML (causality-anchored) — per brainstorm §4 treat it like Analytics-validated but distinct. If Plan 01 added an `abioc[]` array, ensure the rollup counts ABIOC bodies as **validated** detection (they carry real logic), unlike the `analytics_modules[]` named-only references (schema line 592-610, `validated: false` by default). If path (B), no rollup change.

**`infra_modules_needed` / `external_tools`.** All CDR scenarios set `infra_modules_needed: [cdr, base]` (the EKS module at `infra/modules/aws/cdr/` — verified present: main.tf provisions `aws_eks_cluster` + node group; content.yml ships deepce/botb/kube-hunter/trivy/falco). SIM-MP-006 sets `[base, ndr, cdr]` (mirrors SIM-MP-001's `[base, edr, ndr]`). Wire `adapter_ref` to verified adapter ids: `TOOL-TRIVY` (tools/packs/trivy.yml), `TOOL-KUBE-BENCH`, `TOOL-SLIVER`, `TOOL-DEEPCE`, `TOOL-KUBESCAPE`, `TOOL-GITLEAKS`, `TOOL-CLOUDSPLAINING` — all packs exist on disk. SIM-MP-006 uses `TOOL-SLIVER` for the beacon shape (as SIM-MP-001 does).

**EAL note (unchanged).** CLAUDE.md line 131 states CDR + ANALYTICS have **no EAL plugins** by design — served by the identity harness + signalbench + IaC-planted findings. These new scenarios use the identity harness (`container-runtime`/`www-data`/`root`) exactly like CDR-001/007. Do not add EAL plugins.

---

## 6. Validation & acceptance criteria

1. **Detection-corpus validator stays green.** `python3 detection_scanner/scripts/validate.py --strict` → 0 FAIL (currently 140 pass / 0 fail per CLAUDE.md line 189). The 7 new cards must each pass all 13 checks (esp. filename↔id, single primary ref, GAP-12 grammar lint).
2. **Real loader in the prod image, 0 rejected.** Build/run `cortex-pov-engine-simcore:latest` (`docker compose up -d --build`) and assert boot log `scenario loader: N loaded, 0 rejected` with N = **82** (75 + 7). Confirm `TTP catalog loaded: M detection cards across 83 entries (rejected=0)`. **The ABIOC scenarios will be rejected if Plan 01 is not merged** — verify Plan 01 first, or stage C1+C2 (N=80) before C3 (N=82).
3. **detection_id resolution — 0 dangling.** Every `expected_detections[].detection_id` across the 7 new scenarios resolves via `ttp_catalog.find()` (0 dangling `ttp_ref`, 0 dangling `detection_id`, 0 dangling `adapter_ref` — the GAP-4 / boot invariants from CLAUDE.md line 133). Grep the boot log for `dangling`.
4. **pytest green in the image.** Run the backend suite in the prod image (CLAUDE.md CI: `pytest`, currently 1596 pass / 80 skip). No new failures; scenario-count assertions (if any test pins 75) updated to 82.
5. **Deterministic export regen.** `python3 detection_scanner/scripts/export_artifacts.py` then `sha256sum -c` (CI's `detection` job, SKELETON=0) → clean. Commit the regenerated `detection_scanner/exports/*`.
6. **Exact target counts after full landing:** CDR **14** scenarios; ANALYTICS multi-plane **6**; total loadable scenarios **82**; TTP cards **83**; new ABIOC scenarios = 2 (SIM-CDR-009, SIM-CDR-014).
7. **SIM-MP-006 stitch assertion.** The F2 verifier XQL asserts ≥ 2 distinct planes (CDR + NDR) under one `incident_id` joined on the 5-tuple within `correlation_window_seconds: 10` — mirror SIM-MP-001's `plane_count >= 2 and incident_count = 1` verifier (mp-001 lines 145-150).

---

## 7. Effort & risk

**Effort: L** (7 new scenario YAMLs + 7 new TTP cards + doc/count/export updates; cards are the bulk of the work since each carries real BIOC/XQL/correlation/IOC bodies passing the GAP-12 lint). Roughly 7 commits as in §2: C1 (4 cards+4 scenarios) is the largest; C3 (2 ABIOC) is gated on Plan 01; C2/C4/C5/C6/C7 are small.

**Top 2 risks:**
1. **Plan-01 coupling / ABIOC authoring shape unknown until Plan 01 merges.** If Plan 01 ships path (A) `abioc[]` array vs path (B) reuse `biocs[]`, the `detection_id` slug prefix (`abioc-` vs `bioc-`) and the card `detections` JSON shape differ for TTP-2026-0085/0086. **Mitigation:** land C1+C2 (the 5 non-ABIOC scenarios, CDR 8→12 + MP 5→6) independently first; author C3 only after reading the merged Plan 01, matching its chosen shape exactly. This also de-risks the whole plan against Plan 01 slipping.
2. **GAP-12 grammar lint + count-drift breaking CI.** Hand-authored XQL/BIOC bodies frequently fail the balanced-quote/paren and "must reference a dataset" lint, and the `sha256sum -c` export check fails if `export_artifacts.py` is not re-run after adding cards. The lab scripts also contain genuinely destructive payloads (real malware ELF fetches in `k8-busybox-attack.yml`, `chroot`/`nsenter` escapes) — the **scenario commands must wrap/neuter these the way CDR-001 does** (echo + truncated/safe variants, `safety_class` set, `destructive: false` only where true) rather than copying the raw lab payload. **Mitigation:** run `validate.py --strict` + `export_artifacts.py` + `sha256sum -c` locally before each commit; set `pov_engine.destructive`/`safety_class` honestly per card (ransomware/escape cards are `destructive: true`, `safety_class` gated); reuse CDR-001's neutering pattern (curl/echo stubs, `[truncated for safety]`).

### Critical Files for Implementation
- /home/henry/Github/cortex-pov-engine/scenarios/cdr/cdr-007-cluster-posture-sweep.yml
- /home/henry/Github/cortex-pov-engine/scenarios/multi_plane/mp-001-c2-beacon-ngfw-xdr-stitch.yml
- /home/henry/Github/cortex-pov-engine/detection_scanner/ttps/TTP-2026-0065-sim-cspm-001.json
- /home/henry/Github/cortex-pov-engine/core/engine/scenario_loader.py
- /home/henry/Github/cortex-pov-engine/core/engine/ttp_catalog.py
