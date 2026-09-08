# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
From `v1.0.0` onward this project follows [Semantic Versioning](https://semver.org/);
the earlier `0.y.z` pre-releases (see `v0.1.0`) predate that commitment.

## [Unreleased]

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

[Unreleased]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/hankthebldr/cortex-pov-engine/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/hankthebldr/cortex-pov-engine/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/hankthebldr/cortex-pov-engine/releases/tag/v0.1.0
