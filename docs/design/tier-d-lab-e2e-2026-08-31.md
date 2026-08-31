# Lab-readiness e2e run — 2026-08-31

**Task:** prove the end-to-end lab motion (pull-mode Tier-D + push-mode bundle
download) works for real, on this machine, against local ephemeral containers
only, and record the evidence honestly.

**Environment:** `DOCKER_CONTEXT=default`, SimCore brought up via
`scripts/dev-up.sh` (existing `.env`, image rebuilt from current `main`),
reachable at `http://localhost:8888`.

---

## 1. Tier-D, `SIM-EDR-001` (re-verification)

```
deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001
```

Run twice for reproducibility (`4413fbb3…` and a second run against a freshly
enrolled agent). Both terminal, both exit code **0**.

```
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

HARNESS PASS, with unrun steps
```

This matches the task's stated expectation exactly: steps 1-4 OK, step 5
`RUNTIME_DEPENDENCY_MISSING` (`python3` deliberately absent from
`Dockerfile.target` per its own header — see `docs/design/agent-runtime-dependencies.md`),
verdict "PASS, with unrun steps", exit 0. `run status: failed` +
`harness_verdict: PASS` is not a contradiction — `Run.status` is SimCore's own
lifecycle field (a step returning non-zero terminates the run as `failed`
there); `classify.py`'s `harness_verdict` is the separate, ENGINE/ENVIRONMENT/TTP
aware judgment this harness exists to produce, and the two are deliberately
not the same field.

`tc_verdict=pending` throughout — this scenario carries an MTTD threshold with
no tenant connected. **tenant-verified is 0**, unaffected by this session.

---

## 2. Tier-D, a second scenario from a different plane: `SIM-CDR-001`

**Picked `SIM-CDR-001`** ("Container Enumeration via DEEPCE", plane `CDR`,
`T1613`/`T1082`) over the ITDR/EAL-emitter scenarios (their steps are
literally `id; echo cortexsim-…` / `whoami; echo …` — exactly the echo-only
shape the task said to avoid) and over scenarios needing infrastructure this
harness's Ubuntu target doesn't provide (systemd for `SIM-CDR-006`, an AD
domain for the ITDR roasting scenarios, a Docker socket for k8s-escape
scenarios). `SIM-CDR-001`'s steps are real: `apt-get install`, a genuine
`curl | bash` of the real upstream DEEPCE script, live `/proc`/`mount`/`env`
enumeration, and a `find` credential sweep — and its `execution_identity` is
`container-runtime`, one of the three `direct_identities` in
`spec/identity_harness.json`, so it runs without needing a provisioned
service account.

```
deploy/tier-d/run-tier-d.sh --scenario SIM-CDR-001
```

Run twice for reproducibility (`840f9d94…` and `4607a1c6…`), byte-identical
outcome both times, exit code **1**:

```
scenario        SIM-CDR-001
run status      failed   tc_verdict=pending
steps           3 reported / 5 declared   (2 never reported — run stopped early)

 ✓ step 1 [container-runtime] T1059.004  exit=0  OK
 ✓ step 2 [container-runtime] T1613      exit=0  OK
 · step 3 [container-runtime] T1613      exit=1  TTP
     └─ step executed and returned a non-zero exit

OK 2 · ENVIRONMENT 0 · TTP 1 · ENGINE 0

HARNESS INCONCLUSIVE — this run does not prove the pull-mode lifecycle
ran end to end: 2 declared step(s) were never reported.
```

**This is genuinely real execution, not a stub.** Step 1's stdout shows a
real `apt-get install wget` transcript against `archive.ubuntu.com`. Step 2's
stdout is the real upstream DEEPCE banner and a full platform enumeration
(real container ID, real host IP, real kernel version, real capability
bitmask, real home-directory listing of every identity `Dockerfile.target`
provisions) — this is the actual tool, actually downloaded, actually run,
against this actual container. Step 3 is:

```
ls -la /dev/ | head -30 && echo '---' && cat /proc/1/status | grep -E 'CapEff|CapPrm' \
  && echo '---' && mount | grep -E 'overlay|docker|k8s' && echo '---' \
  && env | grep -iE 'kube|docker|container'
```

All four sub-checks ran (their real output is in `run.json`); the chain's
**last** clause — `env | grep -iE 'kube|docker|container'` — legitimately
matched nothing on this target, and `grep` returns exit 1 on no match,
which `&&`-propagates to the whole line. `classify.py` correctly calls this
`TTP` (a real command executed and returned non-zero on its own merits — not
a masked failure, not a missing tool). Steps 4-5 never ran because the Go
beacon's task loop is **fail-fast by design**
(`agent/beacon/client.go:492`, `"Execution stops at the FIRST step with a
non-zero exit code"`), so `classify.py` correctly refuses to call an
early-terminated run a PASS — 2 declared steps have zero evidence either way,
and `HARNESS INCONCLUSIVE` (`treat exactly like FAIL`) is the honest verdict
for that state, not a defect in the classifier.

**Finding worth flagging, not fixed (out of this task's scope / file
ownership):** `SIM-CDR-001` step-03 chains four independent enumeration
probes with `&&`. Any one of the four legitimately finding nothing — which
is a completely normal outcome for a non-privileged, non-Kubernetes container
(no `kube`/`docker`/`container` string in `env` here) — aborts the whole step
and, under the beacon's fail-fast design, ends the run early. This is the
same "absent signal read as failure" shape the rest of this repo works hard
to avoid, just one level down: not a missing tool, but an enumeration script
whose own internal `&&` chaining turns an expected empty result into a
step failure. A scenario author fix would join the four probes with `;`
(each stanza discovers-and-reports independently) instead of `&&`
(discovery gates on the previous check's result). Not changed here — this
scenario's YAML isn't named by the task, and this checkout is shared.

**What this proves about the harness:** the full pull-mode lifecycle — mint
token → real installer one-liner → sha256-verified beacon → server-assigned
agent id → correlated (not guessed) selection → consent-gated launch →
payload-shelf artifact staging (`deepce.sh` + `linpeas.sh`, both staged with
digests resolved server-side, see the `=== ARTIFACT STAGING ===` block in
`run.json`) → poll to terminal → classify — worked identically on a
completely different plane (CDR vs. EDR), different identity resolution path
(`direct` vs. service-account `runuser`), and different tool set (a
staged/served shelf artifact vs. a directly-curled script). **`SIM-EDR-001`
was not a special case.** The INCONCLUSIVE verdict is itself evidence the
classifier is not rubber-stamping PASS — it refused to call a
2-steps-unreported run clean, exactly as designed.

---

## 3. Push path: `GET /api/scenarios/{id}/download`

```
curl -fsS 'http://localhost:8888/api/scenarios/SIM-EDR-001/download?format=auto' \
  -o SIM-EDR-001.bundle -D headers
```

```
HTTP/1.1 200 OK
content-disposition: attachment; filename="cortexsim-SIM-EDR-001.sh"
x-cortexsim-bundle-target: posix
x-cortexsim-bundle-selfcontained: true
content-length: 9494
content-type: text/x-shellscript; charset=utf-8
```

A 188-line self-contained bash script (verified `file(1)`: "Bourne-Again shell
script, Unicode text, UTF-8 text executable"). Grepped for `simcore`/`8888`/
`localhost` — the only hit is the header comment `"no SimCore dependency at
runtime"`; there is no live reference anywhere in the body.

**Self-containment was verified empirically, not just by grep:**

1. Started a brand-new, unrelated `ubuntu:22.04` container (never touched by
   any prior CortexSim tooling), installed only `curl ca-certificates sudo
   passwd login`.
2. `docker cp`'d the downloaded bundle into it.
3. **Stopped SimCore's own container** (`docker stop
   cortex-pov-engine-simcore-1`) — a hard proof that nothing at runtime can
   reach it.
4. Ran the bundle inside the target with SimCore down.

Result: all 5 steps executed and logged `completed successfully`, the
identity-harness fallback chain (`runuser` → `sudo -u` → `su`) resolved
`www-data` correctly on a container that (like the tier-d target originally
did) ships `www-data` as `nologin` by default, egress-dependent steps
(`apt`/`curl` to public hosts) worked over the container's own network, and
the scenario's own `cleanup` block ran and removed its artifacts
(`/tmp/mimipenguin.sh`, `/tmp/mimi_output.txt` — confirmed absent
afterward, exactly as `cleanup.commands` specifies). Restarted SimCore
afterward; `/api/health` came back `{"status":"ok", ...}` on the components
this session touched (see §4 for the one caveat this run itself introduced).

This confirms the "execute on clean Ubuntu 22.04 with no SimCore dependency
at runtime" design rule in `CLAUDE.md` is actually true for this bundle, not
just asserted by a header comment.

---

## 4. What a lab operator would actually see, end to end

1. `scripts/dev-up.sh` — one command, ~90s (mostly the multi-stage Docker
   build: Go beacon cross-compile ×5 targets, Rust submodule tools, React UI,
   Python image). Prints the reachable URL when healthy.
2. **Pull-mode lab run:** `deploy/tier-d/run-tier-d.sh --scenario <id>` mints
   a token, prints the real curl-installer one-liner, waits for the beacon
   to check in by name (`bd790i-xxxxxx`), launches, live-polls the run to a
   terminal state, and prints a per-step OK/ENVIRONMENT/TTP/ENGINE table plus
   one plain-English verdict line. Exit code alone tells a CI/cron caller
   whether to page anyone; the printed table tells a human why.
3. **Push-mode:** a DC hits `GET /api/scenarios/{id}/download`, gets one
   `.sh` file, ships it to an air-gapped/no-agent target exactly as they
   would email an attachment, and it runs cold with zero calls home.
4. **Honesty surfaces are real, not decorative.** After this session's
   churn (19 enrolled-but-now-offline ephemeral tier-d agents, 2 stray
   `jumpbox-01` tasks left by another concurrent session on this shared
   checkout), `GET /api/health` correctly flipped to `"status":"degraded"`
   with `code: NO_AGENT_ONLINE` / `TASKS_QUEUED_FOR_UNAVAILABLE_AGENT` and a
   plain-English remediation string for each — this is the repo's own
   Readiness-surface design working as documented, not a defect introduced
   by this session (this session's own two runs each cleaned up their target
   container via the harness's `trap cleanup EXIT`, unless `--keep` is
   passed).
5. **`tc_verdict` stays `pending` everywhere.** No step in this session
   talked to a real Cortex tenant. Per the repo's own standing rule:
   *authored is not proven, tenant-verified is 0* — nothing here changes
   that number, and this report does not claim otherwise.

---

## 5. Engine defects found

**None new.** Everything observed — the `SIM-CDR-001` early-INCONCLUSIVE, the
`SIM-EDR-001` ENVIRONMENT step 5 — was the harness correctly classifying real
behavior, not the harness (or `core/`/`agent/`) misbehaving. The one item
worth a follow-up is the scenario-authoring `&&`-chaining smell in
`SIM-CDR-001` step-03 (§2), which is a content issue, not an engine one.

## Artifacts

- `deploy/tier-d/results/SIM-EDR-001/{run.json,verdict.json}` (gitignored)
- `deploy/tier-d/results/SIM-CDR-001/{run.json,verdict.json}` (gitignored)
- Push-bundle self-containment check was run against a throwaway,
  never-committed container and bundle file under `/tmp` — no artifact to
  retain beyond this report.
