# CortexSim — Detection Simulation Engine

Enterprise detection simulation engine for Palo Alto Networks Cortex
Domain Consultants. It generates controlled, high-fidelity signal into a
customer's Cortex environment (XSIAM / XDR / Cortex Cloud) to validate
detection logic across the full `detection_type` vocabulary —
**`BIOC · XQL · Analytics · Correlation · IOC · ABIOC`** — plus the XDM
modeling-rule normalization substrate and cross-plane stitching.

> **Analogy:** MITRE Caldera's opinionated nephew — not a red-team C2, but a
> *detection quality-assurance engine*. Real binaries, real process causality,
> real telemetry, measured against the same UC/TC the customer is buying.

---

## Honesty contract — read this first

This repository distinguishes three things that are easy to conflate, and
refuses to report them as one number:

| Term | Meaning | Count |
|---|---|---|
| **Authored** | A scenario or assertion exists and loads clean | 177 scenarios · 22 assertions |
| **Executed** | It has run end-to-end through a beacon or push bundle | partial (see [`docs/reference/`](docs/reference/README.md)) |
| **Tenant-verified** | It has run against a **live Cortex tenant** and the result was read back | **0** |

***tenant-verified is 0.*** Every green in the test suite, in the console, and in
this file comes from an injected transport or `httpx.MockTransport`. **Authored is
not proven.** The console's *Readiness* surface (`#/readiness`) states this verbatim
and renders the connector ladder as four never-collapsed rungs —
**AUTHORED · CONFIGURED · REACHABLE · VERIFIED**.

---

## Current state — counted, not estimated

All figures below were re-measured on **2026-08-30** by running the real scenario
loader (`core/engine/scenario_loader.py`) and the real EAL plugin registry against
this tree. Where any prose in `docs/` disagrees, **re-run the count and the count wins.**

| Surface | Count |
|---|---|
| Loadable scenarios | **177** (0 rejected, 0 dangling refs) |
| Detection planes | **16**, all `status: active` |
| Scenario steps · step-detections | **667 · 1116** |
| TTP detection cards | **175** (`detection_scanner/ttps/*.json`) |
| Assertion artifacts (POS/PLT/AUT) | **22** (15 · 4 · 3) |
| EAL simulator plugins | **21** |
| Tool-adapter packs | **91** (8 payload-shelf-backed · 48 exemption-declared) |
| AWS IaC modules | **11** |
| HTTP routes at boot | **133** |
| MITRE ATT&CK tactics covered | **14** |

Reproduce the scenario census yourself:

```bash
make validate && make check-refs && make coverage-strict
```

### Detection-type distribution (scenario-level declarations)

| Type | Scenarios | |
|---|---:|---|
| XQL | 160 | `████████████████████` |
| Correlation | 115 | `██████████████` |
| BIOC | 112 | `█████████████` |
| ABIOC | 66 | `████████` |
| IOC | 40 | `█████` |
| Analytics | 30 | `████` |

ABIOC = PANW-authored, auto-tuned behavioral ML with a causality chain. XDM modeling
rules are a normalization **substrate** (`detections.modeling_rules[]`) — surfaced and
exported, counted informationally, *not* a `detection_type`.

---

## Quick start

### Local dev — Docker

```bash
cp .env.example .env        # set CORTEXSIM_MASTER_KEY etc.
./scripts/dev-up.sh         # builds the image + brings up SimCore on :8888
```

`scripts/dev-up.sh` is the canonical easy-deploy entry point. `.env.example`
documents every required and optional env var, including the master-key guard the
compose stack enforces.

### Local dev — no Docker

The Docker-free twin. Generates `.env`, creates the venv, builds the React UI into
`core/static/`, launches SimCore, and polls `/api/health` until it reports ok:

```bash
./scripts/dev-up-native.sh              # full bring-up
./scripts/dev-up-native.sh --skip-ui    # reuse an existing core/static/ build
./scripts/dev-up-native.sh --stop       # stop the running instance
```

By hand — note the UI build step. SimCore serves the SPA from `core/static/`, so
without it `/` returns 404 while the API still answers:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r core/requirements.txt
(cd ui && npm ci && npm run build) && rm -rf core/static && cp -r ui/dist core/static
cd core && CORTEXSIM_ENV=development CORTEXSIM_BASE_DIR=$(pwd)/.. \
  uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

### Full bootstrap — Linux jumpbox

```bash
git clone https://github.com/hankthebldr/cortex-pov-engine.git
cd cortex-pov-engine
./install.sh    # system deps, submodules, Go agent, Rust tools, React build, compose up
```

> **Build from source.** CortexSim is not currently distributed as a tagged release or
> a published container image — there is no `ghcr.io` package and no GitHub Release to
> download. `.github/workflows/release.yml` implements that pipeline and fires on a
> `v*.*.*` tag, but **no tag has ever been cut**, so every artifact you run comes from
> this tree. Clone and build.

---

## Architecture

Three tiers, plus a signal-injection subsystem that sits beside them.

```
┌───────────────────────────────────────────────────────────────────────┐
│  SimCore — FastAPI, port 8888, 133 routes                             │
│  ┌──────────┐ ┌─────────────┐ ┌────────────┐ ┌──────────────┐ │
│  │ Scenario │ │ Orchestrator│ │ Tool       │ │ EAL Simulator│ │
│  │ Loader   │ │ (pull/push) │ │ Instantiator│ │ /api/eal/*   │ │
│  └──────────┘ └─────────────┘ └────────────┘ └──────────────┘ │
│  ┌──────────┐ ┌─────────────┐ ┌────────────┐ ┌──────────────┐ │
│  │ UC/TC    │ │ Assertions  │ │ Payload    │ │ Connectors   │ │
│  │ Registry │ │ (POS/PLT/AUT)│ │ Shelf      │ │ (read-back)  │ │
│  └──────────┘ └─────────────┘ └────────────┘ └──────────────┘ │
└───────────────────────────────────────────────────────────────────────┘
      ↑ HTTP poll             ↑ native CLI          ↑ HTTP API
┌────────────────┐    ┌──────────────────────┐  ┌─────────────┐
│ cortexsim-agent│    │ signalbench / ackbarx│  │ React UI    │
│ Go, pull model │    │ mocktaxii / xdrtop   │  │ (SPA at /)  │
└────────────────┘    └──────────────────────┘  └─────────────┘
```

1. **SimCore** (`core/`) — FastAPI orchestrator. Loads scenarios from YAML, manages
   tool lifecycle, dispatches tasks to agents or generates push bundles.
2. **cortexsim-agent** (`agent/`) — Go pull-model beacon, stdlib only. Polls SimCore
   for tasks, executes via the identity harness, streams output back.
3. **React UI** (`ui/`) — SPA served by SimCore's static mount at `/`.

### Execution modes

- **Pull** — agent polls SimCore, receives a task, resolves identity agent-side,
  executes, POSTs per-step output. Live progress over SSE
  (`GET /api/runs/{id}/events`); operators can abort mid-run. The task queue is
  **durable** — a write-through cache over the `queued_tasks` table, rehydrated on
  startup, so a SimCore restart restores undelivered tasks.
- **Push** — SimCore generates a self-contained bash bundle or PowerShell `.ps1` or
  K8s YAML. Executes on a clean Ubuntu 22.04 (or Windows PS 5.1) host with **no
  SimCore dependency at runtime**.
- **EAL simulator** (`/api/eal/*`) — declarative traffic and log-emission campaigns
  for signal shapes the identity harness cannot produce.

### Identity harness

Every TTP step runs via a service account (`www-data`, `postgres`, `node`, `nobody`)
to create realistic process-causality chains in XSIAM. Push and pull resolve a step's
identity from one shared spec (`spec/identity_harness.json`); a Go test guards drift.

**Windows never fakes identity.** Windows has no credential-free unattended
impersonation, so `identity.ResolveFor` collapses every non-direct identity to
`direct` and writes an explicit `IDENTITY NOT HONOURED` degradation into the run
record. A Windows beacon registers `["shell","powershell"]` and does **not** claim
`identity-harness`.

### Causality contract

Optional, additive, back-compat. Declaring `cgo_anchor` (scenario-level) plus
per-step `causality` collapses the causality graph's synthetic `cortexsim-agent` star
into one connected CGO→process→process spine. 100% of process-lineage-spine scenarios
yield a connected `proc:`-sourced chain.

---

## Detection planes

| Plane | Cortex engine | Scenarios |
|---|---|---:|
| **CDR** | Cortex Cloud / Prisma Cloud Compute | 28 |
| **ANALYTICS** | XSIAM Correlation Engine (multi-plane stitching) | 23 |
| **EDR** | Cortex XDR Agent | 22 |
| **ITDR** | Cortex ITDR | 20 |
| **NDR** | Network Security / Firewall Analytics | 12 |
| **CLOUD_APP** | Cortex Cloud App Security | 10 |
| **TIM** | Cortex Threat Intel Management | 9 |
| **KOI** | Agentic endpoint / supply chain | 8 |
| **AI_SPM** | Cortex AI Security Posture Management | 7 |
| **AI_ACCESS** | Cortex AI Access Security | 6 |
| **ASM** | Cortex Attack Surface Management | 6 |
| **BROWSER** | Prisma Browser | 6 |
| **AIRS** | Cortex AI Runtime Security | 5 |
| **CSPM** | Cortex Cloud Posture Management | 5 |
| **EMAIL** | XSIAM / NG-SIEM (Proofpoint TAP + M365 ingestion) | 5 |
| **DLP** | Enterprise DLP · DSPM · DDR · Endpoint DLP | 5 |
| | **Total** | **177** |

EMAIL is third-party log ingestion + correlation, **not** a first-party PANW product
surface. Full inventory: [`docs/reference/scenario-catalog.md`](docs/reference/scenario-catalog.md).

---

## UC/TC alignment — the FY27 v2.2 index

The FY27 Use-Case / Test-Case master index is the sales-motion source of truth:
**49 UC · 203 UCS · 266 TC · 140 POV-SC payloads · 38 SKU**, snapshotted at
`docs/uc_tc_mapping/_v2.2-source/` and loaded at boot into frozen dataclasses.

**Scenario refs are a validated foreign key into that index, not free text.** The
loader enforces codes **S-10** through **S-16**; ERRORs reject when
`CORTEXSIM_STRICT_REFS` is true, and **it defaults true**. `make check-refs` walks the
real corpus through the real loader under strict mode — that gate is what makes the
enforcement meaningful.

### Coverage by validation class — read this before quoting a number

The index is not one population. A flat percentage hides which mechanism owes the work.

| class | total | by scenario | by assertion | union | open | index-scoreable | **tenant-verified** |
|---|---:|---:|---:|---:|---:|---:|---:|
| DET | 102 | 63 | 0 | 63 | 39 | 49 | **0** |
| HNT | 5 | 4 | 0 | 4 | 1 | 1 | **0** |
| POS | 110 | 18 | 11 | 19 | 91 | 19 | **0** |
| PLT | 43 | 1 | 4 | 5 | 38 | 16 | **0** |
| AUT | 6 | 0 | 3 | 3 | 3 | 6 | **0** |
| **all** | **266** | **86** | **18** | **94** | **172** | **91** | **0** |

*index-scoreable* is how many rows carry a measurable threshold at all. Nobody should
report the union as coverage.

---

## Assertions — the second proof mechanism

140 of the index's open rows are **not detections** and cannot be closed by authoring
more scenario YAML. POS asks whether a posture state *holds*; PLT whether a capability
is *present*; AUT whether an outcome *occurs inside a budget*.

`core/engine/assertions.py` + `assertions/{pos,plt,aut}/*.yml` are the artifact type
for those, mirroring `Scenario`/`Run`/`Result` with `Assertion`/`AssertionRun`/
`AssertionCheck` so **`verifier.score_run` scores both with no parallel scorer.**

Five read-only XQL probes ship: `xql_rows`, `xql_distinct`, `xql_scalar`, `xql_ratio`
(refuses to call 0-of-0 100%), and `xql_latency` (measures in the *platform's* clock,
never wall-clock). Thresholds live in the artifact, never in the query —
`| filter sla <= 300` returns zero rows for a tenant that took 412 s, indistinguishable
from one that never responded.

**The guard: an assertion that cannot fail does not load, proven by execution at load
time.** `A-17` builds measurements across the probe's declared domain plus the
neighbourhood of the authored threshold, pushes each through the *real* evaluator, and
rejects unless the check produces **both** a `fail` and a `pass`. `A-18` requires an
authored `negative_control` and proves it really evaluates `fail`.

**No tenant is never green.** No integration / unreachable / 401 / 429 / bad dataset /
`PRECURSOR_MISSING` / `POPULATION_EMPTY` / dry run all resolve **`pending`**. Only
`NOT_ENTITLED` resolves `not_applicable`.

Contract + authoring guide: [`docs/uc_tc_mapping/assertions.md`](docs/uc_tc_mapping/assertions.md).

---

## The measurement loop — optional, read-only

**The Cortex connection is OPT-IN and READ-ONLY.** SimCore's job is to generate signal
INTO the environment; it **never writes** to Cortex —
`CORTEXSIM_XSIAM_ALLOW_WRITE` and `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off.
Nothing is polled without a flag.

When a credential is configured it reads out on three opt-in paths:

1. **Tenant health/metrics** — `/healthcheck`, XQL over `metrics_*`.
2. **Alert read-back** (`core/connectors/`) — pulls observed alerts and a pure
   `matcher` auto-validates seeded `Result` rows on technique / detection-id / name
   within a time window → real, evidence-backed MTTD, no manual checkbox.
3. **Tier-2 verification XQL** — `POST /api/runs/{id}/verify`, its own flag
   (`CORTEXSIM_AUTO_VERIFY`) and its own credential kind (`xsiam_tenant`, not
   reconcile's `xsiam`). Configuring reconcile does not authorise XQL.

**Preflight before the POV, not during it.** `POST /api/connectors/{kind}/preflight`
answers "is my connection working?" *before* the customer is watching — staged
(config → dns_tls → auth → scope → datasets → clock), every stage reported even when
an earlier one degraded, and every response carries `queries_issued` so a preflight run
against an injected transport cannot be quoted as tenant proof.

**Scoring runs in two tiers.** Tier 1 is offline, makes no outbound calls, and is
deliberately **not** flag-gated — gating it would gate honesty, not risk. Tier 2 is
opt-in, quota-disciplined (max attempts, exponential backoff, per-sweep query cap,
circuit breaker), and a spent budget degrades to `pending`, never `fail`.

> **Quantified limit:** only **59 of 177** scenarios declare an MTTD-shaped primary KPI,
> the only KPI the engine measures natively. The rest declare thresholds nothing
> produces a `measured_value` for, so `score_run` returns `pending` permanently. Wiring
> the caller did not create these — it made them visible.

---

## EAL Traffic Simulator

`core/eal_simulator/` hosts **21 plugins** in two families.

**Signal injection** — network / identity / SaaS / AI / browser / email shapes the
identity harness cannot produce:

| Plugin | Purpose |
|---|---|
| `c2_http_beacon` | Periodic HTTP/S beacon — unusual UA, DGA URI |
| `dns_tunnel_exfil` | DNS-tunnelling exfiltration, high-entropy labels |
| `bulk_https_exfil` | Large outbound transfer |
| `stratum_tcp_connect` | Cryptojacking JSON-RPC |
| `smb_rpc_sweep` | Lateral SMB / RPC sweep |
| `ftp_egress` · `ssh_egress` | Cleartext FTP STOR · SSH outbound + KEXINIT |
| `airs_prompt_attack` | AIRS validation runner |
| `llm_provider_egress` | Outbound to OpenAI / Gemini / Anthropic with planted DLP markers |
| `agentic_egress` | KOI agentic supply-chain artifact fetch |
| `browser_attack_runner` | Prisma Browser drive via Playwright |
| `oauth_grant_emulator` | OAuth 2.0 authorize with planted risky scopes |
| `idp_signin_emulator` | Synthetic IdP sign-in audit events |
| `email_emitter` | Synthetic Proofpoint TAP / M365 events |

**Analytics log-streamers** — POST shape-true audit JSON to an operator-supplied
collector (HTTP log collector / XSIAM Broker VM) so a customer can validate their
Analytics / ABIOC detections fire on that data source:
`cloud_audit_emitter`, `cloud_storage_compute_emitter`, `azure_audit_emitter`,
`k8s_audit_emitter`, `m365_activity_emitter`, `ad_windows_emitter`, `ngfw_eal_emitter`.

**Delivery is accounted, not assumed.** Only **2xx** counts as delivered.
`events_emitted` / `bytes_sent` report what the collector **accepted**; a 401 from a
Broker VM, a 404 on a mistyped path, and a 302 to a captive portal are each a distinct
code in a 12-code taxonomy with a remediation line. Runs expose a campaign-level
`delivery_verdict`. `GET /api/eal/campaigns/{id}/collectors` (+ `/preflight`) settles
"will this ingest?" before the customer is watching.

Catalog: [`docs/reference/eal-plugin-catalog.md`](docs/reference/eal-plugin-catalog.md).

---

## Tool adapters and the payload shelf

**91 adapter packs** across a 5-tier model (1 in-tree · 2 submodule · 3 IaC-provisioned
· 4 runtime-fetched · 5 external-only). One YAML per tool under `tools/packs/<tool>.yml`
tells the engine where a tool lives, how to install and invoke it, its dual-use safety
class, and which Cortex plane its signal lands on. Scenarios reference adapters by id
(`external_tools[].adapter_ref: TOOL-NMAP`) instead of hand-rolling CLI.

### Why the payload shelf exists

Every tier-4 pack installs its tool **from the public internet, on the target host, at
dispatch**. Customers who buy Cortex run default-deny egress — that is the first thing
their network blocks. A step whose tool never arrived **runs anyway**, produces no
detection, and the absent detection reads in a POV report as *"Cortex missed it"*: a
manufactured false negative on the customer's own stack, in a document a DC shows a
customer.

A tier-4 pack may declare one staged artifact (`install.artifact`), validated by
seventeen `TA-01..TA-17` codes. One resolver walks
`scenario → adapter_ref → pack → artifact → shelf` and **refuses at compose time** with
`PAYLOAD_NOT_STAGED` or `PAYLOAD_PIN_MISMATCH` (409).

**The integrity model:** the digest is recomputed from the shelf bytes on the DC's own
SimCore at compose time and baked into whatever the consumer carries. The consumer
verifies against a value it **carried in**, never one it fetched from the server it is
trusting.

The **destination path** is overridable, which makes a **rename negative control**
expressible: stage `linpeas` as `/tmp/.cache/sysinfo.sh` and the filename-keyed BIOCs
correctly go dark while the behavioural ones must still fire. Every rename emits
`FILENAME_KEYED_DETECTIONS_SUPPRESSED` stating the inverted reading.

State: `tier4 staged 8 · exempt 48 · undeclared 0`. Every tier-4 pack declares
**exactly one** of `install.artifact` or `install.artifact_exempt {reason_code, reason}`
— `TA-13` *rejects* a pack declaring neither. Details:
[`docs/reference/payload-shelf.md`](docs/reference/payload-shelf.md) and
[`docs/tool-adapters.md`](docs/tool-adapters.md).

---

## Agent onboarding

The front door is the **enrollment-token** flow. Mint a TTL / max-uses / revocable
token, run one line on the jumpbox, and SimCore *assigns* the agent id — no more
self-asserted `--id`:

```bash
curl -fsSL '<server>/api/agents/install?os=linux' | CORTEXSIM_TOKEN='cxs_…' bash
```

The console emits the token as an env var so it stays out of shell history and proxy
logs. The script needs **no Go toolchain and no public-internet egress on the target**
— it downloads the prebuilt beacon from *this* SimCore and sha256-verifies it.

`?mode=service` (default) installs a systemd unit (Linux) or launchd job (macOS) so the
beacon survives the SSH session and a reboot, degrading honestly to `setsid`+`nohup`
with a `DEGRADED_NO_SUPERVISOR` code when no supervisor exists. `?uninstall=1` returns
an idempotent removal script. Every stage exits with a stable code that is both printed
and POSTed to `/api/agents/install/telemetry`, readable at
`GET /api/agents/install/attempts` — so "ran the one-liner, nothing appeared" has an
answer.

**Beacon build matrix — five targets:** `linux/{amd64,arm64}`,
`darwin/{amd64,arm64}`, `windows/amd64` (`.exe`).

```bash
make agent-dist    # cross-compile the matrix -> agent-dist/ + SHA256SUMS
```

`docker build` bakes the matrix into the image; a **host-run dev SimCore needs
`make agent-dist` once** or `/api/agents/binary` returns an actionable 404.

> **Windows caveat.** `GET /api/agents/binary?os=windows&arch=amd64` serves a verified
> `PE32+`, and `?os=windows` returns a PowerShell installer with no preflight refusal.
> **Still unproven: no Windows host has executed the beacon or the installer.** Serving
> a correct PE is not the same as `sc.exe` service creation working on Server 2022.

---

## AIRS validation stack

A self-contained canary + attacker pair, so a customer's AIRS layer can be validated
without a real LLM, real keys, or any external dependency.

```
┌──────────────────────┐  HTTP  ┌──────────────────────┐
│ cortex-prompt-       │ ─────> │ cortex-vulnerable-   │
│ attacker             │        │ llm                  │
│ probes/mutators/     │        │ Flask + canary       │
│ scorers              │ <───── │ OWASP LLM01..LLM10   │
└──────────────────────┘ JSONL  └──────────────────────┘
```

**Canary** — deterministic regex-driven Flask app, one blueprint per OWASP LLM Top 10
class. **No real LLM calls. No keys. Ever.**

**Attacker** — Probe → Mutator → Target → Scorer pipeline. Probes are
promptmap-compatible YAML (schema mirrored only, no GPL imports); the mutator chain is
PyRIT-shape; JSONL output mirrors NVIDIA garak's `Attempt` field naming.

```bash
cortex-vulnerable-llm serve --port 8089 --vuln all

cortex-prompt-attacker run \
    --probes scenarios/airs/probes/llm01/ \
    --target-url http://127.0.0.1:8089/owasp/llm01/chat \
    --scorers system_prompt_leak,secret_leak \
    --out /tmp/airs-001.jsonl
```

---

## IaC topology generator

Produces Terraform bundles Torque can consume as blueprints. AWS is feature-complete
with **11 modules** covering every active plane: `base`, `edr`, `cdr`,
`content-library`, `itdr`, `ndr`, `cspm`, `asm`, `tim`, `telemetry-replay`, `ai-spm`.

```
POST /api/infra/generate              # generate a bundle
GET  /api/infra/modules[?provider=aws]
GET  /api/infra/bundles/{id}/download
```

Module metadata lives in each module's `README.md` frontmatter, not in Python — adding
a module is filesystem-only. GCP (Phase C) and Azure (Phase D) ports are pending; an
`onprem` provider (Ansible + Docker Compose) is design-only.

Catalog: [`docs/reference/iac-module-catalog.md`](docs/reference/iac-module-catalog.md).

---

## Repository layout

```
cortex-pov-engine/
├── install.sh              ← jumpbox bootstrap
├── docker-compose.yml      ← SimCore container
├── Makefile                ← every gate: validate, check-refs, coverage, ci
├── core/                   ← SimCore FastAPI app (Python 3.11)
│   ├── api/                  REST routers — 133 routes
│   ├── engine/               scenario_loader · orchestrator · push_generator
│   │                         uctc_registry · verifier · assertions · payload_shelf
│   ├── connectors/           optional read-back measurement loop
│   ├── integrations/xsiam/   ~116 read-only operation packs + Tier-2 XQL
│   ├── eal_simulator/        EAL traffic simulator + 21 plugins
│   └── planes/               declarative PlaneDescriptor registry (16 planes)
├── agent/                  ← Go pull-model beacon (stdlib only, 5-target matrix)
├── ui/                     ← React 18 + Vite console
├── scenarios/              ← 177 scenario YAMLs, per plane
├── assertions/{pos,plt,aut}← 22 POS/PLT/AUT artifacts
├── detection_scanner/ttps/ ← 175 TTP detection cards
├── tools/packs/            ← 91 tool-adapter packs
├── payloads/               ← digest-pinned payload shelf (generated manifest)
├── infra/modules/aws/      ← 11 Terraform modules
├── sources/                ← 10 submodules + 4 in-tree tools
├── deploy/                 ← Helm charts + Tier-C isolated-exec harness
├── spec/                   ← identity_harness.json (shared push/pull spec)
├── tests/                  ← pytest suite
└── docs/
    ├── reference/            ← counted ground truth — the authority
    ├── uc_tc_mapping/        ← FY27 v2.2 index + assertions contract
    ├── site/                 ← GitHub Pages landing page
    └── wiki/                 ← GitHub wiki source
```

---

## CI and quality gates

`.github/workflows/ci.yml` runs a **7-job matrix**; `.github/workflows/test.yml` adds a
second layer.

| Job | What it proves |
|---|---|
| `backend` | pytest inside the prod image |
| `agent` | Go `build` + `vet` + `test -race`, **plus cross-compile for linux / darwin / windows** |
| `ui` | vitest + `vite build` |
| `detection` | corpus validator **346 pass / 0 warn / 0 fail** + deterministic export regeneration (`sha256sum -c`) |
| `refs` | `make check-refs` — all 177 scenarios through the real loader under `CORTEXSIM_STRICT_REFS=true` |
| `adapters` | tier-2 source trees must exist on disk; de-hand-rolling gate (a scenario naming a tool that HAS an adapter pack must wire it) |
| `e2e-isolated` | Tier-C isolated-execution assertion suite |

The Windows cross-compile arm is load-bearing: the beacon silently could not compile
for Windows while 71 scenarios declared `platforms: [windows]`.

```bash
make -n ci    # enumerate the local equivalents
```

---

## Documentation map

| Doc | What it is |
|---|---|
| [`docs/reference/README.md`](docs/reference/README.md) | **Counted ground truth.** When a doc and the code disagree, re-run the count. |
| [`docs/reference/scenario-catalog.md`](docs/reference/scenario-catalog.md) | Every scenario, enumerated |
| [`docs/reference/adapter-catalog.md`](docs/reference/adapter-catalog.md) | Every adapter pack + tier-4 exemption triage |
| [`docs/reference/payload-shelf.md`](docs/reference/payload-shelf.md) | Shelf design, integrity model, open items by owner |
| [`docs/reference/api-and-agent-surface.md`](docs/reference/api-and-agent-surface.md) | Full HTTP + agent surface incl. installer exit codes |
| [`docs/uc_tc_mapping/README.md`](docs/uc_tc_mapping/README.md) | FY27 v2.2 index, S-10..S-16 enforcement |
| [`docs/uc_tc_mapping/assertions.md`](docs/uc_tc_mapping/assertions.md) | POS/PLT/AUT contract + authoring guide |
| [`docs/tool-adapters.md`](docs/tool-adapters.md) | Adapter framework, shipped vs pending |
| [`docs/operator-runbook.md`](docs/operator-runbook.md) | DC playbook for a live engagement |
| [`CLAUDE.md`](CLAUDE.md) | Working agreement for AI agents in this repo |
| Wiki | https://github.com/hankthebldr/cortex-pov-engine/wiki |
| Pages | https://hankthebldr.github.io/cortex-pov-engine/ |

---

## Contributing

Work flows `feature/*` -> `dev` -> `main`. **`main` is never pushed to directly** —
it is what a Domain Consultant deploys into a customer lab, so it advances only by
a reviewed merge from `dev`.

```
  feature/*  fix/*  docs/*  chore/*      one task each, cut from dev
        |
        |  PR  ==>  Gate A: 8 CI jobs + author's evidence + review
        v
      dev                                 integration trunk, always green
        |
        |  PR  ==>  Gate B: release readiness (image parity, counts, CHANGELOG)
        v
      main                                releasable, tagged
```

Two things make this process different from a normal repo, and both come from what
CortexSim is for: a defect here does not produce a broken build, it produces a
**false claim about a customer's security coverage** in a document that customer
is shown.

1. **Every guard must be capable of failing.** PRs state how the author verified
   the new test goes red without the fix. A test written after the fix and never
   observed failing is an assumption with good syntax.
2. **Authored is not proven.** `tenant-verified` is 0 until a run executes against
   a live Cortex tenant. No PR, doc, or console surface may report authored
   coverage as proven coverage.

Full model, naming, commit conventions, and both gate checklists:
**[`CONTRIBUTING.md`](CONTRIBUTING.md)**.

## Testing

```bash
make test                 # full suite
make validate             # detection-corpus validator
make check-refs           # UC/TC foreign-key integrity, strict mode
make coverage-strict      # MITRE / plane / detection-type floors — exits non-zero below floor
make test-agent-cross     # beacon cross-compile gate

.venv/bin/pytest tests/ -v
pytest sources/cortex-vulnerable-llm/tests/
pytest sources/cortex-prompt-attacker/tests/
```

See [`TESTING.md`](TESTING.md) for the tier model.

---

## Engineering invariants

These are the rules the gates in [`CONTRIBUTING.md`](CONTRIBUTING.md) enforce.

- **Scenarios are YAML source-of-truth.** The DB stores run history only.
- **Schema validation is strict.** Invalid files are rejected at startup, not tolerated.
- **No wrapper code around external tools.** The Tool Instantiation Layer calls real
  binaries with their native CLI flags.
- **Push bundles must be self-contained** — clean Ubuntu 22.04, no SimCore at runtime.
- **All API responses are structured JSON**, including errors:
  `{"error": "...", "code": "...", "detail": "..."}`.
- **Do not edit source files under `sources/`** — they are submodules.
- **Never commit `.terraform/`**, `agent-dist/`, or `rust-dist/` — build artifacts.

---

*CortexSim · Palo Alto Networks Cortex Domain Consulting · Owner: Henry Reed*
