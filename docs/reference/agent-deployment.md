# Agent Deployment — enrolling a beacon on a jumpbox

> **Standalone fragment** for inclusion in the Lab Runbook / GitHub README.
> Audience: a Domain Consultant who knows Cortex cold and has SimCore running
> (`scripts/dev-up.sh`) but has never deployed a CortexSim beacon before.
> Every command below was run against a live local SimCore while writing this
> doc (repo `main` @ `17552ff`, 2026-08-31, `DOCKER_CONTEXT=default`,
> `http://localhost:8888`) — none of it is copied from memory or from the
> source without also being executed.

---

## 1. The one-liner, exactly as it works today

Two calls. The first mints a token from SimCore; the second runs on the
**target jumpbox** — the host you want to generate signal on, not the host
running SimCore.

```bash
# 1. Mint a short-lived, single-use enrollment token (run against SimCore)
curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"ttl_seconds":900,"max_uses":1,"label":"my-jumpbox"}' \
  'http://localhost:8888/api/agents/enroll/tokens'
```

Verified live response shape (token value shown once, then only its tail is
ever returned again by `GET /api/agents/enroll/tokens`):

```json
{
  "id": 31,
  "token": "cxs_1yY0EOSrxaqWai3nFkq5XKMSz9Zd40wu1-klOIiftkE",
  "label": "my-jumpbox",
  "created_at": "2026-08-31T20:44:28.704236",
  "expires_at": "2026-08-31T20:59:28.704236",
  "max_uses": 1,
  "used_count": 0,
  "remaining_uses": 1,
  "revoked": false
}
```

```bash
# 2. On the TARGET jumpbox — installs as a supervised service by default
curl -fsSL 'http://<simcore-host>:8888/api/agents/install?os=linux' \
  | CORTEXSIM_TOKEN='cxs_1yY0EOSrxaqWai3nFkq5XKMSz9Zd40wu1-klOIiftkE' bash
```

Replace `<simcore-host>` with SimCore's **routable** address, not
`localhost` — see §5.

**The token belongs in the env var, not the URL.** The install endpoint also
accepts `?token=` as a query parameter and will bake it into the returned
script (verified: `GET .../install?os=linux&token=cxs_…` returns a script
whose `TOKEN="${CORTEXSIM_TOKEN:-cxs_…}"` line carries the value as the
default), but that puts a live credential in the jumpbox's shell history and
any proxy access log the customer keeps. `?os=linux` with no `token` query
param bakes `TOKEN="${CORTEXSIM_TOKEN:-}"` — empty — so the env-var form is
the only one that keeps the token out of both. This is why the console's
Deploy-agent panel (§4) builds the one-liner with the token in the env var
and the CLAUDE.md project doc documents it that way too — treat any
`?token=` link you see as something to avoid pasting into a real engagement.

macOS is the same shape with `os=darwin` (`os=macos`/`os=mac` also accepted).
Windows is PowerShell — see §7.

## 2. What the script actually does (verified, not just read)

Fetched directly and inspected (`curl -fsS '.../install?os=linux'`, 353
lines, `Content-Type: text/x-shellscript`, `Content-Disposition: attachment;
filename="install-cortexsim-agent.sh"`):

1. Detects OS/arch, refuses anything outside
   `{linux,darwin}×{amd64,arm64}` / `windows/amd64` with a stable `BAD_OS`/
   `UNSUPPORTED_ARCH` code.
2. **Enrolls first** — redeems the token against `POST /api/agents/enroll`.
   SimCore assigns the agent id; the target never invents one. A denied
   token (expired, over its use limit, revoked, or simply wrong) fails
   `ENROLL_DENIED` with the remediation "mint a fresh token" — nothing
   partially installs.
3. Downloads the **prebuilt** beacon from this same SimCore
   (`GET /api/agents/binary`) and verifies its sha256 against
   `GET /api/agents/binary/sha256`. **No compiler and no public-internet
   egress are required on the target** — confirmed live, `GET
   /api/agents/binaries` on this stack today serves all five matrix members:

   ```json
   {"binaries": [
     {"os": "linux",   "arch": "amd64", "sha256": "7e1660c5…"},
     {"os": "linux",   "arch": "arm64", "sha256": "a363cf9d…"},
     {"os": "darwin",  "arch": "amd64", "sha256": "99f17404…"},
     {"os": "darwin",  "arch": "arm64", "sha256": "d728eb5b…"},
     {"os": "windows", "arch": "amd64", "sha256": "c4c571b0…"}
   ], "total": 5, "dist_dir": "/app/agent-dist"}
   ```

   A checksum mismatch is a hard stop (`CHECKSUM_MISMATCH`), never a silent
   fallthrough.
4. Installs and starts the beacon per `?mode=` — see §3.
5. POSTs every stage's stable exit code to
   `POST /api/agents/install/telemetry`, readable back at
   `GET /api/agents/install/attempts` — so "I ran the one-liner and nothing
   appeared" always has an answer, even without shell access to the
   jumpbox:

   ```bash
   curl -fsS 'http://localhost:8888/api/agents/install/attempts?limit=10'
   ```

   This is an **in-memory ring buffer (max 100), lost on a SimCore
   restart** — it is diagnostics, not an audit log. `Agent` rows themselves
   persist in the DB and survive a restart.

## 3. `?mode=service` is the default — `foreground` blocks on purpose

Verified from source (both variants fetched and diffed live):

- **`?mode=service` (default).** Installs a supervised **systemd unit**
  (Linux, `Restart=always`) or **launchd job** (macOS) so the beacon
  survives the SSH session ending and a reboot — a POV runs for weeks, not
  for one open terminal. If no supervisor is available at all, it degrades
  honestly to `setsid`+`nohup` and reports `DEGRADED_NO_SUPERVISOR` — it
  never claims a service exists when one doesn't.
- **`?mode=foreground` blocks by design.** The installed script's own tail,
  verbatim from a live fetch with `mode=foreground` baked in:

  ```bash
  if [ "$MODE" = "foreground" ]; then
    cs_report run OK "foreground mode"
    cs_say "running in the FOREGROUND (Ctrl-C to stop) — dies with this session, demo use only"
    exec "$BIN" --server "$SERVER" --id "$AGENT_ID" --interval "$INTERVAL"
  fi
  ```

  That `exec` replaces the shell — the command never returns. Reserve
  `foreground` for a throwaway container or a one-off manual check where you
  intend to watch it run; **piping it into a backgrounded/detached shell is
  the only sane way to use it unattended** (the harness in
  `deploy/tier-d/run-tier-d.sh` does exactly that via `docker exec -d`).
  Running it attached on a jumpbox you meant to walk away from is the
  mistake that looks like a hang.

## 4. Confirming the beacon is live

```bash
curl -fsS 'http://localhost:8888/api/agents' | python3 -c '
import sys, json
for a in json.load(sys.stdin)["agents"]:
    print(a["agent_id"], a["status"], a["os"], a["last_seen_age_seconds"], "s ago")'
```

`status` is `online` (< 30 s) / `stale` (< 5 min) / `offline` (≥ 5 min),
derived from `last_seen` age — SimCore is pull-model and never dials the
target, so this is a liveness inference, not a probe. A background sweep
recomputes it every 30 s and emits `agent.status` SSE frames when it flips.

**Console path (verified read-only, code unchanged):** `TargetsView.jsx`'s
Deploy-agent panel is the same flow — mint token → copy token → copy
one-liner — with a copy-to-clipboard button on both the token
(`data-testid="deploy-token"`) and the one-liner
(`data-testid="deploy-one-liner"`), and a loopback guard: if the console's
own origin is `localhost`/`127.0.0.1`/`[::1]`/`0.0.0.0`, it warns before you copy
that the baked `SERVER` will resolve to the *jumpbox itself* once pasted
there, not back to SimCore. The five-stop first-run tour's final stop
anchors on this exact control (`data-tour-id="agent-enroll"`, stop id
`enroll` in `ui/src/components/onboarding/tourStops.js`) — "Deploy one now
… Mint an enrollment token and run the single line it gives you on the
target host. SimCore assigns the agent id." Deploy button, panel open/close,
and both copy buttons all read correctly from source; nothing here needed a
wiring fix.

## 5. The loopback trap (the other silent-failure mode)

If the console is open at `http://localhost:8888` (the common case right
after `scripts/dev-up.sh`), the address baked into the one-liner will be
`localhost` too. Pasted on a **remote** jumpbox, `localhost` resolves to the
jumpbox itself — the beacon "installs successfully," calls a SimCore that
isn't there (or is a different, unrelated service on that port), and the
agent roster on your actual SimCore stays empty with no error to look at.
`agentInstallUrl()` refuses to *derive* a routable address on its own; the
console instead warns at copy time (§4). Fetch the one-liner from the
control plane's real address:

```
http://<simcore-host-or-ip>:8888/api/agents/install?os=linux
```

## 6. The false-negative trap this deployment step exists to close

**A bare `ubuntu:22.04` (or most default cloud images) cannot run this
corpus, and the failure looks exactly like a Cortex miss.**

Every non-`root`/non-`direct` TTP step runs under the **identity harness**
(`runuser -l <account>`, falling back to `sudo -u` then `su`) so XDR sees a
realistic process-causality chain instead of everything running as the
beacon's own account. A stock Ubuntu ships `www-data` with
`/usr/sbin/nologin` and no `/var/www`. `runuser -l www-data` then dies with
*"This account is currently not available"* — **in about 7 ms, before the
technique does anything** — and the run simply reports `failed`. In a POV
report, an absent detection under that step reads as *"Cortex missed it."*
Nothing was ever executed for Cortex to miss. This was measured directly
against this repo before the fix: see `deploy/tier-d/TIER-D-RUN-REPORT.md`
§4 for the before/after transcript (`www-data:…:/usr/sbin/nologin` failing
in 7 ms vs. the provisioned `www-data:…:/bin/bash` succeeding).

**Reference for what a target actually needs:**
`deploy/tier-d/Dockerfile.target`. Its header states the identity counts
measured from the real corpus, not guessed — `container-runtime` 249 · `root` 152 · `svc-backup` 117 ·
`www-data` 104 · `node` 11 · `administrator` 10 · `user` 7 ·
`runner` 6 · `app` 6 · `vertex-agent` 5 · `developer` 1 · `nobody` 1 — — all of them now DECLARED in spec/identity_harness.json and enforced
at load time by S-17 — and it provisions
each with a login shell (`/bin/bash`) and a real home directory, plus the
toolchain the Linux steps invoke (`binutils`, `procps`, `iproute2`, `curl`).
Two things worth pointing out about it directly, because they change how you
read it:

- `python3` is **deliberately absent**. It exists to keep proving the
  *other* half of this trap honestly (§6.1) rather than papering over it —
  do not "fix" a customer jumpbox by copying this Dockerfile's package list
  verbatim; provision identities, and let the interpreter gap (below) prove
  itself or not on the real host.
- The three identities that need **no** provisioning at all —
  `root`, `container-runtime`, `direct` — are `spec/identity_harness.json`'s
  `direct_identities`: no impersonation wrapper is used, so a nologin shell
  can't break them.

### 6.1 Two preflight layers exist. Only one is wired to a real check.

| Gap type | Wired preflight? | Endpoint / check |
|---|---|---|
| Missing **interpreter** (e.g. a step needs `python` and the target has none) | **Yes** | `GET /api/agents/{agent_id}/preflight?scenario_id=<id>` |
| Missing/broken **identity** (nologin, no home dir, account absent) | **No — do it by hand** | see below |

**The interpreter preflight, verified live** against a real online agent:

```bash
curl -fsS 'http://localhost:8888/api/agents/bd790i-24719f/preflight?scenario_id=SIM-EDR-001'
# -> {"agent_id":"bd790i-24719f","scenario_id":"SIM-EDR-001","ready":true,
#     "gaps":[],"agent_interpreters":["node","perl","python"]}
```

It compares a scenario's declared per-step `requires_interpreters` (today:
1 scenario, `SIM-EDR-001` step-05, `["python"]` — see
`scenarios/_schema.yml` for the field) against the agent's advertised
roster, itself reported honestly by the beacon at registration
(`executor.AvailableLogicalNames()`). **This is advisory, not the
enforcement** — the roster is a snapshot that can go stale. The real
enforcement is beacon-side, at the moment of execution: a step whose
`requires_interpreters` cannot be satisfied is **never executed** and
reports a distinguishable, non-maskable result instead. Verified live from
an actual run against the Tier-D target (which deliberately lacks python3):

```json
"runtime_dependency_gaps": [{"step_id": "step-05", "missing": ["python"]}]
```

and, in that same run's step output:

```
=== STEP 5/5 · step-05 · T1003 · identity=root ===
!! RUNTIME_DEPENDENCY_MISSING: python — this step's command was NOT
   executed, so it produced no signal. This is an ENVIRONMENT gap, not a
   detection result.
--- exit_code=127 duration=0s ---
```

`GET /api/runs/{run_id}` (the UUID `run_id`, not the numeric `id`) always
carries `runtime_dependency_gaps` (`null` = never checked, `[]` = checked
and clean) and `runtime_install_authorized` — check both before reading a
`failed` run as a detection result. Full mechanism:
`docs/design/agent-runtime-dependencies.md`.

**No equivalent preflight exists yet for identity readiness.** Until one
does, run this by hand before the first launch against a new jumpbox —
pull the identities a scenario actually needs straight from the API (no
repo checkout required):

```bash
curl -fsS 'http://localhost:8888/api/scenarios/SIM-EDR-001' | python3 -c '
import sys, json
d = json.load(sys.stdin)
ids = sorted({s["identity"] for s in d["steps"] if s.get("identity")
              and s["identity"] not in ("root", "container-runtime", "direct")})
print("non-direct identities this scenario needs:", ids)'
# -> non-direct identities this scenario needs: ['www-data']
```

then, **on the target jumpbox itself**, confirm each one has a real shell
and home directory:

```bash
for u in www-data; do
  getent passwd "$u" || { echo "$u: ACCOUNT MISSING"; continue; }
done
```

A shell field of `/usr/sbin/nologin`, `/sbin/nologin`, or `/bin/false` — or
`ACCOUNT MISSING` outright — means this scenario **will** fail with an
ENVIRONMENT-class error the moment it reaches that step, not a real
detection outcome. Fix it the same way `Dockerfile.target` does
(`usermod --shell /bin/bash --home /var/www www-data; install -d -o
www-data -g www-data /var/www`), or provision the jumpbox from that
Dockerfile directly if it's disposable.

### 6.2 Reading a failed run without misreading it

Don't classify a failed step by its exit code alone. This repo's Tier-D
harness (`deploy/tier-d/classify.py`) names three classes, and the
vocabulary is worth carrying into a live engagement even though the script
itself is a lab-only tool:

| Class | Meaning | Counts against CortexSim? |
|---|---|---|
| **ENGINE** | the beacon/orchestrator/identity harness itself broke — a real CortexSim defect | Yes |
| **ENVIRONMENT** | the target couldn't support the step (no login shell, missing tool, no egress) — **the TTP never ran** | No — fix the target, relaunch |
| **TTP** | the technique ran for real and legitimately did not succeed | No — this is real signal |

`classify.py`'s `ENVIRONMENT_PATTERNS` are real strings observed from real
failures, not guesses — `"This account is currently not available"` (no
login shell), `"runuser: warning: cannot change directory to"` (no home
dir), `"runuser: user … does not exist"`, `"command not found"` (missing
tool), `"Could not resolve host"` (no egress the step needed). If a step's
stored `output` (in the run record — §6.1's `GET /api/runs/{run_id}`)
contains any of these, that step is ENVIRONMENT, not a Cortex miss,
regardless of what the overall `Run.status` says. `Run.status=failed` is
SimCore's own lifecycle field — a non-zero step terminates the run there —
and is deliberately a different concept from "did Cortex miss a real
technique."

## 7. Windows and the identity harness

Windows pull mode is real (§ the project CLAUDE.md's Windows-execution
section) but its identity story is different in kind, not just detail:
Windows has no credential-free unattended impersonation, so every
non-`direct` identity **collapses to `direct`** and the run record carries
an explicit `!! IDENTITY NOT HONOURED:` marker (`agent/beacon/client.go`'s
`identityDegradedMarker`) rather than silently running everything as the
beacon account and calling it done. A Windows beacon registers capabilities
`["shell", "powershell", "artifact-fetch"]` (`agent/capabilities.go`) —
notably **not** `identity-harness`, deliberately, so SimCore's own agent
list never implies a Windows target can honour a scenario's declared
`identity:`. If a Windows scenario's realism depends on running as a
specific service account, that dependency is not satisfiable today; the run
record says so rather than presenting a `direct`-identity execution as the
requested one.

**Console discrepancy found while verifying this (not fixed here — read-only
per this task's file ownership, reported for the console owner):** the
Deploy-agent panel shows a static warning when you select Windows — *"No
Windows beacon ships yet — this script refuses before it enrolls
(`WINDOWS_AGENT_UNAVAILABLE`)"* — unconditionally, without ever calling
`GET /api/agents/binaries` to check. On this stack today that's wrong: the
binary is staged (§2) and a live fetch of the generated script
(`GET /api/agents/install?os=windows`) contains **zero** occurrences of
`WINDOWS_AGENT_UNAVAILABLE` — confirmed by grep against the actual bytes
returned. `WINDOWS_AGENT_UNAVAILABLE` is a real, correctly-conditional
backend code (fires only when `_binary_path("windows","amd64")` is missing
from the deployed image) — the bug is the console showing it as always-true
copy instead of checking. Trust `GET /api/agents/binaries` over that console
message. **Separately**, the same panel's loopback warning cites a code,
`INSTALLER_SERVER_LOOPBACK`, that does not exist anywhere in
`core/api/agents.py` — confirmed by grep across the whole tree (Python, Go,
JS/JSX). `_resolve_server()`'s own docstring is explicit that a loopback
address is deliberately **not** refused server-side (installing on the
SimCore host itself is a legitimate flow the server can't distinguish from a
jumpbox paste). The warning's actual advice — reopen the console on a
routable address before copying — is correct and worth keeping; only the
"the install endpoint refuses this too" clause is fiction.

## 8. Uninstall

```bash
curl -fsSL 'http://<simcore-host>:8888/api/agents/install?uninstall=1' | bash
```

Idempotent — safe to run against a host with no beacon installed.

## 9. Honest limits that apply to this step specifically

- **`tenant-verified is 0`** — deploying and enrolling a beacon proves
  nothing about detection efficacy on its own; it only proves signal
  generation is possible. See the connector preflight
  (`POST /api/connectors/{kind}/preflight`) before quoting any tenant claim.
- **A clean interpreter/identity check is not proof a run will succeed** —
  both are snapshots (interpreter roster at last registration; identity
  check run once by hand). The beacon's live, per-execution check is what
  actually enforces "never present as success," not either preflight.
- **`GET /api/agents/install/attempts` is not durable.** It resets on a
  SimCore restart. If you need a permanent record of who deployed what,
  correlate against `GET /api/agents` (`registered_at`) instead.

## References

- `docs/design/agent-runtime-dependencies.md` — the interpreter-gap design,
  full evidence, and every code path that enforces it.
- `deploy/tier-d/Dockerfile.target` — the reference provisioned target.
- `deploy/tier-d/TIER-D-RUN-REPORT.md` §4 — the original www-data/nologin
  measurement, before and after.
- `deploy/tier-d/classify.py` — the ENGINE/ENVIRONMENT/TTP taxonomy,
  pattern-matched against real failure strings.
- `spec/identity_harness.json` — the canonical identity list and resolution
  rules shared by push and pull.
- `docs/reference/api-and-agent-surface.md` §1.6 — the full agent endpoint
  table, request/response shapes, and every install stage code.
- `ui/src/components/console/TargetsView.jsx` — the console's Deploy-agent
  panel; `ui/src/components/onboarding/tourStops.js` — the first-run tour
  stop that anchors on it.
