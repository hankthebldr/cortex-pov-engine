# MVP launch runbook — `dev` → `main`, v1.0.0

**Status as of 2026-09-06.** This is the ordered procedure for cutting the
CortexSim 1.0.0 MVP, plus the measured state of every gate at the time of
writing. It exists because the `dev` → `main` merge is not a merge — it is the
release trigger. `tag-on-main.yml` fires on any push to `main` that touches
`CHANGELOG.md`, reads the first `## [X.Y.Z]` heading, cuts that tag, and
dispatches `release.yml` (multi-arch GHCR image + GitHub Release). There is no
staging step between the merge button and a published artifact.

Run `make launch-preflight` before the merge. Everything below is either what
that script checks or what it deliberately cannot.

> **Beta addendum (2026-11-10).** §7 is the Windows onboarding procedure for
> beta DCs, and §8 lists known issues, open decisions and deferred findings.
> Read both **before** the first customer endpoint. Nothing on the Windows side
> has executed on a Windows host. The `windows-2022` CI job is authored and
> **has not yet run on GitHub**. `tenant-verified` is 0.

---

## 0 · The one-liner

```bash
make launch-preflight          # exit 0 = releasable · 1 = failed · 2 = withheld
```

Exit **2** means no gate failed but at least one could not run. That is not a
pass. The script names each one; run those on a host that has the missing
capability before merging.

---

## 1 · What fires on merge

```
 merge dev -> main
        │
        ├─ push to main touches CHANGELOG.md
        │        │
        │        └─> tag-on-main.yml
        │               ├─ read first `## [X.Y.Z]`      -> 1.0.0
        │               ├─ tag exists already?          -> no-op if yes (idempotent)
        │               ├─ git tag -a v1.0.0 && push
        │               └─ gh workflow run release.yml --ref v1.0.0
        │                        │
        │                        └─> release.yml
        │                              resolve-tag -> lint-shell -> build-image
        │                              -> build-stage2 -> manifest -> release
        │
        ├─ pages.yml      (GitHub Pages)
        └─ wiki-sync.yml  (wiki tree)
```

Only `secrets.GITHUB_TOKEN` is referenced by any workflow — there is no
external secret that can be missing at release time.

**Pre-merge coherence, all verified:**

| fact | value |
|---|---|
| `main` CHANGELOG top | `0.1.0` |
| `dev` CHANGELOG top | `1.0.0` |
| `core/main.py` version | `1.0.0` |
| `v1.0.0` tag on origin | does not exist |
| `main` ahead of `dev` | 0 commits |
| `dev` ahead of `main` | 73 commits |

Because `main` is 0 ahead, this is a clean merge with no risk of reverting a
direct-to-`main` commit.

---

## 2 · Gate matrix — measured state

Legend: **PASS** ran and holds · **FAIL** ran and is broken · **BLOCKED** could
not run in this environment.

### Corpus and reference gates

| gate | result | evidence |
|---|---|---|
| `validate-detection` | PASS | 356 pass / 0 warn / 0 fail, exports deterministic |
| `check-uctc-sheet` | PASS | engine coverage sheet in sync |
| `check-streamer` | PASS | 14 checked, 9 field-advisory, 0 dataset-fail |
| `check-adapters` | PASS | tier-2 sources present, 0 de-hand-rolling candidates |
| `check-lab-ready` | PASS | manifest regenerates byte-identical |
| `coverage-strict` | PASS | 0 warn, 0 strict-breach |
| `check-ground-truth` | PASS | regenerates in sync |

### Test suites

| suite | result | evidence |
|---|---|---|
| backend (`pytest tests/`) | PASS | **5070 passed · 0 failed · 258 skipped** |
| ui (`vitest`) | PASS | **998 passed · 88 files** |
| agent cross-compile | PASS | linux · darwin · windows |
| agent (`go test -race`) | PASS | 4 packages |
| Tier-C isolated exec | PASS | 53 passed · 4 skipped |

### Image-parity gates — BLOCKED locally, since proven green in CI

`make launch-preflight` reports these BLOCKED on a host without Docker Hub blob
egress (`production.cloudfront.docker.com` → 403 by policy), which is the case
in the Claude Code web sandbox. That is a statement about the host, not about
the tree — and it is deliberately not reported as a pass. GitHub's runners have
the egress, so CI is where the claim actually gets settled.

Settled on `d0fee34`, [CI run 34071919266](https://github.com/hankthebldr/cortex-pov-engine/actions/runs/34071919266) — **all 8 jobs green**:

| gate | locally | in CI |
|---|---|---|
| `backend` — pytest inside the prod image | BLOCKED | **success** (image built, suite ran) |
| UI image-parity — image serves the console this tree builds | BLOCKED | **success** |
| `refs` — every scenario under `CORTEXSIM_STRICT_REFS` | BLOCKED | **success** |
| ground-truth determinism | BLOCKED | **success** |
| `rust-dist` — static musl matrix, SHA256SUMS, executed on clean ubuntu **and** alpine | BLOCKED | **success** |
| `detection` · `adapters` · `agent` · `ui` · Tier-C | pass | **success** |

The docker-gated Tier-C end-to-end stays opt-in behind the `tier-c-e2e` label
and did not run; it is the one image-parity claim still unproven on this head.

### Known red, pre-existing, non-blocking

`e2e (smoke + playwright)` in the legacy `test.yml` fails at
`ui/tests/e2e/05-eal-campaign.spec.ts:43`. It is red on `dev` identically
(same spec, same line, `1 failed · 22 passed`), carries
`continue-on-error: true`, and is therefore invisible in the run rollup — the
*run* reports success while the *job* reports failure.

Deterministic, not flaky: `PluginRegistry.manifest()` sorts by name so
`/api/eal/plugins[0]` is always `ad_windows_emitter`, which declares
`data_sources` and is classified `analytics_log_streamer`; the Traffic/EAL
builder filters to `network_eal`, so that plugin can never be an `<option>`.
The spec predates the Data Streams family split. Diagnosis and a proposed
four-line patch: PR #105. Not a v1.0.0 blocker — the console works, the spec is
wrong — but it should not ship red, and `continue-on-error` on a permanently
red check is the same signal-erosion this pass fixed in `wiki-sync`.

---

## 3 · Defects found and fixed in this pass

Each with the failing-before evidence Gate A requires.

> **Coordination note.** A parallel session fixed SIM-MP-020 independently
> (`0d01fd0`, on `dev`) while this pass was running. That fix restored
> emittability by adding `platform_variants` and leaving `command` unchanged.
> This branch is rebased onto it. Reviewing the two side by side is what
> surfaced the generator defect below: their fix satisfied every test and the
> bundle still shipped the wrong command, because no test compared the
> generator's output to the resolver's answer.

**`fix(scenarios): mp-020 could emit no push bundle for either target`**
`bf4b307` converted SIM-MP-020's SAFE-MODE echoes to active signal by folding
Windows and POSIX into one hybrid line:

```
cmd.exe /c "whoami & echo ..." 2>nul || (whoami && id)
```

`push_generator` classifies per target on command *text*. `cmd.exe` trips a
strong Windows marker and `2>nul` is not a POSIX anchor (`2>/dev/null` is), so
steps 01/02/05 were not POSIX-shaped. Step 06's narration carried a `;` inside
the echo string, splitting it into statements that are not `echo '<literal>'`,
so it was not shell-neutral and failed Windows too. `emittable_targets()`
returned `()` — **the scenario could emit nothing**, while the lab-readiness
manifest advertised it GREEN ("runs offline on a provisioned target").

That is the manufactured-false-negative shape: a DC is told the scenario is
lab-ready, then gets `409 BUNDLE_TARGET_UNSATISFIABLE` for both formats.

Fixed with the mechanism the resolver's own error hint names — a clean POSIX
`command` plus `platform_variants.windows` per step. Also removed step-05's
POSIX fallback `curl http://127.0.0.1:8888/api/health`, a **SimCore call inside
a bundle whose cardinal rule is no SimCore dependency at runtime**, and made
cleanup actually `rm -rf` the staging directory rather than assert "no
persistent artifacts" over one.

**`fix(engine): the bash bundle shipped a command the resolver never chose`**
The most consequential find, and it generalises beyond mp-020. `generate_bash`
read `step["command"]` directly and ignored `platform_variants` entirely, while
`resolve_target(..., "posix")` consults `platform_variants.linux` when the
primary is Windows-shaped. A scenario could therefore be judged POSIX-emittable
*because of* its Linux variant and then ship the Windows-shaped primary.
`generate_powershell` has always resolved correctly; only the bash loop did not.

On `dev` this produced, for SIM-MP-020 step-05:

```
run_as 'svc-backup' 'powershell.exe -NoProfile -Command Write-Output
'\''[cortexsim-mp-020] rclone exfil check'\'' 2>nul ||
(curl -s -m 2 http://127.0.0.1:8888/api/health || true)' 'step-05' || true
```

On a clean Ubuntu 22.04: `powershell.exe` absent, `2>nul` creates a file named
`nul`, the fallback curls a SimCore that is not running, `|| true` swallows it,
and the step reports success having emitted nothing.

Nothing caught it because the emittability test and the golden digests both
describe the *resolver's* answer — which was right — and the generator's answer
was never compared to it. Two guards close that:
`test_bash_bundle_ships_what_the_resolver_resolved`, and `:8888` added to
`_SIMCORE_MARKERS` (the list held only k8s delivery paths, so a test whose
docstring promises "names no SimCore endpoint" passed on a bundle curling
SimCore). Behaviour-preserving: all 176 other scenarios emit byte-identical
bundles.

**`test(scripts): pin mp-020 on the real-signal side of the tier invariant`**
The RED-list still named SIM-MP-020 after the conversion deliberately moved it.
Moved rather than deleted, so a regression back to narration fails there.

**`test(installer): make the root system-unit test idempotent`**
`test_reinstall_restarts_the_stale_unit_so_the_new_id_takes_over` writes
`/etc/systemd/system/cortexsim-agent.service`, outside `tmp_path`. On a second
run in the same container the "first install" is no longer first and the
assertion fails for reasons unrelated to the code. CI never saw it (fresh
container per job); locally it turns a green suite red on the second run, which
is how a real signal teaches a developer to ignore it. Failed 3/3 before the
fixture, passes 2/2 after.

---

## 4 · Open decisions before merging

### 4a · Abandoned branch — RESOLVED

`claude/composer-workflow-design-32785d` (Composer Phase 2 — XDM stitch-context
resolver, `stitch_context` persistence, run-command injection, UI stitch panel)
carried 5 unmerged commits while its *design* doc already sat on `dev`. It was
merged as PR #104 on 2026-09-06 and `no-abandoned-branches` now passes: every
remote branch is an ancestor of `dev`.

The check that caught it had a flaw of its own — it also flagged the branch you
are standing on, which is in flight, not abandoned. It now excludes the current
branch and reports it separately as a `note`, so the gate cannot fail on every
run from a topic branch and thereby train people to ignore it.

### 4b · GitHub publication surfaces

Reviewed and corrected in the same pass. What was wrong and what it now says:

| surface | was | now |
|---|---|---|
| Pages (`docs/site/index.html`) | 22 assertions · 21 plugins · 199 authored · Correlation 115 · IOC 40 | 28 · 26 · 205 · 114 · 39 |
| README | 127 routes / 21 router modules · 21 plugins ×3 · no licence section | 134 / 23 · 26 plugins · Licence + Security sections |
| Wiki (`docs/wiki/Home.md`) | 133 routes · 21 plugins · 22 assertions | 134 · 26 · 28 |
| `LICENSE` | **absent** on a public repo (all rights reserved) | Apache-2.0, byte-identical to the canonical SPDX text |
| `NOTICE` | absent | trademark + affiliation terms; not an official PANW product |
| `SECURITY.md` | absent | disclosure path, non-vulnerabilities, enforced guarantees |
| `wiki-sync.yml` | **failed on every no-op run** | publishes or exits 0 |
| `ci.yml` / `test.yml` | no `permissions:` block | `contents: read` |
| repo About / topics / homepage | already accurate (177 · 16 planes) | unchanged |

Two of those are more than cosmetic:

**The wiki has not published since 2026-09-03 and the failure was self-inflicted.**
The publish step checked for changes *before* `git add -A`, at a point where
`git rm -rfq .` had just staged a deletion for every page — so it always entered
the commit branch, `git commit` exited 1 with "nothing to commit, working tree
clean", and `set -e` failed the job. A successful no-op published a red X.
Reproduced locally byte-for-byte against the CI log, then fixed and verified
across three cases: identical tree (exit 0, no publish), changed tree
(publishes), deleted page (publishes).

**`CORTEXSIM_MASTER_KEY` is not read by anything.** The variable the code
validates is `CORTEXSIM_SECRET`. CLAUDE.md's quick-start and three reference
docs named the wrong one, so an operator following them set a variable with no
effect and got the development-mode warning path instead of the production boot
refusal. Corrected in all four.

### 4c · Pages and wiki republish on release

`pages.yml` triggers on `docs/site/**` pushed to `main` **and** on
`release: published`; `wiki-sync.yml` on `docs/wiki/**` or `scenarios/**`. The
v1.0.0 release therefore rebuilds both automatically — which is only safe
because the corrected counts and the wiki-sync fix land in the same merge.

### 4d · CLAUDE.md counted-ground-truth drift

`README.md` and `CHANGELOG.md` were corrected in this pass. `CLAUDE.md` was
not, and several of its headline counts now lag the tree:

| claim in CLAUDE.md | ground truth |
|---|---|
| 170 loadable scenarios | **177** |
| 170 TTP cards | **175** |
| 15 detection planes | **16** |
| 133 routes | **134** |
| 18 assertion artifacts | **28** |
| 21 EAL plugins | **26** |
| CI "7-job matrix" | **8 jobs** |

Not corrected here because CLAUDE.md mixes counted facts with deliberately
historical narrative ("the prior baseline was…"), and rewriting those
unilaterally would destroy provenance the file is keeping on purpose. Gate B
asks for counted ground truth in README **and** CLAUDE.md, so this is a real
pre-merge action item — it just needs an author's judgement about which numbers
are current claims and which are history.

---

## 5 · Merge procedure

```bash
# 1. Confirm the tree
git checkout dev && git pull origin dev
make launch-preflight              # must be exit 0

# 2. Image parity — REQUIRED. Skip locally ONLY if CI has already settled it
#    on the exact head you are merging; otherwise run it here. Confirm the
#    green belongs to THIS sha, not an earlier one.
make build
make check-agent-shelf             # image serves all 5 beacon targets
make check-refs                    # strict refs through the real loader

# 3. Merge (never rebase dev or main)
git checkout main && git pull origin main
git merge --no-ff dev
git push origin main

# 4. Watch the release fire
#    tag-on-main.yml -> v1.0.0 -> release.yml
```

After the tag lands, verify `GET /api/health` on the published image reports
`1.0.0` and that `GET /api/agents/binary?os=windows&arch=amd64` returns a real
`PE32+` — the two claims most likely to be true in the tree and false in the
image.

`check-agent-shelf` is worth keeping in the list even though CI is green: the
`backend` job proves the image serves the **console**, and `rust-dist` proves
the Rust matrix executes, but neither asserts the image serves all five
**beacon** targets. That gap is exactly how the Windows beacon came to be
compiled-but-not-shipped once already (`agent-builder` listed four targets
while `build-agent-dist.sh` listed five).

---

## 6 · What 1.0.0 does not claim

Restated because a version number invites the wrong reading, and the CHANGELOG
says the same:

- **`tenant-verified` is 0.** No run and no assertion in this repo has been
  executed against a live Cortex tenant. Every green above comes from an
  injected transport. **Authored is not proven** — do not report the two as one
  number. The console's Readiness surface states this verbatim.
- **Windows pull is servable, not proven.** The image serves a correct
  `PE32+` and a PowerShell installer, but no Windows host has executed the
  beacon or the installer. Since 2026-11 the same is true of the Windows
  uninstaller, the beacon's new service handler and every `.ps1` push bundle.
  Until the `windows-2022` job is observed green on GitHub, nothing may say
  "works on Windows" (§7, §8).
- **The Rust tool matrix is baked, not servable.** There is no
  `/api/tools/binary/{tool}` route; `docker cp` is still the only way off the
  image.

---

## 7 · Beta: onboarding a Windows endpoint (read before the first one)

> **Authored, not proven.** Every line below is read from the installer, beacon
> and push-generator source, and parsed under pwsh 7.6 on macOS/Linux. pwsh 7.6
> is not Windows PowerShell 5.1, the product floor. No Windows host has
> executed the beacon, the installer, the uninstaller or a bundle. The
> `windows-2022` CI job is the first place any of it can run, and it **has not
> yet run on GitHub**. If something here disagrees with what you see on a real
> host, the host wins. File it (§8.4) with the `stage=` / `code=` line it
> printed.

### 7.1 Before the customer is watching

1. **Get an AV/EDR allowance.** The beacon `cortexsim-agent.exe` is **unsigned**
   (decision CI-06). The `iwr … | iex` cradle is exactly what Cortex XDR or
   Defender in prevent mode is built to stop. Ask the customer for an
   allow-list entry or exclusion for
   `C:\ProgramData\CortexSim\cortexsim-agent.exe` and its sha256. Read the
   sha256 from `GET /api/agents/binary/sha256?os=windows&arch=amd64` on your
   SimCore, or from the release's `cortexsim-agent-SHA256SUMS`. Without the
   allowance, expect `BINARY_QUARANTINED` or `PROCESS_START_BLOCKED` at install,
   and `ARTIFACT_QUARANTINE_SUSPECTED` on staged tools.
2. **Make SimCore reachable by name.** If the endpoint will reach SimCore as
   `http://JUMPBOX:8888`, that name must be in `CORTEXSIM_ALLOWED_HOSTS`
   (`scripts/dev-up.sh` seeds the jumpbox's own names when it writes `.env`).
   Otherwise every poll is `400 HOST_NOT_ALLOWED`. An IP literal always
   passes.
3. **Know what you will measure.** Only steps that resolve for Windows run.
   13 scenarios have a Windows push target (9 also POSIX, 4 Windows-only), and
   69 carry a Windows-labelled step. The console's launch preflight reads the
   server's `bundle_targets`. There is **no Windows canary**
   (`CORTEXSIM_CANARY_SCENARIO_LINUX` only).

### 7.2 Install (pull mode)

Mint a token in the console (Targets → Deploy agent), then on the endpoint, in
**Windows PowerShell opened as Administrator**:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; $env:CORTEXSIM_TOKEN='cxs_…'; iwr -useb "http://<simcore-host>:8888/api/agents/install?os=windows" | iex
```

- It registers the **`CortexSimAgent`** service (LocalSystem, auto-start,
  restart on failure) and logs to `%ProgramData%\CortexSim\agent.log`. It
  prints `OK` only when the service is Running, still Running 35 s later, and
  the beacon has polled SimCore. **Expect 45–70 s.**
- **Not elevated** → `NOT_ELEVATED`, and nothing changed. Do not reach for
  `$env:CORTEXSIM_ALLOW_UNSUPERVISED='1'` unless you mean it. It gives a
  per-user beacon that **dies at logoff and at reboot**, cannot run admin-only
  TTPs, and ends `DEGRADED_NO_SUPERVISOR`.
- **Proxy.** The service runs as SYSTEM and does **not** inherit your proxy
  settings. If the endpoint can only reach SimCore through a proxy, the beacon
  will not. `CORTEXSIM_NO_PROXY=1` makes the installer go direct.
- **Air-gapped.** Pre-stage the exe and set `$env:CORTEXSIM_BIN` to its path.
  It is digest-checked when SimCore can serve a digest, otherwise
  `CHECKSUM_SKIPPED`.
- **Downloaded instead of piped.** Run
  `Unblock-File .\install-cortexsim-agent.ps1; $env:CORTEXSIM_TOKEN='cxs_…'; powershell -NoProfile -ExecutionPolicy Bypass -File .\install-cortexsim-agent.ps1`.
  Without the token set, every host run this way claims the same baked-in id.
- **`INSTALL_DIR_UNTRUSTED`** means an elevated admin ran the **old**
  installer on this host and owns the directory. Uninstall (§7.4), then
  install again.

### 7.3 Verify

```powershell
sc.exe query CortexSimAgent        # STATE : 4  RUNNING
sc.exe qc CortexSimAgent           # START_TYPE AUTO_START, SERVICE_START_NAME LocalSystem
Get-Content "$env:ProgramData\CortexSim\agent.log" -Tail 20     # startup line ends mode=service
```

On SimCore, `GET /api/agents` must show `last_seen_age_seconds` falling on
repeat calls. In the console, a row the installer only enrolled reads
"enrolled — waiting for first beacon poll". That is not a live beacon.

| `sc.exe query` reads | Meaning |
|---|---|
| died around 30 s, error 1053 | This beacon build has no service handler. Rebuild SimCore's agent-dist from a current tree. |
| `SERVICE_EXIT_CODE` 3 | Did not stop within 60 s of a stop and was forced STOPPED. An in-flight run may read as orphaned. |
| `SERVICE_EXIT_CODE` 1 or 2 | The service command line failed validation or flag parsing. `agent.log` has the error. |
| `SERVICE_EXIT_CODE` 4 | `RegisterServiceCtrlHandlerExW` failed. |

### 7.4 Uninstall

Elevated:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; iwr -useb 'http://<simcore-host>:8888/api/agents/install?os=windows&uninstall=1' | iex
```

It is idempotent and removes the service, `%ProgramData%\CortexSim`, a per-user
install, and the beacon's cache and staging roots. Those roots hold staged
dual-use tools: **run the uninstaller before you leave the customer's host.** It
ends `UNINSTALL_INCOMPLETE` if anything it found survives. A
`1072 / SERVICE_PENDING_DELETE` means `services.msc` or Event Viewer is holding
the service open, so close them or reboot. Deleting the agent in the console
removes only the registry row. POSIX hosts:
`curl -fsSL '<server>/api/agents/install?uninstall=1' | sudo bash`.

### 7.5 What a Windows run is, and is not

- **Every step runs as `NT AUTHORITY\SYSTEM`** under the service. XDR causality
  shows SYSTEM, not the admin who installed it. Every non-`direct` identity
  collapses to that account, with `!! IDENTITY NOT HONOURED` in the run record
  and the POV report. A lineage-keyed miss on such a step is **not** a
  customer gap.
- **Variant steps** (`!! PLATFORM VARIANT`) ran a Windows command whose expected
  detections were authored against a different one (decision BEACON-01).
- **Pull mode never runs scenario cleanup.** The POV report lists the commands
  it did not run under "Cleanup Not Run". Remove the residue by hand.
- **A missing tool runs untooled with no marker on Windows**
  (`requires_interpreters` / `required_binaries` are no-ops there).
- **MTTD is launch-to-alert**, including queue wait and earlier steps.

### 7.6 Push mode on Windows (`.ps1`)

From the jumpbox (`curl '<server>/api/scenarios/SIM-EDR-013/download?format=powershell' -o cortexsim-SIM-EDR-013.ps1`, then copy it over), or on the endpoint itself:

```powershell
# curl.exe, not curl: in Windows PowerShell 5.1 `curl` is an alias for Invoke-WebRequest
curl.exe -fsS "http://<simcore-host>:8888/api/scenarios/SIM-EDR-013/download?format=powershell" -o cortexsim-SIM-EDR-013.ps1
Unblock-File .\cortexsim-SIM-EDR-013.ps1       # clears Mark-of-the-Web; the bundle never calls Set-ExecutionPolicy
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\cortexsim-SIM-EDR-013.ps1
```

- **Exit codes:** `0` clean · `1` a step non-zero / threw / timed out · `2`
  refused, residue not acknowledged · `3` a step not executed (`PREREQ_MISSING`,
  `OPERATOR_INPUT_MISSING`, `NOT_ELEVATED`, `SHELL_UNAVAILABLE`, `NOT_REACHED`) ·
  `4` cleanup failed. Read the outcome table it prints last.
- **9 of the 13 Windows bundles refuse to start** until you set
  `$env:CORTEXSIM_ACCEPT_RESIDUE='1'`, because part of their cleanup cannot run
  on Windows. The header names exactly what is left behind.
- **Inputs:** `SIM-EDR-005` and `SIM-MP-020` need a **domain-joined** host
  (`USERDNSDOMAIN`). `SIM-EDR-017` needs `CORTEXSIM_C2_HOST` set to an
  egress host you own.
- **Scoping.** A push run has no host, so reconcile leaves its matches
  `pending / UNSCOPED_MATCH`, never `detected`. Pass the endpoint:
  `POST /api/runs/{id}/reconcile?host=<name>`, or `host` in the
  `/observations` body.

---

## 8 · Beta known issues, open decisions and deferred findings

Read this list before you hit one of these. Finding ids (`SEC-01`, `BEACON-05`, …)
are the beta-readiness review's; quote them when you report.

### 8.1 Standing limits a beta DC will see

| Limit | What you see | Why / workaround |
|---|---|---|
| **API is unauthenticated** (SEC-01) | `/api/health` is `degraded`, with `api_auth: API_UNAUTHENTICATED` on every deployment | By design until auth lands. Any **other** degraded component is a real fault. Keep SimCore on an isolated lab segment; `CORTEXSIM_BIND=127.0.0.1` for a console-only host. |
| **Installer checksum is corruption-only** (SEC-12) | `CHECKSUM_MISMATCH` / `verify` stage | SimCore serves plain HTTP, so the digest proves integrity in transit, not authenticity. |
| **Nothing proven on Windows** | — | The `windows-2022` job is authored and has not run. It is expected red until first observed green. |
| **Unsigned Windows beacon** (CI-06) | Quarantine / block | Exclusion plus published sha256 (§7.1). |
| **Correlation share is exactly at its floor** | — | 10.0% (137 of 1369). The next scenario without a Correlation row turns `coverage-strict` red. |
| **Install attempts reset on restart** | `GET /api/agents/install/attempts` empty after a restart | They live in memory (a deque of 100). Use `GET /api/agents` `registered_at`. |
| **MTTD is launch-to-alert** (OPS-07) | MTTD includes queue wait and earlier steps | Use `POST /api/runs/{id}/observations` with the alert's own timestamp, or reconcile. **Do not** read MTTD from `PUT /api/results/{id}/validate`: that records click latency (DOCS-04). |
| **Push runs read `pending` until scoped** (DOCS-03) | `UNSCOPED_MATCH` | Pass `?host=` / `host` (§7.6). |
| **Abort drops host step evidence** | An aborted step's `step_outcomes` never reads `termination=aborted` | After `/abort` the beacon's `/complete` is `409`. This may matter for the prevention two-witness rule; the owner decides. |
| **Multi-endpoint pull liveness** (OPS-13, deferred) | A fan-out can hang | Rare in beta, and the failure is a hang, not a false claim. Single-endpoint pull is the beta path. |
| **Push bundles are not consent-gated** (PUSH-08) | `GET /download` hands out dual-use tooling with no prompt | Decision open. Treat a downloaded bundle as you would the tool itself. |
| **`CORTEXSIM_TRUSTED_PROXIES` in a host-run `.env`** | SimCore refuses to boot (`Extra inputs are not permitted`) | It is read from the environment, not declared on `Settings`. Export it in the shell, or pass it through compose (which does). A one-line `Settings` declaration fixes it. |
| **PSScriptAnalyzer unpinned** | — | The `windows` job installs the newest. Pin it after its first run. |
| **Release beacons ≠ image beacons** (CI-11) | Different sha256 | `ci.yml` / `release.yml` build with Go 1.21; `core/Dockerfile` uses `golang:1.22-alpine`. The new `release.yml` has never run. |

### 8.2 Decisions still open for Henry

Each row says what ships **today** (the default a lane implemented), so a DC
knows the behaviour regardless of the outcome.

| Finding | Question | Ships today | Recommended |
|---|---|---|---|
| BEACON-01, CONT-01/08/09, DOCS-01 | What may Windows pull run, when "both" scenarios carry Windows variants under Linux-keyed detections? | Server refuses unsatisfiable targets (`PULL_TARGET_UNSATISFIABLE`), dispatches resolved variant + shell, `!! PLATFORM VARIANT`; beacon refuses `PLATFORM_UNSATISFIABLE` | (a) now; per-variant `expected_detections` + Windows cards before GA |
| BEACON-05 | Native exit 0 + 5.1 `NativeCommandError`: success? | Trust `$LASTEXITCODE` (beacon and bundle share the truth table); unobserved on 5.1 | Trust `$LASTEXITCODE` |
| BEACON-08 | Pull CGO anchors relabel only; detections keyed on anchor image (EDR-017 explorer.exe, MP-020 lsass.exe)? | Comment corrected only | Document as a label; re-key on `causality_id` + chain shape |
| BEACON-09 | How does Windows prevention earn a host witness? | No witness fabricated (rc 129–159 not `signaled` on Windows) | Reason `HOST_WITNESS_UNAVAILABLE_WINDOWS` now; measured mapping after a lab run |
| BEACON-11, OPS-04 | Tolerance for a control-channel blip / SimCore restart mid-step? | Fail-closed, labelled `CONTROL_CHANNEL_LOST`; bounded `/complete` + `/output` retry; abandoned runs reaped | Abort after ~30 s of failures + spool to disk |
| BEACON-16 | Is a quarantined staged artifact a prevention witness? | Reworded to `ARTIFACT_QUARANTINE_SUSPECTED`; not fed to scoring | Reword now; feed scoring after a lab run |
| INST-15, UI-05 | How to represent enrolled-but-never-polled? | UI only: "enrolled — waiting for first beacon poll" | Enroll with `last_seen=None`; require two polls |
| PUSH-08 | Consent gate on push bundles? | None | Gate POST + runtime guard in the bundle |
| PUSH-14, CONT-03 | May SIM-EDR-004 clear real Security/System logs? | No: clears a CortexSim-owned channel (keeps event 104, not 1102) | Keep for beta |
| PUSH-18 | Infer the Windows dialect, or author `shell:`? | Shared structural inference; `MIXED_DIALECT` refused | Inference; authored `shell:` only where ambiguous |
| CONT-07, CONT-14 | Model elevation for Windows steps? | Beacon advertises `elevated`; `IDENTITY NOT HONOURED` for root/container-runtime/administrator unelevated; bundle `NOT_ELEVATED`; failed-step scoring unchanged | `requires_elevation` + score failed steps pending/not_executed |
| CONT-11 | Should pull run `cleanup.commands`? | No; the report lists what it did not run; in-step teardown in EDR-003/006, ITDR-006, MP-030 | Beacon runs per-OS cleanup for beta; in-step teardown for new content |
| CI-02, DOCS-02, UI-01 | Keep the stdlib SCM handler (vs a SYSTEM scheduled task)? | SCM handler | Keep; review `windows-proof` before any doc says "service works" |
| CI-06 | Authenticode-sign the Windows beacon before GA? | Unsigned; hashes + buildinfo published | Sign before GA |
| CI-11 | One Go toolchain for CI, Dockerfile and `go.mod`? | Mixed (1.21 / 1.22) | Pin a supported toolchain everywhere after a SHA-1 cert test |
| SEC-01 | Operator API authentication? | None; `api_auth` degraded, Host guard, CORS off, CIDR floor | Bearer token + split listeners |
| SEC-04 | Agent-plane auth? | None | Per-agent secret at enrollment |
| SEC-06 | Stop storing harvested credentials in run output? | Stored | Content discards secrets + server-side redactor |
| SEC-11 | Compose default `CORTEXSIM_ENV=production`? | `development` (placeholder secret refused in every env) | Yes |
| SEC-12 | Transport integrity over plain HTTP? | None (checksum is corruption-only) | Per-deploy CA, fingerprint pinned, before GA |
| OPS-07 | When does the MTTD clock start? | Launch time | `max(executed_at, delivered_at)` now |
| OPS-08 | Expire undelivered queued tasks? | No TTL; launch at a not-enrolled target refused | TTL 30 min → `TASK_EXPIRED_UNDELIVERED` |
| DOCS-03 | Scope push-run reconcile to a host? | Unscoped → `pending / UNSCOPED_MATCH`; `?host=` / `host` override (ce9ed13); the alternative is reverting it to a docs-only note | Optional `target_host` at push launch + (c) |
| DOCS-04 | Should click-to-validate produce MTTD? | Yes (records click latency) | Exclude attested-only rows from MTTD |
| DOCS-12 | Gate B requires Windows evidence? | Job added, not required | Checkbox; require the job once first green |
| DOCS-15 | Publish beta scope, known issues and a feedback channel? | This section | `docs/release/BETA.md` + issue form, linked from README |
| UI-08 | Platform check keyed on server truth? | Both: `bundle_targets` consumed, 100 steps relabelled | Done |
| UI-11 | Elevation guidance in the console? | Honest trade-off text | Recommend elevation once the job is green |
| UI-15 | Should the downloaded `.ps1` carry the token? | Tokenless; exact run line shown | Keep |

### 8.3 Deferred findings

- **OPS-13**: multi-endpoint liveness needs the beacon to identify itself on
  `/control` and a per-(run, agent) delivery record. Deferred, because the
  failure is a hang, not a false claim.
- **SEC-03 (partial)**: the `docker.sock` mount is removed. A non-root `USER`
  in the image is deferred, because it cannot be verified without an image run.
- **CI-12 (partial)**: per-job least-privilege permissions landed. Pinning
  every action by commit SHA needs a network lookup; `dependabot.yml` is in
  place so pinning can follow.
- **OPS-08 (partial)**: a launch at a not-enrolled target is refused. Failing
  undelivered runs and dropping their queued tasks when an agent is deleted is
  deferred, and so is the queued-task TTL (decision above).
- **Integration follow-ups:**
  - `make check-powershell` is green but not yet part of `make validate` /
    `make ci`; it runs only in CI's `refs` job.
  - `tests/tools/test_check_adapter_wiring.py::test_real_corpus_passes`
    shells out to the system `python3` and fails on a host without PyYAML.
    This predates the pass; it is green in the image.
  - `scripts/check-adapter-sources.sh` reports `FAIL=2` in any worktree whose
    tier-2 submodules are not initialised.

### 8.4 Unproven on Windows until the `windows-2022` job is green

SimCore booting natively on Windows; the Host guard admitting a Windows
jumpbox's own (uppercase, NetBIOS-style) name; the SCM handler and
`StartServiceCtrlDispatcherW` fallback; Job Object tree-kill; 5.1
`NativeCommandError` semantics; OEM vs UTF-8 output; token elevation; every
installer and uninstaller path (`icacls`, `New-Service`, the 1053 / 1072
handling, the check-in proof under SYSTEM); every `.ps1` bundle (none of the
13 has executed on any OS); `taskkill /T` on timeout; and cmd-dialect steps.
When you are the first to run one of these on a real host, attach the
installer's `stage=` / `code=` line, `agent.log`, `sc.exe qc` / `queryex`
output and the bundle's outcome table to your report.
