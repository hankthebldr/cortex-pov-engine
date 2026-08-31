# CortexSim — Detection Simulation Engine

CortexSim generates controlled, high-fidelity attack signal into a Palo Alto
Networks Cortex environment (XSIAM/XDR) so a Domain Consultant can validate
detection logic — BIOC, XQL, Analytics, Correlation, IOC, and ABIOC — before a
customer's own SOC has to. Think "MITRE Caldera's opinionated nephew": not a
red-team C2, a detection quality-assurance engine. It runs 177 scripted
scenarios across 16 detection planes via either a pull-model agent or a
self-contained push bundle, under a realistic per-step identity harness so
XDR sees real process-causality chains instead of everything running as one
account. **There is no login and no API key anywhere in this app.** It is
built to run on a customer-lab jumpbox where the operating DC already has
full admin access — see [No authentication, by design](#no-authentication-by-design)
before you put it anywhere else.

## Read this before anything else

> **`tenant-verified` is `0`.** No run, no assertion, and no test in this
> repo has ever executed against a live Cortex tenant. Every green checkmark
> — in `/api/health`, in the console, in this file — comes from an injected
> transport or a local lab container. **Authored is not proven.** Connecting
> a real tenant is opt-in (`POST /api/connectors/{kind}/preflight` before any
> POV) and CortexSim never writes to it.
>
> **A bare `ubuntu:22.04` target cannot run this corpus.** Every non-`root`
> step runs under a service-account identity (`www-data`, `postgres`, `node`,
> …), and a stock cloud image ships those accounts with no login shell. The
> harness dies in milliseconds, the run reads `failed`, and an absent
> detection under that step reads exactly like *"Cortex missed it"* — nothing
> was ever executed for Cortex to miss. Use
> [`deploy/tier-d/Dockerfile.target`](deploy/tier-d/Dockerfile.target) (a
> real provisioned target) for anything beyond reading this file, and see
> [Reading a run honestly](#reading-a-run-honestly) before you report a
> `failed` run as a coverage gap.
>
> **A meaningful slice of the corpus is placeholder.** At minimum 100 of the
> corpus's 654 steps are pure `echo`/`printf` — they declare
> `expected_detections` without producing the underlying signal a sensor
> could catch. Open a scenario's YAML under `scenarios/{plane}/` and read the
> `command:` lines before you build a POV plan around it; don't assume the
> declared detections mean real signal will land.

---

## 5-minute quickstart

```bash
export DOCKER_CONTEXT=default          # only needed if Docker Desktop hijacked your context
git clone https://github.com/hankthebldr/cortex-pov-engine.git
cd cortex-pov-engine
./scripts/dev-up.sh
```

Verified live, this session:

```
[dev-up] docker: Docker version 29.7.2, build a7dcaa6 — daemon reachable.
[dev-up] .env already present — leaving it untouched.
[dev-up] Building and starting SimCore: docker compose up -d --build
...
[dev-up] Waiting for http://localhost:8888/api/health ...
  ✓ CortexSim is up:  http://localhost:8888
```

That's it — one command, Docker is the only prerequisite. `scripts/dev-up.sh`
needs **no Go/Rust/Node toolchain and no git submodules on your host**: the
image bakes the Go agent matrix, the Rust tools, and the tool-payload shelf
at build time. It's idempotent — re-run it any time; an existing `.env` (and
its generated secret) is never touched. Open `http://localhost:8888`.

**A `degraded` status on first boot is normal, not broken** — it almost
always just means no agent has enrolled yet:

```bash
curl -s http://localhost:8888/api/health | python3 -m json.tool
```

`GET /api/health` is the one diagnostic surface, and it obeys one rule: it
never reports green for something it didn't check. Read the `code` and
`detail` on any non-`ok` component before you treat `degraded` as a problem —
`dev-up.sh` itself prints them for you. A genuinely empty catalog (`count: 0`
on scenarios or adapters) is the one shape of `degraded` that means something
is actually wrong — see `docs/reference/lab-runbook.md` for the full read.

No Docker on this host, or the daemon/registry unreachable (sandboxed CI,
cloud dev container)? `./scripts/dev-up-native.sh` is the Docker-free twin —
generates `.env`, builds the venv, builds the UI, launches SimCore natively,
polls the same health endpoint.

From here: [deploy an agent](#deploy-an-agent) →
[launch a simulation](#launch-a-simulation).

---

## Two ways to get CortexSim running

Both paths below start with a plain `git clone` — **do not** pass
`--recurse-submodules`. Two of the ten registered submodules
(`sources/CDR`, `sources/xsiam-prisma-cdr-lab`) live in repos that may be
private/org-restricted; a plain clone sidesteps the question entirely, and
each script below handles submodules on its own terms.

### Container path — recommended, what's tested above

```bash
git clone https://github.com/hankthebldr/cortex-pov-engine.git
cd cortex-pov-engine
./scripts/dev-up.sh
```

Needs only Docker (+ internet for the *first* build, to pull base images and
language deps — nothing after that). No submodules touched, no toolchain
installed on your host. This is the path verified live above and the one the
rest of this README assumes.

A prebuilt image (`ghcr.io/hankthebldr/cortexsim`) is the intended fast path
once a release is cut, but **v0.1.0 has not been pushed yet** — see
[Releases & Packaging](#releases--packaging). Don't `docker pull` it today;
it 404s. `scripts/dev-up.sh` builds the equivalent image locally instead, and
is the working path right now.

### Source build path — contributors, or building the toolchain itself

```bash
git clone https://github.com/hankthebldr/cortex-pov-engine.git
cd cortex-pov-engine
./install.sh
```

`install.sh` is the developer path: it installs a Go/Rust/Node/Python
toolchain on the host, initializes all ten submodules, and builds every
component from source (agent, Rust tools, React UI) before bringing up
Docker Compose. Use it when you're modifying the toolchain itself, not to
just run CortexSim. Requires Ubuntu 22.04 LTS+ or Debian 12+.

If `sources/CDR` or `sources/xsiam-prisma-cdr-lab` are inaccessible to your
GitHub account, `install.sh` now **degrades to a named warning and
continues** — it used to abort the entire bootstrap (Go build, Rust build, UI
build included) on that one private-submodule 404; that's fixed. Nothing
else in the installer depends on either submodule.

---

## Deploy an agent

Two calls: mint a token against SimCore, then run the one-liner it gives you
**on the target** — the jumpbox you want to generate signal on, not the host
running SimCore.

```bash
# 1. Mint a short-lived, single-use enrollment token
curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"ttl_seconds":900,"max_uses":1,"label":"my-jumpbox"}' \
  http://localhost:8888/api/agents/enroll/tokens
```

Verified live this session:

```json
{"id":34,"token":"cxs_DFNcubHUIm985G8Mv2e4TaT8awUIqHEROyO13N6WkM0",
 "label":"readme-verify","expires_at":"2026-08-31T21:14:10.331093",
 "max_uses":1,"remaining_uses":1,"revoked":false}
```

```bash
# 2. On the TARGET jumpbox — installs as a supervised service by default
curl -fsSL 'http://<simcore-host>:8888/api/agents/install?os=linux' \
  | CORTEXSIM_TOKEN='cxs_...' bash
```

Replace `<simcore-host>` with SimCore's **routable** address, not
`localhost` — pasted on a remote jumpbox, `localhost` resolves to the
jumpbox itself and the beacon "installs successfully" while talking to
nothing. Put the token in the env var, not a `?token=` query param — the
query form works but leaves a live credential in shell history and proxy
logs.

No compiler and no public-internet egress are required on the target — the
script downloads a **prebuilt** beacon from this same SimCore and
sha256-verifies it. Verified live, this SimCore serves the full five-target
matrix today:

```json
{"binaries":[
  {"os":"linux","arch":"amd64"},{"os":"linux","arch":"arm64"},
  {"os":"darwin","arch":"amd64"},{"os":"darwin","arch":"arm64"},
  {"os":"windows","arch":"amd64"}], "total":5}
```

macOS is `os=darwin`. Windows gets a PowerShell installer at `os=windows` —
its identity story is different (see the fragment below). Confirm the beacon
checked in:

```bash
curl -s http://localhost:8888/api/agents | python3 -c '
import sys, json
for a in json.load(sys.stdin)["agents"]:
    print(a["agent_id"], a["status"], a["os"], a["last_seen_age_seconds"], "s ago")'
```

**Check `last_seen_age_seconds`, not just `status`.** `status` derives from
`last_seen` age (`online` < 30s / `stale` < 5min / `offline` ≥ 5min), but a
freshly-enrolled agent gets `last_seen` stamped at enrollment time — before
its beacon has polled even once. That reads `online` for the first 30
seconds whether or not a real beacon is actually running: a re-install that
rewrote a systemd unit without restarting the old process (fixed, but check
your installer's version) would print `online` for a phantom id the whole
window. If `last_seen_age_seconds` isn't dropping toward zero on repeat
calls a few seconds apart, nothing is actually polling — full detail in
[`docs/reference/agent-deployment.md`](docs/reference/agent-deployment.md) §4.

Full detail — `?mode=` semantics, the loopback trap, the identity-harness
false-negative it exists to close, and a console discrepancy worth knowing
about — is written up, verified live, in
**[`docs/reference/agent-deployment.md`](docs/reference/agent-deployment.md)**.

---

## Launch a simulation

```bash
curl -s -X POST http://localhost:8888/api/runs -H "Content-Type: application/json" -d '{
  "scenario_id":"SIM-EDR-001","mode":"pull",
  "target_agent_id":"<agent_id from /api/agents>",
  "consent":{"simulation_authorized":true}
}'
```

**The `consent` gate is real, at both `pull` and `push` mode** — a scenario
that binds a dual-use adapter (e.g. Atomic Red Team) is refused `409
CONSENT_REQUIRED` without `simulation_authorized: true`. Don't script around
it. It does **not** cover the raw bundle-download endpoint
(`GET /api/scenarios/{id}/download`) — a downloaded bundle carries dual-use
tooling with no prompt, by design; keep that in mind before handing one to
someone else.

No agent, or want a bundle with no SimCore dependency at runtime instead?

```bash
curl -s "http://localhost:8888/api/scenarios/SIM-EDR-001/download?format=auto" \
  -o SIM-EDR-001.sh
```

### Run hanging at `running`?

A pull-mode run's own `status` stays `running` — with `completed_at: null`
and no `output` — for as long as the target agent hasn't polled. That is
**not** the same as `pending`; grepping a run for `pending` finds nothing.
Check the queue directly:

```bash
curl -s http://localhost:8888/api/health | python3 -c '
import sys, json
print(json.load(sys.stdin)["components"]["task_queue"])'
```

A `degraded` / `TASKS_QUEUED_FOR_UNAVAILABLE_AGENT` result names which
agent(s) the queue is stuck on. It's not lost — the queue is durable and
survives a SimCore restart — but nothing will collect the task until that
agent is actually online (see "Confirming the beacon is live" above; check
`last_seen_age_seconds`, not just `status`).

### Reading a run honestly

Trust the **console's live Run Detail / Storyline view** to distinguish an
unrun step from a real Cortex miss — it's verified correct: a `failed`/
`aborted` run banners *"coverage figures on this run are not a measurement of
the tenant's detections"* above every tab, and the Storyline view downgrades
every un-reviewed detection on an unproven run from `missed` to
`not-executed`.

**Do not** trust that same silence in the **exported** markdown/JSON POV
report, or in a **push-mode** bundle for a scenario declaring
`requires_interpreters` — both were verified this repo's own hardening pass
to render a silent false success today (a missing-interpreter refusal that
the pull-mode beacon correctly reports as `RUNTIME_DEPENDENCY_MISSING` has no
equivalent in `push_generator.py`, and the exported report's own integrity
check doesn't look for that marker yet). Full walkthrough, run against a real
beacon and a real Tier-D target, both gaps reproduced live with verbatim
output:
**[`docs/reference/launching-a-simulation.md`](docs/reference/launching-a-simulation.md)**.

For a fully automated, re-runnable proof that classifies a run's failures
into **ENGINE** (a real CortexSim defect) / **ENVIRONMENT** (the target
couldn't support the step — not a Cortex miss) / **TTP** (the technique ran
for real), run the Tier-D harness before you're in front of a customer:

```bash
export DOCKER_CONTEXT=default
deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  SimCore (FastAPI, port 8888)                                    │
│  ┌──────────┐ ┌─────────────┐ ┌────────────┐ ┌──────────────┐   │
│  │ Scenario │ │ Orchestrator│ │ Tool       │ │ EAL Simulator│   │
│  │ Loader   │ │ (pull/push) │ │ Instantiator│ │ /api/eal/*   │   │
│  └──────────┘ └─────────────┘ └────────────┘ └──────────────┘   │
│       ↓             ↓                ↓               ↓           │
│  scenarios/    Agent Task        sources/       plugin registry  │
│   (YAML)        Queue           (submodules)    + 21 built-ins   │
└──────────────────────────────────────────────────────────────────┘
         ↑ HTTP poll              ↑ native CLI          ↑ HTTP API
┌────────────────┐         ┌──────────────────────┐ ┌─────────────┐
│ cortexsim-agent│         │ signalbench / ackbarx│ │ React UI    │
│ (pull model)   │         │ mocktaxii / xdrtop   │ │ console     │
└────────────────┘         └──────────────────────┘ └─────────────┘
```

**Three execution surfaces:**

- **Pull (agent)** — the Go beacon polls SimCore, executes each step through
  the identity harness, streams output back.
- **Push (bundle)** — SimCore renders a self-contained bash or PowerShell
  bundle; the DC downloads and runs it with no SimCore dependency at runtime.
- **EAL simulator (`/api/eal/*`)** — declarative log/traffic campaigns
  (C2 beacon, DNS tunnel, cloud-audit streaming, OAuth grant abuse, LLM
  egress, browser attacks, agentic supply-chain artifacts…) via 21 plugins.

**Identity harness** — every step runs under a realistic service-account
identity (`www-data`, `postgres`, `svc-account`, `node`, …), wrapped with
`runuser -l` / `sudo -u` / `su`, to build honest process-causality chains in
XSIAM instead of everything running as the beacon's own account.

---

## Detection planes

Counts below are machine-generated — `python3 scripts/generate_ground_truth.py`,
gated in CI against the corpus on disk. Full breakdown (per-type, MITRE
coverage, adapters, assertions):
**[`docs/reference/ground-truth.md`](docs/reference/ground-truth.md)**.

| Plane | Cortex engine | Scenarios |
|---|---|---:|
| CDR | Cortex Cloud / Prisma Cloud Compute | 28 |
| EDR | Cortex XDR Agent | 22 |
| Analytics | XSIAM Correlation Engine (multi-plane stitching) | 23 |
| ITDR | Cortex ITDR | 20 |
| NDR | Network Security / Firewall Analytics | 12 |
| Cloud App | Cortex Cloud App Security | 10 |
| AI_SPM | Cortex AI Security Posture Management | 7 |
| KOI | Agentic endpoint / supply-chain | 8 |
| TIM | Cortex Threat Intel Management | 9 |
| ASM | Cortex Attack Surface Management | 6 |
| AI_ACCESS | Cortex AI Access Security | 6 |
| Browser | Prisma Browser | 6 |
| CSPM | Cortex Cloud Posture Management | 5 |
| AIRS | Cortex AI Runtime Security | 5 |
| Email | XSIAM / NG-SIEM (3rd-party log ingestion, ITDR-pattern) | 5 |
| DLP | Data Security (DSPM · DDR · Endpoint DLP) | 5 |
| **Total** | | **177** |

**177 loadable scenarios · 175 TTP cards · 1,096 step-level expected
detections · 1,777 catalog detection objects** across the `BIOC | XQL |
Analytics | Correlation | IOC | ABIOC` vocabulary, plus the XDM
modeling-rule normalization substrate. `make validate` is green
(346 pass / 0 warn / 0 fail).

---

## EAL Traffic Simulator

A plugin-based subsystem under `core/eal_simulator/` that emits controlled
network/log telemetry to validate NGFW Enhanced Application Logs and
Cortex analytics without hand-rolling raw traffic. 21 plugins today, split
into two families: **signal-injection** (C2 beacon, DNS tunnel, OAuth grant
abuse, LLM-provider egress, browser attacks, agentic supply-chain fetches)
and the **analytics log-streamer** family, which POSTs shape-true
audit/log JSON (AWS/GCP CloudTrail, Azure Activity, Kubernetes audit, M365,
AD/Windows security, NGFW EAL, Okta/Entra sign-in) to an operator-supplied
collector so a customer can validate Analytics/ABIOC detections against
their real ingestion pipeline.

```bash
python -m scripts.eal_simulator.cli list-plugins | jq .
python -m scripts.eal_simulator.cli run path/to/campaign.yml --live
```

Delivery is accounted, not assumed — only a genuine `2xx` from the collector
counts as delivered; `GET /api/eal/campaigns/{id}/preflight` answers "will
this ingest?" before a customer is watching. Emitting from *inside* a
customer network with no SimCore at runtime: `POST
/api/eal/campaigns/{id}/bundle` (stdlib-only `urllib`, no pip install).

---

## AIRS validation stack

For AI Runtime Security POVs, a self-contained canary + attacker pair so the
customer's AIRS layer can be validated with no real LLM, keys, or external
dependency:

```bash
cortex-vulnerable-llm serve --port 8089 --vuln all
cortex-prompt-attacker run --probes scenarios/airs/probes/llm01/ \
  --target-url http://127.0.0.1:8089/owasp/llm01/chat \
  --scorers system_prompt_leak,secret_leak --out /tmp/airs-001.jsonl
```

`sources/cortex-vulnerable-llm/` — Flask app, one blueprint per OWASP LLM
Top 10 class, deterministic regex canary, no real LLM calls ever.
`sources/cortex-prompt-attacker/` — Probe → Mutator → Target → Scorer
pipeline; promptmap-compatible probe YAML (schema only, no GPL import);
JSONL output mirrors garak's `Attempt` shape.

---

## Releases & Packaging

- **Container image:** `ghcr.io/hankthebldr/cortexsim`, tagged `:v0.1.0` and
  `:latest` — **not yet pushed.** `docker pull` today 404s. Build it locally
  instead (`./scripts/dev-up.sh`, or `docker build -f core/Dockerfile -t
  cortexsim:v0.1.0 .` directly).
- **`v0.1.0` is tagged locally, not pushed to the remote** — the operator's
  exact publish commands (including a real blocker in the tag-triggered CI
  workflow, and a working manual `docker buildx` path around it) are in
  **[`docs/release/PUBLISH-v0.1.0.md`](docs/release/PUBLISH-v0.1.0.md)**.
  Changelog: **[`CHANGELOG.md`](CHANGELOG.md)**.
- **GitHub Releases:** https://github.com/hankthebldr/cortex-pov-engine/releases
  — empty until the tag above is pushed.
- **Landing page:** [`docs/site/`](docs/site/) — a GitHub Pages site that
  queries the GitHub Releases API at load time and renders "no release yet"
  honestly until one exists; deploys on push to `main` via
  [`.github/workflows/pages.yml`](.github/workflows/pages.yml).

---

## Repository layout

```
cortex-pov-engine/
├── install.sh              ← full source bootstrap (contributors)
├── scripts/dev-up.sh        ← THE canonical bring-up (Docker only)
├── scripts/dev-up-native.sh ← Docker-free twin
├── docker-compose.yml      ← SimCore container
├── .gitmodules             ← 10 tool submodules (2 may be private)
├── core/                   ← SimCore FastAPI app (Python 3.11)
│   ├── api/                  ← REST routers (scenarios, runs, agents, eal, ...)
│   └── eal_simulator/         ← EAL traffic simulator + plugins
├── agent/                  ← Go pull-model beacon
├── ui/                      ← React 18 + Vite console
├── scenarios/               ← YAML scenario library (UC/TC tagged)
├── sources/                 ← submodules + in-tree AIRS/KOI/Browser tools
├── infra/                   ← Terraform IaC modules (AWS; 11 modules)
├── deploy/tier-d/            ← the provisioned target + ENGINE/ENVIRONMENT/TTP harness
├── docs/reference/           ← ground-truth.md, agent-deployment.md,
│                                launching-a-simulation.md, lab-runbook.md, ...
├── docs/release/              ← v0.1.0 publish hand-off + draft notes
└── tests/                    ← pytest suite
```

---

## Contributing

Work flows `feature/*` → `dev` → `main`. **`main` is never pushed to
directly** — it's what a DC deploys into a customer lab, so it advances only
by a reviewed merge from `dev`. Two rules matter more here than in a typical
repo, because a defect in this codebase doesn't just break a build — it
produces a **false claim about a customer's security coverage** in a
document that customer is shown:

1. **Every guard must be capable of failing.** State how you verified a new
   test goes red without the fix.
2. **Authored is not proven.** `tenant-verified` is `0` until a run executes
   against a live Cortex tenant. No PR, doc, or console surface may report
   authored coverage as proven coverage.

Full model, naming, and both gate checklists: **[`CONTRIBUTING.md`](CONTRIBUTING.md)**.

## Test

```bash
# Fastest/most reliable — matches CI, avoids host Python-version drift:
docker run --rm -v "$(pwd):/repo" -w /repo \
  -e CORTEXSIM_BASE_DIR=/repo -e CORTEXSIM_ENV=development \
  -e CORTEXSIM_SECRET="$(openssl rand -hex 32)" -e PYTHONPATH=/repo/core \
  cortex-pov-engine-simcore sh -c \
  "pip install --no-cache-dir -q pytest pytest-asyncio httpx && pytest tests/ -q"

# UI (needs Node — vitest)
cd ui && npm ci && npm test

# Go agent
cd agent && go test ./...
```

Host `python3` running this suite directly needs **3.11**, not whatever your
system default is — the prod image is the reliable path and what
`.github/workflows/ci.yml`'s `backend` job actually runs.

---

## No authentication, by design

There is no login, no token, no session, and no RBAC anywhere in this app.
CORS is wide open (`allow_origins=["*"]`, `allow_credentials=False` — the
spec-valid form of fully-open, since the two together are rejected by every
browser). This is deliberate: the DC running a POV lab is already full admin
on that jumpbox and that network. **Run this only on a trusted, isolated lab
network you control** — never expose it to the open internet or a shared
corporate segment.

## Cortex connection (opt-in, read-only)

SimCore's job is generating signal **into** the customer's environment; it
**never writes** to Cortex (`CORTEXSIM_XSIAM_ALLOW_WRITE` and
`CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay off by default and no path in this
repo flips them). No Cortex connection is required to run a simulation at
all. When you *do* configure a read-only integration credential
(`POST /api/credentials/integrations`), three opt-in paths become
available: tenant health/metrics, **alert read-back** for auto-validating
seeded results into evidence-backed MTTD
(`POST /api/runs/{id}/reconcile?connector=xsiam`), and Tier-2 XQL
verification (`POST /api/runs/{id}/verify`). Nothing is polled without an
explicit flag, and `POST /api/connectors/{kind}/preflight` tells you whether
the connection actually works — staged, every stage reported — *before* a
POV instead of discovering it mid-run.

---

*CortexSim | Owner: Henry Reed, DC2 GTM NAM Cortex*
