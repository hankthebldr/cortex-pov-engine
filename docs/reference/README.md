# CortexSim Reference Library

> **As-of date:** 2026-06-07 · **Repo state:** branch `main` @ commit `b7eebc5`
>
> This directory is the **deep-catalog reference library** for CortexSim — eight
> exhaustive, enumerated inventories produced by a domain-by-domain sweep of the
> engine, plus a single consolidated gap backlog. Each domain doc is *complete by
> design* (nothing sampled): it lists every adapter, every scenario, every TTP
> card, every plugin, every route, every IaC module. The companion
> [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) rolls every gap found across all eight docs
> into one prioritized execution backlog.

## How to use this reference

- **Building a feature?** Start in the domain doc for that surface (it is the
  source-enumerated truth), then check [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) for the
  open work in that theme. Each gap carries an id (e.g. `GAP-AGENT-002`,
  `S-05`, `PLANE-CSPM-ASM-TIM-NOSCENARIO`) that traces back to its domain doc.
- **Scoping the engagement?** Read [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) top-to-bottom:
  gaps are grouped by theme, each with severity, affected files, a recommended fix,
  and an effort estimate. The "Top 10 highest-leverage actions" list at the bottom is
  the recommended execution order.
- **These docs are derived archives, not source of truth.** When a doc and the code
  disagree, the code wins — every doc header names the exact source files it was
  generated from. Regenerate (or hand-patch the affected row) when you change the
  underlying content.
- **Stable IDs cross every doc.** Scenario `SIM-{PLANE}-{NNN}`, TTP card
  `TTP-2026-NNNN`, tool adapter `TOOL-*`, IaC module names, and EAL plugin slugs are
  used consistently so you can pivot between domains.
- **Counted ground truth (original audit, 2026-06-07):** 65 launchable scenarios
  (58 loaded by the schema validator) · 63 active TTP cards · 13 EAL plugins ·
  69 tool-adapter packs · 11 AWS IaC modules · 48 HTTP routes · 14 detection planes
  (13 documented + the then-undocumented AI_SPM).
- **Counted ground truth (verified 2026-06-15):** **75 loadable scenarios**
  (all `status: active`, 0 rejected / 0 dangling refs; **494/494 detection_id slugs
  resolve** — GAP-4 closed) · **76 TTP cards** (676 deployable detection objects +
  analytics-module refs) · 13 EAL plugins · **69 tool-adapter packs**
  (**34 distinct adapters wired across 35 scenarios**, up from 17) · 11 AWS IaC
  modules · 14 detection planes · **all 10 source submodules provisioned**. Per-plane:
  ASM=4 (+web-app enumeration); EDR=9 (+rclone exfil); CDR=8 (+cluster posture sweep,
  +IAM-key abuse); ITDR=8 (+AD privesc, +helpdesk-MFA); TIM=2 (+adversary infra
  staging — first TA0042 coverage); NDR=7. Every plane now carries IOC coverage
  (GAP-10 closed). See [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) "CLOSED in the 2026-06-15
  pass."
- **Counted ground truth (verified 2026-08-05 — payload-shelf content pass):**
  **170 loadable scenarios · 170 TTP cards** across **15 detection planes** ·
  **91 tool-adapter packs**, of which **8 declare a payload-shelf
  `install.artifact`** (`TOOL-LINPEAS`, `TOOL-PSPY`, `TOOL-SUID3NUM`,
  `TOOL-LSE`, `TOOL-LINENUM`, `TOOL-DEEPCE`, `TOOL-TRAITOR`,
  `TOOL-AMICONTAINED`) — every one commit/tag-pinned with a real digest and a
  licence read from the project's own LICENSE file. `payloads/sources.json` is
  fully **derived** from those packs and its `unbound[]` migration bucket has
  reached **zero**. `PAYLOAD_ALLOW_UNPINNED=0 ./scripts/build-payloads.sh`
  stages 8 artifacts, **0 unpinned**. `make validate` **346 pass / 0 warn /
  0 fail**; exports byte-identical across two regenerations; `make check-refs`
  **6 passed**; `coverage_report.py --strict` exits 0.
  This pass adds **1 scenario + 1 card** — `SIM-EDR-022` / `TTP-2026-0176`,
  the EDR sibling of the proven CDR/K8s shelf path: an enrolled beacon runs a
  shelf-staged privilege-escalation enumerator under `www-data` with `apache2`
  as the causality group owner.
  * **Index coverage is UNCHANGED at 89/266 (DET/HNT 70/107).** `SIM-EDR-022`
    binds `TC-EDR-05` + `TC-EDR-03`, both already evidenced. It adds a second,
    *behaviourally* keyed piece of evidence and a delivery path that survives
    default-deny egress — not new index breadth. Reporting it as coverage growth
    would be the false-green this repo keeps catching.
  * The card ships **seven behavioural BIOCs** (service-account interpreter
    spawn under a web-server CGO, discovery-binary breadth burst, privesc read
    fan-out, staging-directory execution, `/proc` cmdline sweep, cron-path read,
    SUID sweep) and **two deliberately filename-keyed detections plus two
    filename IOCs labelled NEGATIVE-CONTROL TARGET** — authored to go dark under
    a rename so the behavioural rules have to carry the detection alone. Three
    `file-sha256` IOCs are pinned to the packs' `install.artifact.sha256`
    because a rename changes the destination and never the bytes.
  * `k8s_manifest._resolve_payloads` now **delegates** to
    `payload_shelf.compose(consumer="k8s")` instead of keeping its own shelf
    lookup, and `build_objects` **refuses** a `delivery=served` manifest while
    `CORTEXSIM_K8S_PAYLOAD_AUTH=token` (the init container's bare `wget` carries
    no Authorization header, so the manifest would apply cleanly and 403 in the
    pod). Guarded by `tests/engine/test_k8s_manifest_shelf.py`.
- **Counted ground truth (verified 2026-08-02 — library-breadth authoring pass):**
  **169 loadable scenarios · 169 TTP cards** across **15 detection planes** (all
  `status: active`, 0 rejected / 0 dangling refs; **1073/1073 scenario
  `detection_id` slugs resolve** — GAP-4 held) · **1756 catalog detection objects** ·
  1082 step-detections. `make validate` **344 pass / 0 warn / 0 fail**; exports
  byte-identical across two regenerations (535 files); `make check-refs` **6 passed**;
  full backend suite **3935 pass / 236 skip / 0 fail**.
  This pass adds **7 scenarios + 7 cards** (`TTP-2026-0169..0175`) authored against the
  library-breadth audit, weighted ABIOC/Analytics/Correlation so the detection-type
  gate improves rather than breaks: **ABIOC+Analytics 15.0% → 15.9%** (it was passing
  its 15% target by 0.0pp) and Correlation holds 10.7%.
  * `SIM-MP-021` — 800-alert **labelled** corpus (malicious / hard-positive / benign
    near-miss), so triage precision, recall and grouping collapse are **computed**
    against an author-time truth column rather than asserted.
  * `SIM-APB-001` — governed-autonomy audit reconstruction across three action classes.
  * `SIM-MP-022` — encryption-less extortion (no T1486 anywhere in the run).
  * `SIM-CLOUD-010` — trusted-relationship vendor-integration pivot (T1199 had **one**
    step corpus-wide before it).
  * `SIM-AIACC-006` — token jacking, the inverse direction of the shipped AI Access set.
  * `SIM-TIM-008` / `SIM-TIM-009` — dangling-DNS takeover as allowlisted C2, and
    pre-weaponization capability development. First `T1584` and first `T1588` in the
    corpus.
  **Index coverage moves 86 → 89 of 266** (DET/HNT **67 → 70 of 107**); the three
  net-new rows are `TC-AGTX-03`, `TC-AES-08` and `TC-APB-06` — the last being the only
  unevidenced **P1** row in the index carrying a measurable threshold, since the other
  eight are `Qualitative pass` and clamp to `not_applicable`. Open DET/HNT gaps
  **40 → 37**; payloads in use **31 → 33**.
  **`make coverage-strict` now exits 0** (was 1): Resource Development (TA0042) went
  **3 → 5** scenarios, clearing the analyzer's only strict breach. Six scenarios that
  carried no `methodology_family` (`SIM-CDR-022/023/025/026`, `SIM-EDR-020`,
  `SIM-MP-018`) were silently absent from the family table and are now counted —
  F1 104→111, F9 2→3.
  **S-13 tier drift rises 100 → 104** and is deliberate: four new scenarios carry a
  higher `moat_tier` than their index row, because the bound rows are tiered PARITY for
  the table-stakes version of a capability that these scenarios prove the
  differentiated version of. **S-14 holds at 13** — `TC-XTI-09` was examined for
  `SIM-TIM-008`, is unevidenced and scoreable, and was **deliberately not bound**
  because its `validation_class` is POS and a scenario must not claim a state property
  it only provokes. It is recorded as a candidate for `assertions/pos/`.
- **Counted ground truth (verified 2026-07-31 — FY27 v2.2 UC/TC index reconciliation):**
  The FY27 master index snapshot (**49 UC · 203 UCS · 266 TC · 140 POV-SC payloads · 38 SKU**)
  is versioned in-repo at `docs/uc_tc_mapping/_v2.2-source/` and loaded at boot by
  `core/engine/uctc_registry.py`. Scenario `uc_ref` / `tc_ref` / `tc_refs[]` /
  `pov_scenario_id` are now a **validated foreign key** into it (`S-10..S-16`;
  `CORTEXSIM_STRICT_REFS` **defaults true**). **162 / 162 scenarios resolve, zero
  S-10/S-11/S-12/S-15**; **86 of 266** index test cases are evidenced by a scenario
  (**67 of 107** DET/HNT), plus **18 bound by an assertion artifact** (union **94 of
  266**, tenant-verified **0** — authored is not proven; see
  [`../uc_tc_mapping/assertions.md`](../uc_tc_mapping/assertions.md)). The index is browsable **in the product**: `core/api/uctc.py` (9 read
  endpoints) + the console **UC / TC Index** destination (`#/uctc`), with
  `core/api/pov.py` scoping the corpus to a tenant entitlement set. The
  **101 S-13 tier disagreements and 13 S-14 posture bindings are deliberate**, as is
  the fact that 57 of the 107 detection-backable TCs are `is_scoreable: false` —
  see [`../uc_tc_mapping/README.md`](../uc_tc_mapping/README.md) and
  [`../uc_tc_mapping/index-gaps-v2.2.md`](../uc_tc_mapping/index-gaps-v2.2.md).
  Full suite **3718 pass / 3 fail / 221 skip** (the 3 are the known `edr-013/017/021`
  PowerShell-in-bash-bundle push-generator defect, not a regression);
  `make validate` **330 pass / 0 warn / 0 fail**; exports byte-identical; UI **449 tests**.
- **Counted ground truth (verified 2026-07-25 — analytics log-streamer + ABIOC/Analytics content):**
  **162 loadable scenarios** across **15 detection planes** (all `status: active`,
  0 rejected / 0 dangling refs; **1001/1001 detection_id slugs resolve** — GAP-4 held) ·
  **162 TTP cards** (**1674 catalog detection objects**) · **21 EAL plugins** ·
  85 tool-adapter packs (adapter wiring unchanged — the 14 new scenarios are
  analytics-streamer-driven with 0 new `adapter_ref`) · 11 AWS IaC modules.
  `make validate` is green (**330 pass / 0 warn / 0 fail**). This pass adds the 14
  `TTP-2026-0154..0167` analytics-streamer pairs (147 + 14 = 161) and a **new analytics
  log-streamer EAL plugin family** — spine `core/eal_simulator/analytics_emitter.py` + 7
  net-new emitters (**14 → 21 plugins**) that POST **shape-true audit/log JSON** to an
  operator-supplied collector so a customer validates their **Analytics / ABIOC** alerts
  fire per data source: AWS/GCP CloudTrail + cloud storage/compute (`cloud_audit_logs`),
  Azure Activity/Audit (`msft_azure_audit`, newly registered), Kubernetes audit
  (`kubernetes_audit_logs`, newly registered), M365/Exchange (`msft_o365_audit`), Active
  Directory/Windows (`msft_windows_security`), PAN-OS NGFW-EAL, and Okta/Entra IdP sign-in
  (`okta_sso`, via the extended `idp_signin_emulator`). The cloud-audit emitters drive the
  new CDR-plane cloud-audit scenarios (`SIM-CDR-019..026`). Content emphasis is the two
  below-target detection types: **ABIOC + Analytics step-share rises 12.5% → 15.2%**
  (crossing the 15% floor — ABIOC 11.6%, Analytics 3.6% of 1010 step-detections);
  Correlation holds at 10.7%. Two datasets registered in `validate.py` KNOWN_DATASETS
  (`msft_azure_audit`, `kubernetes_audit_logs`). Per-plane on disk now: CDR=26 · EDR=21 ·
  ANALYTICS=20 · ITDR=20 · NDR=12 · CLOUD_APP=9 · KOI=8 · AI_SPM=7 · ASM=6 · BROWSER=6 ·
  TIM=6 · AIRS=5 · AI_ACCESS=5 · CSPM=5 · EMAIL=5. **Known open item (false-green):** the
  `ngfw_eal_emitter` still emits `dataset=panw_ngfw_eal_raw` + endpoint-process context
  fields, while its card `SIM-NDR-012` queries the normalized `panw_ngfw_traffic_raw`;
  aligning the emitter's `_DATASET` (and dropping the process fields) is a pending Build
  task — the corpus/CI gates are green but the two disagree on the emitted dataset.
- **Counted ground truth (verified 2026-07-24 — F5/F10 methodology-family integration):**
  **147 loadable scenarios** across **15 detection planes** (all `status: active`,
  0 rejected / 0 dangling refs; **932/932 detection_id slugs resolve** — GAP-4 held) ·
  **147 TTP cards** (**1289 resolvable catalog detection objects**) · **14 EAL plugins** ·
  **84 tool-adapter packs** (45 distinct adapters wired across 42 scenarios; this pass
  wired no new adapters — the content is detection-corpus depth) · 11 AWS IaC modules.
  `make validate` is green (**300 pass / 0 warn / 0 fail**). This pass adds the 12
  `TTP-2026-0142..0153` pairs (135 + 12 = 147), all Unit 42-sourced, targeting the two
  **empty methodology families** and the two shallow tactics the coverage-analyzer flagged:
  **F5 Automation & Workflow 0→3** (`SIM-MP-020` BlackSuit ransomware-precursor
  auto-containment, `SIM-ITDR-016` closed-loop account auto-disable, `SIM-CLOUD-007`
  auto-revoke OAuth tokens — each terminating on an XSOAR playbook + closed-loop/SLA
  measurement query against the newly-registered `xsiam_incidents` dataset); **F10
  Qualitative Evidence 0→2** (`SIM-EDR-021` GentleKiller ThrottleStop.sys BYOVD EDR-kill
  ABIOC, `SIM-ITDR-017` AD CS ESC1→PKINIT privileged-impersonation ABIOC); **Resource
  Development (TA0042) 1→3** (`SIM-TIM-004` TLS-fingerprint infra pivot, `SIM-TIM-005`
  rogue code-signing-cert impersonation IOC); **Reconnaissance (TA0043) 4→6** (`SIM-TIM-006`
  edge-VPN probing surge, `SIM-ASM-006` Scattered Spider OSINT vishing precursor). Also
  ships `SIM-EMAIL-005` (AiTM session-token theft ATO), `SIM-ASM-005` (WSUS CVE-2025-59287
  exposure-to-RCE), and `SIM-CSPM-005` (exposed-.env code-to-cloud extortion, F9). Corpus
  correlation share rises to the 10% floor (9.4%→10.0%); ABIOC+Analytics holds at 12.5%
  (the new content is correlation/IOC-terminal, not ML-anchored). All 12 multi-step
  scenarios yield a single-root, fully-connected causality graph. Per-plane on disk now:
  EDR=21 · ANALYTICS=20 · CDR=18 · ITDR=17 · NDR=11 · KOI=8 · AI_SPM=7 · CLOUD_APP=7 ·
  ASM=6 · BROWSER=6 · TIM=6 · AIRS=5 · AI_ACCESS=5 · CSPM=5 · EMAIL=5.
- **Counted ground truth (verified 2026-07-24 — Kali-toolkit final integration):**
  **135 loadable scenarios** across **15 detection planes** (all `status: active`,
  0 rejected / 0 dangling refs; **854/854 detection_id slugs resolve** — GAP-4 held) ·
  **135 TTP cards** (**1184 resolvable catalog detection objects**) · **14 EAL plugins** ·
  **84 tool-adapter packs** (45 distinct adapters wired across 42 scenarios) · 11 AWS
  IaC modules. `make validate` is green (**276 pass / 0 warn / 0 fail**). The
  2026-07-24 pass added a **chainable Kali toolkit** — 15 tier-4 adapters + the 2 kill-chains
  `SIM-MP-019`/`SIM-ITDR-015`; see [`kali-toolkit.md`](kali-toolkit.md). This pass
  installs the **causality contract** — optional/additive schema fields (`cgo_anchor`
  scenario-level; per-step `causality{parent_step,pivot}`, `platforms`,
  `platform_variants`) that drive `core/engine/causality_graph.py` to build a real
  CGO-rooted, parent→child **connected** process spine (and typed cross-plane pivot
  edges) instead of a synthetic `cortexsim-agent` star. 117 scenarios declare the
  contract; the connectedness sweep confirms **100 % (53/53)** of process_lineage-spine
  scenarios yield a connected `proc:`-sourced chain and **114/117 (97.4 %)** of contract
  scenarios are non-star. 114 cards were retuned to key on `causality_actor_process_*` +
  `causality_id`, and **8 new causality-strong pairs** ship: `TTP-2026-0132..0139` /
  `SIM-EDR-019` (Akira/Howling Scorpius vCenter→ESXi), `SIM-EDR-020` (CL-UNK-1068 web-shell
  → in-memory Mimikatz + FRP), `SIM-ITDR-014` (ROADtools/roadtx Entra token abuse),
  `SIM-MP-016` (Muddled Libra Okta admin takeover), `SIM-MP-017` (React2Shell pod-RCE →
  cloud control-plane, CVE-2025-55182), `SIM-MP-018` (TeamPCP weaponized-scanner supply
  chain), `SIM-KOI-008` (Shai-Hulud self-replicating npm worm), `SIM-AISPM-007` (GCP
  Vertex AI double-agent). Per-plane on disk now: EDR=20 · ANALYTICS=18 · CDR=18 · ITDR=14 ·
  NDR=11 · KOI=8 · AI_SPM=7 · BROWSER=6 · CLOUD_APP=6 · AIRS=5 · AI_ACCESS=5 · ASM=4 ·
  CSPM=4 · EMAIL=4 · TIM=3.
- **Counted ground truth (verified 2026-07-23 — Unit 42 two-track final integration):**
  **125 loadable scenarios** across **15 detection planes** (all `status: active`,
  0 rejected / 0 dangling refs; **761/761 detection_id slugs resolve** — GAP-4 held) ·
  **125 TTP cards** (**1078 resolvable catalog detection objects**) · **14 EAL plugins** ·
  **69 tool-adapter packs** (34 distinct adapters wired across 41 scenarios) · 11 AWS
  IaC modules. `make validate` is green (**256 pass / 0 warn / 0 fail**). This pass adds
  the 12 `TTP-2026-0120..0131` pairs (113 + 12 = 125) sourced from Unit 42 threat research,
  emphasizing cross-plane CORRELATION and ABIOC depth: Phantom Taurus NET-STAR stitch
  (`SIM-MP-011`), IAM PassRole posture→runtime AssumeRole/IMDS (`SIM-CSPM-003`), EDR-blinding
  BYOVD race (`SIM-MP-012`), Muddled Libra rogue-VM alert-storm grouping (`SIM-MP-013`),
  AzureHound cloud-identity enumeration (`SIM-MP-014`), lateral-tool-transfer chisel/PsExec
  (`SIM-EDR-015`), ClickFix pastejacking + interpreter-depth (`SIM-EDR-016/017`), BadSuccessor
  dMSA privesc (`SIM-ITDR-013`), unencrypted-snapshot KMS-gap export (`SIM-CSPM-004`), and
  AI-SOC triage/summarization validation (`SIM-MP-015`, `SIM-EDR-018`). This closes the CSPM
  sub-floor (2→4, meets floor 3) and adds methodology-family depth (F1/F2/F4/F6/F7). It also
  ships a new **coverage-analyzer** (`detection_scanner/scripts/coverage_report.py`, `make
  coverage` / `make coverage-strict`). Per-plane on disk now: CDR=18 · EDR=18 · ANALYTICS=15 ·
  ITDR=13 · NDR=11 · KOI=7 · ai_spm=6 · browser=6 · cloud_app=6 · ai_access=5 · airs=5 ·
  asm=4 · cspm=4 · email=4 · tim=3.
- **Counted ground truth (verified 2026-07-23 — Unit 42 final integration):**
  **113 loadable scenarios** across **15 detection planes** (all `status: active`,
  0 rejected / 0 dangling refs; **683/683 detection_id slugs resolve**) · **113 TTP
  cards** (**965 resolvable catalog detection objects**) · **14 EAL plugins** ·
  **69 tool-adapter packs** (34 distinct adapters wired across 38 scenarios) · 11 AWS
  IaC modules. This block corrects a documentation drift as well as adds content: the
  2026-06-17 bullet claimed 88 scenarios / 89 cards, but disk already carried **99 / 99**
  (undocumented interim content) before this pass. This final integration adds the 14
  `TTP-2026-0106..0119` pairs (99 + 14 = 113) sourced from Unit 42 threat research —
  edge-appliance zero-days (Cisco ASA/FTD `SIM-NDR-010`, Ivanti `SIM-MP-010`, PAN-OS
  GlobalProtect `SIM-ITDR-012`), autonomous-agent / MCP / prompt-injection AI threats
  (`SIM-KOI-007`, `SIM-MP-009`, `SIM-BROWSER-006`), AWS IAM Roles Anywhere abuse
  (`SIM-CDR-017/018`), Salesforce OAuth token abuse (`SIM-CLOUD-006`), aged sleeper-domain
  DGA (`SIM-TIM-003`), MDM mass-wipe (`SIM-ITDR-011`), and China-nexus staging
  (`SIM-EDR-013/014`, `SIM-NDR-011`). Per-plane on disk: CDR=18 · EDR=14 · ITDR=12 ·
  multi_plane=10 · NDR=11 · KOI=7 · ai_spm=6 · browser=6 · cloud_app=6 · ai_access=5 ·
  airs=5 · asm=4 · email=4 · tim=3 · cspm=2. `detection_type` vocabulary stays six
  (`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`); XDM modeling rules remain a
  substrate. The `make validate` gate is green (232 pass / 14 warn / 0 fail; the 14
  WARNs are new-but-real datasets `cisco_asa_raw`, `ivanti_ics_raw`,
  `panw_ngfw_globalprotect_raw` — advisory, not gating).
- *Prior (verified 2026-06-17 — detection-substrate expansion): 88 scenarios (99 on disk,
  undocumented) · 89 cards · 550/550 slugs · 14 EAL plugins · 15 planes.*
- *Prior (verified 2026-06-15): 75 scenarios · 76 cards · 494/494 slugs · 13 EAL plugins · 14 planes.*
- *Prior (Fable pass, 2026-06-10): 68 scenarios · 72 cards · 70-pack claim · 17 wired.*

## The eight domain references

### [`adapter-catalog.md`](adapter-catalog.md) — Tool Adapter Framework
Exhaustive inventory of all 84 `tools/packs/*.yml` adapter packs across the 5-tier
model (in-tree / submodule / IaC-provisioned / runtime-fetched / external-only),
their safety classes, and their wiring to scenarios. State (2026-07-24): all 10 source
submodules (incl. `TOOL-ATOMIC-RED-TEAM`) are provisioned and `check-adapter-sources.sh`
is green; **45 of 84 adapters are now wired** across 42 scenarios, with the c2/tier-5
packs reference-only by design. The 2026-07-24 Kali-toolkit pass added 15 tier-4
adapters — see [`kali-toolkit.md`](kali-toolkit.md).

### [`scenario-catalog.md`](scenario-catalog.md) — Scenarios
Canonical row-per-scenario archive of every YAML in `scenarios/` (58 loadable
scenarios, 195 steps, 342 expected_detections, 70 unique MITRE techniques across 11
planes). Documents where the real Pydantic validator is *looser* than the schema doc,
the 8 dead IOC `detection_id` refs that silently drop cards from POV reports, the
missing `airs` IaC module, and the v2.0 MOAT/methodology metadata adopted by only 8
of 58 scenarios.

### [`ttp-catalog.md`](ttp-catalog.md) — TTP Detection Cards
Full enumeration of the `detection_scanner/ttps/*.json` cards (now **67 cards** after
the revamp added SIM-NDR-005's `TTP-2026-0068` plus the CSPM/ASM/TIM and EDR-006/007
cards) — every detection body, MITRE chain, threat actor, PANW product mapping, and
scenario binding. The original audit's gating findings are now **RESOLVED
(2026-06-08):** the corpus passes its own validator (140 pass / 0 fail), exports are
regenerated and deterministic (213 artifacts, SKELETON=0), and the manifest/loader
contract is settled. All card bodies carry real logic (no skeletons).

### [`detection-coverage.md`](detection-coverage.md) — Detection Coverage & ATT&CK Map
Cross-reads the card corpus and the scenario corpus to map the true detection surface:
82 distinct ATT&CK techniques across 12 of 14 tactics (Reconnaissance and Resource
Development entirely uncovered). Surfaces the two parallel, non-agreeing detection-type
vocabularies (scenarios omit XQL+Correlation entirely), the silently-dropped
`additional_techniques` field, and the fact that the customer-facing coverage heatmap
is the *least* complete view that exists (it reads thin DB rows, not the 82-technique
card corpus).

### [`eal-plugin-catalog.md`](eal-plugin-catalog.md) — EAL Simulator + Plugins
First complete enumeration of all 13 EAL (Emulated Attack Layer) plugins, the
7-layer safety model, the campaign/executor machinery, and the Probe→Mutator→Target→
Scorer pipeline. Documents that the EAL campaign consent model is disjoint from the
CLAUDE.md launch-consent gate (no `c2_authorized`), several docstring/enforcement
mismatches (host-only authorisation despite port claims, absent canary checks,
`verify=False` everywhere), and that the EAL subsystem docs are stale (describe only
the 5 original NDR plugins).

### [`plane-coverage.md`](plane-coverage.md) — Detection Planes
Per-plane maturity synthesis across all 14 planes (the 13 documented + the
undocumented **AI_SPM**). Grades each plane on scenario/card/plugin/adapter/IaC
completeness. Headline findings: CSPM/ASM/TIM are IaC-only shells with zero
launchable content, AI_SPM is a fully-functional 13th plane missing from CLAUDE.md,
SIM-NDR-005 ships a scenario+plugin but no card, and detection-card depth is wildly
uneven (CDR ~9 BIOCs/card vs all AI_SPM cards at 0 BIOC/0 IOC).

### [`iac-module-catalog.md`](iac-module-catalog.md) — IaC Topology Generator
Module-by-module reference for the Terraform bundle generator: all 11 AWS modules,
every provisioned resource, every planted finding/seed, and the generate→bundle→
download flow. Headline findings: the fully-built `ai-spm` module is unreachable
(not in `ALLOWED_MODULES`), the `airs` module is referenced by 5 scenarios but does
not exist, gcp/azure are accepted by Pydantic but have zero modules on disk, and
`ttl_hours` is a no-op never rendered into any template.

### [`api-and-agent-surface.md`](api-and-agent-surface.md) — HTTP API + Agent Lifecycle
Exhaustive map of all routes (**99 registered routes** as of 2026-07-31, including the
new `/api/uctc` read surface and `/api/pov` entitlement scoping), the ORM state machines,
the Go beacon poll/execute/report loop, the orchestrator task queue, and the
push-bundle generator. The original audit's headline findings are now **RESOLVED
(2026-06-08):** pull mode works end-to-end (wire-shape aligned), `/abort` + the
`aborted` state + `/control` shipped, SSE streaming (`/events`) shipped, agent
online/stale/offline heartbeat shipped, and the `complete`/`completed` token mismatch
is reconciled. Remaining open: queue durability (GAP-API-005), push-run terminal
state (GAP-API-004). The doc's "MISSING" notes have been updated in place.
