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
Exhaustive inventory of all 69 `tools/packs/*.yml` adapter packs across the 5-tier
model (in-tree / submodule / IaC-provisioned / runtime-fetched / external-only),
their safety classes (33 safe · 32 dual-use · 4 c2-framework), and their wiring to
scenarios. State (2026-06-15): all 10 source submodules (incl. `TOOL-ATOMIC-RED-TEAM`)
are provisioned and `check-adapter-sources.sh` is green; **34 of 69 adapters are now
wired** (was 17 — the 81%-orphan finding is superseded), with ~21 low-priority tier 1-4
candidates left to wire opportunistically and the c2/tier-5 packs reference-only by
design.

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
Exhaustive map of all routes (now **65 across 11 routers**), the ORM state machines,
the Go beacon poll/execute/report loop, the orchestrator task queue, and the
push-bundle generator. The original audit's headline findings are now **RESOLVED
(2026-06-08):** pull mode works end-to-end (wire-shape aligned), `/abort` + the
`aborted` state + `/control` shipped, SSE streaming (`/events`) shipped, agent
online/stale/offline heartbeat shipped, and the `complete`/`completed` token mismatch
is reconciled. Remaining open: queue durability (GAP-API-005), push-run terminal
state (GAP-API-004). The doc's "MISSING" notes have been updated in place.
