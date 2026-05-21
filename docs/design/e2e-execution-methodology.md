# E2E Execution Methodology — Confirming TTP Scripts Actually Run

Author: DC2 GTM NAM Cortex · Status: **design** (awaiting review)
Companion to: `docs/design/console-redesign.md`, the playwright spec migration
backlog, and the `.github/workflows/test.yml` `e2e-stack` job.

## The gap this addresses

Today's test pyramid:

```
┌─────────────────────────────────────────────────────────────┐
│ Tier 2b — Playwright E2E (UI behavior)                      │
│   ✓ "DC can click Launch and a run appears in the Evidence  │
│     tab"                                                     │
│   ✗ does not confirm that the agent actually executed grep, │
│     curl, aws-cli — only that SimCore returned 200          │
├─────────────────────────────────────────────────────────────┤
│ Tier 1 — pytest smoke (API surface)                          │
│   ✓ /api/health responds 200                                │
│   ✓ /api/scenarios returns the loaded library               │
│   ✗ does not exercise scenario execution                    │
├─────────────────────────────────────────────────────────────┤
│ ui-unit / python — pure unit tests                          │
│   ✓ Pydantic schema accepts the YAML                        │
│   ✗ does not validate the embedded shell commands           │
└─────────────────────────────────────────────────────────────┘
```

What's missing: **proof that when SimCore dispatches scenario step N, the
agent's identity harness wraps the command correctly, the underlying binary
fires with the expected `effective_uid`, network calls go to the expected
endpoints, and the cleanup block removes every staged artifact.**

Without this, a regression like "the harness silently swallowed identity
substitution after the credentials refactor" would ship green through every
existing test layer. The agent calling `bash -c 'id'` looks identical to
`runuser -l www-data -c 'id'` from the SimCore-API perspective; the
difference only shows up in process-tree telemetry that Cortex XDR sees.

## Proposed tier model

Four tiers, each layer adding fidelity. CI runs tiers A–C on every PR; tier
D is a manual operator validation that lives outside CI.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Tier D — Real Cortex telemetry (manual POV)                          │
│ ▸ Real Cortex XDR agent + cloud account                              │
│ ▸ Validates: detections fire in customer console                     │
│ ▸ Runs: by the DC during POV; not in CI                              │
├──────────────────────────────────────────────────────────────────────┤
│ Tier C — Isolated container execution + process auditing  ← NEW      │
│ ▸ Hermetic Ubuntu 22.04 container with audited syscalls              │
│ ▸ Stubbed external services (AWS CLI, DNS, HTTP) into a sinkhole     │
│ ▸ Validates: process tree, effective UIDs, network shape, cleanup    │
│ ▸ Runs: gated CI job per scenario YAML change                        │
├──────────────────────────────────────────────────────────────────────┤
│ Tier B — Push bundle integrity                            ← NEW      │
│ ▸ POST /api/run mode=push → grab the generated bash bundle           │
│ ▸ Validates: every step embedded, harness wraps right, no            │
│   placeholder leaks ({}, {{}}, $undefined), cleanup present          │
│ ▸ Runs: per scenario YAML change in CI                               │
├──────────────────────────────────────────────────────────────────────┤
│ Tier A — Static script analysis                           ← NEW      │
│ ▸ shellcheck + bash -n every ttps/*.sh                              │
│ ▸ Validate each step's mitre_technique exists in the schema         │
│ ▸ Validate cleanup block matches staged artifacts                    │
│ ▸ Runs: per push, fast, hard gate                                    │
├──────────────────────────────────────────────────────────────────────┤
│ Tier 0 — Existing (unit, API smoke, UI)                              │
│ ▸ As today; no change                                                │
└──────────────────────────────────────────────────────────────────────┘
```

The rest of this doc focuses on **Tier C** — the isolated container — because
A and B are mostly straightforward extensions of existing static-validation
patterns. Tier C is where the real architectural decisions live.

## Tier C — Isolated container design

### Goals

1. **Real process execution.** A scenario step that says `runuser -l www-data
   -c 'grep -r AKIA ...'` must actually fork `runuser`, which must actually
   fork `grep` under the `www-data` UID. No mocking the binary, no
   short-circuiting `subprocess.Popen` — the harness must produce the same
   process tree it would on a customer host.
2. **No real attack surface.** AWS API calls, DNS lookups, and HTTP egress
   must terminate inside the container, not reach real services. CI runners
   leaking egress to AWS is a security and cost issue.
3. **Cheap to spin up.** Each PR run should add <60s to CI for a typical
   scenario set. Spinning a fresh container per scenario is fine; per
   *step* is too slow.
4. **Deterministic.** No flakes. Same scenario, same commit → same audit
   log every time.
5. **Inspectable on failure.** When a test fails, we need a way to see the
   actual process tree and network log without re-running.

### Container — `cortexsim-e2e-target`

A new Dockerfile under `tests/e2e_isolated/target/Dockerfile`:

```dockerfile
FROM ubuntu:22.04

# ── Stage 1: real binaries that scenarios call ─────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash coreutils findutils grep gawk sed jq \
    curl wget dnsutils netcat-openbsd openssl \
    runuser sudo procps auditd \
    && rm -rf /var/lib/apt/lists/*

# ── Stage 2: stubbed external clients ──────────────────────────────
#   Every binary that hits the network is replaced by a wrapper that
#   logs invocations to JSONL and emits a deterministic response shape.
#   The real binary still works behind the wrapper (for cases where a
#   scenario specifically needs real output), but only against the
#   sinkhole's localhost listeners.
COPY stubs/aws       /usr/local/bin/aws
COPY stubs/kubectl   /usr/local/bin/kubectl
COPY stubs/sliver    /usr/local/bin/sliver
RUN chmod +x /usr/local/bin/aws /usr/local/bin/kubectl /usr/local/bin/sliver

# ── Stage 3: service-account identities used by scenarios ──────────
RUN useradd -r -s /bin/bash www-data 2>/dev/null || true; \
    useradd -r -s /bin/bash nobody   2>/dev/null || true; \
    useradd -r -s /bin/bash postgres 2>/dev/null || true; \
    useradd -r -s /bin/bash node     2>/dev/null || true

# ── Stage 4: audit + sinkhole boot ─────────────────────────────────
COPY audit/rules.d /etc/audit/rules.d
COPY sinkhole/     /opt/sinkhole/
COPY init.sh       /usr/local/bin/cortexsim-target-init
RUN chmod +x /usr/local/bin/cortexsim-target-init

ENTRYPOINT ["/usr/local/bin/cortexsim-target-init"]
```

The image is built once per CI run and cached. Sub-second container start.

### Process auditing — start with auditd, evolve to eBPF

**Phase 1 (recommended starting point):** `auditd` with rules that capture:

- `execve` syscall on every binary spawn (gives us full process tree)
- `connect` syscall on outbound TCP (catches network exfil intent)
- `open` syscall on `/etc/shadow`, `/var/log/auth.log`, `~/.aws/credentials`
  (catches credential access patterns)

Sample rule (`audit/rules.d/cortexsim.rules`):

```
-a always,exit -F arch=b64 -S execve -k cortexsim_proc
-a always,exit -F arch=b64 -S connect -k cortexsim_net
-w /etc/shadow              -p r -k cortexsim_creds
-w /var/log/auth.log        -p r -k cortexsim_creds
-w /root/.aws/credentials   -p r -k cortexsim_creds
```

After the scenario run completes, the harness reads `/var/log/audit/audit.log`
and parses it with `auparse` (or a small Python parser). Each test asserts
the expected events fired.

**Why auditd over eBPF for v1:**
- Ships in the base Ubuntu image; no kernel-header dependency
- Doesn't require a privileged container (just `--cap-add=AUDIT_WRITE`)
- Mature parsing tooling (`auparse`, `ausearch`)
- Slightly less precise than eBPF but adequate for the assertion shapes we
  need

**When to upgrade to eBPF:** if we ever need to assert *thread-level*
behavior, syscall arguments beyond what audit captures (memory addresses,
struct fields), or sub-microsecond timing. Park it as a Phase 2 upgrade.

### Network sinkhole

Every outbound HTTP/DNS/TCP call inside the container must hit a local
listener. Three pieces:

1. **DNS** — `coredns` instance bound to `127.0.0.53:53`, container's
   `/etc/resolv.conf` points there. Zone file rewrites known scenario
   targets:

   ```corefile
   . {
     hosts {
       127.0.0.1 testmynids.org
       127.0.0.1 *.amazonaws.com
       127.0.0.1 *.googleapis.com
       127.0.0.1 *.microsoft.com
       fallthrough
     }
     forward . 0.0.0.0:0   # any unmatched query → NXDOMAIN
   }
   ```

2. **HTTP/HTTPS** — small Python/aiohttp server bound to `127.0.0.1:80` and
   `:443`. Returns a deterministic 200 for known endpoints with the
   scenario-expected response shape. Self-signed cert generated at image
   build. Logs every request to `/var/log/sinkhole/http.jsonl`:

   ```json
   {"ts": "2026-05-20T22:14:01Z", "method": "GET", "host": "testmynids.org",
    "path": "/uid/index.html?beacon=1", "ua": "Mozilla/5.0 (CortexSimBeacon/1.0)",
    "src_pid": 4231, "src_uid": 33}
   ```

3. **iptables egress block** — REJECT outbound to anything that isn't
   `127.0.0.1` or the SimCore container's internal IP. Inserted at
   container boot.

### Stubbed external clients

For tools whose presence is part of the scenario semantic — `aws`, `kubectl`,
`sliver-client` — we ship wrappers that:

- Log every invocation as JSON (args, env, stdin, stdout) to a per-tool
  JSONL file
- Hit only the sinkhole, never real services
- Return realistic JSON response shapes (lifted from real API examples)
  so downstream scenario steps that parse the output still work

Example `stubs/aws`:

```bash
#!/usr/bin/env bash
# Stub for AWS CLI in the e2e_isolated container.
# Logs the call, returns a canned response that matches the real API shape.

set -eu
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
mkdir -p /var/log/cortexsim-stubs
jq -nc --arg ts "$TS" --arg cmd "$*" \
       --arg uid "$EUID" --arg user "$(id -un)" \
       '{ts: $ts, tool: "aws", cmd: $cmd, euid: ($uid | tonumber), user: $user}' \
       >> /var/log/cortexsim-stubs/aws.jsonl

# Dispatch on subcommand → emit realistic JSON to stdout.
case "$1 $2" in
  "sts get-caller-identity")
    cat <<'EOF'
{"UserId": "AIDAEXAMPLE", "Account": "123456789012",
 "Arn": "arn:aws:iam::123456789012:user/cortexsim-e2e"}
EOF
    ;;
  "ec2 describe-instances")
    cat <<'EOF'
{"Reservations": []}
EOF
    ;;
  "s3 ls"|"s3 cp"|"s3api get-bucket-acl")
    echo "{}"
    ;;
  *)
    echo '{"stub": true, "args": "'"$*"'"}'
    ;;
esac
```

The wrapper is small and contains no secrets. It's deterministic and
inspectable.

### Test harness — `tests/e2e_isolated/`

New directory next to `tests/smoke/`:

```
tests/e2e_isolated/
├── conftest.py                 # pytest fixtures
├── target/
│   ├── Dockerfile
│   ├── init.sh
│   ├── stubs/                  # AWS, kubectl, sliver wrappers
│   ├── audit/rules.d/cortexsim.rules
│   └── sinkhole/               # coredns config + http listener
├── test_static_a.py            # tier A — shellcheck, bash -n
├── test_push_bundle_b.py       # tier B — bundle integrity
├── test_isolated_exec_c.py     # tier C — actual execution
└── README.md
```

### Tier C test shape

A typical test:

```python
def test_sim_mp_004_executes_cleanly(isolated_target, simcore, audit, sinkhole):
    """SIM-MP-004 — APT29 cloud cred theft: five steps fire as the
    declared identities and produce the expected outbound shape."""
    # Launch
    run = simcore.launch_run(scenario_id="SIM-MP-004", mode="pull",
                             target_agent=isolated_target.agent_id)
    run.wait_for_completion(timeout=90)

    # Per-step process-tree assertions
    step1 = audit.events_for_step(run.id, step_index=1)
    assert any(e.comm == "grep" and e.euid == www_data_uid
               for e in step1), "step 1 grep did not fire under www-data"

    step2 = audit.events_for_step(run.id, step_index=2)
    assert any(e.comm == "aws" and e.euid == www_data_uid
               for e in step2), "step 2 aws cli did not fire under www-data"

    # Network shape assertions
    aws_calls = sinkhole.calls_for_tool("aws")
    assert any(c.cmd.startswith("sts get-caller-identity") for c in aws_calls), \
        "step 2 expected sts:GetCallerIdentity"
    assert any(c.cmd.startswith("ec2 describe-instances") for c in aws_calls), \
        "step 3 expected ec2:DescribeInstances"

    # Cleanup assertion
    cleanup_events = audit.events_after(run.completed_at, comm="rm")
    assert len(cleanup_events) >= 1, "cleanup did not remove staged artifacts"

    # SimCore result rows populated
    results = simcore.results_for_run(run.id)
    assert len(results) >= 5, "expected at least one Result row per step"
    assert all(r.executed_at is not None for r in results)
```

The assertions are **scenario-shape, not implementation detail**. They
say "the right binaries fired as the right identity," not "the harness
called `subprocess.Popen` with these exact args." Refactors of the harness
don't break the test; regressions in identity wrapping do.

### CI integration

New job in `.github/workflows/test.yml`:

```yaml
e2e-isolated:
  name: e2e (isolated execution)
  runs-on: ubuntu-22.04
  needs: [python-tests, ui-unit]
  # HARD gate — regressions here are real and must block.
  # No continue-on-error.
  if: |
    contains(github.event.pull_request.labels.*.name, 'touches-scenarios')
    || contains(toJson(github.event.pull_request.files.*.path), 'scenarios/')
    || contains(toJson(github.event.pull_request.files.*.path), 'core/engine/')
    || contains(toJson(github.event.pull_request.files.*.path), 'agent/')
  steps:
    - uses: actions/checkout@v4
    - name: Build isolated target image
      run: docker build -t cortexsim-e2e-target tests/e2e_isolated/target/
    - name: Run isolated-execution suite
      run: pytest tests/e2e_isolated/test_isolated_exec_c.py -v --tb=short
```

The path-filter is important: this job is moderately expensive (~3 min),
so it only runs when scenario YAML, the harness, or the engine actually
changed. Pure UI PRs skip it.

## Where the existing playwright suite fits

The current playwright specs become **Tier E1 — UI workflow integration**.
After the spec-migration PR (the one that removes `?theme=legacy`), they:

- No longer assert on TTP execution shape (they're not equipped to)
- Assert UI behavior end-to-end: click Launch → drawer closes → telemetry
  strip shows the run → Evidence tab populates within N seconds
- Continue to run as a soft-gate until console-stable, then become hard

The Tier C suite owns "did the script actually fire correctly." Playwright
owns "does the UI reflect that it did."

## Tradeoffs and open questions

### 1. Stubs vs. virtualized services (LocalStack, moto)

**Stubs** — what's proposed here. ~10 lines per tool. Deterministic. Cheap.
But fidelity is limited: a stub doesn't catch "we built an STS request with
malformed JSON."

**LocalStack/moto** — real AWS SDK code paths, validation, IAM. Higher
fidelity, but adds ~30s to container start and requires a much bigger
service definition surface to keep in sync.

**Recommendation:** stubs for tier C on every PR; a nightly tier C+ job
that swaps stubs for LocalStack catches the higher-fidelity regressions
without paying the cost on every push.

### 2. Shared container vs per-test container

**Shared** — one container per test session. 10× faster. Risk: cross-test
state pollution (artifacts left in `/tmp`, residual env vars).

**Per-test** — one container per scenario test. Slower but bulletproof.

**Recommendation:** shared by default with an aggressive setUp that
truncates audit logs, sinkhole logs, and wipes `/tmp/cortexsim-*`. Opt-in
fresh container via `@pytest.mark.fresh_container` for tests that touch
shared state (e.g. credential file scenarios).

### 3. eBPF vs auditd

Started with auditd above. eBPF (via bpftrace) would give us:
- Per-thread granularity
- Sub-microsecond timing
- Syscall argument inspection beyond what audit captures

**Recommendation:** auditd in Phase 1. Revisit eBPF only if assertion
shapes need it.

### 4. Agent process model

Today the Go agent (`bin/cortexsim-agent`) shells out via `os/exec` and
identity-wraps via `runuser`/`sudo -u`. If we ever rewrite the agent
internals (e.g. cgroups-isolated subprocess execution, namespace-per-step),
Tier C must keep working unchanged because the assertions are on the
*observed process tree*, not the agent's internal API.

This is a feature — the test pyramid stays stable across agent rewrites.

### 5. Network egress rules

We REJECT outbound non-127.0.0.1. What about scenarios that intentionally
test "scenario tries to exfil to a real domain"? Those scenarios need a
SimCore-controlled domain (`*.cortexsim-sinkhole.test`) that resolves
in-container to a SimCore-internal IP, NOT 127.0.0.1. This keeps the
sinkhole logic for the actual scenario behavior, not "all outbound is
local."

### 6. Performance budget

Target: tier C suite under 3 minutes wall-clock for the full scenario
catalog (currently 15 active + drafts).

If we exceed: parallelize (5 containers × 3 scenarios each), or shard by
plane (one container per plane, all that plane's scenarios in series).

## Roadmap to implement

| Phase | Scope | CI impact |
|---|---|---|
| **0** (now) | This design doc reviewed + approved | none |
| **1** | Tier A — shellcheck + bash -n on all ttps/*.sh | +5s, hard gate |
| **2** | Tier B — push bundle integrity tests (no execution) | +10s, hard gate |
| **3** | Container image + auditd + sinkhole infrastructure (no tests yet) | none, image cached |
| **4** | Tier C — 3 reference scenarios end-to-end (SIM-EDR-001, SIM-CDR-001, SIM-MP-004) | +90s, hard gate, path-filtered |
| **5** | Tier C — backfill every active scenario | +60s incremental, hard gate |
| **6** | Playwright spec migration to console UI + remove `?theme=legacy` + remove e2e `continue-on-error` | Playwright back to hard gate |

Each phase is its own PR. Phase 1 is small and could land tomorrow; phase 4
is the meaty one.

## Open questions for the DC team

1. **Sinkhole egress for cloud scenarios** — is intentionally-leaked DNS
   to a customer-controlled `*.cortexsim-sinkhole.test` zone acceptable
   from an OPSEC perspective? (We're not exfiltrating real data, just
   simulating the network shape.)
2. **LocalStack integration** — is the ~30s/run cost on nightly builds
   acceptable? If yes, we can promote it from "future option" to "phase 7".
3. **Scenario-level fixtures** — some scenarios (e.g. ITDR) need a
   running AD-equivalent. The IaC generator already produces that for
   real labs; for tier C, do we want an LDAP-stub container as a sidecar,
   or do we mark those scenarios `skip in tier C` and rely on tier D?
4. **Author ergonomics** — when a new scenario lands, what's the
   minimum the author has to write to get tier C coverage? A
   pytest-parametrized test that just dispatches on `scenario_id` works
   if the YAML's expected behavior is self-describing. Should we extend
   the scenario schema with an optional `e2e_assertions:` block that
   declares "expect grep under www-data in step 1" so the test harness
   reads it directly?

## Non-goals

- Replacing tier D (real Cortex telemetry). The whole point of CortexSim
  is the customer's real sensors firing. Tier C confirms the *attacker
  half* of the equation; tier D confirms the *detection half*.
- Sandboxing real C2 frameworks (sliver, havoc) — those are tier D
  territory. Tier C uses behavioral stubs.
- Catching every possible regression. Tier C catches regressions in
  process tree, identity, and network shape. Bugs in YAML-to-bash
  translation, harness ordering, etc. are tier B's job.
