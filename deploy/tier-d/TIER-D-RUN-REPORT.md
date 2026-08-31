# Tier-D pull-mode agent-path validation — run report (2026-08-30, HISTORICAL)

> **HISTORICAL RECORD — DO NOT QUOTE §1.2 OR §2 AS CURRENT BEHAVIOUR.**
> Flagged by the 2026-08-31 pre-merge whole-branch review: **§1.2 below
> documents a fix ("added `python3` to `Dockerfile.target`'s apt-get install
> list") that was deliberately REVERTED the same day it was written.**
> `deploy/tier-d/Dockerfile.target`'s own header comment says so verbatim —
> python3 is now *deliberately* absent from that image, because installing it
> there "makes the lab pass while fixing nothing on a customer host, whose
> packages this repo does not control." The real fix that replaced it lives in
> `agent/beacon/client.go::resolveRuntimeDeps` (a step declaring
> `requires_interpreters` is checked against the target's REAL PATH before it
> runs, and is refused — never silently masked — when the interpreter is
> genuinely absent). See `docs/design/agent-runtime-dependencies.md` for that
> design and `docs/design/tier-d-classifier-fixes.md` for the 2026-08-31 pass
> that closed the classifier-side gaps this exposed (C1/C2/I1/I2/I3).
>
> Consequently **§2's "verbatim" verdict output below is also stale**: it
> shows step 5 as `exit=0 OK` (from the now-reverted python3-in-the-image
> state). A genuine post-fix re-run on 2026-08-31 produces step 5 as
> `exit=127 ENVIRONMENT` (`RUNTIME_DEPENDENCY_MISSING`) instead — see
> `docs/design/tier-d-classifier-fixes.md` §"Real harness re-run" for that
> verbatim, current output. Sections 1.1, 1.3, 1.4, 1.5, 3 (steps 1-4), 4, 5,
> 6 and 7 are unaffected by the revert and remain accurate as a historical
> record of that day's work.

**Date:** 2026-08-30
**Scenario:** `SIM-EDR-001` (Credential Dumping — /etc/shadow and Mimipenguin)
**Harness commit at start:** `dcdb6b3` (never executed before this session)
**Environment:** local Docker (`DOCKER_CONTEXT=default`), SimCore already running as
`cortex-pov-engine-simcore-1` at `http://localhost:8888`

---

## 1. What broke on first run, and what was fixed

### 1.1 ENGINE-class bug in the harness itself: agent-selection raced onto a stale, unprovisioned agent

First run (`run-tier-d.sh --scenario SIM-EDR-001 --keep`) reported:

```
✓ beacon online: bd790i-876696
launching SIM-EDR-001 in pull mode against bd790i-876696
✓ terminal status: failed  (run.json saved)
  ~ step 1 [www-data] T1087.001  exit=1  ENVIRONMENT
      └─ identity has no login shell on the target (nologin)
```

Root cause: this Docker host already had an **unrelated, leftover container**
(`cxs-target`, a bare `ubuntu:22.04` from an earlier, unfinished manual attempt at
this same task) with a beacon still alive and heartbeating. Because the harness
runs its target with `--network host`, the container inherits the **host's real
kernel hostname** (`bd790i`) rather than getting a random per-container one, so
`agent_id` (`f"{hostname}-{token_hex(3)}"`, `core/api/agents.py:1158`) only differs
from a totally unrelated container's agent by a 6-hex-char random suffix. The
original selection logic —

```python
live = [x for x in a if x.get("status") == "online"]
print(live[0]["agent_id"] if live else "")
```

— picks "the first online agent" with no correlation to the enrollment this run
just performed. It raced onto the leftover, unprovisioned `cxs-target` beacon
instead of the freshly built, provisioned `cortexsim-tier-d-target`. The step-1
failure was real (that OLD container genuinely lacked the identity fix) and
`classify.py` classified it correctly (ENVIRONMENT, not ENGINE) — but the harness
had silently tested the wrong host, which would have produced a false
"provisioning didn't fix it" reading.

**Fix** (`deploy/tier-d/run-tier-d.sh`): correlate the agent id via
`GET /api/agents/install/attempts`, which the real installer POSTs to
(`stage=run code=OK agent_id=<id>`) the moment it starts the beacon. We now record
`INSTALL_START_TS` immediately before running the installer, then take the
newest attempt with `stage=="run" && code=="OK"` whose `reported_at >=
INSTALL_START_TS` — i.e. the record produced by *this* install, not any other
beacon that happens to be alive on the host — with a belt-and-suspenders check
that the correlated id is actually `status=="online"` in `/api/agents` before
trusting it.

After the fix, three consecutive clean runs each correlated a distinct,
freshly-enrolled agent id (`bd790i-18c16a`, `bd790i-0093b4`, `bd790i-9125b6`,
`bd790i-b6db10`) rather than racing onto a stale one.

### 1.2 Real ENVIRONMENT gap in `Dockerfile.target`, masked as OK by the scenario's own fallback

With agent selection fixed, the harness ran against the correct target and step
5 (download + execute `mimipenguin.sh`) reported `exit_code=0` / class `OK`, but
its actual stdout was:

```
Error: No supported version of 'python' found in /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

`mimipenguin.sh` shells out to `python` internally; `Dockerfile.target`'s package
list never installed one. The step's own command ends in
`... | tee /tmp/mimi_output.txt || echo '[*] Mimipenguin execution complete'`,
and `tee` exits 0 regardless of what the earlier pipeline stage did, so the whole
line exits 0 no matter what mimipenguin does internally — the scenario's own
non-fatal fallback silently absorbed a real environment gap. This is exactly the
class of failure this harness exists to catch, just at one level removed: not
the identity harness this time, but a tool the step itself downloads and runs.

**Fix**: added `python3` to `Dockerfile.target`'s apt-get install list, with a
comment documenting why (measured on a real run) and the caveat that
`classify.py`'s exit-code-only classification cannot detect this class of
masked failure on its own — see §7.

Verified before/after on the same target:
- Before: `Error: No supported version of 'python' found`
- After: `MimiPenguin Results:` header — the real tool now runs to completion
  (it found no plaintext credentials on this synthetic box, which is the
  expected, legitimate TTP-class outcome: there is no `gnome-keyring`/`vsftpd`/
  etc. process on it for mimipenguin to scrape).

### 1.3 Exit-code plumbing: dead code after `set -e`, silently dropping the "artifacts saved" message on failure

`run-tier-d.sh` ended with:

```bash
python3 "${SCRIPT_DIR}/classify.py" ...
RC=$?
log "artifacts in ${RESULTS_DIR}"
exit $RC
```

Under `set -euo pipefail`, a plain (non-conditional) command that exits non-zero
terminates the script **on that statement** — `RC=$?` never executes. The final
exit code the shell reports happens to equal `classify.py`'s own exit code
either way (bash exits with the failing command's status), so this was not an
exit-code correctness bug, but it silently dropped the
`log "artifacts in ${RESULTS_DIR}"` line — the one message that tells an
operator where `verdict.json` landed — on precisely the run (an ENGINE-class
failure) where they most need to find it.

**Fix**: `python3 ... || RC=$?` (with `RC=0` initialized above it), so the
script always reaches the final log line and `exit $RC` regardless of
`classify.py`'s exit code. Verified directly (§5) that the message now prints
on both PASS and FAIL.

### 1.4 Minor: silenced a Python 3.14 deprecation warning polluting stdout

`INSTALL_START_TS` acquisition used `datetime.utcnow()` (deprecated on the host's
Python 3.14, though not on the target's/image's 3.11). Kept `utcnow()`
deliberately — it must stay **naive UTC** to lexically compare against the
server's own `datetime.utcnow().isoformat()` timestamps in
`/api/agents/install/attempts` (a timezone-aware `+00:00`-suffixed string would
compare unreliably against the server's un-suffixed one) — and just added
`python3 -W ignore` so the warning doesn't clutter the harness's own output.

### 1.5 Housekeeping

Added `deploy/tier-d/.gitignore` (`results/`), mirroring the existing
`deploy/tier-c/.gitignore` convention — the harness's own output directory was
untracked and would otherwise be one `git add -A` away from getting committed.

---

## 2. Final verdict output, verbatim

```
[tier-d] ✓ SimCore reachable at http://localhost:8888
[tier-d] building the provisioned target image (bare ubuntu cannot run this corpus)
[tier-d] ✓ target 'cortexsim-tier-d-target' up
[tier-d] minting an enrollment token
[tier-d] running the real installer one-liner on the target (detached)
[tier-d] waiting for the beacon to check in
[tier-d] ✓ beacon online: bd790i-b6db10
[tier-d] launching SIM-EDR-001 in pull mode against bd790i-b6db10
[tier-d] ✓ run 99f3ca78-2445-45ce-9eaf-b514a9285126
[tier-d] waiting for the run to reach a terminal state
[tier-d] ✓ terminal status: complete  (run.json saved)

  scenario        SIM-EDR-001
  run status      complete   tc_verdict=pending
  steps           5 reported / 5 declared

   ✓ step 1 [www-data] T1087.001  exit=0  OK
   ✓ step 2 [www-data] T1003.008  exit=0  OK
   ✓ step 3 [www-data] T1552.001  exit=0  OK
   ✓ step 4 [root] T1003  exit=0  OK
   ✓ step 5 [root] T1003  exit=0  OK

  OK 5 · ENVIRONMENT 0 · TTP 0 · ENGINE 0

  HARNESS PASS — full pull-mode lifecycle exercised end to end.
[tier-d] artifacts in /home/henry/Github/cortex-pov-engine/deploy/tier-d/results/SIM-EDR-001
[tier-d] ! leaving target container 'cortexsim-tier-d-target' up (--keep)
```

Script exit code: `0`. This exact sequence — enroll token → real installer
one-liner → server-assigned agent id → correlated (not guessed) agent selection
→ consent-gated launch → poll to terminal → classify — was reproduced across
four consecutive clean runs after the fixes landed, each with a freshly built
target and freshly enrolled agent.

`tc_verdict=pending` is expected and correct, not a defect: `SIM-EDR-001`
declares an `MTTD` threshold, which only resolves once a connected Cortex tenant
observation matches the seeded `Result` rows (the measurement loop,
`core/connectors/`). No tenant is connected in this environment — per the repo's
own stated posture, **tenant-verified is 0** everywhere, and this run does not
change that.

---

## 3. Per-step classification for SIM-EDR-001

| step | identity | technique | exit | class | judged correct? |
|---|---|---|---|---|---|
| 1 — read /etc/passwd | www-data | T1087.001 | 0 | OK | **Yes.** Genuine `cat /etc/passwd \| grep ...` under a real `runuser -l www-data` shell; enumerated the 14 login-shell accounts provisioned by `Dockerfile.target`. |
| 2 — read /etc/shadow | www-data | T1003.008 | 0 | OK | **Yes.** Real non-root permission denial (`cat /etc/shadow` fails as a non-root user should), scenario's own fallback message printed. This is the technique legitimately not succeeding under a correctly-enforced permission boundary — real signal, not a masked failure. |
| 3 — credential file sweep | www-data | T1552.001 | 0 | OK | **Yes.** Real `find` sweep across `/home`, `/root`, `/tmp`, `/var/tmp`; found nothing (the synthetic bait files live under different home dirs — `svc-account`/`svc-backup`/`user`/`developer` — not the ones this step searches), a legitimate empty-result outcome. |
| 4 — /proc memory scrape | root | T1003 | 0 | OK | **Yes.** Real `/proc/<pid>/{maps,environ}` reads against live `sleep`-holder processes (`tier-d-processes.sh`); actually surfaced a live `CORTEXSIM_TOKEN=...` value from the beacon's own install-shell environment — genuine matchable credential-shaped data, not a fabricated echo. |
| 5 — mimipenguin download+exec | root | T1003 | 0 | OK | **Yes, after the Dockerfile fix (§1.2).** Downloaded the real script from `raw.githubusercontent.com` (egress worked) and executed it for real once `python3` was provisioned; found no plaintext credentials, which is the correct outcome for a box with no exploitable cred-bearing service running. Before the fix this same exit-0/OK label would have been **technically non-misleading per classify.py's own contract** (exit code really was 0) but was masking a real environment gap — see §7 for the general limitation this exposes. |

All five step classifications are judged correct for the run that actually
executed. Zero ENGINE-class steps, zero unreported steps.

---

## 4. Did provisioning fix the www-data defect?

**Yes — reproduced the original defect and the fix side by side, on this run:**

```
=== BARE ubuntu:22.04 (the original defect) ===
www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
runuser: warning: cannot change directory to /var/www: No such file or directory
This account is currently not available.

=== PROVISIONED tier-d target (cortexsim-tier-d-target) ===
www-data:x:33:33:www-data:/var/www:/bin/bash
www-data
/var/www
```

The bare image reproduces the exact reported message verbatim
(`runuser: warning: cannot change directory to /var/www: ...` /
`This account is currently not available.`). The provisioned target's
`runuser -l www-data -c 'whoami; pwd'` succeeds cleanly, landing in the now-real
`/var/www` home directory. Steps 1–3 of `SIM-EDR-001` (§3) then executed real
commands under that identity end to end via the full pull-mode lifecycle — not
just the isolated `runuser` check — confirming the fix holds under the actual
beacon/orchestrator path, not only in a manual docker exec.

---

## 5. ENGINE-class negative control

Constructed a synthetic `run.json` fixture (not a real run — `classify.py` has
no dependency on the live system, so this exercises the classifier in
isolation) with step 2's body containing a Python-style traceback signature
(`ENGINE_PATTERNS` in `classify.py` matches
`Traceback \(most recent call last\)|panic: |runtime error:`):

```
=== STEP 2/5 · step-02 · T1003.008 · identity=www-data ===
--- STDOUT ---
Traceback (most recent call last):
  File "/app/agent/beacon/client.go", line 905, in resolveIdentity
    panic: identity harness: nil pointer dereference resolving spec for 'www-data'
--- exit_code=1 duration=3ms ---
```

Output:

```
  scenario        SIM-EDR-001
  run status      failed   tc_verdict=pending
  steps           2 reported / 5 declared   (3 never reported — run stopped early)

   ✓ step 1 [www-data] T1087.001  exit=0  OK
   ✗ step 2 [www-data] T1003.008  exit=1  ENGINE
       └─ an unhandled exception in the engine or beacon

  OK 1 · ENVIRONMENT 0 · TTP 0 · ENGINE 1

  HARNESS FAIL — an ENGINE-class failure means CortexSim itself is broken.
EXIT CODE: 1
```

Confirms the classifier and the harness's exit-code plumbing can both actually
fail, not just always PASS. This is now a permanent regression test —
`test_classify_engine_negative_control_fails` in
`tests/e2e_isolated/test_tier_d_agent_path.py` — plus three sibling fixtures
(`PAYLOAD_PIN_MISMATCH` → ENGINE, `command not found` → ENVIRONMENT, the exact
`www-data`/nologin message → ENVIRONMENT) so the taxonomy is pinned by tests
going forward rather than eyeballed on a terminal once.

---

## 6. Engine defects found in `core/` or the beacon

**None.** Every defect found and fixed in this pass lives in the newly-written
harness itself (`deploy/tier-d/run-tier-d.sh`, `deploy/tier-d/Dockerfile.target`)
— none of it required touching `core/` or `agent/`. Per the task's constraint,
had a genuine engine defect turned up it would be reported here rather than
quietly patched; the pull-mode lifecycle (enroll → poll → identity-harness
execution → per-step output → complete) behaved correctly against a properly
provisioned target across four consecutive clean runs.

One observation worth flagging as a **possible hardening item, not a defect**:
`register_agent`'s `agent_id = f"{hostname}-{secrets.token_hex(3)}"`
(`core/api/agents.py:1158`) makes agent ids collide-adjacent whenever two
targets report the same `hostname` (as happens naturally under
`--network host`, or more generally on any fleet where multiple ephemeral
jumpboxes share a hostnaming convention) — nothing in `/api/agents` marks
"this is the enrollment I just performed" without the correlation trick this
harness now uses. This is not a bug (agent ids are still unique; the harness
bug was on the *caller* side, picking one without correlating), but a
console/API consumer doing the same "pick the first online agent" thing this
harness's first draft did would hit the exact same failure mode.

---

## 7. What is still not covered

- **No Windows target.** This harness only builds/runs a Linux (`ubuntu:22.04`)
  target; the repo's own Windows pull-mode path (`GOOS=windows` beacon,
  `powershell.exe` execution) is unexercised by Tier-D.
- **Only one scenario proven end-to-end** (`SIM-EDR-001`). The provisioning in
  `Dockerfile.target` covers the identity/toolchain footprint measured across
  the *whole* corpus (per its header comment: `www-data 103 · svc-account 80 ·
  svc-backup 26 · node 11 · user 7 · runner 6 · app 6 · vertex-agent 5 ·
  developer 1 · nobody 1`), but this pass only ran the one reference scenario
  through the live lifecycle. The identity-provisioning breadth is
  provisioned, not proven, for the other 168 scenarios.
- **`classify.py`'s exit-code-only classification cannot see failures a
  scenario's own shell masks behind `|| echo` / `tee` non-fatal fallbacks**
  (§1.2). It correctly classifies a step by its OWN exit code and stdout
  signatures, but a scenario author who writes `real-tool || echo done` gets an
  "OK" from this harness even when `real-tool` failed internally, as long as
  the fallback prints normal completion text with no matching ENGINE/ENVIRONMENT
  signature. This is inherent to reading harness output text, not a bug in this
  pass's fixes, and is worth a follow-up if more scenarios are pulled into
  Tier-D coverage.
- **Consent/dual-use adapter path not exercised for a REFUSAL.** This run
  always supplied `simulation_authorized:true`; the harness never proves the
  `CONSENT_REQUIRED` 409 refusal path actually refuses when the flag is
  omitted (the task's context confirms it works from prior manual testing, but
  Tier-D itself doesn't assert it).
- **Payload-shelf-backed tier-4 tools not exercised.** `SIM-EDR-001` doesn't
  reference a shelf-backed adapter (`atomic-red-team` is `install_inline:
  false`/`type: script`, mimipenguin is a direct download), so this pass
  proves nothing about `PAYLOAD_NOT_STAGED`/`PAYLOAD_PIN_MISMATCH` on a real
  run (only via the synthetic fixture in §5).
- **Single-run, single-host.** No concurrency test (two scenarios / two agents
  enrolling against the same SimCore at once) — a scenario where the
  install-attempts-correlation fix (§1.1) could plausibly race again under
  truly concurrent enrollments from hosts sharing a hostname, though the
  `reported_at >= INSTALL_START_TS` + explicit online-status check narrows
  that window considerably versus the original code.

---

## Artifacts

- `deploy/tier-d/results/SIM-EDR-001/run.json` — full run record (gitignored)
- `deploy/tier-d/results/SIM-EDR-001/verdict.json` — classifier output (gitignored)
- `tests/e2e_isolated/test_tier_d_agent_path.py` — 5 pure classifier tests
  (always run, no docker) + 1 docker+SimCore-gated e2e test (skips cleanly
  when either is unavailable); all 6 pass locally, and the 5 pure tests were
  additionally verified inside the prod image (`cortex-pov-engine-simcore:latest`,
  Python 3.11.15) to match how this repo's CI actually runs `pytest`.
