# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
From `v1.0.0` onward this project follows [Semantic Versioning](https://semver.org/);
the earlier `0.y.z` pre-releases (see `v0.1.0`) predate that commitment.

## [Unreleased]

## [1.1.0] - 2026-10-30

**The library grows from 177 to 193 scenarios, fifteen of them Unit 42
chains (thirteen counted as stitched) plus a macOS agentic-endpoint
takeover; the engine learns to say what a run proved: an outcome axis that
tells a prevention from a failure, Detection Accuracy and correlation rate
measured from the reconcile pull; and the console shows real state only,
with the Composer as a full-screen node graph.** Counted on this tree: 193
scenarios · 191 TTP cards · 27 EAL plugins · 1377 step-detections · 2123
catalog detection objects; lab-readiness GREEN 161 · YELLOW 27 · RED 5.

**`tenant-verified` is still 0.** Nothing in this release has executed
against a live Cortex tenant; the paired block-mode run (plan Task 18) and
the macOS lab run are both still owed. Authored is not proven.

### Added — SIM-KOI-009: GlassWorm poisoned IDE extension on a macOS developer endpoint

Spec `docs/superpowers/specs/2026-10-28-koi-aes-glassworm-sim-design.md`, plan
`docs/superpowers/plans/2026-10-28-koi-009-glassworm-aes.md`; source report:
Unit 42, "GlassWorm Goes Mac: Fresh Infrastructure, New Tricks".

- **`SIM-KOI-009`** (plane KOI, `UCS-SCA-01` / `TC-SCA-01`, `POV-SC-012`;
  T1195 Supply Chain Compromise plus seven follow-on techniques), six steps on
  a macOS developer endpoint: install a poisoned "Prettier Pro" extension
  through the IDE's own CLI, activation spawns a scripting-host child, decoy
  Keychain access, a LaunchAgent written and loaded from the extension host,
  multi-credential read and local staging, archive and send to a canary
  destination. Runbook rung `## Agentic endpoint (macOS)` in
  `docs/reference/lab-runbook.md` lists the prerequisites and the teardown.
- **Card `TTP-2026-0197`**: 3 BIOCs, 4 XQL queries, 1 ABIOC, 1 correlation
  rule (terminating on the staging query), 2 analytics-module references,
  8 IOCs; exported at `detection_scanner/exports/xql/TTP-2026-0197.xql`.
- **Seven step-detections, all endpoint-reachable, by design.** The scenario
  first declared nine. The two network-side queries (the archive send from
  IDE lineage, and the NGFW session to a non-marketplace host) need a sink the
  documented lab does not have, and a declared detection that cannot fire
  scores `missed`, never `not_reached`, so six of nine would have printed
  "detection accuracy 67 %, below target" for a tenant whose detections all
  worked. Both stay on the card as `purpose: hunt`;
  `tests/engine/test_sim_koi_009_shape.py` guards the count of seven.
- **Decoy pack component**
  `sources/cortex-malicious-agentic-pack/vscode/prettier-pro-canary/`:
  URI-triggered, decoy-only; `tests/eal_simulator/test_agentic_pack_prettier_pro_canary.py`
  keeps it benign with a negative control through the real check.
- **UC/TC:** crosswalk row for `SIM-KOI-009`; `TC-AEPS-09` is proposed for the
  v2.3 index (`docs/uc_tc_mapping/proposed-tc-v2.3.csv`), not a v2.2 binding.
- Corpus: 192 -> 193 scenarios, 190 -> 191 cards; golden push-bundle digests
  and ground truth regenerated (1377 step-detections, 2123 catalog detection
  objects). Never executed on a Mac: `Last executed: never`,
  **`tenant-verified` remains 0**.

### Added — Unit 42 recent-posts chains: SIM-MP-035/036/037

Spec `docs/superpowers/specs/2026-10-28-unit42-xsiam-recent-posts-research.md`.

- **Three multi-plane scenarios** with cards `TTP-2026-0194..0196` and UC/TC crosswalk rows: `SIM-MP-035` (BUGSTER AI-assisted WordPress credential attack, database exfiltration, C2), `SIM-MP-036` (CVE-2026-0257 post-exploitation: VPN session, RMM recon, vCenter/ESXi, offline DC credential dump), `SIM-MP-037` (PHP2Shell post-exploitation under a PHP worker; detections are the published Unit 42 hunting queries).
- **Emitter extensions, no plugin-count change (27):** `ngfw_threat_emitter` event pattern `vulnerability_inbound`; `globalprotect_emitter` event pattern `gateway_connected_without_auth`; `ngfw_eal_emitter` accepts `dst_ip` without `dst_domain` plus `dst_port`, `app`, `process_name` overrides; `llm_provider_egress` providers `deepseek` and `openrouter`.
- **Signal quality, measured:** 13 stitched chains (was 11), 236 high-quality signals of 1370 step-detections (17.2%, was 195/1320), marker-only 100/790 (12.7%); floors set to `hq_signal_floor` 236, `stitched_chain_floor` 13, `marker_only_ceiling` 0.1266. **`SIM-MP-037` is authored but not counted as a high-quality signal, ruling R2: its report publishes no indicator.**
- Network-leg correlation rows `CR-MP-NET-0035` and `CR-MP-NET-0036` (NGFW-only joins) on `SIM-MP-035` and `SIM-MP-036` lift Correlation to 10.1% (138/1370); `make coverage-strict` exits 0.
- Corpus: 189 -> 192 scenarios, 187 -> 190 cards; exports, golden push-bundle digests and ground truth regenerated. `tenant-verified` remains 0.

### Added — library signal expansion: 12 stitched, Unit 42-enrichable chains

Spec `docs/superpowers/specs/2026-10-27-library-signal-expansion-design.md`.

- **`indicators[]`** on scenarios (published hashes, IPs, domains as structured data) with lint/loader codes **S-22..S-27**; marker-only steps now warn.
- **`ngfw_threat_emitter`** EAL plugin (27 plugins total) and source/destination overrides on the analytics emitters.
- **Campaign-id lint** rejects `campaign_id` values the EAL campaign validator would refuse.
- **Coverage `signal_quality`** in `coverage_report.py` with gates `hq_signal_floor`, `stitched_chain_floor`, `marker_only_ceiling`, set to the measured values: 11 stitched chains, 195 high-quality signals (195/1320, 14.8%), marker-only 100/765 (13.1%, was 137/667).
- **12 Unit 42 stitched chains** `SIM-MP-023..034` with cards `TTP-2026-0182..0193`; UC/TC crosswalk bound. `SIM-MP-034` is identity-led and is not counted as a stitched chain.
- Corpus: 177 -> 189 scenarios, 175 -> 187 cards, 26 -> 27 EAL plugins; exports and ground truth regenerated. `tenant-verified` remains 0.
- `make coverage-strict` exits 0 (Correlation 10.1%, 133/1320).
- `push_supported: false` is now honoured by the bundle suite and the download/bundle routes (409 `PUSH_NOT_SUPPORTED`).

### Changed — the Composer is a full-screen node graph ("composer mode")

The Composer was a window inside a window: the nav rail, the Library tab strip
and five stacked rows of its own around a fixed 620px canvas. See
`docs/superpowers/specs/2026-10-21-composer-mode-design.md`.

- **Composer mode.** Opening the Composer keeps only the console header and its
  banners; everything below is the graph. The palette, inspector and
  workstream float over the canvas as drawers (both start closed). Leave with
  `← Exit` or Esc twice; unsaved edits ask first. Destinations can opt in with
  `immersive: true`; nothing about it is persisted.
- **Node graph.** The CGO trigger, every step and the Proof terminal are nodes,
  flowing left to right without overlap. Edges carry their relationship: select
  one to change its pivot or delete it. Drags preview live, the MiniMap works,
  palette items drag onto the canvas, and releasing a connection on empty
  canvas creates a step there. Delete re-attaches a removed step's children.
- **Off-chain steps are named.** A second root (which the loader rejects once
  any step declares a parent) now reads as a named validation problem, an
  off-chain chip and a warning edge, instead of "Chain valid" and a 422 at save.
  `+ Add step` extends the chain from the selected step or last leaf.

### Fixed — Composer: what Launch runs, what a delete or a fork keeps, what validation names

- **Launch could run something other than the canvas.** Editing a step in place
  (a pivot, a command, a re-parent) left the draft "unedited", so Launch ran the
  original corpus scenario. Any change to what executes now requires saving the
  draft first; moving a card does not.
- **Deleting a step left dangling causality references** its children pointed
  at; they now re-attach to the deleted step's parent. The card's `×`, the
  Delete key and a multi-select now share one delete, and the Delete key only
  acts with focus inside the graph.
- **A forked scenario lost its `external_tools`** (and its measurement
  contract, k8s teardown, interpreters and verification XQL). The gated tool
  still ran from its `{adapter:…}` command, but without the launch consent
  gate or the payload staging that `external_tools` drives. A fork now carries
  every field the draft contract accepts; all 193 loaded scenarios' fork
  bodies validate against `DraftScenarioSchema` (re-measured 2026-10-28 on the
  rebased tree; 176 when authored). Outside that contract and still dropped on
  a fork: `indicators[]` (14 scenarios) and `threat_report` /
  `threat_report_url`; `push_supported` / `pull_supported` are re-derived from
  the draft's own content by the drafts API (2 of the 3 rows authored
  `push_supported: false` (SIM-MP-031, SIM-MP-034) derive `true`, so a draft
  forked from them is offered a push bundle its origin refuses).
- **One `+ Add step` could reorder a branching chain.** The spine repair
  level-sorted a valid array, changing the execution order of steps the DC
  never touched. A valid array is now left alone and an invalid one moves only
  the steps it must.
- **Launch accepted a placeholder command.** A saved draft whose step still
  read `# configure the command for this step` passed the server's chain check and would have
  run a shell comment, reporting that step's detection as missed. Launch stays
  off until validation passes.
- **The Run lens could lend a verdict to an edited step.** A step that changed
  since the run now reads "not in run" instead of the old run's state.
- **A step placed before its parent** (which the loader rejects) is named in
  validation, and the card `↑`/`↓` refuses the move with the reason.

### Added — prevention efficacy, phase 1: the engine can tell "the agent stopped it" from "it failed"

Spec `docs/superpowers/specs/2026-10-26-prevention-detection-efficacy-design.md`,
plan `docs/superpowers/plans/2026-10-26-prevention-efficacy-phase1-engine-contract.md`,
wire contract `docs/reference/api-and-agent-surface.md` §1.3.1. **No live
paired run has executed yet (plan Task 18), so `tenant-verified` for
prevention is 0;** the agent version and the modules a profile can set to
Block are still owed, and the spec's §6 is provisional until they land.

- **An outcome axis on every Result** (`core/engine/outcome.py`): `prevented ·
  detected · missed · pending · not_reached · not_executed`. A block-mode run
  used to be one `failed` run whose Results read as detection misses. Now the
  step the agent killed reads `prevented`, the steps after it `not_reached`,
  and a tool that never arrived `not_executed`.
- **`Prevention` is the seventh `detection_type`**, the only one an expectation
  may carry `outcome: prevent` on; cards gain the matching object kind and the
  scorecard's type breakdown a Prevention bucket.
- **Two witnesses, or `pending`.** A prevention is confirmed only when the host
  records that the step's process was terminated (`step_outcomes`, from the
  beacon's `/complete` `steps[]`) **and** the tenant alert's action reads
  Prevented (the connector now reads `action`; the matcher gains a `prevention`
  family and per-type windows). One witness lands on `pending` with its
  reason, a lost beacon can only ever read `pending`, and a prevention is never
  hand-marked.
- **Beacon:** per-step termination cause (`exited · signaled · anchor_terminated
  · beacon_lost · timeout · aborted · not_run`), direct detection of the
  anchor's death with the orphaned step reaped, and tool presence on
  `/complete`. The orchestrator projects each task's required binaries and
  surfaces the egress warning on the run.
- **Paired runs and the canary.** `POST /api/runs` takes the launch context
  (`agent_profile_mode`, `bas_mode_attested`, `soakless_attested`,
  `attested_by`, `paired_with_run_id`, `canary_run_id`); `GET
  /api/runs/{id}/pair` returns the report and block runs with the
  per-detection delta and `severed_at_step`; `POST /api/runs/canary` links a
  block-mode launch to proof that the profile is real (409
  `CANARY_NOT_AVAILABLE` without `CORTEXSIM_CANARY_SCENARIO_LINUX`). New
  `runs`/`results` columns arrive through the boot-time `ADD COLUMN` helper;
  new tables `step_outcomes` and `observations`.
- **A run whose beacon dies terminalises** (gated on the configured liveness
  threshold), blaming the in-flight step and holding its prevent expectations
  `pending`.
- **Surfaces:** the causality graph and storyline draw the severed chain,
  `not_reached` and `prevented`; the report carries outcome icons, chain
  integrity, the prevention delta and attestation lines, and excludes
  prevented rows from MTTD; every console surface renders the outcome axis,
  the launch form the attested context, Readiness the new rungs. A manual
  validation is a visible, bounded attestation that never overwrites an
  engine miss.
- **One denominator rule** for `/api/results` coverage, the MITRE heatmap and
  the TTP badge: `pending` is never a coverage hit, excluded rows leave the
  denominator, a row whose step never ran is never certified or scored, and a
  silent detect row is written `missed` only once its own window closes.
- **Tier-D end to end:** a kill injector produces the three host witnesses and
  the classifier carries them into the verdict.

### Changed — the console says what to do next, in fewer colours, on real state only

From DC feedback on a live install: overwhelming, no clear next task, colour
without purpose, and numbers that were not this instance's. See
`docs/design/DESIGN-SYNC.md` § Minimal pass.

- **Get started is the home page.** A five-step checklist — install an agent,
  connect the Cortex tenant, data collector (optional), Broker VM (optional),
  first simulation — read from SimCore (`/api/agents`, registered tenants,
  `/api/runs`). The agent step mints a one-use enrollment token and shows the
  install command, warns when the address is loopback or the server has no
  prebuilt beacons, and polls until the agent checks in. Collector and Broker
  VM have no SimCore API, so they are confirmed by the DC and labelled as
  such. A failed first run is not "done": the step says it stopped early and
  links to the run. The suggested first run opens the guided launch with the
  active online agent preselected as the target.
- **Task rail.** Seventeen phase-grouped destinations became five tasks
  (Get started · Simulate · Runs · Results · Catalog) plus Agents and Tenant;
  other pages are tabs inside a task. Every old route still resolves. The
  footer flow bar is removed; the tour no longer auto-starts.
- **Colour rule.** Orange is the single primary action; green means healthy /
  done / detected; amber and red are real warnings and failures; everything
  else is neutral. Fixes legacy aliases that painted healthy agents, detected
  chips and "ok" dots orange, and a dark `--crit` that read as brand orange.
- **Seed data removed from live surfaces.** The prototype's catalogs rendered
  as facts (tenant "Acme Financial" with 7/9 checks passing, a `jumpbox-lin-01`
  launch-gate PASS, 12 components, a fixed coverage matrix, seed workflows in
  the composer). The Tenant page now shows the real registry; Components, CLI
  items, the scoping wizard and Tenant validation are retired behind
  redirects; `povdata/` is lane/plane vocabulary only.

### Fixed — `install.sh` reported success when it had installed nothing

- **Outside a checkout, `install.sh` ended "Installation complete" with exit
  0.** It builds from the files next to it. Piped (`curl … | bash`) or run as
  a lone downloaded copy, it installed the Go/Rust/Node toolchain, skipped
  every build step and `docker compose up` with a warning, and still printed
  the success banner with nothing listening on :8888. It now refuses first,
  before the OS check or any `sudo`, with `INSTALL_NOT_A_CHECKOUT` (exit 2),
  the missing files, and the `git clone` / `scripts/dev-up.sh` fix. The
  in-tree skips (`.gitmodules`, `agent/`, `ui/`, `ui/dist`,
  `docker-compose.yml`) are now hard failures; absent submodule tools still
  warn. The published v1.0.0–v1.0.2 `install.sh` assets predate this guard.
  `tests/installer/test_install_sh_checkout_guard.py` runs the real script
  with every host-changing command stubbed.

### Fixed — the clock preflight measured event age, not clock skew

- `_probe_clock` read one sampled `xdr_data` event's `_time` and reported
  `|event_time - now|` as skew. A perfectly NTP-synced tenant read **836 s**
  because its newest event was about 14 minutes old, and a tenant with no
  events read unmeasured. True skew now comes from the tenant's own clock in
  the HTTP `Date` header (`XsiamClient.server_time`, present on every response
  including a 403, no XQL, no dependence on ingested data), with SimCore's
  local time captured around the read and compared at the round-trip
  midpoint rather than at preflight start, which had added the XQL-scope poll
  (~17 s) to the reading. Live: 17.2 s -> 0.3 s. A malformed `Date` is
  unmeasured; a failed probe is counted. Green under `TZ=UTC` and
  `America/Los_Angeles`.

### Fixed — gates that could pass on an empty or unreadable input now fail closed

From the infra-reliability pass (2026-10-04):

- The adapter-wiring lint fails on an empty or unreadable corpus and its lost
  fail-closed guard is restored; `coverage_report.py --strict` fails on an
  unreadable corpus file; the export-determinism gate fails on a stale or
  untracked export; the tier-2 adapter preflight fails on an empty submodule
  directory; Tier-D classifies a dash "not found" as ENVIRONMENT, not TTP.
- The rust-builder stage is pinned to linux/amd64 so arm64 build hosts
  produce the same static binaries; the payload-shelf CLI defaults its base
  to the repo root; the scenario lint hook agrees with the loader on planes
  and the CGO spine.
- Console: a failed agent or tenant list fetch keeps the operator's choice;
  the Tenant seed is never presented as the bound tenant's results; a failed
  scenario-list fetch reads as a failure in the Library; Launch runs the
  scenario the operator picked last, not the slowest response; SimCore's
  top-level 422 envelope is shown instead of "HTTP 422".

### Fixed — a finished run's verdict could change after the fact

- **A late or duplicate `/complete` reopened a finished run.** The only guard
  on `complete_run`'s UPDATE was `status != "aborted"`. A second `/complete`
  with exit 1 on a run that had completed with exit 0 flipped it `complete` →
  `failed`, re-stamped `completed_at`, appended a second summary, re-published
  the terminal SSE frame and re-scored it; a push-mode `staged` run flipped to
  `complete` the same way. The UPDATE now uses the in-flight guard `/abort`
  already uses, and a callback on a finished run is a 200 no-op with
  `already_terminal: true`. A sticky-failed fan-out still owed endpoints is in
  flight and keeps accepting them. The beacon does not retry `/complete`, so
  the trigger was a replay, a proxy or another caller.
- **An `AGENT_CANNOT_STAGE_ARTIFACTS` refusal overwrote writes that landed
  while it ran.** It read the run, checked it was in flight, then wrote back
  `failed`, `completed_at` and `output` computed from that read. A fan-out's
  other endpoints keep calling back meanwhile: their `/output` chunk or
  `/complete` summary was dropped, an operator abort was turned into `failed`
  with its marker gone, and two beacons refused at once published two terminal
  frames. The refusal is now one guarded UPDATE that appends its marker in
  SQL, the same in-flight guard `/complete` and `/abort` use, and only the
  request that failed the run publishes the frame. The beacon still gets the
  409.

### Fixed — the alert read-back path could not be trusted on a shared, busy tenant

Sprint 1 of `docs/uc_tc_mapping/IMPL-PLAN-xsiam-readback-and-index-honesty.md`,
from the findings in `docs/uc_tc_mapping/audit-2026-09-20.md`. **No new write
path to Cortex; `tenant-verified` is still 0.**

- **One page of 100 alerts, silently.** `XsiamConnector.pull` asked for a single
  `search_to: 100` page sorted ascending over the whole run window, so on any
  tenant raising more than 100 alerts in that window the run's own (newest)
  alerts were the ones dropped, and `reply.total_count` was never read. The
  pull now pages (`search_from`/`search_to`, ≤100/page, `max_pages` cap,
  default 20), reads `total_count`/`result_count` (including Cortex's capped
  `"9,999+"` string), and reports `truncated` / `truncation` in
  `PullResult.detail`; `reconcile_run` carries it into the summary with a
  warning that names coverage a **floor**. A non-200 on page N fails the whole
  pull rather than returning page 1 as the tenant's total.
- **No host scoping.** `ObservedAlert.host` was captured and never used, so an
  earlier alert from another endpoint won MTTD via the technique-base key.
  `reconcile_run` resolves `Run.target → Agent.hostname` and
  `matcher.scope_to_host` drops alerts naming a *different* host (host-less
  alerts are kept — identity/cloud alerts carry none). The summary reports
  `host_scope` and an unscoped run says so in `warning`.
- **One alert satisfied every detection type in a step.** All N `Result` rows
  of a step share the step's technique, so one BIOC firing marked BIOC, XQL
  and Correlation expectations observed. The alert's `source` is now captured
  (`ObservedAlert.alert_source`) and both sides map onto one family
  vocabulary (`bioc · abioc · analytics · correlation · ioc`); known-and-
  different families are not a match, agreement adds `source` to
  `matched_on`, and an unknown source ("XDR Agent") stays unconstrained but
  is recorded on the verdict.
- **Multi-technique strings lost every id after the first.** `_technique_id`
  used `re.search`; `findall` now keeps them all. `matching_service_rule_id`
  (the field Cortex documents) joins the rule-id keys.

### Changed — a malformed identity spec stops SimCore; an absent one shows on `/api/health`

`engine.identity_spec` used to answer any failure to read
`spec/identity_harness.json` with its hard-coded copy, and any empty key with
the same copy, logging one warning.

- **Present but malformed now raises `IdentitySpecError`, naming the file and
  the fault.** This covers invalid JSON, a non-object root, a missing list, a
  non-list, an empty list, a blank or non-string entry, a duplicate entry, and
  a path that exists but is not a readable file. The old reading was wrong, not
  merely quiet: `"service_accounts": "www-data"` served its characters, and on
  boot S-17 rejected 71 of 177 scenarios, each blaming the *scenario*
  ("identity 'www-data' is not declared"). `/api/health` then reported 106
  scenarios as ok. **SimCore now refuses to boot** with a malformed spec and
  scenarios present (uvicorn exit 3, the error is the last traceback line).
- **Absent still serves the built-in copy, which is pinned identical to the
  file, so bundle bytes do not change.** It is no longer silent:
  `/api/health` gains an `identity_spec` component that reports
  `IDENTITY_SPEC_ABSENT` (degraded, with the path and the `COPY spec/` fix) or
  `IDENTITY_SPEC_MALFORMED`. `identity_spec.spec_source()` says which source is
  in force.
- **Removed:** the import-time constants `DIRECT_IDENTITIES`,
  `SERVICE_ACCOUNTS` and `IMPERSONATION_PLATFORMS`. Call the accessors instead.
  Importing the module no longer reads the spec, so a malformed file raises at
  the call that needs it and the health probe can still import the module to
  report it.

### Added — the Composer canvas is direct manipulation, with a Lanes lens

Spec `docs/superpowers/specs/2026-09-08-composer-direct-manipulation-design.md`.

- **The design lens renders through React Flow.** Step nodes drag by a grip
  handle and their positions persist in `composer_layout`; a stored position
  overrides the computed layout and **Re-layout** clears it. Causality edges
  are drawn, and an illegal one (a forward or self reference, a second root
  once any parent is declared) is refused visibly through one spine-legality
  predicate shared with the parent picker. Zoom toolbar, stitch overlay and
  pivot typing on the edge; the Run lens is scoped to the open draft's own
  scenario; a saved draft keeps its `origin.steps`.
- **Lanes lens:** swimlanes by ingestion door (agent telemetry, EAL emitter,
  collector) with drag-to-retarget (`ui/src/components/console/composerLanes.js`).
  A step's channel wins over a stale lane override.

### Changed — the console moved to the PANW Executive design system

`docs/design/DESIGN-SYNC.md`. The Executive design-system tokens are vendored
under `ui/src/styles/ds/` and the accent contract inverted; the shell chrome
was rebuilt and the destination information architecture rewritten, with an
object sheet and five new surfaces; Coverage, the Launch Gate and the Tenant
page were brought to the design; the generated run topology is wired and the
seed catalogs guarded; every destination is guarded by a test, which caught
one; the 1200 px floor moved off the document and onto the bars. The minimal
pass below (2026-10-04) then reduced that architecture to five tasks.

### Added — a detector-readiness gate before the POV runs

`POST /api/eal/campaigns/{campaign_id}/detector-readiness`: the vendor alert
profiles are transcribed, every EAL detector is bound to the vendor alert it
should raise or the gap is stated, and a campaign can be gated on that
readiness before the customer is watching rather than after. Documented in
`docs/reference/eal-plugin-catalog.md`.

### Added — a high-density Library table

`ScenarioTable` (`ui/src/components/console/ScenarioTable.jsx`): the
simulation library as a dense table alongside the card grid, with the plane
and technique collectors exported for both.

### Added — sprint 2: the loop can now measure two more KPIs (still `tenant-verified: 0`)

- **Detection Accuracy is measured** (`core/connectors/measures.py`). The
  reconcile loop always computed matched and seeded and threw the ratio
  away; `reconcile_run` now hands the scorer a pull context (host scope,
  truncation, matched alerts' incident ids) and `score_run` receives
  `observed / seeded` as the measured value for the 47 scenarios whose
  primary KPI is Detection Accuracy. Machine-PASS-reachable DET/HNT rows go
  from 6 to 14 with zero extra tenant calls.
- **Correlation rate is measured** for the 21 scenarios whose KPI is
  Cross-Source Correlation Rate / Stitch Completeness / Correlation
  Coverage: the collapse ratio `(alerts − incidents) / (alerts − 1)`, from
  the `incident_id` the tenant stamps on alert objects when it does, else
  from one host-scoped `get_incidents` read (`XsiamConnector.pull_incidents`,
  same paging and truncation accounting). The basis is recorded
  (`alert_incident_ids` | `host_incidents`).
- **One honesty guard on both.** An **unscoped** pull may never produce a
  PASS (any endpoint's alert could have been credited; a FAIL stands since
  extra alerts only raise the number). A **truncated** pull may never
  produce a FAIL (the value is a floor; a floor that clears the bar passes).
  **Zero matched** stays `pending` — not landed yet is not missed. Every
  withheld value carries its reason in `tc_verdict_detail.measurement`, and
  a re-score with no new evidence keeps the last measured value rather than
  flipping a pass back to pending.
- **Exact rule-name key.** `Result.detection_name` is seeded from the card's
  detection name (new nullable column, migrated on boot) and the matcher
  consumes an alert taken on that key, so a step expecting N distinct
  detections needs N alerts instead of one alert N times. Whether the key
  fires on a real tenant depends on card names matching the tenant's alert
  names — authored content, reported per match in `matched_on`.
- **API paths are credential config.** `alerts_path` / `incidents_path`
  (v1 defaults) so a tenant on the newer alerts endpoint is a setting, not a
  patch; the path that answered is on `PullResult.detail.path` and the
  preflight `alert_shape` rung, which also reports which correlation basis
  the tenant will get.

### Added

- **Preflight `alert_shape` rung** on `POST /api/connectors/xsiam/preflight`:
  samples one alert and reports which of the keys the connector reads are
  present, the `source` value and `total_count`, with codes
  `PF_ALERT_SHAPE_PARTIAL` / `_UNMATCHABLE` / `_UNKNOWN`. An empty tenant is
  now **degraded, not ready** — auth can be green while nothing proves
  matching would work.
- `docs/uc_tc_mapping/audit-2026-09-20.md` (counted state vs the five in-tree
  "current state" documents; the generated scoreboard under-counts assertion
  bindings 4×) and the three-sprint implementation plan.
- **A non-UTC pass in the `backend` CI job.** The runner and the image are UTC,
  so a naive-UTC datetime read as host-local time went green in CI and red on
  DC laptops, twice. The step reruns the whole `tests/` tree in the same image
  and order under `TZ=America/Los_Angeles` (`scripts/test-backend-non-utc.sh`,
  `make test-backend-non-utc`). It refuses to start pytest if the zone resolves
  to UTC, because an unknown zone name or an image without tzdata does that
  silently.

### Docs

- Design specs: third-party content import (Sigma + ATT&CK Navigator,
  `docs/superpowers/specs/2026-09-29-third-party-content-import-spec.yaml`),
  the homelab detection-validation ladder
  (`docs/superpowers/specs/2026-10-04-homelab-detection-validation-ladder.md`),
  and the late-breaking-scenario authoring spec
  (`docs/authoring/late-breaking-scenarios.md`). The README's release claims
  were replaced with measured ones and the Pages site re-measured against the
  corpus and the v1.0.2 release.

### Dependencies

- `ui`: `@vitest/ui` 5.0.0 -> 5.0.5, `vite` 7.3.6 -> 7.3.7, `source-map-js`
  1.2.1 -> 1.2.2, and the vitest family bump of 2026-09-10 (`vitest`,
  `@vitest/mocker`, `@vitest/coverage-v8`). Dev-only; nothing ships in the
  image.

## [1.0.2] - 2026-09-08

**Three console defects that a DC would hit inside the first minute of a
demo, plus the deploy-hygiene defect that hid the fix for them.**

Nothing here changes a count, a verdict, or a detection claim.
**`tenant-verified` is still 0**; authored is not proven.

### Fixed

- **The destination rail presented a truncated list as a complete one.**
  `.shell` is a fixed-height `overflow: hidden` column stacking four bands
  above `.workspace` — the un-acknowledged safety gate (197px), header
  (56px), phase bar (59px) and readiness line (41px), 353px total — which
  left `.rail--nav` a 337px scrollport for 663px of destinations at
  1280x720. Chromium paints an *overlay* scrollbar there (measured
  `offsetWidth - clientWidth` = **1px**), so Readiness, Runs, EAL, Data
  Streams, Coverage and TTPs were scrolled off with no cue at all, and
  "Tools & Payloads" / "UC / TC Index" sat flush against the cut. A rail
  that silently ends does not read as scrolled; it reads as a console that
  has no Coverage surface. Density scoped to `--nav` reclaims 144px
  (663px → 519px), and a new `overflowEdges()` classifier publishes
  `data-overflow` on the nav which CSS turns into an edge fade over
  whichever side has more. The classifier fails toward *showing* the cue:
  unmeasurable scroll metrics return `both`, because a false "there is more
  below" costs one scroll and a false "that is everything" costs a
  destination the DC never opens. Routing was never implicated —
  `#/adapters` and `#/uctc` resolved correctly throughout.

- **A rebuilt console could keep serving the previous build.** The static
  mount was bare `StaticFiles`, which sends `ETag` + `Last-Modified` and no
  `Cache-Control`, handing the browser RFC 9111 heuristic caching (4.2.2):
  with no stated freshness a response may be reused *without* revalidating.
  `index.html` keeps its name across every deploy while `npm run build`
  rewrites every hashed chunk name and deploying replaces `core/static`
  wholesale, so a heuristically-fresh `index.html` kept requesting chunks
  that no longer existed. `ConsoleStatic` now states both halves explicitly
  — `assets/*` immutable for a year (the filename *is* the version),
  everything else `no-cache` (keep it, but revalidate every load, which the
  existing ETag turns into a 304). Deliberately not `no-store`, which would
  also defeat the back button.

- **The Composer's causality parent picker offered parents the model
  silently refuses.** It listed every step except the selected one,
  including *later* ones; `setCausalityParent` returns the same array
  unchanged for a forward or self reference, mirroring the loader's rule
  that a step may only descend from an earlier step. Selecting one produced
  no error, no change and no explanation. The picker is now restricted to
  steps preceding the selected one, so every offered option is one the
  model accepts.

### Changed

- **Both Palo Alto Networks marks removed from the console header.** The
  left brand block carried a `panw-mark` lockup beside the Cortex mark, and
  a second text wordmark rendered further down the header. CortexSim is not
  a Palo Alto Networks product and the header asserted otherwise in two
  places. The Cortex mark stays as the sole brand. A negative guard now
  checks class names *and* `innerHTML` — it is what located the second mark
  after the first pass missed it.

- **`CORTEXSIM_VERSION` default moves to `1.0.2`**, so the image tag and
  container name (`cortex-pov-engine-simcore-v1.0.2`) change with the code
  they carry. A container named for the old version cannot quietly serve the
  new one, or the reverse.

## [1.0.1] - 2026-09-07

**The first release that actually ships artifacts.** `v1.0.0` was tagged
correctly and then produced nothing: `release.yml` failed in its `lint-shell`
job, so there is no GHCR image, no stage2 bundles, no `SHA256SUMS`, and no
GitHub Release behind that tag. The engine at `v1.0.0` and at `v1.0.1` is
byte-identical apart from three comments — the version exists because the
release pipeline, not the product, was broken.

The honest limitations recorded under `[0.1.0]` still stand and are not
superseded by either version number. **`tenant-verified` is 0**; authored is
not proven.

### Fixed

- **`release.yml` could never publish a release** — `shellcheck install.sh`
  exits 1 on any finding regardless of severity, and it reported three, none
  of them errors: one `SC2154` and two `SC2016`, all on code that is
  deliberately written the way it is. `SC2154` is a false positive — `_ec=$?`
  *is* assigned, as the first statement in that trap's own body, but
  ShellCheck does not parse inside the single-quoted trap argument. The two
  `SC2016` findings are backwards: deferred expansion is the point, because
  the literal `$PATH` / `$HOME` must reach the user's profile unexpanded and
  resolve when they source it — taking the advice would bake the installer
  machine's `PATH` into `~/.bashrc`. Fixed with three targeted
  `# shellcheck disable=` directives that each carry their reason, rather than
  `shellcheck -S error`, which would have greened the job by disarming every
  future genuine warning in these scripts.

### Changed

- Application version reported by `GET /api/health` and the OpenAPI document
  is now `1.0.1`.

## [1.0.0] - 2026-09-04

First official launch release — the MVP. Promotes CortexSim from its `v0.1.0`
pre-release to a tagged 1.0. The engine, detection corpus, cross-platform
agent, and console described under `[0.1.0]` below are the shipping baseline;
this release adds the scenario Composer and closes the release-automation
loop. The honest limitations recorded under `[0.1.0]` — chiefly
**`tenant-verified` is 0**, authored is not proven — still stand and are not
superseded by the version number.

### Added

- **Scenario Composer (Phase 1)** — author, persist, and launch *draft*
  scenarios from the console: a draft schema + ORM converter, a
  `/api/scenarios/drafts` CRUD router, a TC-bound launch gate for draft runs,
  and a spine-constrained graph canvas with an editable inspector. Drafts are
  excluded from the Library, coverage, scope, and adapter surfaces, so an
  unfinished draft never inflates a published count.
- **Release automation off `main`** — `tag-on-main.yml` reads the version from
  `CHANGELOG.md` on a merge to `main`, cuts the matching `vX.Y.Z` tag, and
  dispatches `release.yml` (multi-arch GHCR image + GitHub Release). It is
  idempotent: an unchanged top version never re-releases, so the existing
  Pages and wiki syncs on `main` are unaffected.
- **Apache-2.0 licence, `NOTICE`, and `SECURITY.md`** — the repository was
  public with no licence, which legally reserved all rights and made it
  unusable by anyone who found it. `NOTICE` records the trademark and
  affiliation terms: CortexSim is an independent project, not an official Palo
  Alto Networks product. `SECURITY.md` gives the private disclosure path and,
  as importantly, states which properties are deliberate design rather than
  bugs — beginning with the fact that SimCore has no authentication on purpose,
  and naming the assumption that carries it (never exposed to an untrusted
  network).
- **`make launch-preflight`** (`scripts/launch-preflight.sh`) — Gate B as one
  command: branch topology, version coherence, no-abandoned-branches, the
  CLAUDE.md Gate A5 honesty invariants, the corpus gates, and image parity.
  Reports three outcomes, not two: a gate that could not RUN exits 2 and
  withholds the verdict rather than reading as a pass.
- **`docs/release/mvp-launch-runbook.md`** — the ordered `dev` -> `main`
  procedure with the measured state of every gate.

### Changed

- **Console UX overhaul** — the shell/console redesign lands: a code-split
  entry chunk for faster first paint, the TTP browser as a paged grid with a
  full-width breakout (and a visible way back), navigation aligned to the POV
  run phases, a session-scoped safety gate, and a set of accessibility and
  theming fixes (tour controls, dropdown clipping, Library height,
  always-dark bubble contrast).
- **SecOps assertion coverage** — new POS/PLT/AUT assertion artifacts close
  SecOps test cases (XTI/ERV/NDR/AGTX/APB) through the same
  `verifier.score_run` path, and UC/TC index rows whose success criteria
  contradicted their own title were corrected.
- **`generate_bash` now resolves through `_resolve_step_posix`.** It read
  `step["command"]` directly and ignored `platform_variants`, while
  `resolve_target(..., "posix")` consults `platform_variants.linux` when the
  primary command is Windows-shaped. A scenario could be judged POSIX-emittable
  *because of* its Linux variant and then ship the Windows-shaped primary —
  `SIM-MP-020`'s exfiltration step emitted `powershell.exe ... || curl
  http://127.0.0.1:8888/api/health` into a bundle whose cardinal rule is no
  SimCore dependency at runtime. On a clean Ubuntu 22.04 that step reported
  success having emitted no telemetry, so its absent detection read as "Cortex
  missed it". `generate_powershell` always resolved correctly; only the bash
  loop did not. Guarded by
  `test_bash_bundle_ships_what_the_resolver_resolved` plus `:8888` in
  `_SIMCORE_MARKERS`. Behaviour-preserving: all other scenarios emit
  byte-identical bundles.
- **`wiki-sync` no longer fails on a no-op.** The change check ran before
  `git add -A`, when `git rm -rfq .` had just staged a deletion for every page,
  so it always entered the commit branch; `git commit` then exited 1 with
  "nothing to commit" and `set -e` failed the job. A successful no-op published
  a red X, which is how a real publish failure would have gone unnoticed.
- **Corrected published counts** — Pages (28 assertions, 26 EAL plugins, 205
  authored, Correlation 114, IOC 39), README (134 routes / 23 router modules,
  26 EAL plugins), and the wiki (134 routes, 26 plugins, 28 assertions). Every
  figure is from `scripts/generate_ground_truth.py`, not retyped.
- **`CORTEXSIM_MASTER_KEY` -> `CORTEXSIM_SECRET`** across CLAUDE.md and three
  reference docs. Nothing has ever read `CORTEXSIM_MASTER_KEY`; an operator
  following the quick-start set a variable the code ignores.
- **`ci.yml` and `test.yml` declare `permissions: contents: read`.** Neither
  uses `GITHUB_TOKEN`; on a public repo an undeclared block is how a
  compromised action escalates from running tests to writing code.
- **Reported application version** is `1.0.0` (`GET /api/health`,
  OpenAPI/`/api/docs`), matching this tag.
- **`docs/reference/ground-truth.*`** regenerated: 134 route decorators, 25
  `APIRouter` instances, 23 router files.

### Fixed

- **`cp .env.example .env` no longer breaks the boot.** The quick-start's first
  line made `core/config.py::Settings()` raise, which took down the entire
  pytest suite and any local process constructing `Settings` from the repo root.
  `.env.example` gained `CORTEXSIM_VERSION` (it drives the image tag and
  container name) without a matching field on `Settings`, and pydantic-settings
  hands every dotenv key to the model — so an undeclared one is rejected, not
  ignored. The variable is now declared. Fixed by declaring it rather than by
  relaxing to `extra="ignore"`: rejecting unknown dotenv keys is the only thing
  that catches a typo'd setting name, which would otherwise read as "absent" and
  silently change boot behaviour. `tests/test_config.py` constructs `Settings()`
  against the shipped `.env.example` verbatim so the next compose-only variable
  cannot re-open this, and asserts an unknown key still raises so the guard
  cannot be removed quietly.

## [0.1.0] - 2026-08-31

First tagged pre-release. CortexSim is an enterprise detection simulation
engine for Palo Alto Networks Domain Consultants: it generates controlled,
high-fidelity signals into customer Cortex environments (XSIAM/XDR) to
validate detection logic across the full `detection_type` vocabulary
(`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`), plus the XDM
modeling-rule normalization substrate and cross-source stitching. Think
"MITRE Caldera's opinionated nephew" — a detection quality-assurance engine,
not a red-team C2. There is no authentication anywhere in this app by design:
it is built to run on a customer-lab jumpbox where the operating DC already
has full admin access.

### Added

- **SimCore orchestrator** (FastAPI, `core/`) — scenario loading from
  versioned YAML with strict Pydantic schema validation, run lifecycle
  (launch → seed → execute → complete/abort), a durable task queue that
  survives a restart, and live progress over Server-Sent Events
  (`/api/runs/{id}/events`, `/api/events`).
- **Dual execution modes** — **pull**: a Go beacon (`cortexsim-agent`) polls
  SimCore, executes steps through an identity harness, and streams output
  back; **push**: SimCore renders a self-contained bash or PowerShell bundle
  the DC downloads and runs with no SimCore dependency at runtime.
- **Cross-platform agent** — the beacon cross-compiles for
  `linux/{amd64,arm64}`, `darwin/{amd64,arm64}`, and `windows/amd64`, served
  directly from the running SimCore image (`GET /api/agents/binary`); an
  enrollment-token flow (`POST /api/agents/enroll/tokens`) mints a scoped,
  revocable one-liner so an install script never carries a bare shared
  secret.
- **Identity harness** — every step runs under a realistic service-account
  identity (`www-data`, `postgres`, `node`, `svc-account`, …) to build honest
  process-causality chains in XSIAM, driven by one shared spec
  (`spec/identity_harness.json`) consumed by both push and pull.
- **Causality contract** — an optional, additive per-scenario/per-step
  contract (`cgo_anchor`, `causality`, `platforms`, `platform_variants`) that
  collapses the synthetic beacon "star" into a connected CGO-rooted
  process/network causality graph.
- **React console UI** (`ui/`) — scenario browser and launcher, MITRE ATT&CK
  coverage heatmap, results/validation views, UC/TC index explorer, adapter
  registry, causality view, and a **Readiness** surface that renders the
  connector ladder (Authored → Configured → Reachable → Verified) so a
  green screen never overstates what has actually been proven.
- **Detection content corpus** — 177 loadable scenarios across 16 detection
  planes (EDR, CDR, NDR, ITDR, CSPM, ASM, TIM, Cloud App, Analytics,
  AI Access, AIRS, Browser, KOI, AI_SPM, Email, DLP), 175 TTP cards, 1,096
  step-level expected detections, and 1,777 catalog detection objects across
  the `BIOC | XQL | Analytics | Correlation | IOC | ABIOC` vocabulary plus
  the XDM modeling-rule substrate.
- **UC/TC alignment (FY27 v2.2 index)** — every scenario carries a validated
  foreign-key reference (`uc_ref`/`tc_ref`/`tc_refs[]`/`pov_scenario_id`) into
  the sales-motion master index, enforced at load under
  `CORTEXSIM_STRICT_REFS` (default on), and surfaced read-only in-product at
  `GET /api/uctc/*` and the console's UC/TC Index view.
- **Assertion substrate (POS/PLT/AUT)** — a second proof mechanism for the
  140 index rows that are not detection-shaped (posture, capability-presence,
  outcome-within-budget), scored through the same `verifier.score_run` as
  scenarios, with a load-time guard (`A-17`) that rejects any check that
  structurally cannot fail.
- **Optional, read-only measurement loop** (`core/connectors/`) — when a
  tenant credential is configured, SimCore reads alerts back and
  auto-validates seeded results into evidence-backed MTTD (Tier 1, offline
  scoring; Tier 2, opt-in outbound XQL verification). No credential, no
  outbound call, ever.
- **Preflight** (`POST /api/connectors/{kind}/preflight`) — answers "is my
  connection working?" before the POV starts, staged config → DNS/TLS →
  auth → scope → datasets → clock skew, so a broken tenant integration is
  caught before it is quoted as tenant proof.
- **Tool adapter framework** — 91 declarative adapter packs across a 5-tier
  model (in-tree, submodule, IaC-provisioned, runtime-fetched,
  external-only), and a **payload shelf** that stages digest-pinned
  third-party tool bytes on the DC's own SimCore so a default-deny customer
  network never has to reach the public internet to run a tier-4 tool.
- **EAL Traffic Simulator** — 21 plugins covering signal-injection (network,
  identity, SaaS, AI, browser, email) and shape-true analytics log streaming
  (CloudTrail, Azure Activity/Audit, Kubernetes audit, M365, AD/Windows
  security, NGFW EAL, Okta/Entra sign-in) to an operator-supplied collector,
  with accounted (2xx-only) delivery verdicts.
- **IaC topology generator** — Terraform bundles (AWS, 11 modules: base,
  edr, cdr, content-library, itdr, ndr, cspm, asm, tim, telemetry-replay,
  ai-spm) that Torque or a bare `terraform apply` can consume to stand up a
  target environment with intentional, documented findings.
- **`GET /api/health`** — the one diagnostic surface: never reports green for
  something it did not check, makes zero outbound calls, and names its own
  `not_checked[]` boundary explicitly.
- **CI** — a 6-job matrix (backend, agent incl. Windows cross-compile, ui,
  detection-corpus validation, UC/TC ref strictness, adapter-source
  preflight) plus a deterministic `docs/reference/ground-truth.*` regeneration
  gate.

### Changed

- CORS now serves `allow_origins=["*"]` with `allow_credentials=False`. The
  app has no authentication anywhere, so nothing ever sent credentials — the
  previous `allow_credentials=True` combination is invalid per the CORS
  spec and browsers silently rejected it. This is a correctness fix, not a
  narrowing: every route stays open, exactly as designed for an
  unauthenticated jumpbox tool.
- Reported application version moved from a placeholder `1.0.0` to `0.1.0`
  (`GET /api/health`, OpenAPI/`/api/docs`) to match this first real,
  git-tagged release rather than implying nine prior ones that never
  existed.

### Known limitations (read before you brief a customer on this)

These are not bugs to be fixed quietly later — they are the honest current
state of the project, and they are restated here on purpose so a release
note never overstates what has been proven.

- **`tenant-verified` is 0.** No run, no assertion, and no test in this repo
  has ever executed against a live Cortex tenant. Every green result — in
  the test suite, the console, and this changelog — comes from an injected
  transport. **Authored is not the same as proven.** The console's Readiness
  surface states this verbatim, and `docs/reference/ground-truth.json`
  (`tenant_verified: 0`) is the machine-checkable form of the same fact.
- **A bare Ubuntu target cannot run this corpus.** Stock `ubuntu:22.04`
  ships `www-data` with `/usr/sbin/nologin` and no home directory, so the
  identity harness fails in milliseconds and the run reads "failed" having
  executed nothing — indistinguishable from a real miss unless you know to
  look for it. `deploy/tier-d/Dockerfile.target` documents (and provisions)
  what a target actually needs; see `docs/reference/lab-runbook.md`.
- **A meaningful slice of scenario steps are placeholders.** At minimum 100
  of the corpus's 654 total steps are pure `echo`/`printf` statements that
  declare `expected_detections` without producing the underlying signal a
  sensor could catch — staged for content authoring, not yet load-bearing
  TTPs. The reproduction command and the honest caveat that the true count
  is likely higher (once `|| echo` fallbacks on real commands are counted
  too) live in `docs/reference/lab-runbook.md`.
- **Only 59 of 177 scenarios declare an MTTD-shaped primary KPI** — the only
  KPI class the engine measures natively today. The rest declare thresholds
  the engine cannot yet produce a `measured_value` for and score `pending`
  indefinitely; that is a stated gap, not a silent one.
- **The Rust tool matrix (`signalbench`, `ackbarx`, `xdrtop`) is
  `linux/amd64`-only.** No arm64 or macOS build exists yet, for reasons
  recorded in `core/Dockerfile`'s `rust-builder` stage (an OpenSSL/vendoring
  constraint on a Rust submodule this repo does not own).
- **This release was built and proven only for the host's native
  architecture** (`linux/amd64`). The published multi-arch image
  (`linux/amd64` + `linux/arm64`) is produced by CI on tag push, not by this
  local build — see `docs/release/PUBLISH-v0.1.0.md`.

[Unreleased]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/hankthebldr/cortex-pov-engine/releases/tag/v0.1.0
