# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
From `v1.0.0` onward this project follows [Semantic Versioning](https://semver.org/);
the earlier `0.y.z` pre-releases (see `v0.1.0`) predate that commitment.

## [Unreleased]

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
- **Reported application version** is `1.0.0` (`GET /api/health`,
  OpenAPI/`/api/docs`), matching this tag.
- **`docs/reference/ground-truth.*`** regenerated: 133 route decorators (the
  Composer's drafts router adds six), 25 `APIRouter` instances, 23 router
  files.

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

[Unreleased]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/hankthebldr/cortex-pov-engine/releases/tag/v0.1.0
