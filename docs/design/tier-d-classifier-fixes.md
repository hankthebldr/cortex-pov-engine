# Tier-D pre-merge blocker fixes (2026-08-31)

Branch: `feature/ui-onboarding-activation-tour`. This pass closes two
Criticals and three Importants found by the final whole-branch review, all
verified by execution rather than inferred. Scope: `deploy/tier-d/`,
`agent/beacon/`, `agent/executor/`, plus their tests. `ui/` was not touched —
three other reviewers were working that area concurrently in the same
working tree.

## Summary

| id | defect | fix | status |
|---|---|---|---|
| C1 | harness reports PASS for a run in which nothing executed | `classify.py` refuses PASS on zero steps, unreported steps, or a non-terminal `run_status`; scans whole output for ENGINE signatures; `run-tier-d.sh` poll loop now detects and exits non-zero on timeout | closed |
| C2 | authorized install never re-verifies the interpreter it installed | `executor.PostInstallVerifyShim` re-resolves + PATH-shims after every authorized install, converging with the "already there" branch | closed |
| I1 | ENGINE markers invisible on a zero-exit step | `classify_step` checks `ENGINE_PATTERNS` unconditionally; the exit-0 shortcut only governs ENVIRONMENT/TTP | closed |
| I2 | bare `Permission denied` discards real TTP signal | narrowed to ENVIRONMENT only when co-located with identity-harness/install-staging tooling on the same line; otherwise falls through to TTP | closed |
| I3 | PATH shim unreachable by any non-root step identity | `NewInterpreterShim` chmods the temp dir 0755 after `MkdirTemp` (which defaults to 0700) | closed |
| — | 3 tests that cannot fail | `TestResolveRuntimeDeps_AliasedInterpreter_ShimsAndRunsForReal` (deterministic PATH, no more silent skip), `…GuardOnlyOnNonPOSIX` (now `TestResolveRuntimeDeps_WindowsGOOSIsANoOp`, actually flips `runtimeDepsGOOS` and asserts), `TestDetectInterpreters_CoversKnownRoster` (now pins a real PATH shape, not just a length match) | closed |
| — | `TIER-D-RUN-REPORT.md` documents a reverted fix | banner added marking §1.2/§2 historical/stale, points at this doc for current behaviour | closed |

## C1 — classifier fix (`deploy/tier-d/classify.py`)

`build_verdict()` (new, pure — no I/O) now computes:

- `no_steps_executed = not steps` — zero `=== STEP ===` blocks parsed.
- `unreported = declared_total - observed` — same as before, but now gates.
- `unsound_status = run_status not in {"complete", "failed", "staged"}` —
  `"aborted"` is deliberately excluded (an aborted run's verdict is
  undefined, mirroring `connectors/service.py`'s `abort_run` never scoring a
  `tc_verdict`); `"running"/"pending"/"queued"/""/None` mean the run never
  reached a conclusion at all.
- `global_engine_hits` — `ENGINE_PATTERNS` scanned over the **whole** `output`
  string, not only the bodies captured inside recognized step blocks. This is
  what catches a Traceback that occurs before the first step or between
  steps, outside any step's own captured body — exactly the C1 repro.

```
harness_verdict = FAIL          if ENGINE signal (per-step or whole-output)
                 = INCONCLUSIVE  elif no steps executed, or unreported > 0,
                                  or run_status is not a clean terminal state
                 = PASS          otherwise
```

`exit_code_for(verdict)` returns `0` only for `PASS`; both `FAIL` and
`INCONCLUSIVE` are non-zero. `INCONCLUSIVE` exists because "the harness could
not prove what happened" is a different, less alarming claim than "CortexSim
is broken" — but it must never be silently equal to PASS.

**Deliberate scope decision — `unreported > 0` always blocks PASS, even when
the reported step that triggered fail-fast is itself correctly classified
ENVIRONMENT.** The pre-existing regression test
`test_classify_nologin_is_environment_not_engine`
(`tests/e2e_isolated/test_tier_d_agent_path.py`) asserted the opposite: 1 of 5
steps reported, that step ENVIRONMENT-classified (nologin), and the OLD code
called that an unqualified `PASS`. Knowing *why* step 1 didn't run its
command does not prove *why* steps 2-5 never even started — fail-fast
stopping there is a fact about the engine's dispatch loop, not evidence every
subsequent step would have hit the identical environment gap. That test was
updated (not weakened) to assert the per-step classification is still
correctly ENVIRONMENT/never-ENGINE while the run-level verdict is now
`INCONCLUSIVE`/non-zero; a new companion test,
`test_classify_all_steps_reported_environment_only_still_passes`, pins the
real SIM-EDR-001 shape (all 5 of 5 reported, one ENVIRONMENT) as a clean PASS
— confirmed against the live re-run below.

### I1 — ENGINE markers on a zero-exit step

`classify_step(body, code)` now checks `ENGINE_PATTERNS` **first and
unconditionally**. The exit-0 shortcut (`if code == 0: return "OK"`) only
runs *after* that check, so it only ever governs the ENVIRONMENT/TTP split —
never whether an ENGINE marker gets seen.

### I2 — narrowed `Permission denied`

Removed from the blanket `ENVIRONMENT_PATTERNS` list entirely.
`_permission_denied_environment_reason(body)` now classifies a line
containing `Permission denied` as ENVIRONMENT **only** when that same line
also contains `runuser|su|sudo|apt-get|dpkg|payload|staging|chmod|mkdir` —
i.e. the identity harness or install/staging tooling was denied, so the
step's own command never got a chance to run. A bare `Permission denied` from
the step's own command (`cat: /etc/shadow: Permission denied` as `www-data`)
now falls through to the ordinary exit-code-driven TTP classification — the
technique ran and a privilege boundary held, which is real signal, not a
non-event.

**Why narrow rather than drop entirely:** the identity-harness-denial case
(`runuser: Permission denied`, an install/staging tool refused) is genuinely
indistinguishable from an environment gap — the step's command *never ran* in
that case, same as the nologin/no-home-dir signatures already in this list.
Dropping the pattern outright would have reclassified that case as TTP too,
which is just as wrong in the other direction. Co-location on one line is a
narrow, mechanical test that separates "the harness/tooling was denied" from
"the step's own command was denied" without needing a parser.

### Shell timeout gate (`run-tier-d.sh`)

Extracted `is_non_terminal_status()` into `deploy/tier-d/lib/poll_status.sh`
(sourced by `run-tier-d.sh`), used by both the poll loop's `case` and a new
post-loop gate. Previously the loop just fell out after its fixed budget
(120 × 3s) regardless of *why*, and the script printed `terminal status:
running` as if that were legitimate. Now:

```bash
if is_non_terminal_status "$STATUS"; then
  err "run ${RUN_ID} never reached a terminal state within the poll budget..."
  exit 3
fi
```

`classify.py` is never even invoked on a timed-out run. Exit code taxonomy
extended: `0` genuine PASS, `1` FAIL/INCONCLUSIVE, `2` harness setup failure
(unchanged), `3` new — poll timeout.

## C2 — post-install re-verification (`agent/executor/interpreter.go`,
`agent/beacon/client.go`)

`resolveRuntimeDeps`'s authorized-install branch previously composed
`"apt-get install -y -qq python3" && (<original command>)` and stopped.
`apt-get install python3` produces `/usr/bin/python3` and **never**
`/usr/bin/python` — a step whose own command hardcodes the bare name
`python` (the overwhelming majority of this corpus's
`requires_interpreters: [python]` declarations) would fail to resolve it
post-install, and a step carrying its own `|| echo ...` fallback (the exact
SIM-EDR-001 masking pattern this whole feature exists to stop) would report
exit 0 with no signal — the operator's own authorization reintroducing the
defect the feature exists to close.

New `executor.PostInstallVerifyShim(logical string) string` returns a
POSIX-sh snippet that, after its own install command runs, re-resolves
`logical` and — if it only landed under an alias — creates the same kind of
PATH shim `NewInterpreterShim` would have created had the alias been there up
front (including the I3 `chmod 0755` fix, inline). On failure it emits the
same `!! RUNTIME_DEPENDENCY_MISSING:` marker and exits 127. `client.go` now
composes `installCmd1 && verify1 && installCmd2 && verify2 && (original)` —
each install is individually re-verified before the next one or the real
command runs. The two branches (already-present-via-alias, and
just-installed-via-alias) now converge on the identical guarantee instead of
diverging.

## I3 — world-traversable shim directory (`agent/executor/interpreter.go`)

`os.MkdirTemp` defaults to `0700`, owned by the beacon's euid (root — the
systemd unit declares no `User=`). A step running as a different identity
(`runuser -l 'www-data'`) cannot traverse a `0700` root-owned directory, so
it could never reach the shim even though the step's own output claims
`PATH-shimmed`. One-line fix: `os.Chmod(dir, 0o755)` right after
`MkdirTemp`, in both `NewInterpreterShim` and the new
`PostInstallVerifyShim` shell snippet (`chmod 0755 "$__cortexsim_shimdir"`).

## Tests — RED before, GREEN after, for every fix

Per the task's requirement, each fix below was proven with a test that
genuinely failed against the pre-fix code and passes against the fix. RED was
captured either via `git stash` of just the source file(s) or via a
hand-reverted temp copy where stashing the whole file would have broken
compilation for an unrelated reason (noted below).

### C1 — `tests/deploy_tier_d/test_classify.py` (new, 20 tests)

CLI-level tests (`TestCLIRegression`) invoke the real
`python3 deploy/tier-d/classify.py --run ... --scenario ... --out ...`
subprocess — the one interface stable across the refactor — against the
**exact repro from the review**:

RED (pre-fix, `git stash push -- deploy/tier-d/classify.py`):
```
FAILED test_c1_dead_run_never_passes_via_cli
  AssertionError: a run in which nothing executed must never classify as
  PASS (got 'PASS')
FAILED test_i1_engine_marker_on_exit_zero_via_cli
  AssertionError: an ENGINE marker inside an exit-0 step must not classify
  OK: [{'class': 'OK', 'exit_code': 0, ...}]
FAILED test_i2_bare_permission_denied_is_ttp_via_cli
  AssertionError: a bare permission denial from the step's own command is
  real signal, not an environment non-event: [{'class': 'ENVIRONMENT', ...}]
3 failed in 0.98s
```

GREEN (post-fix): `20 passed in 0.97s` (run inside `cortexsim:dev`, Python
3.11.15 — matches CI's `backend` job environment).

Unit-level tests (`build_verdict()` called directly — valid only post-fix,
since the function didn't exist pre-refactor) additionally cover: healthy
5-of-5 PASS, unreported-steps gate, all five non-terminal `run_status` values
(`running/pending/queued/""/None`), `aborted` excluded from PASS, and the
honest ENVIRONMENT-only-with-nothing-unreported PASS case.

### C1 shell timeout gate — `tests/deploy_tier_d/test_run_tier_d_status.sh`

Plain-bash test (no `bats` dependency — not wired into this repo's CI), run
directly: `bash tests/deploy_tier_d/test_run_tier_d_status.sh`.

RED (pre-fix — `deploy/tier-d/lib/poll_status.sh` moved aside):
```
FAIL: .../deploy/tier-d/lib/poll_status.sh does not exist — the poll-status
helper has not been extracted yet
EXIT=1
```

GREEN (post-fix): all 9 assertions (`running/pending/queued/""` non-terminal,
`complete/failed/aborted/staged` terminal) pass, `EXIT=0`.

Scope note: this proves the extracted classification helper is correct; it
does not mock a live SimCore poll loop end-to-end (that would require a
stub HTTP server and was out of scope for this pass's effort budget). The
real harness re-run below exercises the un-timed-out path of the same code.

### C2 — `agent/beacon/runtime_deps_test.go::TestResolveRuntimeDeps_AuthorizedInstall_ReResolvesAndShimsAfterInstall`

Simulates a **real** install: a fake `apt-get` script writes `python3` onto
PATH at shell-runtime (not at Go-test setup time, mirroring how a real
package manager only makes the binary appear once the composed command
actually executes on the target), then the fully composed command is run for
real via `executor.RunCommand`.

RED (pre-fix — hand-reverted `installSteps`/`PostInstallVerifyShim`
composition in `client.go` back to the old `installCmds`/no-verify shape,
since a full `git stash` of `client.go` also removes `runtimeDepsGOOS`,
which would break compilation of the *other* new test in the same file
rather than demonstrate this specific defect):
```
composed command exited 127 (stdout="" stderr="sh: 1: python: not found")
— apt-get reporting success is not proof the step's hardcoded `python`
resolves; this is the exact C2 regression
--- FAIL: TestResolveRuntimeDeps_AuthorizedInstall_ReResolvesAndShimsAfterInstall
```

GREEN (post-fix): `PASS`.

### I3 — `agent/executor/interpreter_test.go::TestNewInterpreterShim_DirIsWorldTraversable`

Asserts `stat(shim.Dir).Mode().Perm() & 0o755 == 0o755`.

RED (pre-fix, `git stash push -- agent/executor/interpreter.go`):
```
interpreter_test.go:100: expected shim dir ... to be at least 0755
(world-traversable so a non-root step identity can reach it), got -rwx------
--- FAIL: TestNewInterpreterShim_DirIsWorldTraversable
```

GREEN (post-fix): `PASS`.

### Test hygiene — 3 tests that could not fail

- `TestResolveRuntimeDeps_AliasedInterpreter_ShimsAndRunsForReal`: rebuilt
  around a deterministic PATH (empty temp dir containing only `python3`,
  never `python`, plus a symlinked `sh`) instead of prepending to the real,
  inherited PATH. The old version `t.Skipf`'d silently on any host where
  `python` already resolves (e.g. `python-is-python3` systems) — the ONE
  end-to-end proof the shim genuinely works could vanish without failing the
  suite. Now `t.Fatalf`s instead of skipping if the PATH shape isn't as
  expected.
- `TestResolveRuntimeDeps_DeclaredRequirementIsIgnoredOnNonPOSIXGuardOnly` →
  renamed `TestResolveRuntimeDeps_WindowsGOOSIsANoOp`. `resolveRuntimeDeps`
  now reads the host OS through a package var `runtimeDepsGOOS` (default
  `runtime.GOOS`) instead of the bare global, specifically so a test can flip
  it to `"windows"` and assert the no-op branch returns a completely
  untouched, zero-value outcome even when a requirement IS declared and
  install IS authorized — the one case an unguarded caller could get wrong.
  The old version could only skip-if-actually-windows and assert nothing.
- `TestDetectInterpreters_CoversKnownRoster`: previously compared
  `len(DetectInterpreters())` against `len(KnownLogicalInterpreters())` —
  tautological, since the former is implemented by iterating the latter; the
  length can never disagree with itself regardless of what
  `ResolveInterpreter` returns. Now pins a deterministic PATH (only `python3`
  present) and asserts `python` resolves via that alias while
  `perl/ruby/node` are correctly reported absent.

These three are hygiene fixes with no RED/GREEN pair in the usual sense —
they were never wrong assertions that a bug would trip, they were assertions
that could never trip at all. Their "before" state is the tautology/skip
itself, confirmed by reading the prior test bodies (see the review's
findings, reproduced in the task brief).

## Go test suite

```
$ cd agent && gofmt -l .   # only agent/identity/harness_test.go, pre-existing,
                           # not touched by this pass
$ go vet ./...             # clean
$ go test ./... -race -count=1
ok  	github.com/hankthebldr/cortexsim/agent
ok  	github.com/hankthebldr/cortexsim/agent/beacon
ok  	github.com/hankthebldr/cortexsim/agent/executor
ok  	github.com/hankthebldr/cortexsim/agent/identity
$ GOOS=linux/darwin/windows GOARCH=amd64 go build ./...   # all three clean
```

## Real harness re-run

Rebuilt the SimCore image (`docker compose build simcore`) so the running
container's `/api/agents/binary` serves a beacon built from this branch's
fixed `agent/` source, then:

```
$ DOCKER_CONTEXT=default deploy/tier-d/run-tier-d.sh --scenario SIM-EDR-001

[tier-d] ✓ SimCore reachable at http://localhost:8888
[tier-d] building the provisioned target image (bare ubuntu cannot run this corpus)
[tier-d] ✓ target 'cortexsim-tier-d-target' up
[tier-d] minting an enrollment token
[tier-d] running the real installer one-liner on the target (detached)
[tier-d] waiting for the beacon to check in
[tier-d] ✓ beacon online: bd790i-4a0963
[tier-d] launching SIM-EDR-001 in pull mode against bd790i-4a0963
[tier-d] ✓ run 571a7be0-c670-4c19-b078-b5302e94aa52
[tier-d] waiting for the run to reach a terminal state
[tier-d] ✓ terminal status: failed  (run.json saved)

  scenario        SIM-EDR-001
  run status      failed   tc_verdict=pending
  steps           5 reported / 5 declared

   ✓ step 1 [www-data] T1087.001  exit=0  OK
   ✓ step 2 [www-data] T1003.008  exit=0  OK
   ✓ step 3 [www-data] T1552.001  exit=0  OK
   ✓ step 4 [root] T1003  exit=0  OK
   ~ step 5 [root] T1003  exit=127  ENVIRONMENT
       └─ a step-declared interpreter (docs/design/agent-runtime-dependencies.md)
          was not found on the target and no authorized install could supply
          it — the step's own command was NEVER executed, so any absent
          detection here is not a Cortex miss

  OK 4 · ENVIRONMENT 1 · TTP 0 · ENGINE 0

  HARNESS PASS, with unrun steps — the engine worked; the target could not
  support every step. Those steps produced NO signal, so their absent
  detections must NOT be reported as a coverage gap.
[tier-d] artifacts in deploy/tier-d/results/SIM-EDR-001
EXIT=0
```

Matches the expected outcome exactly: steps 1-4 OK, step 5 ENVIRONMENT
(`RUNTIME_DEPENDENCY_MISSING`), harness PASS-with-unrun-steps, `exit 0`. The
target container was removed automatically on exit (no `--keep`). Also ran
the docker-gated pytest e2e (`tests/e2e_isolated/test_tier_d_agent_path.py
::test_tier_d_e2e_edr001_pull_mode`) against the same live SimCore: `1 passed
in 12.64s`.

## What was not fixed, and why

- **The shell timeout gate has no mocked end-to-end proof** (a stub HTTP
  server standing in for SimCore's `/api/runs/{id}` across 120 polls). The
  extracted `is_non_terminal_status()` helper is directly unit-tested and is
  the same function both the poll loop and the post-loop gate call, so the
  logic is proven — just not the full "SimCore never advances past `running`
  for 6 minutes, then the script exits 3" wall-clock scenario. Flagged rather
  than silently skipped.
- **`I2`'s narrowing is line-based, not AST/parser-based.** A `Permission
  denied` message that happens to share a line with an unrelated mention of
  `mkdir` (e.g. inside an error message quoting a failed `mkdir` from a
  DIFFERENT, unrelated command earlier in the same echoed line) could
  misclassify. Chosen over a full parser because every real fixture examined
  (including the live SIM-EDR-001 run above, which never actually hit a
  `Permission denied` on step 2 — the container's `/etc/shadow` permissions
  allowed it to reach a clean exit-0 in this run) puts the two on the same
  line, and a parser is a much larger, riskier change for a classifier this
  narrowly scoped.
- **`harness_verdict` gained a fourth value, `INCONCLUSIVE`, distinct from
  `FAIL`.** This was a judgment call, not directed by the review text
  verbatim ("refuse to PASS"). Chosen because collapsing "cannot prove what
  happened" into "CortexSim is broken" would itself be a false claim in the
  other direction — both are non-zero exit, both are printed loudly, neither
  is silently absorbed.
