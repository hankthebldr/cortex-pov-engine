# Report — agent runtime dependencies

**Branch:** `feature/ui-onboarding-activation-tour`

**Commits:**
- `288952f` fix(agent): never let a missing runtime dependency present as success
- `ab0533e` feat(orchestrator): opt-in runtime-install authorization + target preflight
- `4f37884` content(edr-001): declare mimipenguin's python dependency explicitly
- `a045f30` fix(tier-d): revert the python3 lab workaround, teach classify.py the new marker
- `3a2f494` docs(design): agent runtime dependencies

## Status

Done. The defect is fixed at the execution layer (Go beacon), wired through the
orchestrator/API for visibility and opt-in installs, applied to the scenario that exposed
it, and verified live end to end against a real `deploy/tier-d/` target with no Python
installed — both the default-refusal path and the opt-in install path.

## What was chosen and why (one sentence)

The beacon now re-checks a step's declared interpreter requirement against the real target
at the real moment of execution and refuses to run the step's (possibly failure-masking)
command at all when the requirement is genuinely unmet — satisfying "a python path" by
PATH-shimming an aliased interpreter (e.g. `python3` under the name `python`) into that
one subprocess, and satisfying "deliver system updates" via an explicit, two-key,
off-by-default, run-record-logged package-manager install — while staging a full portable
interpreter from the payload shelf (the other named option) was scoped out as its own
multi-track feature the shelf's own docs say no consumer can unpack yet.

## Design doc

`docs/design/agent-runtime-dependencies.md` — problem/evidence, options considered
(including why the payload-shelf-interpreter route was rejected for this pass), what was
built, what remains unsolved.

## Implementation surface

**Go beacon** (`agent/`):
- `agent/executor/interpreter.go` (new) — `ResolveInterpreter` (exact + alias-aware
  lookup via real `exec.LookPath`), `DetectInterpreters`/`AvailableLogicalNames`,
  `InterpreterShim` (scoped PATH symlink, host untouched), `InstallPackageCommand`
  (apt-get/dnf/yum/apk detection; never runs anything itself).
- `agent/beacon/client.go` — `Step.RequiresInterpreters`, `Task.RuntimeInstallAuthorized`,
  `resolveRuntimeDeps` (the one function all three execution paths — `executeTask`,
  `executeTaskChained`, `executeTaskPerStep` — route through before turning a step into a
  real subprocess), `runtimeDepMissingMarker`, `Register()` now also sends the beacon's
  live interpreter roster.
- `agent/main.go` — computes and sends `executor.AvailableLogicalNames()` at registration.

**SimCore** (`core/`):
- `core/engine/scenario_loader.py` — `StepSchema.requires_interpreters` (optional,
  back-compat).
- `core/engine/runtime_preflight.py` (new) — pure `evaluate_runtime_readiness` /
  `interpreter_satisfied`, no I/O.
- `core/engine/orchestrator.py` — two-key `runtime_install_authorized` gate,
  advisory preflight gap computation at launch, `Task.runtime_install_authorized` on the
  wire (omitted when `False`), round-trips through the durable queue/rehydrate path.
- `core/api/agents.py` — `RegisterRequest`/`EnrollRequest.interpreters`,
  `GET /api/agents/{agent_id}/preflight?scenario_id=` (new target-readiness endpoint).
- `core/api/runs.py` — `LaunchRequest.allow_runtime_install`.
- `core/config.py` — `CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL` (default `False`).
- `core/models.py` / `core/database.py` — `Agent.interpreters`,
  `Run.runtime_install_authorized`, `Run.runtime_dependency_gaps`, with the standard
  idempotent `ALTER TABLE` migration helper this repo already uses for every prior schema
  bump.

**Content:**
- `scenarios/edr/edr-001-credential-dumping.yml` step 5 declares
  `requires_interpreters: ["python"]`; `scenarios/_schema.yml` documents the field.

**Tier-D harness:**
- `deploy/tier-d/Dockerfile.target` — the `python3` workaround package install is reverted
  (with a comment explaining why), so the target genuinely has no interpreter again.
- `deploy/tier-d/classify.py` — new `RUNTIME_DEPENDENCY_MISSING` ENVIRONMENT signature.

## Tests, with RED/GREEN evidence

Every new test below was observed to genuinely fail before its fix and pass after, per the
task's requirement. Full transcripts are in this session; summarized here.

### Go (`agent/`)

- `agent/executor/interpreter_test.go` — 12 tests covering exact match, alias fallback,
  genuine absence, shim creation/cleanup, package-manager detection. RED proven by
  replacing `ResolveInterpreter`'s body with a stub that always reports found — 3 tests
  failed (`TestResolveInterpreter_AliasFallback`, `_GenuinelyAbsent`,
  `_UnknownLogicalStillChecksItsOwnName`). Restored → GREEN, file byte-identical to
  pre-injection (`diff` confirmed).
- `agent/beacon/runtime_deps_test.go` — the crux guarantee, at two levels:
  - `TestExecuteTask_RuntimeDependencyMissing_NeverRunsTheRealCommand` and its chained-path
    sibling: a step with an impossible `RequiresInterpreters` and a command that would
    print `SHOULD_NEVER_RUN` (plus SIM-EDR-001's exact `|| echo` masking pattern) if
    executed. RED proven by short-circuiting `resolveRuntimeDeps` to always return an
    empty (non-blocking) outcome: both tests failed with `SHOULD_NEVER_RUN` actually present
    in the recorded `/output` POST body and `exit_code=0`. Restored → GREEN (`exit_code=127`,
    marker present, `SHOULD_NEVER_RUN` absent).
  - `TestResolveRuntimeDeps_AliasedInterpreter_ShimsAndRunsForReal` — runs the shimmed
    command for real via `executor.RunCommand` and asserts on genuine stdout from a fake
    script, not just internal state.
  - `TestResolveRuntimeDeps_AuthorizedInstall_ComposesCommand` and
    `_AuthorizedButNoPackageManager_StillBlocked` — same RED/GREEN injection technique
    applied to the whole `resolveRuntimeDeps` function body; 6 of 6 new tests in this file
    failed, all with the real `SHOULD_NEVER_RUN` command observed to have executed.
- Full `agent/...` suite (`go build`, `go vet`, `go test ./... -race`, plus the existing
  `TestCrossCompile_{Windows,Darwin,Linux}` gate) green after every change, including the
  pre-existing tests this touched (`client_test.go`'s `Register` call site).

### Python (inside the prod image, `cortexsim:dev`, Python 3.11.15 — per CLAUDE.md, never
the host venv)

- `tests/engine/test_runtime_preflight.py` (10 tests) — RED proven by making
  `interpreter_satisfied` always return `True`: 6 tests failed. Restored → GREEN.
- `tests/engine/test_step_schema_requires_interpreters.py` (5 tests) — including a direct
  assertion that the live `edr-001-credential-dumping.yml` file declares the field, so the
  content commit and the schema commit cannot silently drift apart.
- `tests/engine/test_orchestrator_runtime_install.py` (7 tests, real in-memory SQLite,
  real `Orchestrator.launch()`) — proves the two-key gate against a real DB, not just by
  reading the code. RED proven by changing the gate's `and` to `or`:
  `test_request_flag_alone_does_not_authorize_without_deployment_flag` failed
  (`runtime_install_authorized` was `True` when it must be `False`). Restored → GREEN.
- `tests/e2e_isolated/test_tier_d_agent_path.py::test_classify_runtime_dependency_missing_is_environment_not_ok`
  — RED proven by removing the new regex from `classify.py`: the step reclassified from
  `ENVIRONMENT` to `TTP` (wrong class). Restored → GREEN.
- Full suite: **4736 passed, 225 skipped, 0 failed** (`pytest tests/ --ignore=tests/smoke`
  inside `cortexsim:dev`), both before and after the RED/GREEN injections were reverted —
  confirming zero regressions across the existing 4700+ tests.

## Live verification against `deploy/tier-d/` (no host Docker Desktop; `DOCKER_CONTEXT=default`)

Rebuilt the prod image (`docker build -f core/Dockerfile -t cortexsim:dev .` and the
compose image), which bakes the new Go beacon into `agent-dist/` and the new Python into
`core/`. Restarted the compose SimCore on the new image. `Dockerfile.target` reverted to
having **zero** Python of any kind (confirmed: the live-enrolled beacon's own registration
recorded `"interpreters": ["perl"]` — no python entry).

**Default posture (no consent given):**
```
step 5 [root] T1003  exit=127  ENVIRONMENT
  └─ a step-declared interpreter ... was not found on the target and no authorized
     install could supply it — the step's own command was NEVER executed, so any
     absent detection here is not a Cortex miss
OK 4 · ENVIRONMENT 1 · TTP 0 · ENGINE 0
HARNESS PASS, with unrun steps
```
Raw step output confirmed no `curl` attempt, no masked completion text — the real command
never ran. `run.json`: `status=failed`, never `exit_code=0`.

**Opt-in system-update posture** (`CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL=true` on the
deployment + `allow_runtime_install:true` on the launch request, against the same
no-python target):
```
[cortexsim] RUNTIME_INSTALL_AUTHORIZED: python missing — attempting
  "DEBIAN_FRONTEND=noninteractive apt-get update -qq && ... apt-get install -y -qq python3"
Setting up python3 (3.10.6-1~22.04.1) ...
MimiPenguin Results:
exit_code=0 duration=15.521s
```
Real `apt-get` output (package unpacking, real version numbers), real mimipenguin
execution to completion afterward (found no credentials on this synthetic host — the
correct legitimate outcome, not a masked failure). `Run.runtime_install_authorized: True`,
`Run.runtime_dependency_gaps: [{"step_id": "step-05", "missing": ["python"]}]` — both
recorded on the run record as required.

Reverted the temporary compose env-var demo change afterward (`git checkout --
docker-compose.yml`) and re-ran the default-posture case once more to confirm the
off-by-default posture was cleanly restored — same `exit=127`/`ENVIRONMENT` result.

## What remains unsolved

- A fully air-gapped target with **no** Python under any name **and** no usable package
  manager. Still refuses honestly (never a false success) but cannot self-heal; the
  payload-shelf-interpreter route is the documented follow-up, scoped out here because it
  needs archive-artifact support the shelf's three consumers do not have yet
  (`docs/reference/payload-shelf.md` §11).
- Windows: `resolveRuntimeDeps` is a no-op on `GOOS=="windows"` — no scenario currently
  needs it there, but it is not built.
- The orchestrator-side preflight is advisory (a registration-time snapshot); only the
  beacon's live, execution-time check is the actual enforcement. This is by design (see
  the design doc §6) but is worth stating plainly: `GET /api/agents/{id}/preflight` can
  under-report a gap that has since resolved, or over-report one that has since appeared.
- The Go and Python interpreter-alias tables are hand-kept in sync, no drift guard.
