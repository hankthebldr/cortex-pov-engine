# CortexSim — Consolidated Gap Analysis & Execution Backlog

> **As-of date:** 2026-06-07 (original audit) · **Resolution pass:** 2026-06-08 ·
> **Repo state:** branch `main` @ commit `b7eebc5` (audit baseline)
>
> This is the **single master backlog** consolidating every gap found across the
> eight domain reference docs in this directory. Gaps are grouped by theme; within
> each theme a table carries `[id · severity · title · affected files · recommended
> fix · est. size]`. Every id traces back to its domain doc (see
> [`README.md`](README.md)). The **Top 10 highest-leverage actions** at the end is
> the recommended execution order.
>
> **Severity legend:** `critical` = blocks a core path or makes the product look
> broken/much-less-capable · `high` = a launchable surface is wrong or unrunnable ·
> `medium` = correctness/consistency gap with a workaround · `low` = polish, drift,
> or documented-but-incomplete.
>
> **Size legend:** `S` = < ½ day · `M` = ½–2 days · `L` = > 2 days / multi-file design.
>
> **Tally (original audit):** 73 gaps — **6 critical · 12 high · 26 medium · 29 low.**
>
> ⚠️ **The bulk of this backlog is now CLOSED.** See the RESOLUTION STATUS section
> immediately below for what shipped across the seven-theme revamp. The theme tables
> further down are preserved as the original audit record; treat the RESOLUTION
> STATUS section as the authoritative current state.

---

## RESOLUTION STATUS (2026-06-08)

A seven-theme multi-wave revamp closed the large majority of this backlog. The
fixes were verified across five independent verification lanes (V1 pytest · V2 Go
agent · V3 UI · V4 detection corpus + scenarios · V5 adapters + deploy). **Verified
results:**

- **V1 — Python suite:** 1596 passed · 80 skipped · 0 failed · 0 errors.
- **V2 — Go agent:** build/vet/test all exit 0 (`-race -count=1`); 4 packages
  (agent has no test files; beacon/executor/identity all OK); 0 failures.
- **V3 — UI:** vitest 33 files / 305 tests passed, 0 failures; `vite build` SUCCESS
  (100 modules, `index-*.js` 462.82 kB / gzip 130.73 kB).
- **V4 — Detection corpus + scenarios:** validator 140 pass / 0 warn / 0 fail;
  213 export files (sigma 56 · xql 67 · correlation 67 · xsoar_playbook 22),
  deterministic (`sha256sum -c` 213/213 OK), SKELETON=0; scenarios 63 loaded /
  63 unique / 0 rejected / 0 dangling `ttp_ref` / 0 dangling `adapter_ref`;
  catalogs 519 detection cards across 67 `ttps/*.json`, 69 adapter packs.
- **V5 — Adapters + deploy:** adapter catalog 69 loaded / 0 rejected
  (tier1=3 · tier2=1 · tier3=20 · tier4=34 · tier5=11); `check-adapter-sources.sh`
  PASS=5 WARN=29 FAIL=1; `docker compose config` exit 0; `bash -n dev-up.sh`
  exit 0; `ci.yml` valid (5 jobs); `make -n ci` exit 0.

### CLOSED gaps by theme

**Theme 1 — Detection content:**
`G-01` (corpus now passes its own validator — source ids added, 140/0/0) ·
`G-02` (stale skeleton exports regenerated — SKELETON=0) ·
`G-03` (exports now cover the corpus — 213 deterministic artifacts) ·
`G-04`/`G-08` (manifest/loader contract resolved; dead CI gate removed) ·
`G-05`/`GAP-7` (AISPM `simulation_class` relabelled to cloud-posture) ·
`S-05` (the 8 dead IOC `detection_id` refs — already-fixed at this pass) ·
`G-09` (Theme1 `_drafts/` workflow input resolved).

**Theme 2 — Agent lifecycle / backend:**
`GAP-AGENT-002` (task wire-shape aligned — pull mode no longer dispatches an empty
command) · `GAP-API-001` (`POST /api/runs/{id}/abort` + `aborted` Run state +
`GET /api/runs/{id}/control` agent stop-signal poll) · `GAP-API-003`
(`complete`/`completed` token reconciled to `complete`) · `GAP-AGENT-001`
(agent `online`/`stale`/`offline` derived from `last_seen` + background heartbeat
sweep emitting `agent.status` SSE) · `GAP-API-002`/`GAP-AGENT-004`
(SSE transport: `GET /api/runs/{id}/events` scoped + `GET /api/events` global, plus
real incremental beacon output streaming) · the **EAL-G01..G13** hardening set
(consent `c2_authorized`, port-level authorisation, redirect canary, `verify_tls`
knob, aggregate byte/rate budget, `os.urandom` bulk buffers, username masking,
parameterised model strings, dead-var cleanups, etc.) · the **108 pytest
event-loop errors** (suite is now 1596 pass / 0 errors).

**Theme 3 — Redesign / coverage views:**
`GAP-2` (scenario `detection_type` vocabulary extended to
`BIOC | XQL | Analytics | Correlation | IOC`) · `GAP-5`
(`additional_techniques` modelled + plumbed into the coverage aggregation) ·
`GAP-6` (coverage heatmap now reads the TTP card corpus joined to scenarios) ·
`S-09` (declared `detection_types` reconciled with emitted step kinds).

**Theme 4 — Deploy / CI:**
`GAP-ADAPT-01` (tier-2 adapter source-tree availability resolved; false
`already-submoduled` tags corrected; CI existence check added) · deploy DX +
CI gates (`scripts/dev-up.sh`, `.env.example`, `.github/workflows/ci.yml`
5-job matrix, `scripts/check-adapter-sources.sh`).

**Theme 5 — IaC / providers:**
`GAP-IAC-001` (`ai-spm` now reachable — `ALLOWED_MODULES` + Jinja blocks) ·
`GAP-IAC-002` (`airs` module authored + allow-listed) · `GAP-IAC-003`
(content-library/telemetry-replay frontmatter trimmed to `[aws]`) · `GAP-IAC-004`
(provider-has-modules validated at the request boundary) · `GAP-IAC-005`
(ai-spm doc-only findings reconciled to resources) · `GAP-IAC-007`
(`cdr` template wiring fixed) · `GAP-IAC-008` (telemetry-replay README ↔ content.yml
drift reconciled).

**Theme 6 — Content breadth:**
`PLANE-CSPM-ASM-TIM-NOSCENARIO` (CSPM/ASM/TIM each now ship **1 launchable
scenario + 1 TTP card** against their planted findings — no longer IaC-only shells) ·
`G-06` (LSASS `SIM-EDR-006` + ESXi/Inhibit-Recovery `SIM-EDR-007` scenarios authored
— EDR is now 7 scenarios) · `PLANE-NDR-005-NOCARD` (`TTP-2026-0068` authored for
SIM-NDR-005 — NDR is now 7 scenarios / 7 cards) · `PLANE-AIRS-OWASP-COVERAGE`
(LLM03/04/05/09 probes added — AIRS now covers OWASP LLM01-10) · **partial**
`GAP-ADAPT-02` (highest-value orphan adapters wired; remainder tracked below).

**Theme 7 — Docs / cross-refs:**
`S-11`/`GAP-7`/`PLANE-DOC-AISPM` (AI_SPM documented as the 13th plane; module count
corrected to 11) · `S-12` (Analytics scenario count + `mp-*.yml` glob fixed) ·
`GAP-ADAPT-05` (`docs/tool-adapters.md` + `tools/packs/README.md` counts refreshed
to 69 / Phase A+B+C) · `EAL-G16` (EAL docs point at the reference plugin catalog;
newer plugins backfilled) · `GAP-ADAPT-03` (the 4 dead `equivalents[]` refs fixed
+ loader validation) · `GAP-ADAPT-04` (Gophish/DVWA semantic mismatches corrected) ·
`GAP-ADAPT-07` (`identity_required` enum-validated at load).

### CLOSED in the 2026-06-10 Fable pass

- **`GAP-API-005`** — queue durability **CLOSED**. The orchestrator queue is now a
  write-through cache over a `queued_tasks` DB table; `orchestrator.rehydrate()`
  runs on the FastAPI lifespan, restoring undelivered tasks into the in-memory
  queue and failing any orphaned `pending`/`running` run whose task was lost on
  restart. (`core/models.py::QueuedTask`, `core/engine/orchestrator.py`,
  `core/main.py` lifespan; `tests/engine/test_orchestrator_queue.py`.)
- **`GAP-4`** — per-detection traceability **CLOSED**. A full resolution sweep
  shows **430 of 430** scenario `detection_id` slugs now resolve to a card
  detection object (the former last-holdout SIM-NDR-005 pre-flight step now
  resolves). 0 unresolved.
- **`GAP-API-004` / `GAP-PUSH-001`** (partial) — push-mode runs now advance to a
  terminal `staged` state on bundle generation instead of orphaning at `pending`;
  push and pull resolve step identity from one shared `spec/identity_harness.json`.
- **`GAP-AGENT-003`** — the beacon distinguishes an unknown-agent 404 from idle
  and re-registers transparently.
- **Tier-C isolated execution (H1.1)** — **increment 1 shipped** (`deploy/tier-c/`:
  audited runner image + network sinkhole + operator script + asset tests).
- **`GAP-ADAPT-02`** (high-value increment) — the AD lab is now launchable
  end-to-end: SIM-ITDR-006 detonates AS-REP Roast + Kerberoast against the
  itdr-module-seeded accounts through the consent-gated TOOL-IMPACKET/TOOL-RUBEUS
  adapters; mp-002's impacket step is now adapter-wired. Backing card
  TTP-2026-0075.
- **Low-severity review nits** — dead `ConsoleTabs.jsx` removed; `mp-001`/`mp-003`
  already carry first-class `type: Correlation` steps; the ASM `user-agent`
  `ioc_type` validates against the corpus schema (no change needed).

### CLOSED in the 2026-06-11 Fable continuation

- **`GAP-12`** **CLOSED** — `validate.py` now grammar-lints every BIOC/XQL body:
  dataset↔content coherence is a HARD error (caught + fixed the real
  `okta_sso`-for-AD bug in TTP-2026-0063), plus dataset-registry + stage-verb
  WARN checks with a string-aware tokenizer (0 false positives).
- **`GAP-9`** **CLOSED** — `GET /api/mitre/atlas/coverage` aggregates the ATLAS
  surface for the AI planes (the 10 AI cards' `mitre_attack.atlas_techniques[]`,
  18 mappings) separately from ATT&CK; `catalog.card_atlas_techniques()`.
- **Tier-C increment 2 (H1.1)** **CLOSED** — `deploy/tier-c/tier_c_assert.py`
  (audit-mode-aware observed-signal assertion engine + reference specs for
  SIM-EDR-001/CDR-001/MP-004) + `tests/e2e_isolated/test_tier_c_isolated_exec.py`
  (pure assertion tests + docker-gated e2e driver).
- **`GAP-API-011`** **CLOSED** — EAL campaign runs already carry
  `POST /api/eal/runs/{id}/abort` + the unified `pending|running|complete|
  failed|aborted` status enum (shared with core Runs).
- **Measurement loop** (the headline efficacy gap) **CLOSED** — `core/connectors/`
  pulls observed alerts (XSIAM connector + manual batch ingest) and
  auto-validates `Result` rows on technique/detection/name+window → real MTTD;
  `POST /api/runs/{id}/observations|reconcile`, an opt-in background
  auto-reconcile loop, all on the existing encrypted integration vault.
- **Agent onboarding** — enrollment-token flow (`EnrollmentToken` +
  `/api/agents/enroll*`); SimCore assigns the agent id, one-line installer.
- **`GAP-ADAPT-02` accounting** — `GET /api/tools/adapters/coverage` classifies
  the catalog as wired (17) / reference-only-by-design (14, tier-5 + c2) /
  genuine candidates (38), replacing the misleading raw "orphan" count.

### REMAINS open / deferred

- **`GAP-ADAPT-02` candidate wiring** — 38 tier 1-4 non-c2 adapters are genuine
  (low-priority) wiring candidates; surfaced now by `/api/tools/adapters/
  coverage` but most are on-demand utilities that don't each warrant a bespoke
  scenario. Wire opportunistically as scenarios need them.
- **Tier-C CI wiring** — the path-filtered hard gate for the docker-gated
  isolated-exec e2e (per the methodology doc's "CI integration" section).
- **Live read-only Cortex API poll cadence tuning** — the auto-reconcile loop
  exists and is opt-in; production cadence/back-pressure against a real tenant
  is untuned.

> The theme tables below are the **original 2026-06-07 audit** and are retained for
> traceability. Where a row is listed as CLOSED above, the table entry is historical.

---

## Theme 1 — Detection content (TTP cards, exports, validator, IOC wiring)

The detection corpus is content-rich (all card bodies carry real logic) but the
plumbing around it is broken: it fails its own CI gate, the exports are stale, and
several wiring boundaries silently drop cards from POV reports.

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| G-01 | critical | Corpus FAILS its own validator (12 errors) — references nonexistent source ids `SRC-OWASP-LLM` (5 AIRS cards) and `SRC-MICROSOFT` (1 ITDR card) | `detection_scanner/sources/source-registry.json`, `ttps/TTP-2026-0012..0016`, `ttps/TTP-2026-0041`, `scripts/validate.py` | Add the two source ids to `source-registry.json` (bump `registry_version`) or rename refs to existing `SRC-MICROSOFT-LEARN`/`SRC-MSTIC`. Then make `validate.py` green a CI gate. | S |
| G-02 | high | 18 on-disk export artifacts are stale AUTO-GENERATED skeletons (issue #65) for 9 SIM-* cards whose bodies already have real logic | `exports/xql/TTP-2026-{0007,0012,0017,0023,0027,0032,0038,0042,0047}.xql`, matching `exports/sigma/*.yml`, `scripts/export_artifacts.py` | `python3 detection_scanner/scripts/export_artifacts.py --clean` (verified to regenerate real XQL). | S |
| G-03 | high | 48 of 63 cards have NO export artifacts; `exports/README.md` index is stale | `exports/README.md`, `scripts/export_artifacts.py`, `ttps/*.json` | Full `export_artifacts.py` re-run produces all four artifact kinds for every card with the structure; regenerate the README index from the run. | M |
| G-04 | high | `manifest.json` absent + gitignored, yet RUNBOOK calls it the engine load-time entry point and the CI gate `git diff --exit-code manifest.json` cannot pass | `scripts/build-manifest.py`, `RUNBOOK.md`, `core/engine/ttp_catalog.py`, `.gitignore` | Decide loader contract: either un-ignore + commit `manifest.json` and make `ttp_catalog.py` read it, OR delete the manifest concept from RUNBOOK/CI (engine already globs `ttps/*.json`). | M |
| S-05 / GAP-4 | high | 8 IOC `detection_id` refs are dead — card never embeds onto the Result row, so it is missing from the POV report (root cause: IOC entries lack `type`, so `_slug` makes `ioc-none-*` while scenarios guessed `ioc-domain-*` etc.) | `core/engine/ttp_catalog.py`, `detection_scanner/ttps/TTP-2026-0008.json` + 7 cards, `scenarios/{ai_access,edr,multi_plane,ndr}/*.yml` | Add `type` to the IOC entries in the TTP cards (preferred — fixes slug at source) OR correct the 8 scenario `detection_id` slugs to `ioc-none-<value>`. | M |
| PLANE-NDR-005-NOCARD | high | SIM-NDR-005 (bulk-https-exfil) is launchable (scenario + EAL plugin) but has NO TTP card — NDR has 7 scenarios, only 6 cards | `scenarios/ndr/ndr-005-bulk-https-exfil.yml`, `core/eal_simulator/plugins/bulk_https_exfil.py`, `detection_scanner/ttps/` | Author `TTP-2026-NNNN-sim-ndr-005.json` with real BIOC/XQL/IOC/correlation + PANW/UC-TC mapping. | M |
| G-05 / GAP-7 | medium | AISPM cards 0054-0059 mislabeled `simulation_class: endpoint` (they are cloud-posture scans) | `detection_scanner/ttps/TTP-2026-0054..0059.json` | Change `metadata.pov_engine.simulation_class` to the cloud-posture value used elsewhere. | S |
| G-06 / GAP-3 / PLANE-BESPOKE-CARDS-NOSCENARIO | high | 4 anchor cards carry full logic but are bound to NO scenario — incl. T1003.001 LSASS dump and T1490 ESXi/Inhibit-Recovery, the most-requested EDR/ransomware demos, which are detectable on paper but unrunnable | `detection_scanner/ttps/TTP-2026-{0001,0002,0003,0006}.json`, `scenarios/` | Author scenarios for 0002 (LSASS) and 0006 (ESXi) at minimum; formally mark 0001/0003 as scanner-only reference if intentional. | M |
| GAP-4 | high | 337 scenario `detection_id` slugs imply lookupable rules; only 2 resolve to an actual card detection object (cards identify detections by name only) | `detection_scanner/ttps/*.json`, `scenarios/**/*.yml`, `scripts/validate.py` | Give each card BIOC/XQL/CORR object a stable `detection_id`, or drop the per-detection slug pretense and document traceability as card-level only. | L |
| GAP-1 | medium | Zero coverage of Reconnaissance (TA0043) & Resource Development (TA0042) — silent hole, not a documented stance | `detection_scanner/ttps/`, `scenarios/` | Either author 1-2 ASM/Xpanse-adjacent recon cards (T1595/T1592/T1583) or document the post-compromise scope explicitly. | M |
| GAP-8 | medium | 52% of techniques are single-card; Impact/Persistence/PrivEsc are wide-but-shallow (weakens ransomware POV) | `detection_scanner/ttps/`, `scenarios/` | Add detection-variant depth on Impact (T1486/T1490/T1496) and Persistence (systemd/cron/SSH-keys) so the same technique fires multiple ways. | L |
| GAP-9 | medium | AIRS/AI_ACCESS ATT&CK mappings are forced; T1567 over-loaded as a 6-card catch-all for "LLM egress" | `scenarios/airs/`, `scenarios/ai_access/`, `detection_scanner/ttps/` | Adopt MITRE ATLAS ids alongside ATT&CK for the AI planes; stop over-loading T1567/T1656/T1059. | M |
| GAP-11 | low | 143 `analytics_modules` are named references, not testable logic, but count toward "Analytics" coverage | `detection_scanner/ttps/*.json`, `core/api/mitre.py` | Distinguish validated detection (BIOC/XQL) from mapped analytics module in the report rollup. | S |
| GAP-12 | low | BIOC/XQL dialect unverified against current XSIAM 2.x grammar (self-flagged in README) | `detection_scanner/README.md`, `scripts/validate.py`, `ttps/*.json` | Add a grammar/lint check to `validate.py`, or confirm dialect against a live tenant and remove the caveat. | M |
| G-09 | low | `ttps/_drafts/` empty + untracked; documented promote-from-drafts workflow has no inputs | `detection_scanner/ttps/_drafts`, `scripts/generate_card.py` | `.gitkeep` the dir + document that drafts are local-only, or remove the workflow step. | S |

---

## Theme 2 — Agent lifecycle / backend (execution paths, runs, streaming, consent)

This is the theme with the most *critical* runtime breakage. Both execution paths
have live correctness bugs; the pull path does not work end-to-end at all.

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| GAP-AGENT-002 | critical | Task wire-shape mismatch: orchestrator emits `{steps[], identity_context}`, Go beacon expects `{command, identity{mode,username}}` — pull mode dispatches an EMPTY command via `sh -c` | `core/engine/orchestrator.py`, `agent/beacon/client.go`, `core/api/agents.py` | Align the wire contract: either flatten steps→command server-side and emit `identity{mode,username}`, or teach the beacon to iterate `steps[]` + `identity_context`. Add a contract test. | M |
| GAP-API-001 | critical | No run-abort endpoint and no `aborted` Run state — UI POSTs `/api/runs/{id}/abort` and toasts "not yet implemented" on the 404; running pull tasks cannot be cancelled server-side | `core/api/runs.py`, `core/models.py`, `core/engine/orchestrator.py`, `ui/src/AppConsole.jsx` | Add `POST /api/runs/{id}/abort`, add `aborted` to the Run status enum, dequeue the task + propagate cancel to the beacon. | M |
| GAP-API-002 / GAP-AGENT-004 | high | No SSE/event-stream endpoint (UI falls back to polling) AND the beacon "streaming" is a no-op — `outputBuffer` is never written mid-run, so only one final `/output` POST happens | `core/api/runs.py`, `agent/beacon/client.go`, `ui/src/components/console/{useRunEventStream.js,EventStream.jsx}` | Add `GET /api/runs/{id}/events` (SSE), and make the beacon stream incrementally (pipe `cmd.Stdout`/`Stderr` to the buffer during exec). | L |
| GAP-AGENT-001 | high | Agent `offline` status declared but never set; no heartbeat/staleness sweep — a dead beacon stays `online` forever | `core/models.py`, `core/api/agents.py` | Add a dedicated heartbeat endpoint (or TTL on `last_seen`) + a background sweeper that flips stale agents to `offline`. | M |
| GAP-API-003 | high | Run status mismatch: backend emits `complete`, UI matches `completed` — the "last completed run" fallback never matches a real run | `core/api/runs.py`, `ui/src/AppConsole.jsx` | Normalize to one token (`complete`) on both sides; add `aborted` once GAP-API-001 lands. | S |
| GAP-API-005 | high | In-memory task queue (`Orchestrator._queue` dict) is ephemeral — SimCore restart drops all undelivered tasks while the durable Run stays `running` with no re-enqueue | `core/engine/orchestrator.py` | Persist queued tasks (DB table) and rehydrate on startup, or at minimum mark orphaned `running` runs `failed` on boot. | M |
| GAP-API-004 / GAP-PUSH-001 | medium | Push-mode runs orphan at `pending` forever (bundle never phones home) AND push vs pull use two divergent identity-harness models (allowlist+guess vs explicit `{mode,username}`) | `core/engine/orchestrator.py`, `core/engine/push_generator.py`, `agent/identity/harness.go`, `core/api/scenarios.py` | Advance push runs to a terminal `staged`/`exported` state on bundle generation; unify the two harness models behind one spec so a scenario behaves identically on both paths. | M |
| GAP-AGENT-003 | medium | Failed initial registration is silent and never retried; unknown-agent 404 is indistinguishable from "no task", so a mis-registered beacon polls forever doing nothing | `agent/main.go`, `agent/beacon/client.go`, `core/api/agents.py` | Re-`Register()` inside `beacon.Run` on a 404 from `PollTasks`; distinguish "unknown agent" from "no work" in the API. | S |
| EAL-G01 | high | EAL campaign consent model is disjoint from the CLAUDE.md launch gate — Campaign has `simulation_authorized` but NO `c2_authorized`; a C2-shaped EAL campaign runs with weaker consent than an equivalent scenario adapter | `core/eal_simulator/{campaign.py,safety.py}`, `core/api/eal.py`, `core/api/runs.py`, `CLAUDE.md` | Add `c2_authorized` to Campaign + enforce in `SafetyPolicy`; cross-wire to the scenario/adapter consent gate so both paths share one model. | M |
| GAP-API-011 | medium | EAL campaign runs have no abort and use a separate status enum (`pending/success/failed/...`) from core Runs (`pending/running/complete/failed`) | `core/api/eal.py`, `core/models.py` | Add EAL abort; reconcile the two status vocabularies behind one lifecycle enum. | M |
| EAL-G02 | medium | stratum/ftp/ssh authorise HOST only despite docstrings claiming port-level allowlisting — authorising a host implicitly permits any port | `core/eal_simulator/plugins/{stratum_tcp_connect,ftp_egress,ssh_egress}.py`, `core/eal_simulator/safety.py` | Follow `smb_rpc_sweep`'s model: authorise host+port, or fix the docstrings to state host-level authorisation. | S |
| EAL-G03 | medium | `oauth_grant_emulator` redirect_uri canary-marker check is documented but absent (`_redirect_safe` only checks scheme+host) | `core/eal_simulator/plugins/oauth_grant_emulator.py` | Enforce the canary marker in `redirect_uri` as the docstring promises. | S |
| EAL-G05 | medium | Every httpx client uses `verify=False` with no opt-in to verify TLS — silently trusts any MitM, not just the customer NGFW | `core/eal_simulator/plugins/{c2_http_beacon,bulk_https_exfil,llm_provider_egress,oauth_grant_emulator,idp_signin_emulator,agentic_egress}.py` | Add a per-campaign `verify_tls` knob (default keep `False` for NGFW MitM) so operators can re-enable verification. | S |
| EAL-G06 | medium | No aggregate campaign byte/rate budget — per-plugin bounds only (bulk_https alone permits 16 GiB) | `core/eal_simulator/executor.py`, `plugins/{bulk_https_exfil,c2_http_beacon}.py` | Add a campaign-level cumulative byte + request-rate ceiling in the executor. | M |
| GAP-API-008 | low | Inconsistent launch path: runs router has no prefix, so launch is `POST /api/run` (singular) while everything else is `/api/runs/...` | `core/api/runs.py` | Give the router the `/api/runs` prefix; alias the old path during transition. | S |
| GAP-API-012 | low | Credentials router 404s with a bare-string detail, breaking the structured-error design rule | `core/api/credentials.py` | Return `{error, code, detail}` on 404 like every other router. | S |
| GAP-API-007 | low | Health endpoint hard-codes `version: 1.0.0`, no commit/build stamp, no component health | `core/main.py`, `ui/src/AppConsole.jsx` | Surface commit SHA + per-component (DB, catalog, EAL) status. | S |
| GAP-API-009 | low | MITRE coverage counts only `status='active'` scenarios — understates authored-but-not-active content | `core/api/mitre.py` | Make the status filter explicit/optional and document it. | S |
| GAP-API-010 | low | Infra routes are sync `def` doing blocking copytree/tar on a shared lazy singleton | `core/api/infra.py`, `core/engine/infra_generator.py` | Offload generation to a threadpool/executor; remove shared mutable singleton state. | M |
| EAL-G07 | low | `ctx.authorise` is runtime-`setattr`'d, not on the `SimulationContext` dataclass — docs show `ctx.authorise(...)` which only works via the runtime patch (invisible to type checkers) | `core/eal_simulator/{executor.py,base.py}`, `docs/eal-simulator/plugin-development.md` | Declare `authorise`/`_policy` on the dataclass (or a Protocol) so it is statically visible. | S |
| EAL-G08 | low | airs/browser plugins stash counters via `setattr` on ctx; airs passes a dead `stats=lambda` | `core/eal_simulator/plugins/{airs_prompt_attack,browser_attack_runner}.py` | Return counters from `run()` instead of mutating ctx; delete the dead lambda. | S |
| EAL-G09 | low | `_COMMON_STRATUM_PORTS` lists `14433` twice (set dedups; likely meant a different port) | `core/eal_simulator/plugins/stratum_tcp_connect.py` | Verify intended port set; remove the duplicate. | S |
| EAL-G10 | low | Per-byte `rng.getrandbits(8)` payload generation is pathologically slow at multi-GiB scale (bulk_https/c2/dns) | `core/eal_simulator/plugins/{bulk_https_exfil,c2_http_beacon,dns_tunnel_exfil}.py` | Use `os.urandom`/`random.randbytes` for bulk buffers. | S |
| EAL-G11 | low | `ftp_egress` logs the (overridable) username in cleartext in the ECS audit event | `core/eal_simulator/plugins/ftp_egress.py` | Mask/omit non-sentinel usernames in the audit event. | S |
| EAL-G12 | low | Hardcoded provider model strings (`gpt-4o`, `claude-3-5-sonnet-20241022`) will silently age | `core/eal_simulator/plugins/llm_provider_egress.py` | Parameterise the model body field per campaign. | S |
| EAL-G13 | low | `agentic_egress` computes an unused `sep` var for non-pypi components | `core/eal_simulator/plugins/agentic_egress.py` | Move the `sep` computation into the pypi branch. | S |

---

## Theme 3 — Redesign (UI / console / coverage views)

These are surfaced as API/UI mismatches but land in the console-v2 redesign track —
they are what makes the product *look* less capable or broken to a DC at the keyboard.

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| GAP-6 | high | Coverage heatmap is computed from the thin DB scenario view, not the 82-technique card corpus — the customer-facing coverage view is the LEAST complete view that exists | `core/api/mitre.py`, `core/engine/ttp_catalog.py` | Drive `/api/mitre/coverage` off the TTP catalog joined to scenarios via `ttp_ref`; fuse `additional_techniques` + correlation/analytics content. | L |
| GAP-5 | high | `additional_techniques` is silently dropped at scenario load (absent from `scenario_loader.py` and `models.py`) — heatmap under-reports by dozens of techniques | `core/engine/scenario_loader.py`, `core/models.py`, `core/api/mitre.py`, `scenarios/_schema.yml` | Add `additional_techniques` to `ScenarioSchema` + the ORM + the coverage aggregation. | M |
| GAP-2 | critical | Scenario `detection_type` vocabulary omits XQL and Correlation entirely (`BIOC\|Analytics\|IOC`) — correlation, the strongest XSIAM differentiator, is invisible in the customer-facing POV report; XQL detections are mislabeled `Analytics` | `scenarios/_schema.yml`, `core/engine/scenario_loader.py`, `core/engine/uctc_mapper.py`, `scenarios/**/*.yml` | Extend the enum to `BIOC\|XQL\|Analytics\|Correlation\|IOC` and re-tag all 58 scenarios; render correlation in the report + UC/TC chain view. | L |
| S-09 | medium | `detection_types` declaration drifts from actual step detection kinds (SIM-MP-003, SIM-NDR-003) — report rollups understate real coverage | `scenarios/multi_plane/mp-003-*.yml`, `scenarios/ndr/ndr-003-*.yml`, `core/engine/scenario_loader.py` | Reconcile declared `detection_types` with emitted step kinds; add a loader cross-check. | S |

---

## Theme 4 — Deploy (CI gates, push bundles, runtime hardening)

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| GAP-ADAPT-01 | critical | Tier-2 adapter source trees are MISSING on disk — incl. `TOOL-ATOMIC-RED-TEAM` (the single most-referenced adapter, 8 scenario files), so every EDR-001..005 + MP-005 cannot execute the Atomic tests they claim; pack `already-submoduled` tags are false | `tools/packs/{atomic-red-team,scapy,impacket,chain-reactor,payloadsallthethings,scythe-compound-actions,seclists,yara}.yml`, `.gitmodules`, `scenarios/edr/edr-001-*.yml` | Check out `atomic-red-team` submodule + add/checkout the other 7 trees (submodule or fetch); correct the stale `already-submoduled` tags; add a CI check that every tier-2 `source_path` exists. | L |
| G-08 | low | RUNBOOK/README doc drift — dead `git diff --exit-code manifest.json` CI gate, placeholder rule_ids presented as deployable | `detection_scanner/{RUNBOOK.md,README.md}` | Fix once G-04 decides the manifest contract; annotate rule_ids as illustrative. | S |
| GAP-PUSH-001 | medium | (see Theme 2) Push vs pull identity-harness divergence — a push bundle and a pull task behave differently for the same scenario | `core/engine/push_generator.py`, `agent/identity/harness.go` | Unify both harnesses behind one spec; add a cross-path golden test. | M |

> Deploy is light as a *theme* because most deployment-relevant risk is captured in
> Theme 1 (validator/exports CI gates), Theme 2 (runtime hardening: TLS, byte
> budgets, queue durability), and Theme 5 (IaC reachability). The deploy work is
> primarily wiring the now-green validator + export regeneration into CI and closing
> GAP-ADAPT-01 so push bundles actually self-install what they claim.

---

## Theme 5 — IaC / providers

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| GAP-IAC-001 | critical | `ai-spm` module is fully built (14 resources, 8 planted findings) but UNREACHABLE — not in `ALLOWED_MODULES`, and `main.tf.j2`/`outputs.tf.j2` have no `ai-spm` block; all 6 AI_SPM scenarios + the README claim it auto-suggests | `core/engine/infra_models.py`, `infra/templates/{main.tf.j2,outputs.tf.j2}`, `infra/modules/aws/ai-spm/*`, `scenarios/ai_spm/README.md` | Add `ai-spm` to `ALLOWED_MODULES` AND add the `{% if %}` blocks to both templates; fix the README claim. | M |
| GAP-IAC-002 / S-06 | high | `airs` module referenced by 5 AIRS scenarios but does NOT exist on disk or in `ALLOWED_MODULES` — `infra_modules_needed: [base, airs]` is a dead reference | `scenarios/airs/sim-airs-001..005.yml`, `core/engine/infra_models.py`, `infra/modules/aws/` | Author `infra/modules/aws/airs/` (vulnerable-LLM + prompt-attacker lab) and allow-list it, OR repoint the scenario hint to an existing module. | L |
| GAP-IAC-003 | medium | `content-library` + `telemetry-replay` advertise `providers: [aws, gcp, azure]` but only aws exists — gcp/azure requests fail deep in generation | `infra/modules/aws/{content-library,telemetry-replay}/README.md`, `core/engine/infra_generator.py` | Trim frontmatter to `[aws]` until GCP/Azure modules ship. | S |
| GAP-IAC-004 | medium | `gcp`/`azure` accepted by Pydantic Literal but have zero modules — requests 422 deep in generation instead of failing fast at the API boundary | `core/engine/infra_models.py`, `core/engine/infra_generator.py`, `core/engine/infra_catalog.py` | Validate provider-has-modules at the request boundary with a clear error; gate gcp/azure behind a feature flag until Phase C/D. | S |
| GAP-IAC-005 | medium | `ai-spm` README documents 2 findings (Bedrock logging disabled, vulnerable ML Lambda layer) with NO corresponding Terraform resources | `infra/modules/aws/ai-spm/README.md`, `infra/modules/aws/ai-spm/main.tf` | Add the `aws_bedrock*` + `aws_lambda_layer_version` resources, or remove the two doc-only findings. | M |
| GAP-IAC-007 | low | `cdr` template block omits `jumpbox_security_group_id` + `ssh_key_name` (unlike edr/itdr/ndr) — EKS may be unreachable from the jumpbox | `infra/templates/main.tf.j2`, `infra/modules/aws/cdr/{variables.tf,main.tf}` | Verify cdr wiring; pass the two inputs if the module expects them. | S |
| GAP-IAC-006 | low | Module frontmatter `required_params`/`optional_params` are surfaced via API but never validated or plumbed into the template context | `core/engine/{infra_catalog.py,infra_generator.py,infra_models.py}` | Plumb module-specific knobs into `_template_context` + validate `required_params` against the request. | M |
| GAP-IAC-009 | low | `ttl_hours` request param is validated + placed in context but referenced by NO template — the Torque TTL hint is silently dropped | `core/engine/{infra_models.py,infra_generator.py}`, `infra/templates/{terraform.tfvars.j2,variables.tf.j2}` | Render `ttl_hours` into `terraform.tfvars` (a tag or Torque metadata var), or remove the param. | S |
| GAP-IAC-008 | low | `telemetry-replay` README ↔ content.yml drift (hayabusa, nlp-pdf-malware) | `infra/modules/aws/telemetry-replay/{README.md,content.yml}` | Reconcile the prose list to content.yml (the accurate source). | S |

---

## Theme 6 — Content breadth (scenario/plane/adapter wiring, OWASP coverage, schema enforcement)

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| PLANE-CSPM-ASM-TIM-NOSCENARIO | critical | CSPM, ASM, TIM are IaC-only shells — 0 scenarios, 0 cards, 0 plugins; a DC selecting them finds nothing launchable, no Result rows, no MTTD, no report content | `infra/modules/aws/{cspm,asm,tim}/README.md`, `scenarios/`, `CLAUDE.md` | Author ≥1 launchable scenario + TTP card per plane referencing each module's planted findings. | L |
| GAP-ADAPT-02 | high | 81% of adapters (56 of 69) are orphans referenced by zero scenarios — CDR refs none of Prowler/ScoutSuite/Trivy/Nuclei; ITDR refs none of its Windows toolchain; Empire/Starkiller/Havoc all orphan | `tools/packs/`, `scenarios/` | Wire the highest-value orphans (CDR cloud scanners, ITDR toolchain, NDR web scanners) into scenarios; mark intentional reference-only packs. | L |
| PLANE-ADAPTER-WIRING-LOW | medium | Only 27 of 65 scenarios use `adapter_ref`; 4/5 CDR + 6/7 NDR hand-roll CLI despite mapped adapters — undercuts the safety/consent + self-install machinery | `scenarios/cdr/*.yml`, `scenarios/ndr/*.yml`, `tools/packs/README.md` | Migrate hand-rolled `command:` blocks to `adapter_ref` where an equivalent adapter exists. | M |
| PLANE-AIACCESS-ISOLATED | medium | AI_ACCESS is the only active plane with no adapter, no IaC, no descriptor — hits real OpenAI/Gemini/Anthropic with no lab-contained fallback (fragile in air-gapped POVs) | `scenarios/ai_access/sim-aiacc-001-*.yml`, `core/eal_simulator/plugins/llm_provider_egress.py` | Add a contained/mock provider target (mirror AIRS's in-tree cortex-vulnerable-llm); enrich BIOC/IOC content. | M |
| PLANE-AIRS-OWASP-COVERAGE / S-17 | medium | AIRS probe library covers only OWASP LLM01/02/06/07/08/10 — LLM03/04/05/09 absent, but CLAUDE.md advertises LLM01-10 | `scenarios/airs/probes/`, `CLAUDE.md` | Add probes for the 4 missing categories, or scope the claim to 6/10. | M |
| PLANE-AIRS-PLUGIN-UNUSED | medium | `airs_prompt_attack` plugin is built + unit-tested but referenced by zero scenarios (they use `adapter_ref: TOOL-CORTEX-PROMPT-ATTACKER`) — confusing dual path that contradicts CLAUDE.md | `core/eal_simulator/plugins/airs_prompt_attack.py`, `scenarios/airs/sim-airs-001-*.yml`, `CLAUDE.md` | Either wire the plugin into the AIRS scenarios or document that AIRS uses the adapter path and the plugin is the programmatic equivalent. | S |
| GAP-10 / PLANE-BIOC-IOC-IMBALANCE | low | 3 planes (CLOUD_APP, ITDR, BROWSER) have zero IOC coverage; AI/SaaS cards are XQL-only with near-zero BIOC/IOC; detection enforcement is concentrated in EDR/CDR/Analytics | `scenarios/{cloud_app,itdr,browser}/`, `detection_scanner/ttps/TTP-2026-{0054,0030,0022}.json` | Add cheap IOC wins (SaaS app ids, phishing domains, extension hashes, IdP source IPs); document a per-plane BIOC/IOC content policy. | M |
| S-01 | medium | SIM-NDR-003..007 have only 2 steps, violating the documented 3-step minimum (loader has no min/max constraint) | `scenarios/ndr/ndr-003..007-*.yml`, `scenarios/_schema.yml`, `core/engine/scenario_loader.py` | Either add a 3rd step, fix the doc to drop the floor, or add `min_length` to the loader — pick one and enforce it. | S |
| S-04 | low | F2 stitching metadata (`correlation_window_seconds`/`required_planes_in_incident`/`stitching_key`) only on 2 of 5 ANALYTICS scenarios | `scenarios/multi_plane/mp-002..004-*.yml` | Backfill F2 metadata + `moat_tier`/`methodology_family` on MP-002/003/004. | S |
| S-07 | low | v2.0 KPI/MOAT methodology metadata adopted by only 8 of 58 scenarios — limits POV-report exec-summary ordering + MOAT highlighting for the bulk of the corpus | `scenarios/**/*.yml` | Roll out `moat_tier`/`methodology_family`/`primary_kpi`/`threshold`/`success_criteria` across the corpus (or scope the v2.0 story). | L |
| S-18 | low | Only SIM-MP-004 ships a runnable package; MP-001/002/003/005 have no self-contained bundle — inconsistent "download-and-run" experience | `scenarios/multi_plane/packages/` | Author packages for the other 4 ANALYTICS scenarios, or document MP-004 as the reference exemplar. | M |
| GAP-IAC-005-style content note | — | (covered above under IaC) | — | — | — |
| S-02 | low | Per-step `expected_detections` defaults to `[]` with no min-length — a detection-less step would silently reduce report coverage | `core/engine/scenario_loader.py` | Add a loader guard / startup warning for empty `expected_detections`. | S |
| S-05a | low | SIM-NDR-005 step-01 is the only one of 342 expected_detections with no `detection_id` | `scenarios/ndr/ndr-005-bulk-https-exfil.yml` | Add a `detection_id` or formally accept pre-flight steps as un-wired (consistently). | S |
| S-16 | low | T1176 "Browser Extensions" used for a VS Code extension (SIM-KOI-004) — technique mis-fit distorts the ATT&CK heatmap | `scenarios/koi/sim-koi-004-*.yml` | Remap to T1059/T1195 (supply-chain) or an IDE-plugin abstraction. | S |
| GAP-ADAPT-04 | medium | Semantic mismatches: `TOOL-GOPHISH`→`iac_module: cspm`; `TOOL-DVWA` is tier-3 (runnable) but `planes: []` so it never appears in any plane picker | `tools/packs/{gophish,dvwa}.yml` | Re-pin Gophish to a sensible module; give DVWA a real plane (asm/edr) so it surfaces. | S |

---

## Theme 7 — Docs / cross-refs (CLAUDE.md drift, canonical-doc staleness, schema-vs-loader divergence)

The single most pervasive theme. CLAUDE.md, the canonical adapter doc, the EAL docs,
and the scenario schema all overstate or understate the real surface in ways that
mislead onboarding consultants.

| id | sev | title | affected files | recommended fix | size |
|----|-----|-------|----------------|-----------------|------|
| S-11 / GAP-7 / PLANE-DOC-AISPM | high | AI_SPM — a fully functional 13th plane (6 scenarios, 6 cards, dedicated IaC module) — is ENTIRELY absent from the CLAUDE.md plane table; also makes the "AWS feature-complete with 10 modules" claim wrong (it is 11) | `CLAUDE.md`, `scenarios/ai_spm/`, `infra/modules/aws/ai-spm/`, `detection_scanner/ttps/TTP-2026-0054.json` | Add the AI_SPM row to the plane table; correct the module count to 11. | S |
| GAP-ADAPT-05 | medium | Stale counts in canonical docs: `docs/tool-adapters.md` headline says 69 but body §2/§7 still say "18 packs"/"live: 18"; `tools/packs/README.md` still says "Phase A — only nmap exists" | `docs/tool-adapters.md`, `tools/packs/README.md` | Update both docs to the verified 69-pack / Phase A+B+C-complete reality. | S |
| S-12 | medium | CLAUDE.md stale counts + wrong glob: says Analytics has "3 multi-plane scenarios" (actual 5); references `scenarios/multi_plane/SIM-MP-*.yml` but files are `mp-NNN-*.yml` (glob matches zero) | `CLAUDE.md`, `scenarios/multi_plane/` | Fix the count to 5 and the glob to `mp-*.yml`. | S |
| EAL-G16 / EAL-G15 | medium | EAL subsystem docs are stale — 8 newer plugins enumerated nowhere; `docs/eal-simulator/plugin-catalog.md` was referenced but never existed | `docs/eal-simulator/{architecture.md,plugin-development.md,runbook.md}` | Point plugin-development.md at this reference doc as the canonical catalog; backfill the 8 newer plugins into the EAL docs. | M |
| EAL-G04 | medium | 8 of 13 plugins absent from the central `test_plugins.py` dry-run regression matrix — a regression in the dry-run contract is only caught for the 5 NDR plugins | `tests/eal_simulator/test_plugins.py`, `docs/eal-simulator/plugin-development.md` | Add the 8 newer plugins to the shared parametrized matrix. | S |
| GAP-3 (scenario S-08/S-10) | medium | Schema-vs-loader divergence: `_schema.yml` marks `threat_report`, `created`, `last_updated` "required" but the loader makes `threat_report` Optional and never models `created`/`last_updated` (Pydantic drops them) | `scenarios/_schema.yml`, `core/engine/scenario_loader.py` | Decide the contract: model + validate the fields, or remove the "required" claim from the schema doc. | S |
| GAP-ADAPT-03 | medium | `equivalents[]` in 4 packs point at non-existent adapter ids (silent dead links): `TOOL-SHARPHOUND`, `TOOL-MODLISHKA`, `TOOL-HPING3`, `TOOL-IMPACKET-GETUSERSPNS` — will break the planned naive/intermediate/advanced rotation | `tools/packs/{bloodhound,evilginx2,scapy,rubeus}.yml`, `core/tools/adapter_loader.py` | Fix the 4 refs to existing ids (or create the packs); add loader validation of `equivalents[]`. | S |
| S-12 (count note) / S-13 | low | "Number of scenarios" repeatedly miscounted — `find` returns 77, loader ingests 58; the 19 extra are `_schema.yml` + AIRS probes + browser campaigns + the SIM-MP-004 package | `scenarios/{airs/probes,browser/campaigns,multi_plane/packages}/`, `CLAUDE.md` | Document canonical count (58 loaded / 65 launchable counting AI_SPM live) to stop repeated miscounts. | S |
| GAP-ADAPT-06 | low | Tier-5 empty-`planes` behavior undocumented in the canonical doc — 11 no_invoke packs silently disappear from any per-plane filter | `tools/packs/{ghidra,radare2,capev2}.yml`, `docs/tool-adapters.md` | Document the tier-5 empty-planes convention in `docs/tool-adapters.md`. | S |
| GAP-ADAPT-07 | low | `identity_required` is a free string with no loader enum enforcement — a typo loads silently, fails only at dispatch | `core/tools/adapter_loader.py`, `tools/packs/_schema.yml` | Add an enum validator matching the `_schema.yml` identity vocabulary. | S |
| G-07 | low | `ttp_ref` quoting inconsistent across scenarios (13 quoted / 46 unquoted) — latent tooling hazard (bit a naive regex during the audit) | `scenarios/{airs,edr,multi_plane}/*.yml` | Normalize to one quoting style corpus-wide. | S |
| S-15 | low | AI_SPM tc_ref family-prefix mismatch — `uc_ref: UCS-AISPM-01` but `tc_ref: TC-AISP-NN` (AISP vs AISPM) breaks the implicit same-prefix convention | `scenarios/ai_spm/sim-aispm-001..006-*.yml`, `docs/uc_tc_mapping/` | Normalize to `TC-AISPM-NN` (or document the abbreviation). | S |
| S-14 | low | Multi-plane UC/TC refs are inconsistent (MP-001 borrows NDR UC, MP-002..004 use UCS-MP-NN, MP-005 uses UCS-IR-02) — non-uniform ANALYTICS attribution | `scenarios/multi_plane/mp-001-*.yml`, `mp-005-*.yml` | Establish a uniform UCS-MP-NN / TC-MP-NN family for the multi-plane plane. | S |
| PLANE-DESCRIPTORS-STUB | medium | All `core/planes/*.py` are Phase-2 stubs and 7 of 14 active planes have no descriptor at all — the per-plane Python layer is half-built and misleading | `core/planes/edr.py`, `core/planes/__init__.py`, `core/planes/` | Either complete the descriptor layer for all planes or formally deprecate/remove `core/planes`. | M |
| EAL-G14 | low | No EAL plugins for EDR/CDR/CSPM/ASM/TIM/Analytics planes (architectural — served by harness/signalbench/IaC) — a DC may assume the EAL path covers EDR | `core/eal_simulator/plugins/`, `CLAUDE.md` | Document the EAL plane scope so it is not mistaken for incomplete content. | S |
| GAP-IAC-doc note | low | `scenarios/ai_spm/README.md` falsely claims the IaC generator auto-suggests the ai-spm module | `scenarios/ai_spm/README.md` | Fix once GAP-IAC-001 lands (or remove the claim now). | S |

---

## Top 10 highest-leverage actions (recommended execution order)

Ordered by **(blast radius × how-broken-it-looks-to-a-DC) ÷ effort**. The first
five are cheap fixes to critical/high gaps; the back half are the structural builds
that unlock entire planes.

1. **Make the detection corpus green (G-01) + regenerate exports (G-02).**
   Two S-sized fixes that turn the CI gate from red to green and replace 18
   skeleton exports with real XQL/Sigma. Unblocks every "deploy the detection
   content" story. *(Theme 1 · S+S)*

2. **Fix pull-mode end-to-end: align the task wire-shape (GAP-AGENT-002).**
   Today a dispatched pull task runs an *empty command*. This is the single biggest
   blocker to the agent path working at all. *(Theme 2 · M)*

3. **Add the run-abort endpoint + `aborted` state (GAP-API-001) and fix the
   `complete`/`completed` mismatch (GAP-API-003).** The UI already calls abort and
   already mis-checks status — closing both makes the console stop lying to the
   operator. *(Theme 2/3 · M+S)*

4. **Document AI_SPM + correct the module count (S-11/PLANE-DOC-AISPM) and refresh
   the stale adapter/EAL/scenario counts (GAP-ADAPT-05, S-12, EAL-G16).** A batch of
   S-sized doc fixes that stop CLAUDE.md and the canonical docs from actively
   misleading every onboarding consultant. *(Theme 7 · S×several)*

5. **Restore the tier-2 adapter source trees, starting with `atomic-red-team`
   (GAP-ADAPT-01).** The most-referenced adapter (8 scenario files) has no source on
   disk, so EDR-001..005 + MP-005 cannot execute what they claim. Check out the
   submodule + fix the false `already-submoduled` tags + add a CI existence check.
   *(Theme 4 · L)*

6. **Fix the coverage heatmap to read the card corpus (GAP-6) and stop dropping
   `additional_techniques` (GAP-5).** Today the customer-facing coverage view is the
   *least* complete view that exists, making the product look far less capable than
   it is. Highest "demo impact per unit work" of the structural items. *(Theme 3 · L+M)*

7. **Extend the scenario detection-type vocabulary to include XQL + Correlation
   (GAP-2).** Correlation is XSIAM's strongest differentiator and is currently
   invisible in the POV report. Requires a schema change + re-tagging 58 scenarios.
   *(Theme 3 · L)*

8. **Unlock the AI_SPM POV fixture (GAP-IAC-001).** The module is fully built but
   unreachable — add it to `ALLOWED_MODULES` + the two Jinja blocks so the entire
   AI_SPM plane (already its most metadata-complete) becomes deployable. *(Theme 5 · M)*

9. **Give CSPM/ASM/TIM launchable content (PLANE-CSPM-ASM-TIM-NOSCENARIO).** Three
   planes in the headline table currently launch *nothing*. Author ≥1 scenario +
   TTP card per plane against the existing planted findings — the biggest
   credibility gap for a scored validation engine. *(Theme 6 · L)*

10. **Wire orphan adapters + migrate hand-rolled CLI to `adapter_ref`
    (GAP-ADAPT-02, PLANE-ADAPTER-WIRING-LOW).** 81% of adapters are orphans and only
    27/65 scenarios use the adapter framework. Wiring the high-value CDR/ITDR/NDR
    orphans turns a large-but-inert catalog into actual coverage and re-engages the
    safety/consent + self-install machinery. *(Theme 6 · L)*
