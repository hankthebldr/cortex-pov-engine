# Lab Runbook — Standing Up CortexSim

> **Audience:** Domain Consultants who know Cortex (XDR/XSIAM) cold and are new
> to CortexSim specifically. Every command below was run against a live local
> stack while writing this doc (repo `main` @ `d226b46`, 2026-08-31) — none of
> it is copied from memory.
>
> **Scope:** a local lab bring-up only. Nothing here points at a customer
> tenant or any host that isn't this machine. Where the doc says "Cortex" it
> means the concept you're validating detections against, not a live
> connection — see **Honest limits**, first, before you say anything to a
> customer.

---

## Honest limits — read this before you're in front of anyone

CortexSim's whole job is generating signal that a customer's Cortex tenant is
supposed to catch. Its worst failure mode is a step that looks like it ran and
didn't — an absent detection then reads as "Cortex missed it" in a report you
hand the customer, when nothing was ever executed for Cortex to miss. Four
limits below exist for exactly that reason.

### 1. Tenant-verified is 0

**Nothing in this repo has ever executed against a live Cortex tenant.**
Every green test, every "PASS" in the console, every number in this repo's
docs comes from an injected transport or a local target container — never a
real XSIAM/XDR tenant. **Authored is not proven.** The console's Readiness
surface (`#/readiness`, under *Manage*) states this verbatim and renders the
connector ladder as four never-collapsed rungs — **AUTHORED · CONFIGURED ·
REACHABLE · VERIFIED** — each with what it proves and what it doesn't. When
you connect a real tenant, `POST /api/connectors/{kind}/preflight` is the
first thing to run — it separates unreachable / unauthorized /
authorized-but-not-entitled / working *before* the POV, not mid-run.

### 2. A bare Ubuntu target cannot run this corpus

The identity harness runs every TTP step as a service account (`www-data`,
`postgres`, `node`, …) to build realistic process-causality chains. A stock
`ubuntu:22.04` ships `www-data` with `/usr/sbin/nologin` and no `/var/www` —
`runuser -l www-data` dies with *"This account is currently not available"*
in about 7ms, before the technique does anything. The run then reads
**"failed"** having executed nothing. That is indistinguishable from a real
miss unless you know to look for it.

`deploy/tier-d/Dockerfile.target` is the reference for what a target actually
needs: a login shell + home directory for every identity the corpus uses
(measured, not guessed: `www-data` 103 steps · `svc-account` 80 ·
`svc-backup` 26 · `node` 11 · `user` 7 · `runner` 6 · `app` 6 ·
`vertex-agent` 5 · `developer` 1 · `nobody` 1), plus the toolchain the Linux
steps actually invoke (`binutils`, `procps`, `iproute2`, `curl`). Provision a
real lab target the same way, or use that Dockerfile directly (the Tier-D
harness below does).

### 3. A meaningful slice of steps are echo-only while declaring `expected_detections`

> **The generated manifest does this counting for you.** `make lab-ready`
> writes `docs/reference/lab-readiness.md`, which tiers every scenario
> GREEN/YELLOW/RED and lists the 6 signal-free (RED) scenarios by id. Prefer it
> over the ad-hoc one-liner below — the classifier is quote/comment/probe-aware,
> so it does not mis-score a `&&` inside an `echo` string (which the one-liner
> below does) as a real command.

Of the corpus's 654 total steps, at minimum **100 of them are pure
`echo`/`printf` statements** — placeholders that declare `expected_detections`
without producing the underlying signal a sensor could actually catch — and
the true count is likely higher once you also account for steps that mix a
real command with an `|| echo` fallback the technique never legitimately
reaches. They're staged for content authoring, not yet load-bearing TTPs.
Before you build a POV plan around a scenario, open its YAML under
`scenarios/{plane}/` and read the `command:` lines — don't assume
`expected_detections` means real signal will land. Reproduce (or refine) the
count yourself:

```bash
python3 -c '
import glob, yaml, re
total = echo_only = 0
for f in glob.glob("scenarios/**/*.yml", recursive=True):
    if f.endswith("_schema.yml"):
        continue
    for d in yaml.safe_load_all(open(f)):
        if not isinstance(d, dict):
            continue
        for s in d.get("steps") or []:
            if not isinstance(s, dict):
                continue
            total += 1
            if not s.get("expected_detections"):
                continue
            clauses = [c.strip() for c in re.split(r"&&|;|\n", s.get("command") or "") if c.strip()]
            if clauses and all(re.match(r"^(echo|printf)\b", c) for c in clauses):
                echo_only += 1
print(f"{echo_only} of {total} steps are pure echo/printf with expected_detections declared")'
```

### 4. A missing interpreter now refuses rather than masking

A step can declare `requires_interpreters: [python]` (or similar). If the
beacon can't find that interpreter on the target — and hasn't been
authorized to install one — it refuses the step outright: the step's own
command **never runs**, so a masked `|| echo done` never gets the chance to
report false success. The step reports exit code 127 with a
`RUNTIME_DEPENDENCY_MISSING` marker, and the Tier-D harness classifies that as
an honest **ENVIRONMENT** gap, never a detection miss (see below).

To let the beacon attempt a package-manager install instead of refusing, two
keys must **both** be set — a single flag is deliberately not enough:

- the deployment env var `CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL=true`
- the launch request field `"allow_runtime_install": true` on
  `POST /api/runs`

Neither one alone installs anything on a target.

---

## Prerequisites

- Docker + Docker Compose v2 (`docker compose version`) on this machine.
- `curl`, `python3` (3.11+) on the machine you're driving the lab from.
- Ports free: `8888` (SimCore).
- Nothing else. `scripts/dev-up.sh` builds the image itself — you do not need
  a Python venv, Node, or Go toolchain on the host to bring the stack up.

## Bring-up

```bash
cd cortex-pov-engine
./scripts/dev-up.sh
```

What it does, and why it's safe to re-run:

1. If `.env` doesn't exist, generates one from `.env.example` with a fresh
   `CORTEXSIM_SECRET` and `CORTEXSIM_ENV=development`. **An existing `.env` is
   never touched** — your secret survives every re-run.
2. `docker compose up -d --build`. Verified live: with nothing changed, this
   is fully cache-hit and idempotent — running it against an already-up stack
   left the container's ID and start time untouched.
3. Polls `http://localhost:8888/api/health` until `{"status":"ok"}` (or
   `"degraded"` — see below), ~90s timeout, then prints the URL.

```
[dev-up] Waiting for http://localhost:8888/api/health ...
[dev-up] SimCore is healthy.
  ✓ CortexSim is up:  http://localhost:8888
```

`CORTEXSIM_ENV=development` (the default `.env` this script writes) boots even
with a weak secret. Set `CORTEXSIM_ENV=production` for anything beyond a
throwaway lab — that mode refuses to boot on a short/low-entropy
`CORTEXSIM_SECRET` rather than silently encrypting stored credentials with a
weak key.

## Reading `/api/health`

```bash
curl -s http://localhost:8888/api/health | python3 -m json.tool
```

This is **the one diagnostic surface** — always HTTP 200 so you can read the
detail even when degraded, and it obeys one hard rule: **it never reports
green for something it didn't check.** A `count: 0` on a catalog that should
never be empty is `degraded`, not `ok`.

A real response, captured from this stack mid-lab (trimmed):

```json
{
  "status": "degraded",
  "components": {
    "scenario_catalog": {"status": "ok", "code": "OK", "count": 170},
    "adapter_catalog":  {"status": "ok", "code": "OK", "count": 91},
    "agent_binaries":   {"status": "ok", "code": "OK",
      "available": ["darwin/amd64","darwin/arm64","linux/amd64",
                    "linux/arm64","windows/amd64"], "missing": []},
    "agents": {
      "status": "degraded", "code": "NO_AGENT_ONLINE",
      "detail": "17 agent(s) are enrolled but none has checked in within 30s
                  (2 stale, 15 offline). A pull-mode launch will queue a task
                  nothing collects. Fix: on the target check `systemctl status
                  cortexsim-agent` (or launchctl), and confirm it can reach
                  this SimCore's /api/agents/{id}/tasks.",
      "registered": 17, "online": 0, "stale": 2, "offline": 15
    },
    "task_queue": {
      "status": "degraded", "code": "TASKS_QUEUED_FOR_UNAVAILABLE_AGENT",
      "detail": "2 task(s) are queued for agent(s) that are not online:
                  jumpbox-01 (2). The run's own status stays 'running' with
                  no output until the beacon polls — it does NOT read
                  'pending' — and it is NOT lost (the queue is durable and
                  survives a SimCore restart)."
    }
  },
  "degraded_components": ["agents", "task_queue"],
  "not_checked": [
    {"what": "xsiam_tenant_reachability", "checked_by": "POST /api/xsiam/tenants/{name}/test"},
    {"what": "eal_collector_reachability", "checked_by": "GET /api/eal/campaigns/{id}/preflight"},
    {"what": "agent_target_reachability", "checked_by": "GET /api/agents (last_seen age)"},
    {"what": "payload_upstream_origins", "checked_by": "scripts/build-payloads.sh"},
    {"what": "detection_efficacy", "checked_by": "POST /api/runs/{id}/reconcile and /verify"}
  ]
}
```

How to read a degraded component, using the example above:

- **`status: "degraded"` is not "something is broken."** Here it means no
  beacon has phoned home recently — completely normal before you've enrolled
  one, or if a beacon's service died. Every non-`ok` component carries a
  `code` and a `detail` that names the fix — read those, don't just alarm on
  the top-level status.
- **`degraded_components`** is the flat list to check first (what a console
  banner would render).
- **`not_checked`** is equally load-bearing: it names five reachability-shaped
  claims this endpoint *deliberately does not make* — no outbound tenant
  call, no beacon ping, nothing. A green `/api/health` is not "the POV will
  work"; it's "these components loaded." The gap between those two claims is
  exactly the `not_checked` list.

## Enrolling a beacon

Mint a short-lived, single-use enrollment token, then run the one-liner it
produces on the target:

```bash
# 1. Mint a token (server, not the target)
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"ttl_seconds":900,"max_uses":1,"label":"lab-target-1"}' \
  http://localhost:8888/api/agents/enroll/tokens
# -> {"token":"cxs_...", "expires_at":"...", ...}  (shown in full exactly once)

# 2. On the TARGET host, run the one-liner (systemd/launchd service by default):
curl -fsSL 'http://localhost:8888/api/agents/install?os=linux' \
  | CORTEXSIM_TOKEN='cxs_...' bash
```

What that script does, verified by fetching it directly
(`curl -s '.../api/agents/install?os=linux'`): it needs **no compiler and no
public-internet egress on the target** — it downloads the prebuilt beacon
from this same SimCore (`GET /api/agents/binary`, confirmed live: a real
`ELF 64-bit LSB executable ... statically linked` for `linux/amd64`, and the
matrix also covers `darwin/{amd64,arm64}` and `windows/amd64`) and
sha256-verifies it, then **enrolls** — SimCore assigns the agent id, the
target never invents one.

**`?mode=service` is the default, not `foreground`, on purpose.** Service
mode installs a supervised systemd unit (Linux) or launchd job (macOS) so the
beacon survives the SSH session and a reboot — a POV runs for weeks, not for
one open terminal. `?mode=foreground` blocks intentionally: it babysits the
beacon in your terminal and exists for a throwaway container or a quick
manual check, not for a lab target you're about to walk away from. If a
target has no supervisor available at all, install degrades honestly to
`setsid`+`nohup` and reports `DEGRADED_NO_SUPERVISOR` rather than pretending
it's supervised.

Every install stage posts a stable code back to SimCore, so "ran the
one-liner, nothing appeared" has an answer:

```bash
curl -s http://localhost:8888/api/agents/install/attempts?limit=10 | python3 -m json.tool
```

Confirm the beacon is live:

```bash
curl -s http://localhost:8888/api/agents | python3 -c '
import sys,json
for a in json.load(sys.stdin)["agents"]:
    print(a["agent_id"], a["status"], a["os"], a["last_seen"])'
```

`status` is `online` / `stale` / `offline`, derived from `last_seen` age —
SimCore never dials the target itself (it's pull-model), so this is a
liveness inference, not a probe.

## Launching a run

### Pull mode (agent executes)

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{
        "scenario_id": "SIM-EDR-001",
        "mode": "pull",
        "target_agent_id": "<agent_id from /api/agents>",
        "consent": {"simulation_authorized": true, "c2_authorized": false}
      }' \
  http://localhost:8888/api/runs
# -> {"run_id": "...", ...}
```

`consent` is a real gate, not decoration — a scenario binding a dual-use
adapter (e.g. Atomic Red Team) is refused (`409 CONSENT_REQUIRED`) without
`simulation_authorized: true`. Only set `c2_authorized: true` for a scenario
that genuinely stages a C2 framework, and only on infrastructure you control.

Poll to a terminal state (`complete` / `failed` / `aborted`):

```bash
curl -s http://localhost:8888/api/runs/<run_id> | python3 -c \
  'import sys,json; print(json.load(sys.stdin)["status"])'
```

Live progress streams over SSE if you want it in real time:
`GET /api/runs/<run_id>/events`. To stop a run mid-flight:
`POST /api/runs/<run_id>/abort`.

### Push mode (self-contained bundle, no SimCore at runtime)

```bash
curl -s http://localhost:8888/api/scenarios/SIM-EDR-001/download -o SIM-EDR-001.sh
```

Verified live: this returns a complete, self-contained bash script (identity
harness inlined, logging, cleanup trap, per-step `run_as` wrapper) — hand it
to a DC who will run it on a jumpbox with no SimCore reachability at all.
Windows-only or mixed-platform scenarios add `?format=powershell` (or
`?format=auto`, which prefers POSIX for back-compat); a scenario whose
content can't satisfy the requested target refuses with
`409 BUNDLE_TARGET_UNSATISFIABLE` naming the offending steps rather than
silently emitting a broken bundle.

Push has no live run record in the same sense as pull — it reaches a terminal
`staged` state the moment the bundle is generated. The report and MITRE
coverage still work; MTTD timing does not (nothing calls back).

## Reading a run's verdict

```bash
curl -s http://localhost:8888/api/runs/<run_id>/report?format=markdown
```

Real excerpt, from a completed run in this lab:

```
## Detection Coverage Summary
**Overall: 0/10 detections confirmed (0.0%)**

## Test-Case Verdict
**PENDING — verification outstanding**
primary KPI declared but not measured: no measured value yet.
```

Read this precisely:

- **Coverage 0/10** here does **not** mean Cortex missed anything — it means
  no observations have been reconciled against this run yet (no tenant
  connected, see Honest limit #1). `Result.observed_at` only gets set by
  `PUT /api/results/{id}/validate` (manual) or a connector's alert read-back
  (`POST /api/runs/{id}/reconcile`, opt-in, requires a configured tenant
  credential).
- **`tc_verdict: pending`** is the honest default for "never scored" or "no
  tenant to score against" — it is explicitly *not* the same bucket as
  `not_applicable` (unscoreable-by-construction) or `fail`. Only
  `NOT_ENTITLED` and the unscoreable clamp resolve `not_applicable`; a
  threshold-carrying test case with no measurement sits at `pending`
  indefinitely, by design, rather than defaulting to a false pass.
- To move a run's verdict off `pending` against a real tenant: configure a
  credential (`POST /api/credentials/integrations`), then
  `POST /api/runs/{id}/reconcile?connector=xsiam` (alert read-back →
  auto-matches `Result` rows on technique/detection-id/name → real MTTD) and,
  for XQL-backed Tier-2 verification, `POST /api/runs/{id}/verify`. Both are
  opt-in, outbound, read-only, and off by default.

## Tier-D harness — validate the pull path before a customer is watching

Tier-C detonates a push bundle in an audited container. **Tier-D is the pull
path**: mint token → one-liner → sha256-verified beacon → server-assigned
agent id → enroll → poll → payload staging → execute under the identity
harness → report back — fully automated and re-runnable, against an ephemeral
target this harness builds itself from `deploy/tier-d/Dockerfile.target` (the
provisioned image from Honest limit #2 — **not** a bare `ubuntu:22.04`).

```bash
deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001
```

The harness's entire reason to exist is refusing to collapse three different
kinds of red into one:

| Class | Meaning | Counts as a harness FAIL? |
|---|---|---|
| **ENGINE** | the beacon/orchestrator/identity harness broke — a real CortexSim defect | **Yes** |
| **ENVIRONMENT** | the target couldn't support the step (no login shell, missing tool, no egress) — the TTP **never ran** | No — an honest, accounted-for outcome |
| **TTP** | the technique ran and legitimately did not succeed | No — real signal |

A genuine `verdict.json` from this stack (`SIM-EDR-001`, step-05 hit the
missing-interpreter refusal from Honest limit #4):

```json
{
  "run_status": "failed",
  "counts": {"OK": 4, "ENGINE": 0, "ENVIRONMENT": 1, "TTP": 0},
  "steps": [
    {"n": 5, "exit_code": 127, "class": "ENVIRONMENT",
     "reason": "a step-declared interpreter was not found on the target and
                no authorized install could supply it — the step's own
                command was NEVER executed, so any absent detection here is
                not a Cortex miss"}
  ],
  "harness_verdict": "PASS",
  "interpretation": "ENGINE failures mean CortexSim is broken (harness FAIL).
    ENVIRONMENT failures mean the TTP never ran — do NOT report those as a
    detection miss. TTP failures are real technique outcomes. INCONCLUSIVE
    means this run does not have enough honest evidence to call PASS or
    FAIL — treat it exactly like FAIL until it is understood."
}
```

Note the shape: `run_status: "failed"` (SimCore's own run record — one step
exited non-zero) but `harness_verdict: "PASS"` — because the harness looked
*why* step-05 failed and correctly classified it as ENVIRONMENT, not ENGINE.
This is the distinction Honest limit #2 exists to make legible: a run reading
"failed" is not automatically a defect, and it is not automatically a missed
detection either — read the classification before you report either one.

Exit codes: `0` genuine PASS (including "PASS, with unrun ENVIRONMENT
steps") · `1` FAIL/INCONCLUSIVE (a real defect, or not enough evidence to
call it) · `2` harness setup failed (docker/SimCore unreachable/enroll
failed) · `3` the run never reached a terminal state within the poll budget.

Run it once against every scenario you plan to lean on in a POV, before the
POV — that's the whole point of the harness existing separately from the
product it's testing.

---

## Reference

- `docs/reference/api-and-agent-surface.md` — full HTTP + agent surface.
- `docs/reference/detection-proof-operator-runbook.md` — the storyline/scorecard proof layer.
- `docs/reference/payload-shelf.md` — staged, digest-pinned tool artifacts (why a target doesn't need internet egress to run tier-4 tools).
- `docs/design/agent-runtime-dependencies.md` — the missing-interpreter refusal path in full.
- `deploy/tier-d/Dockerfile.target`, `deploy/tier-d/classify.py` — the provisioned target and the ENGINE/ENVIRONMENT/TTP classifier.
