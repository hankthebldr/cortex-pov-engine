# Tier-C — Isolated Container Execution

> **Increment 1 of 2 — infrastructure.** This directory ships the isolation
> harness (runner image + auditd + network sinkhole + operator script).
> The Tier-C *reference scenarios* (SIM-EDR-001, SIM-CDR-001, SIM-MP-004) and
> the pytest assertions that diff observed signals against
> `expected_detections` are **increment 2** — see "What ships next" below.

Part of the [e2e execution methodology](../../docs/design/e2e-execution-methodology.md)
(roadmap **H1.1**). Tier-C is the test tier that proves **"the right binaries
fire under the right identities"** in an isolated, instrumented sandbox.

## What Tier-C proves

CortexSim runs each TTP step through an identity harness (`runuser -l www-data
-c '...'`, `sudo -u postgres ...`) so the process-causality chain a customer's
Cortex XDR sees is realistic. The existing test pyramid (Tier A static lint,
Tier B push-bundle integrity, Playwright UI) can confirm a bundle is *well
formed* — it cannot confirm that, when run, `grep` actually forks **under
uid 33 (www-data)** rather than root.

Tier-C closes that gap. It executes a real CortexSim **push bundle** (the same
self-contained bash a DC runs on a clean Ubuntu 22.04 host) inside an
ephemeral, audited, network-sinkholed container, then captures ground-truth
telemetry:

| Signal | auditd key | Proves |
|--------|-----------|--------|
| `execve` | `cortexsim_exec` | which binaries forked (process tree spine) |
| `setuid`/`setresuid`/… | `cortexsim_setid` | identity transitions (root → www-data → …) |
| `connect` | `cortexsim_net` | outbound C2/exfil *intent* (lands in the sinkhole) |
| credential-file reads | `cortexsim_creds` | `/etc/shadow`, `~/.aws/credentials`, `~/.ssh`, `/home` access |

The output diffs against a scenario's `expected_detections` (increment 2).

## Isolation guarantees

- **No external egress.** The runner + sinkhole live on a Docker bridge with
  `internal: true` — Docker creates **no gateway** to the host or internet.
  The runner's DNS points at the sinkhole, which answers *every* name with its
  own address, so a C2/exfil step resolves and connects **inside** the
  isolated network and never reaches a real service.
- **Ephemeral.** Containers are torn down (`down -v`) on exit. Re-created per
  run. No state carries between runs.
- **Audited.** auditd loads [`audit.rules`](./audit.rules) at boot; the run
  produces a raw `audit.log` plus a structured `observed-signals.json`.
- **Least privilege.** The runner gets exactly the caps it needs
  (`AUDIT_CONTROL`, `AUDIT_WRITE`, `SETUID/SETGID` + a small DAC set for the
  harness) and drops the rest. The sinkhole drops all caps except
  `NET_BIND_SERVICE`. **No real C2 framework is staged** — Tier-C is not a C2
  sandbox; that's Tier-D territory.

## Layout

```
deploy/tier-c/
├── Dockerfile                  # runner image: harness service accounts + auditd
├── audit.rules                 # auditd ruleset (execve/setuid/connect/creds)
├── entrypoint.sh               # load rules → run bundle → dump audit + summary
├── docker-compose.tier-c.yml   # runner + sinkhole on an internal bridge
├── run-tier-c.sh               # operator entrypoint (build/run/collect/teardown)
├── sinkhole/
│   ├── Dockerfile              # dnsmasq + stdlib HTTP/HTTPS catch-all
│   ├── dnsmasq.conf            # wildcard A record → sinkhole
│   ├── catch_all_http.py       # logs every request, returns 200
│   └── sinkhole-entrypoint.sh  # wires the sinkhole IP into dnsmasq
└── README.md                   # this file
```

## How to run

### Option A — from a local push bundle

Generate a bundle from a running SimCore (or any `*.sh` bundle you already
have):

```bash
curl -s 'http://localhost:8888/api/scenarios/SIM-EDR-001/download?format=bash' \
    -o /tmp/edr-001.sh

deploy/tier-c/run-tier-c.sh --bundle /tmp/edr-001.sh
```

### Option B — fetch the bundle by scenario id

`run-tier-c.sh` will pull the bundle from SimCore for you:

```bash
deploy/tier-c/run-tier-c.sh --scenario SIM-EDR-001 \
    --simcore http://localhost:8888
```

### What you get

Artifacts land under `deploy/tier-c/results/<run-id>/`:

```
observed-signals.json   # structured summary keyed by syscall class
audit.log               # raw auditd ground truth
bundle-stdout.log       # the bundle's own stdout/stderr (per-step harness log)
sinkhole/http.jsonl     # every HTTP(S) request the bundle made
```

`observed-signals.json` shape:

```json
{
  "status": "ok",
  "audit_mode": "auditd",
  "bundle": "bundle.sh",
  "bundle_exit_code": 0,
  "signals": {
    "exec":  { "count": 42, "executables": ["/usr/bin/grep", "/usr/sbin/runuser", "..."] },
    "setid": { "count": 6,  "uids": ["www-data", "postgres", "..."] },
    "net":   { "count": 3 },
    "creds": { "count": 4 }
  },
  "harness_identities_from_stdout": ["www-data", "root"]
}
```

### Flags

| Flag | Meaning |
|------|---------|
| `--bundle <file>` | run a local push-bundle `.sh` |
| `--scenario <id>` | fetch the bundle from SimCore by scenario id |
| `--simcore <url>` | SimCore base URL (default `http://localhost:8888`) |
| `--format bash\|k8s` | bundle format to fetch (default `bash`) |
| `--results <dir>` | where to write artifacts (default `results/<run-id>`) |
| `--keep` | leave the stack up for inspection instead of tearing down |

## Identity harness — service accounts

The runner image pre-creates exactly the usernames the CortexSim identity
harness wraps in `core/engine/push_generator.py`:

```
www-data · postgres · mysql · node · python3 · nobody · svc-backup
```

Each is a system account with a real `/bin/bash` shell so `runuser -l <user>
-c '...'` forks under that uid and the setuid transition shows up in the audit
log. `nobody` (uid 65534) ships with `/usr/sbin/nologin` in the base image and
is re-shelled so `identity: nobody` steps execute.

## Degraded mode (no kernel audit)

Some CI runners and rootless/nested Docker setups don't expose the kernel
audit netlink socket. The entrypoint detects this, falls back to
`audit_mode: "degraded"`, and still produces:

- `bundle-stdout.log` (the harness logs `identity=<user>` per step)
- `harness_identities_from_stdout` in the summary
- `sinkhole/http.jsonl` (network shape is independent of auditd)

In degraded mode the `signals.*.count` fields are `0` — rely on the stdout +
sinkhole evidence, or run on a host with audit support for full ground truth.

## How the audit output maps to `expected_detections`

A scenario step declares, e.g.:

```yaml
- id: step-02
  identity: www-data
  command: "cat /etc/shadow ..."
  expected_detections:
    - plane: EDR
      type: BIOC
      description: "/etc/shadow read by non-root service account (www-data)"
```

The Tier-C mapping (formalised as assertions in **increment 2**):

| `expected_detection` element | Tier-C ground-truth source |
|------------------------------|----------------------------|
| step `identity: www-data` | a `cortexsim_setid` record transitioning to uid 33, **and** `www-data` in `harness_identities_from_stdout` |
| credential-access detection (`/etc/shadow`) | a `cortexsim_creds` record for `/etc/shadow` |
| C2 / exfil detection | a `cortexsim_net` connect record + a `sinkhole/http.jsonl` line for the target host |
| a binary firing (e.g. `grep`, `aws`) | that exe in `signals.exec.executables` |

If the audit log shows `grep` ran as **root** when the YAML said `www-data`,
the harness silently regressed — exactly the failure Tier-C is built to catch.

## Increment 2 — shipped

The expected-signal assertion layer is now in place:

- **`tier_c_assert.py`** (stdlib only, ships in the runner image) turns an
  `observed-signals.json` summary into a verdict against a per-scenario
  **expectation** — the formal version of the mapping table above. It declares
  what each scenario must produce (identities forked, credential files read,
  network egress, binaries fired) and checks the observed ground truth against
  it. It is **audit-mode aware**: in `degraded` mode the count-based checks
  (creds/net/exec) become `skip` rather than `fail`, while the stdout-derived
  identity check still runs.
- **Reference expectations** for `SIM-EDR-001`, `SIM-CDR-001`, `SIM-MP-004` are
  hand-authored in `REFERENCE_EXPECTATIONS`; any other scenario gets a baseline
  auto-derived from its steps (`derive_expectation`).
- **`tests/e2e_isolated/test_tier_c_isolated_exec.py`** drives the assertion
  engine with synthetic passing / failing / degraded fixtures (always run, no
  docker) AND a docker-gated end-to-end test that runs `run-tier-c.sh` against a
  real bundle and applies the same verdict. The e2e test is opt-in: it needs
  docker plus either `CORTEXSIM_TIERC_BUNDLE=<bundle.sh>` or
  `CORTEXSIM_SMOKE_URL=<live SimCore>` (it never fabricates signals — it skips).

To apply the verdict to a run you collected:

```python
import json
from deploy.tier_c.tier_c_assert import evaluate_scenario  # or import by path
observed = json.load(open("results/<run-id>/observed-signals.json"))
scenario = yaml.safe_load(open("scenarios/edr/edr-001-credential-dumping.yml"))
report = evaluate_scenario(scenario, observed)
print(report.to_dict())   # {"passed": true/false, "checks": [...]}
```

Increment-1 assets remain covered by `tests/e2e_isolated/test_tier_c_assets.py`
(no docker required). Still open: optional path-filtered CI wiring per the
methodology doc's "CI integration" section.
