# ARTEX integration into the POV engine (1.3.0) — design

**Status:** draft for approval · **Date:** 2026-11-16 · **Target release:** 1.3.0
**Branches:** `claude/repo-integration-1-3-0-ba57aa` (cortex-pov-engine) · `claude/repo-integration-1-3-0-fc0a41` (ARTEX)

## Intent and the why

CortexSim (the POV engine) validates *detection* quality — it generates signal
INTO a customer's Cortex tenant and measures what fires. It has no *offensive*
surface: a DC cannot, from the POV console, run an autonomous attack against a
lab target and watch the kill-chain unfold. ARTEX is exactly that missing half —
an AI-autonomous penetration-testing platform (traffic interception, LLM agents,
asset-coverage graph, findings, human-in-the-loop intercept approval).

The ask for 1.3.0: make ARTEX **a sub-component of the POV engine** — the DC
selects a new tab, goes through a **separate setup**, and then has **the full
ARTEX functionality** inside the console. One tool for the DC, not two.

"Same functionality" is the governing constraint: we surface ARTEX's real
capability, we do not reimplement it.

## Goals

- A new top-level **console tab** in the POV engine that presents ARTEX's full
  working surface (dashboard, tasks, findings, asset graph, traffic, intercept
  approval, agents, LLM config).
- A **separate setup** path reachable from that tab: configure ARTEX (database,
  LLM keys, first-run admin) without leaving the POV console.
- ARTEX runs **unmodified in substance** — upstream capability intact, upstream
  updates still adoptable.
- The POV engine **owns the deployment** — one compose stack brings up SimCore +
  ARTEX + its Postgres; the POV engine pins ARTEX's version.
- Clean license posture (see Constraints): no AGPL entanglement of the public
  cortex-pov-engine codebase.

## Non-goals (1.3.0)

- Re-skinning ARTEX's UI in the Cortex design language (native React re-build).
  That is the "C" approach; it is a later polish pass, not 1.3.0.
- Single-sign-on / shared identity between SimCore and ARTEX. ARTEX keeps its own
  `/setup` admin + login for 1.3.0 — which is what "separate setup" asks for.
- Porting ARTEX off Postgres onto SQLite, or porting any ARTEX code into Python.
- Wiring ARTEX findings/assets back into CortexSim's scoring, MTTD, or detection
  corpus. The two stay independent surfaces in 1.3.0. (Cross-feeding ARTEX's
  generated attack traffic into CortexSim's detection measurement is the obvious
  1.4.x follow-up, called out at the end — explicitly out of scope here.)

## Anti-goals (what would make this a failure even if it "works")

- Pulling ARTEX **source** into the public cortex-pov-engine repo (an AGPL
  distribution event).
- A tab that silently runs ARTEX's offensive actions without ARTEX's
  human-in-the-loop intercept approval visible and in force.
- Making the base POV deployment heavier/slower for every DC who never runs
  offensive — ARTEX + Postgres must be opt-in.
- Reimplementing ARTEX screens in React and then carrying drift against a fast-
  moving AGPL upstream.

## Constraints (the facts that decided the shape)

1. **ARTEX is AGPLv3; cortex-pov-engine is public.** AGPL is *network* copyleft.
   If ARTEX source is merged/ported into cortex-pov-engine, the combined
   distribution falls under AGPL. If ARTEX runs as a **separate process/image**
   that CortexSim talks to over HTTP, that is mere aggregation — CortexSim keeps
   its own license, ARTEX stays AGPL. **The process boundary is mandatory**, and
   CortexSim Go code must never `import` ARTEX Go packages.
2. **Different stack.** ARTEX: Go monolith serving its own Next.js static-export
   SPA on `:8787`, **PostgreSQL 16**, a mitmproxy traffic listener on `:8788`
   (localhost), JWT auth with an own `/setup` bootstrap, self-update from its own
   releases. CortexSim: Python/FastAPI `:8888`, React from `core/static/`,
   SQLite, signal-generation only. No shared runtime.
3. **ARTEX front-end has no path-prefix support.** `web/next.config.mjs` uses
   `output: "export"`, `trailingSlash: true`, assets at root-absolute `/_next/*`,
   and no `basePath`/`assetPrefix`. ARTEX cannot be served under `/artex/*`
   without an ARTEX-side change. This is what splits B1 from B2 below.
4. **Opposite risk class.** ARTEX takes real offensive action; CortexSim never
   writes to Cortex and only emits signal. The integration must keep ARTEX's
   intercept-approval front-and-centre and respect the operator's scope posture.
5. **Deployment model.** CortexSim today is a single SQLite service, "one fresh
   SimCore per POV." Adding ARTEX + Postgres changes that to a 3-service stack;
   it must be **opt-in** so the default POV is unchanged.
6. **B1 needs a browser-reachable ARTEX origin (builder check).** In B1 the DC's
   *browser* frames ARTEX, so the compose service name `artex:8787` (internal)
   will not do — ARTEX's `8787` must be **published to a browser-reachable
   address**, and `GET /api/artex/status` must probe **that same browser-facing
   origin**, not a server-side internal address, or the setup gate false-greens.
   If a DC fronts the console with HTTPS and frames ARTEX over HTTP, the browser
   blocks the frame as **mixed content**. And ARTEX's upstream compose publishes
   `8787` on `0.0.0.0` — for an unauthenticated-until-`/setup` offensive surface
   that is an exposure decision; it must follow SimCore's `CORTEXSIM_BIND`
   discipline, never default to every NIC. **B2 (reverse proxy under `/artex/*`
   on SimCore's own origin) eliminates all of constraint 6** — one origin, no
   second published port, no mixed content, honest same-origin probe — for the
   price of a `basePath` change in ARTEX.
7. **CI compose gate.** The `backend` job runs `docker compose -f
   docker-compose.yml config -q` with only a throwaway secret interpolated.
   ARTEX's `postgres` service declares a hard-required `POSTGRES_PASSWORD:?…`;
   whether a `profiles:`-gated required var keeps that gate green is
   version-dependent and **must be tested, not assumed** (builder task).

## Approaches considered

**A — Native port/rewrite (rejected).** Port Go→Python, Next.js→React,
Postgres→SQLite into `core/`. ~150K LOC, loses upstream, pulls AGPL source into
the public repo (constraint 1), and is a reimplementation, not "the same
functionality." Months, high risk.

**C — Submodule + native Cortex-skinned tab (deferred).** Vendor ARTEX as a
submodule, run its backend as a separate process, rebuild the tab natively in the
Cortex design language against ARTEX's REST API. Cohesive look, but ~56K LOC of
UI to re-implement and carry against a fast upstream. Deferred to a later release
as polish; not 1.3.0.

**B — Companion service + embedded console tab (chosen).** ARTEX runs as its own
container (its own image) + Postgres, in CortexSim's compose stack under an
opt-in profile. A new POV-console tab embeds ARTEX's own surface. A setup gate in
that tab walks the DC into ARTEX's own `/setup`/LLM config. Functionality is
ARTEX, intact; AGPL cleanly aggregated; upstream preserved.

Within B, two realizations. **1.3.0 ships B2** (decided 2026-11-16 after the
builder check sharpened B1's deployment cost — see Decisions #3):

- **B2 (chosen for 1.3.0): same-origin reverse proxy under `/artex/*`.** Add
  `basePath:'/artex'` + `assetPrefix` to ARTEX's `next.config` + Go prefix
  handling, and an SSE-safe streaming reverse proxy in FastAPI (`/artex/*` →
  internal `artex:8787`). ARTEX renders inside a POV-console tab on SimCore's own
  origin — one origin, no second published port, no mixed content, the health
  probe hits the same path the browser frames. ARTEX login/`/setup` happens in
  the frame = the "separate setup." Cost: a bounded ARTEX front-end change + the
  proxy.
- **B1 (rejected): iframe to ARTEX's own published origin.** Zero ARTEX change,
  but requires publishing+securing a second browser-reachable origin, breaks
  under an HTTPS console (mixed content), and exposes a pre-`/setup` offensive
  surface on a NIC. Rejected for a security product; would only have suited a
  jumpbox-local HTTP-only POV.

## The how, as decided (per repo)

Two PRs, one per repo (matches the two worktrees).

### cortex-pov-engine (this repo) — the integration

- **New console tab.** In `ui/src/app/destinations.jsx`:
  - add a 4th journey to `JOURNEYS`: `{ id: 'offensive', num: '4', label: 'Offensive' }`;
  - add a destination `{ id: 'artex', label: 'ARTEX', group: 'offensive',
    icon: <tbd>, Component: ArtexSurface, immersive: true }`.
  - `ArtexSurface` (new, `ui/src/components/console/ArtexSurface.jsx`): on mount,
    calls `GET /api/artex/status`; if ARTEX is reachable, frames **`/artex/`
    (same origin as the console)**; if the service is down or unconfigured,
    renders a Cortex-styled **setup gate** (status + a "configure ARTEX"
    affordance that deep-links into ARTEX's own `/artex/setup` / LLM config in
    the frame). Uses the console `kit` components (Button/Icon/StatusIcon) and
    design tokens, so the *wrapper* is Cortex even though the framed surface is
    ARTEX's.
- **Reverse proxy (the B2 core).** FastAPI mounts a **streaming, SSE-safe reverse
  proxy** at `/artex/*` → the internal upstream `http://artex:8787` (new
  `core/api/artex.py` or an ASGI mount in `core/main.py`). Must pass through
  chunked/`text/event-stream` responses without buffering (ARTEX's activity
  stream is SSE), forward `?token=`-bearing requests verbatim, and not rewrite
  ARTEX's JSON. This is why ARTEX needs `basePath:'/artex'` (its own assets/API
  must resolve under the prefix). The browser only ever talks to the console
  origin; ARTEX's `8787` is never published to the browser.
- **Backend status.** `GET /api/artex/status` → the upstream's state on **two
  axes** (builder check): reachability (`not_enabled | unreachable | reachable`)
  AND, when reachable, first-run (`initialized: bool`, from ARTEX's
  unauthenticated `GET /artex/api/auth/status {initialized}`) so the setup gate
  can tell "needs first-run admin" from "ready." **Zero outbound when disabled.**
  New `Settings` field `CORTEXSIM_ARTEX_ORIGIN` = the **internal upstream**
  (default empty → "not enabled"; e.g. `http://artex:8787`), used only
  server-side by the proxy and the probe — honest because the browser reaches
  ARTEX through that same proxy. No ARTEX credentials pass through SimCore.
- **Compose (opt-in).** Add `artex` + `postgres` services to `docker-compose.yml`
  under `profiles: ["offensive"]` (default off; `docker compose --profile
  offensive up`). Pin `ARTEX_TAG` to a **concrete version**, never `:latest`
  (the upstream default is `:latest`, which would defeat the pin); **do not** set
  `ARTEX_UPDATE_GITHUB_TOKEN` (self-update off — POV engine owns the version).
  Under B2, ARTEX's `8787` and `:8788` mitmproxy stay **internal to the compose
  network** (reached only via SimCore's proxy) — no host publish of either, which
  also disposes of the pre-`/setup` exposure worry. Postgres on its own named
  volume. The CI `docker compose config -q` gate (constraint 7) must stay
  green — test it.
- **Version + docs.** Bump `CORTEXSIM_VERSION` default (docker-compose.yml),
  `APP_VERSION` (`core/api/health.py`), and `.env.example` to **1.3.0** (held
  together by `tests/scripts/test_dev_up_existing_env.py`). CHANGELOG entry.
  CLAUDE.md gains an "Offensive plane / ARTEX" section. `/api/health` gains an
  `artex` component — added to `COMPONENT_KEYS` (the fixed tuple
  `tests/api/test_health.py` asserts as an exact set). When the origin is unset
  it reports **`ok`/`ARTEX_NOT_ENABLED`**, matching the existing opt-in `mcp`
  component ("a feature never asked for is not a fault"); NOT degraded — my
  earlier draft had this wrong. A configured-but-unreachable ARTEX is the
  degraded case.
- **Tests (functional, not guard-only).** `GET /api/artex/status` states for
  each origin state; destinations registry includes `artex` (the
  `DestinationNav` test already asserts the set); `ArtexSurface` renders the
  setup gate when status is unconfigured and the frame when reachable (jsdom —
  it cannot see the framed ARTEX itself; that is a browser/e2e concern, noted).

### ARTEX (other worktree) — the B2 prefix change

This is the bounded front-end/server change B2 buys us, built in the ARTEX
worktree as its own PR:

- **`web/next.config.mjs`:** add `basePath: '/artex'` (and `assetPrefix` as
  needed) to the static-export branch so assets/routes resolve under `/artex/*`.
  Rebuild the embedded static export; verify `/artex/_next/*` and the in-app
  `/api/*` calls resolve under the prefix (ARTEX's export uses root-absolute
  paths today — this is the whole point of the change).
- **Go server:** ensure the embedded static handler + API router serve correctly
  under the `/artex` prefix (either ARTEX natively honors it, or SimCore's proxy
  strips/keeps the prefix consistently — decide in the plan; keeping the prefix
  end-to-end is simplest and matches `basePath`).
- **Embedded posture:** consume a pinned image, self-update off (omit the token).
  Add `docs/embedded-in-povengine.md` in the ARTEX repo (pinned tag, no
  self-update, internal-only `8787`/`8788`, served under `/artex` behind the POV
  engine's proxy). ARTEX already defaults SSE to same-origin (changelog
  `fix(web): SSE 活动流默认同源`), which the same-origin proxy satisfies.
- **AGPL boundary stays intact:** this is a change to the AGPL ARTEX repo,
  committed there; no ARTEX code is imported into cortex-pov-engine.

## What's left to the builder

- Exact icon for the Offensive journey/ARTEX destination (pick from the existing
  Cortex icon set).
- The precise copy and states of the setup gate (reachable / unreachable /
  not-enabled / ARTEX-needs-first-run-admin).
- **Frame feasibility is confirmed (builder check), so the X-Frame risk is
  closed:** ARTEX sets no `X-Frame-Options`/CSP; its embedded-build auth gate
  reads `localStorage` (not a cookie), and SSE carries its token as `?token=`, so
  an iframe same-origin to ARTEX logs in and streams. The residual B1 risks are
  now (a) **browser-reachability** of the published ARTEX origin and (b)
  **mixed content** if the console is HTTPS and ARTEX HTTP — both per constraint
  6, both gone under B2.
- Whether the frame is a raw `<iframe>` or a managed pane (storage-partitioned
  but functional in current Chrome/FF/Safari).
- The compose Postgres sizing/healthcheck values and whether the POV base image
  documents the `--profile offensive` path in the runbook.

## Decisions (resolved 2026-11-16)

1. **Tab shape — DECIDED:** a new 4th top journey **"Offensive"** with one
   destination **"ARTEX"** (immersive). *(proposed default, not vetoed.)*
2. **Opt-in — DECIDED:** ARTEX + Postgres behind a compose `offensive` profile,
   **off by default**. *(proposed default, not vetoed.)*
3. **1.3.0 realization — DECIDED: B2** (reverse proxy under `/artex/*` on
   SimCore's own origin). Chosen after the builder check killed B1's header risk
   but exposed its deployment cost (a second published origin, mixed content
   under an HTTPS console, a pre-`/setup` offensive surface on a NIC). B2 trades a
   bounded ARTEX `basePath` change for one clean origin and no extra exposure.
4. **ARTEX source — DECIDED:** consume a **pinned upstream image**
   (`autumn27/artex`, concrete tag) with self-update off; the DC's `Hinln/ARTEX`
   fork remains the page-update origin per ARTEX's README. *Open to builder:
   confirm the pinned tag has an `arm64` manifest for Apple-silicon dev; if not,
   building from the fork is the fallback.*
5. **Auth — DECIDED:** keep ARTEX's **own `/setup` + login** in the tab (the
   "separate setup"); SSO deferred. *(from your wording.)*

## Follow-ups (explicitly out of 1.3.0)

- **Signal cross-feed:** route ARTEX's generated attack traffic / findings into
  CortexSim's detection measurement (ARTEX attacks → CortexSim scores what
  Cortex caught). This is the real product payoff and a 1.4.x design of its own.
- **C** native Cortex-skinned re-build of the tab.
