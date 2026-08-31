# MVP blocker fixes — B1 (crosswalk) + B2 (installer re-install) — 2026-08-31

**Task:** close the two blockers the 10-agent verification pass returned
NO-GO-to-publish / GO-for-supervised-internal-pilot on. Both reproduced by
execution before touching code; both re-verified by execution after.

**Environment:** `DOCKER_CONTEXT=default`, `cortexsim:dev` image (host
Python is 3.14, incompatible with the pinned stack — Python suite runs
inside the prod image per repo convention). Go 1.22 host toolchain. Node
v26 / npm host toolchain for the UI suite. B2's live-host verification ran
directly on this machine (`bd790i`), which turned out to already be running
a real SimCore instance (`docker compose`, container
`cortex-pov-engine-simcore-1`) with the exact stale-unit condition from the
bug report already present.

---

## B1 — crosswalk-orphaned scenarios

### Root cause

`SIM-CDR-027`, `SIM-CDR-028` (2026-08 CDR content pass) and `SIM-DLP-001..005`
(the DLP-plane merge, `4e04e4d`) load and pass `make validate`, but were
never added to `scripts/uctc_crosswalk_v2.2.py`'s hand-authored `CROSSWALK`
dict. `make ground-truth` shells out to `uctc_crosswalk_v2.2.py --report`,
which hard-fails (`ERROR: 7 scenario(s) absent from the crosswalk`) if any
loadable scenario has no entry.

### RED

```
$ python3 scripts/uctc_crosswalk_v2.2.py --report
ERROR: 7 scenario(s) absent from the crosswalk: ['SIM-CDR-027','SIM-CDR-028',
'SIM-DLP-001','SIM-DLP-002','SIM-DLP-003','SIM-DLP-004','SIM-DLP-005']
```
(exit 1, reproduced on host Python, no docker needed for this script)

### Fix

Read each scenario's own YAML `uc_ref` / `tc_ref` / `tc_refs` (already
authored correctly at scenario-authoring time — verified each ref resolves
in `docs/uc_tc_mapping/_v2.2-source/{tc_index,detection_spec}_v2.2.csv`) and
added a matching `CROSSWALK` entry for each, binding to the SAME refs the
scenario already declares rather than pattern-matching from the id.

### BREADTH vs DEPTH — per scenario

| scenario | primary tc_ref | class | verdict | why |
|---|---|---|---|---|
| `SIM-CDR-027` | `TC-DSPM-04` | DET | **depth** | already evidenced by `SIM-CDR-008/019/022`, `SIM-CLOUD-006/008/009/010`; this is a second, database-runtime-specific (RDS/Postgres mass dump) proof |
| `SIM-CDR-028` | `TC-DSPM-04` | DET | **depth** | same TC as above; a second data-store shape (DynamoDB/NoSQL vs. relational) |
| `SIM-DLP-001` | `TC-DLP-01` | DET | **depth** | already evidenced by `SIM-EDR-009`, `SIM-NDR-005/006/012`, `SIM-MP-022`, `SIM-AIACC-001`, `SIM-BROWSER-001/002/004`; adds a device/USB-channel proof |
| `SIM-DLP-002` | `TC-DLP-01` (+`TC-DLP-02`) | DET | **depth** | both refs already evidenced (`TC-DLP-02` by `SIM-MP-003/022`); a second, endpoint-native staging proof |
| `SIM-DLP-003` | `TC-DLP-11` | DET | **depth** | already evidenced extensively (`SIM-CLOUD-001..010`, `SIM-AIACC-001..006`, `SIM-BROWSER-*`) |
| `SIM-DLP-004` | `TC-DLP-02` | DET | **depth** | already evidenced by `SIM-MP-003/022`; a second, purpose-built cross-channel correlation instance |
| `SIM-DLP-005` | `TC-DLP-07` | **POS** (index-classified) | **breadth** (whole-index only, NOT DET/HNT) | `TC-DLP-07` (compliance-driven data controls) had zero prior scenario or assertion binding. Because the index itself classifies it POS (posture/policy assertion, not a detection — S-14 fires, a WARNING, consistent with the corpus's existing 13→14 S-14 count), it does **not** move the DET/HNT-evidenced count; it closes one previously-open whole-index row. |

Net: **6 of 7 are depth, 1 is breadth** (and that one breadth row is
POS-class, not DET/HNT) — matching the expectation that most of the ~37
never-evidenced DET/HNT rows were untouched by this pass. No wrong bindings
were forced; every ref above was independently verified against the index
CSVs before being added.

### GREEN

```
$ python3 scripts/uctc_crosswalk_v2.2.py --report
scenarios: 177   crosswalk rows: 177
resolution: {'REMAP': 157, 'NET-NEW': 20}
index TCs evidenced: 90/266  (DET/HNT 70/107)
...
posture-class primary (S-14): 14
```

```
$ make ground-truth   # docker run ... python3 scripts/generate_ground_truth.py
scenarios=177 cards=175 planes=16 adapters=91 assertions=22 eal_plugins=21 iac_modules_aws=11 routes=127
```

**New counted ground truth: 177 scenarios · 175 TTP cards · 16 planes**
(DET/HNT evidenced unchanged at 70/107; whole-index evidenced 89→90;
S-13 tier disagreements 105→110 — new count includes the 5 DLP-plane
scenarios' own tier metadata, not something this pass tuned; S-14
posture-class-primary bindings 13→14).

`make check-refs` (6 passed) and `make validate` (356 pass / 1 pre-existing
unrelated warn / 0 fail across `validate-detection` + `check-refs` +
`check-adapters` + `check-streamer` + `check-agent-shelf` +
`check-ground-truth`) both green with the regenerated
`docs/reference/ground-truth.{json,md}` committed.

Commit: `ccf40d5`.

---

## B2 — re-install strands the old process under a new id

### Root cause (confirmed by reading, then by execution)

`core/api/agents.py`'s `_POSIX_INSTALLER` template:
- `SVC="cortexsim-agent"` is a **fixed** unit name.
- Both systemd arms (`cs_install_systemd`, root and `--user`) call
  `systemctl [--user] enable --now "$SVC"` with no restart. `enable --now`
  is a no-op on an already-active unit of the same name — it rewrites the
  unit file on disk but never signals the running process, which keeps
  polling under whatever `--id` it was originally launched with.
- The final stage printed `done — '$AGENT_ID' should appear…` and POSTed
  telemetry code `OK` **unconditionally** — no check that the id just
  printed ever actually checked in.

### RED — reproduced live on this host, before any code change

This exact condition was already present on `bd790i` from a prior real
install → re-install cycle, independent of anything in this session:

```
$ cat ~/.config/systemd/user/cortexsim-agent.service | grep ExecStart
ExecStart=/home/henry/.local/bin/cortexsim-agent --server http://localhost:8888 --id bd790i-5ed5b2 --interval 10

$ ps aux | grep cortexsim-agent
henry  3852492  ...  /home/henry/.local/bin/cortexsim-agent --server http://localhost:8888 --id bd790i-70d7f5 --interval 10
```

Unit file says one id, the live process says another. A DC re-installing
would see "done" and a console showing nothing for the id just printed.

### RED — pytest, against the pre-fix installer (reverted for the run, restored after)

```
tests/installer/test_posix_installer_e2e.py::test_installer_fails_when_agent_never_checks_in FAILED
  assert p.returncode != 0   # actual: 0 — printed "done — 'phantom-agent' should
                              #  appear in the SimCore console within 1s" and exited clean

tests/installer/test_posix_installer_e2e.py::test_reinstall_restarts_the_stale_unit_so_the_new_id_takes_over FAILED
  assert "unit already existed — restarting" in p2.stdout   # never printed;
                              # systemctl call log never contained "restart cortexsim-agent"
```
(`2 failed, 7 deselected` — the other 7 pre-existing tests in the file were
untouched at that point and irrelevant to this RED capture)

### Fix

1. **Both systemd arms** now record whether the unit file already existed
   before being (over)written; if it did, they issue
   `systemctl [--user] restart "$SVC"` after `enable --now` so the rewritten
   `ExecStart` actually takes effect on the running service.
2. **New `verify` stage**, replacing the unconditional "done" line. Captures
   `last_seen` for `$AGENT_ID` as a baseline immediately after install, then
   polls `GET /api/agents` every `min(max($INTERVAL,1),5)`s for up to
   `max(3×$INTERVAL, 6)`s, and only declares success once `last_seen`
   **advances past that baseline** (an ISO-8601 string comparison — no date
   parser needed on the target). Existence alone was rejected as the check:
   enrollment stamps `last_seen` at token-redemption time, before the binary
   has even run, so an existence-only check would report `OK` for a beacon
   that dies immediately after enrolling. On timeout: exit non-zero with
   `AGENT_NEVER_CHECKED_IN`, POSTed to install telemetry, printing the
   `systemctl status` / `ps aux` / log-tail remediation.
3. `core/api/health.py`'s `TASKS_QUEUED_FOR_UNAVAILABLE_AGENT` detail no
   longer says the run "will sit in `'pending'`" while the run's own
   `status` field actually reads `'running'` — reworded to state that
   explicitly and note that `'pending'` will not be found by grep.
   `docs/reference/lab-runbook.md`'s worked example updated to match.

### GREEN — pytest (fixed code restored)

```
$ pytest tests/installer/test_posix_installer_e2e.py -v
9 passed in ~26s
```
including the two new tests and the updated
`test_service_mode_detaches_when_no_supervisor_is_present` (now stages a
binary that actually keeps polling — a one-shot stub is now *correctly*
reported as `AGENT_NEVER_CHECKED_IN`, which the old test's assumption did
not survive).

### GREEN — reproduced live on this host, re-install for real

Rebuilt `cortex-pov-engine-simcore` (`docker compose build simcore`, cached
layers, fast), restarted the running container, minted a real enrollment
token against it, and ran the real one-liner against the exact stale
`cortexsim-agent` user unit captured in the RED section above:

```
$ curl -fsSL 'http://localhost:8888/api/agents/install?token=...' | bash
...
[cortexsim]   unit already existed — restarting so the new --id bd790i-d6758d takes over
[cortexsim] installed USER unit: /home/henry/.config/systemd/user/cortexsim-agent.service
...
[cortexsim] verifying 'bd790i-d6758d' checks in (up to 30s)...
[cortexsim] done — 'bd790i-d6758d' is live and polling SimCore (confirmed after 10s)
$ echo EXIT=$?
EXIT=0
```

Post-state:

```
$ grep ExecStart ~/.config/systemd/user/cortexsim-agent.service
ExecStart=... --id bd790i-d6758d --interval 10
$ ps aux | grep cortexsim-agent
henry  3959043  ...  ... --id bd790i-d6758d --interval 10        # NEW pid, matches unit
$ ps -p 3852492                                                   # the OLD stale pid
    PID TTY  TIME CMD                                             # gone — restart killed it
$ curl -s localhost:8888/api/agents | jq -r '.agents[] | select(.agent_id=="bd790i-d6758d") | .status, .last_seen_age_seconds'
online
8.4
```

Unit `--id`, live process argv, and the SimCore roster now all agree. The
previously-orphaned process is dead, not merely reparented. This is the
literal re-install scenario the blocker report described, closed for real
on the same host it was found on — left running as the (now correctly
single, correctly configured) beacon for this SimCore instance.

Commits: `9bd2fce` (installer fix + tests), `464cb1e` (health.py wording +
lab-runbook.md), `e52f86d` (README task_queue/last_seen_age_seconds docs).

---

## Full suite status after both fixes

- `make validate` (validate-detection + check-refs + check-adapters +
  check-streamer + check-agent-shelf + check-ground-truth): **green, exit 0**.
- Backend: `pytest tests/ --ignore=tests/smoke` inside `cortexsim:dev`:
  **4828 passed, 232 skipped, 0 failed**.
- Go: `go build ./... && go vet ./... && go test ./... -race -count=1`:
  **all packages ok** (agent, beacon, executor, identity) — untouched by
  this pass, confirmed green as a regression check.
- UI: `npx vitest run`: **78 files / 834 tests, all passed** — exact
  baseline, no drop, untouched by this pass.

## What was not fixed

- The 105→110 S-13 tier-disagreement count is unreviewed — it reflects the
  5 new DLP scenarios' own `moat_tier` metadata against the index, not
  something this pass was asked to reconcile (S-13 is advisory, matching
  the corpus's existing documented posture on tier disagreements).
- Did not attempt to close any of the other pre-existing gaps mentioned
  elsewhere in the repo (payload-shelf exemption surfacing, Windows
  execution unproven on a real host, etc.) — out of scope for these two
  blockers.
- Left the pre-existing ~9 old "offline" phantom `bd790i-*` agent rows in
  the live SimCore's roster untouched (all predate this session, from
  earlier real testing on this host) — not something this fix is
  responsible for cleaning up, and Agent rows are not auto-deleted by
  design.
