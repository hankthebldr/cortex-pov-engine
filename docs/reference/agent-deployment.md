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
default), and that puts a live credential in the URL, in the served script
and in any proxy access log the customer keeps. `?os=linux` with no `token`
query param bakes `TOKEN="${CORTEXSIM_TOKEN:-}"` — empty — so the env-var
form is the one that keeps the token out of all three. It does **not** keep
it out of shell history: the pasted line, token included, is recorded by an
interactive bash unless it starts with a space and `HISTCONTROL` ignores such
lines (corrected 2026-11-10; this paragraph used to say otherwise). Mint
single-use, short-TTL tokens, as above. This is why the console's
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

## 7. Windows: service install, identity, and what is unproven

> **Run once in CI; not on a customer host.** The elevated install, a pull
> run, an abort and the uninstaller ran green once in the `windows-2022` CI job
> (run 42933820773 at `d7f4f29`): a GitHub-hosted Server 2022 runner with
> Windows PowerShell 5.1, an elevated admin, Defender real-time protection off,
> no XDR agent, SimCore at `127.0.0.1`, and CI probe scenarios. The rest is
> read from the installer and beacon source and run under pwsh 7.6 on macOS:
> the non-elevated and per-user paths, a SimCore reached by name or over
> https, a host with XDR, reboot, and every round-2 change after that run. The
> full contract, including every stage code, is in
> `docs/reference/api-and-agent-surface.md` §1.6.1 and §10.

**Install from an elevated Windows PowerShell.** This is the console's line,
with the TLS 1.2 opt-in Windows PowerShell 5.1 needs for an https SimCore:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; $env:CORTEXSIM_TOKEN='cxs_…'; iwr -useb "http://<simcore-host>:8888/api/agents/install?os=windows" | iex
```

- **Elevated:** registers the `CortexSimAgent` service (LocalSystem,
  auto-start). The exe is at `C:\ProgramData\CortexSim\cortexsim-agent.exe`
  and the log at `%ProgramData%\CortexSim\agent.log`. Until this pass the
  beacon had no SCM handler, so by the SCM contract every elevated install
  would have died with 1053 about 30 s after reporting success (inferred and
  simulated, never observed). `OK` now means Running, still Running 35 s
  later, and `last_seen` advanced. In CI the service was still Running 75 s
  after install, and the one timed install took about 47 s; up to about 70 s
  is the installer's waits added up, not a measured range. Check it with
  `sc.exe query CortexSimAgent` (`STATE: 4 RUNNING`).
- **Not elevated:** refused with `NOT_ELEVATED` before anything is enrolled.
  `$env:CORTEXSIM_ALLOW_UNSUPERVISED='1'` gives a per-user beacon in
  `%LOCALAPPDATA%\CortexSim` instead. It **dies at logoff and at reboot**,
  cannot run admin-only TTPs, and ends `DEGRADED_NO_SUPERVISOR`.
- **Pre-staging and proxies:** `CORTEXSIM_BIN` names a pre-staged `.exe`,
  digest-checked when SimCore serves one, else `CHECKSUM_SKIPPED`.
  `CORTEXSIM_NO_PROXY=1` bypasses the system proxy per request. The service
  runs as SYSTEM and does not inherit your proxy, so the **beacon**, not your
  shell, must reach SimCore. The beacon does not read the Windows system
  proxy (WinINET / WinHTTP, PAC, WPAD). Behind a proxy, set a machine-level
  `CORTEXSIM_AGENT_PROXY` (the env form of the beacon's `--proxy`; the
  installer does not yet write the flag into the service). The beacon's
  startup log names its route to SimCore.
- **No foreground mode on Windows.** `?mode=foreground` is `400 BAD_MODE`, and
  `CORTEXSIM_SRC`, `CORTEXSIM_BIN_DIR` and `CORTEXSIM_LOG` are POSIX-only.
- **Without the cradle.** The console also offers a download-then-run path:
  the installer link with your SimCore address baked in, the beacon link, its
  digest, a `Get-FileHash` line that must print `True`, and a run line that
  sets `CORTEXSIM_TOKEN` and `CORTEXSIM_BIN`. Not run on Windows.
- **A downloaded `.ps1`** carries Mark-of-the-Web. Run it as
  `Unblock-File .\install-cortexsim-agent.ps1; $env:CORTEXSIM_TOKEN='cxs_…'; powershell -NoProfile -ExecutionPolicy Bypass -File .\install-cortexsim-agent.ps1`.
- **Host names:** if `<simcore-host>` is a DNS name, SimCore's
  `CORTEXSIM_ALLOWED_HOSTS` must list it, or every poll is
  `400 HOST_NOT_ALLOWED`.
- **AV/EDR:** the beacon is **unsigned**, and `iwr | iex` is a download cradle.
  Cortex XDR or Defender in prevent mode may block it or quarantine the exe
  (`BINARY_QUARANTINED`, `PROCESS_START_BLOCKED`). Get an exclusion for the exe
  path and its published sha256 before the POV.

**Identity collapses to the beacon's account.** Windows has no credential-free
unattended impersonation. Every non-`direct` identity **collapses to
`direct`**, and the run record carries an explicit `!! IDENTITY NOT HONOURED:`
marker (`agent/beacon/client.go`'s `identityDegradedMarker`); the beacon never
silently runs everything as itself and calls it done. Under the service that
account should be `NT AUTHORITY\SYSTEM` (it is registered as LocalSystem; no
run has asserted the account yet). Each Windows step frame now carries
`[cortexsim] executed as <account> (elevated token | token NOT elevated)`, so
the run record will say. `root`, `container-runtime` and
`administrator` on a non-elevated token read `… requires an elevated token`.
A Windows beacon registers `["shell", "powershell", "artifact-fetch"]`, plus
`elevated` only when its token is elevated (`agent/capabilities.go`). It is
**not** `identity-harness`, deliberately, so SimCore's agent list never implies
a Windows target can honour a scenario's declared `identity:`. If a Windows
scenario's realism depends on running as a specific service account, that
dependency is not satisfiable today, and the run record says so.

**Only Windows-resolvable steps run.** A pull launch whose steps the target's
OS cannot run is refused `409 PULL_TARGET_UNSATISFIABLE`. A step the beacon
cannot run is recorded `!! STEP NOT RUN: PLATFORM_UNSATISFIABLE` and scored
`not_executed`. 13 scenarios resolve for Windows (the same 13 have a Windows
push target) and 69 carry a Windows step label (recounted 2026-11-10). There
is no Windows canary. A step's `required_binaries` now yield `TOOL_ABSENT` on
Windows, and a missing or alias-only `requires_interpreters` entry blocks the
step; no current Windows step declares either.

**Console (resolved 2026-11).** The two discrepancies this section used to
report are fixed:
- The Deploy-agent panel reads the shelf (`agent_binaries`) instead of always
  saying "No Windows beacon ships yet".
- The loopback warning no longer cites the non-existent
  `INSTALLER_SERVER_LOOPBACK`.

The panel also polls `GET /api/agents/install/attempts` every 5 s while open.
It shows an installer-only enrolment as "enrolled — waiting for first beacon
poll", never online, and offers per-OS uninstall lines.

## 8. Uninstall

Linux / macOS, as the user who installed the beacon:

```bash
curl -fsSL 'http://<simcore-host>:8888/api/agents/install?uninstall=1' | bash
```

Use `| sudo bash` for a root install; the installer's closing line says which
one you have. A root run also removes user-mode installs under `SUDO_USER` and
under `/home` / `/Users`, and ends `UNINSTALL_INCOMPLETE` if it cannot stop that
login's unit. A non-root run removes its own install and fails `NEEDS_ROOT`
naming any root items. (Driven with stubs under bash 5.3 and 3.2, not under a
real sudo or systemd.)

Windows, from an elevated Windows PowerShell (ran once, elevated, in CI:
"removed 2 of 2 item(s)"):

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; iwr -useb 'http://<simcore-host>:8888/api/agents/install?os=windows&uninstall=1' | iex
```

A per-user Windows beacon (`CORTEXSIM_ALLOW_UNSUPERVISED`) is removed by the
account that installed it, from a normal (not elevated) PowerShell. An
elevated run under another account reports that profile's files and ends
`UNINSTALL_INCOMPLETE`; it never deletes inside another user's profile.

Both are idempotent. They report what they **found** separately from what they
**removed**, remove the beacon's cache and staging roots (which hold staged
dual-use tools), and end `UNINSTALL_INCOMPLETE` if anything they found
survives. Deleting the agent in the console removes only the registry row; a
running beacon re-registers on its next poll. The delete is refused with
`409 AGENT_HAS_OPEN_RUNS` while a run is owed to or in flight on the agent:
the console shows the runs and an Abort for each. If the Windows host cannot
reach SimCore, use the manual fallback in `api-and-agent-surface.md` §1.6.1.

## 9. Honest limits that apply to this step specifically

- **`tenant-verified is 0`** — deploying and enrolling a beacon proves
  nothing about detection efficacy on its own; it only proves signal
  generation is possible. See the connector preflight
  (`POST /api/connectors/{kind}/preflight`) before quoting any tenant claim.
- **A clean interpreter/identity check is not proof a run will succeed** —
  both are snapshots (interpreter roster at last registration; identity
  check run once by hand). The beacon's live, per-execution check is what
  actually enforces "never present as success," not either preflight.
- **`GET /api/agents/install/attempts` is not durable.** It is an in-memory
  deque (100) and resets on a SimCore restart. If you need a permanent record of who deployed what,
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
