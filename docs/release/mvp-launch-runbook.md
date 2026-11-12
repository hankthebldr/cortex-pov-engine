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

> **Beta addendum (2026-11-10, updated after round 2).** §7 is the Windows
> onboarding procedure for beta DCs, and §8 lists known issues, open
> decisions and deferred findings; §8.5 is the upgrade, backup and rollback
> procedure. Read them **before** the first customer endpoint. The Windows
> side has run in exactly one place: the `windows-2022` CI job, on a
> GitHub-hosted runner with Defender real-time protection off and no XDR
> agent. It was green at `d7f4f29` and, on the round-2 code, at `fb0f646` and
> `e434efa` (run 43037077022, all nine jobs green). No shipped scenario and no
> customer endpoint has run it (§8.4). `tenant-verified` is 0.

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
| `check-lab-ready` | PASS | manifest regenerates byte-identical. Since round 2 it also runs in CI's `refs` job and in `make validate`; the manifest was regenerated at `563127b` (Steps 789) |
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
- **Windows pull has run only on a CI runner; it is not proven on a customer
  host.** The image serves a correct `PE32+` and a PowerShell installer. The
  `windows-2022` CI job ran green at `d7f4f29` (2026-11-10) and on the round-2
  code at `e434efa` (2026-11-11, run 43037077022): the beacon's tests, the
  installer, the service handler's start, a pull run, an abort and the
  uninstaller, on a hosted runner, against CI probe scenarios. None of the 13
  shipped `.ps1` bundles has executed on any OS. Write "ran green in CI",
  never "works on Windows" (§7, §8.4).
- **The Rust tool matrix is baked, not servable.** There is no
  `/api/tools/binary/{tool}` route; `docker cp` is still the only way off the
  image.

---

## 7 · Beta: onboarding a Windows endpoint (read before the first one)

> **Run in CI; not proven on a customer host.** The install, verify,
> pull, abort and uninstall steps below ran green in the `windows-2022`
> CI job, most recently on the round-2 code (run 43037077022 at `e434efa`;
> first at `d7f4f29`). That runner was a GitHub-hosted
> Server 2022 with Windows PowerShell 5.1, an elevated admin, code page 1252,
> Defender real-time protection off and no XDR agent, and it reached SimCore
> as `http://127.0.0.1:8888`. Everything else here is read from the source:
> the non-elevated path, a host with XDR, a SimCore reached by name or over
> https, reboot, and every shipped `.ps1` bundle. Lines marked *(round 2)*
> changed after `d7f4f29`; run 43037077022 ran the ones its phases reach, and
> each one it did not reach says so. If something here
> disagrees with what you see on a real host, the host wins. File it (§8.4)
> with the `stage=` / `code=` line it printed.

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
   `http://JUMPBOX:8888`, that name must be in `CORTEXSIM_ALLOWED_HOSTS`.
   `scripts/dev-up.sh` seeds the jumpbox's own names when it writes `.env`; over
   an existing `.env` it does so for one bring-up only, unless you run
   `scripts/dev-up.sh --apply` (§8.5). Otherwise every poll is
   `400 HOST_NOT_ALLOWED`. An IP literal always passes. Since round 2 the
   console's Get started warns when the typed name will be refused, the
   installer route refuses such a `?server=` with `400 SERVER_HOST_NOT_ALLOWED`,
   and `/api/health` lists recently refused names (`api_auth.last_refused_hosts`).
3. **Know what you will measure.** Only steps that resolve for Windows run.
   13 scenarios resolve for Windows, on pull and push alike (9 also POSIX, 4
   Windows-only); the other 180 are refused at a Windows launch. 69 carry a
   Windows-labelled step. The console's launch preflight reads the server's
   `bundle_targets`. There is **no Windows canary**
   (`CORTEXSIM_CANARY_SCENARIO_LINUX` only).
4. **One beacon per host.** Both installers use a fixed service name
   (`cortexsim-agent` on Linux/macOS, `CortexSimAgent` on Windows), so
   installing against a second SimCore replaces the beacon already there.

### 7.2 Install (pull mode)

Mint a token in the console (Targets → Deploy agent), then on the endpoint, in
**Windows PowerShell opened as Administrator**:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; $env:CORTEXSIM_TOKEN='cxs_…'; iwr -useb "http://<simcore-host>:8888/api/agents/install?os=windows" | iex
```

- It registers the **`CortexSimAgent`** service (LocalSystem, auto-start,
  restart on failure) and logs to `%ProgramData%\CortexSim\agent.log`. It
  prints `OK` only when the service is Running, still Running 35 s later, and
  the beacon has polled SimCore. **One install has been timed: about 47 s**,
  on the CI runner (the 35 s hold, then check-in confirmed after 10 s). Allow
  up to about 70 s, which is the installer's own waits added up (35 s plus up
  to 30 s check-in), not a measured range; a slow host or proxy can take
  longer.
- **Not elevated** → `NOT_ELEVATED`, and nothing changed. Do not reach for
  `$env:CORTEXSIM_ALLOW_UNSUPERVISED='1'` unless you mean it. It gives a
  per-user beacon that **dies at logoff and at reboot**, cannot run admin-only
  TTPs, and ends `DEGRADED_NO_SUPERVISOR`.
- **Proxy.** The service runs as SYSTEM and does **not** inherit your proxy
  settings, and the beacon does not read the Windows system proxy
  (WinINET/WinHTTP, PAC, WPAD). If the endpoint can only reach SimCore through
  a proxy, set a machine-level `CORTEXSIM_AGENT_PROXY` (an http, https or
  socks5 URL, or `direct`) before installing. The beacon's `--proxy` flag does
  the same, but the installer does not yet write it into the service. The
  beacon logs its route to SimCore at startup. `CORTEXSIM_NO_PROXY=1` makes
  the installer itself go direct. *(round 2)*
- **Air-gapped.** Pre-stage the exe and set `$env:CORTEXSIM_BIN` to its path.
  It is digest-checked when SimCore can serve a digest, otherwise
  `CHECKSUM_SKIPPED`.
- **Downloaded instead of piped.** The console's deploy modal and Get started
  now offer this path beside the one-liner: the installer link (with your
  SimCore address baked in), the beacon link, its digest, and a
  `Get-FileHash` line that must print `True` *(round 2)*. Then run
  `Unblock-File .\install-cortexsim-agent.ps1; $env:CORTEXSIM_TOKEN='cxs_…'; powershell -NoProfile -ExecutionPolicy Bypass -File .\install-cortexsim-agent.ps1`.
  Without the token set, every host run this way claims the same baked-in id.
  Whether XDR or Defender in prevent mode allows either path is untested.
- **Assume the token is in your shell history.** The env-var form keeps it out
  of the URL and proxy logs. The pasted line itself can still be recorded:
  PSReadLine writes it to `ConsoleHost_history.txt` unless its version filters
  lines that look sensitive, which is not verified for the module Windows
  PowerShell 5.1 ships. On Linux/macOS, bash records it unless the line starts
  with a space and `HISTCONTROL` ignores such lines. Mint single-use,
  short-TTL tokens.
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
| died around 30 s, error 1053 | Expected from a beacon build with no service handler (inferred from the SCM contract; never observed). Rebuild SimCore's agent-dist from a current tree. A current build stayed Running in CI. |
| `SERVICE_EXIT_CODE` 3 | Did not stop within 60 s of a stop and was forced STOPPED. An in-flight run may read as orphaned. |
| `SERVICE_EXIT_CODE` 1 or 2 | The service command line failed validation or flag parsing. `agent.log` has the error. |
| `SERVICE_EXIT_CODE` 4 | `RegisterServiceCtrlHandlerExW` failed. |

### 7.4 Uninstall

Elevated:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; iwr -useb 'http://<simcore-host>:8888/api/agents/install?os=windows&uninstall=1' | iex
```

It is idempotent and removes the service, `%ProgramData%\CortexSim`, the
current account's per-user install, and the beacon's cache and staging roots.
Those roots hold staged dual-use tools: **run the uninstaller before you leave
the customer's host.** It ran once, elevated, in CI ("removed 2 of 2
item(s)"). It ends `UNINSTALL_INCOMPLETE` if anything it found survives. A
per-user beacon (`CORTEXSIM_ALLOW_UNSUPERVISED`) must be removed by the account
that installed it, from a normal (not elevated) PowerShell. An elevated run
under another account stops the process and **reports** that profile's files,
but never deletes inside another user's profile *(round 2)*. A
`1072 / SERVICE_PENDING_DELETE` means `services.msc` or Event Viewer is holding
the service open, so close them or reboot. Deleting the agent in the console
removes only the registry row, and is refused with `409 AGENT_HAS_OPEN_RUNS`
while a run is owed to or in flight on it: abort those runs first. POSIX
hosts, run as the user who installed:
`curl -fsSL '<server>/api/agents/install?uninstall=1' | bash`. Use
`| sudo bash` for a root install; a root run also removes user-mode installs
under `SUDO_USER`, `/home` and `/Users` *(round 2)*.

### 7.5 What a Windows run is, and is not

- **Steps run as the service's account, `NT AUTHORITY\SYSTEM`.** That follows
  from the LocalSystem registration; no run has asserted it yet. Each Windows
  step frame now carries `[cortexsim] executed as <account> (elevated token |
  token NOT elevated)`, so read the account there *(round 2)*. XDR causality
  should show SYSTEM, not the admin who installed it. Every non-`direct`
  identity collapses to that account, with `!! IDENTITY NOT HONOURED` in the
  run record and the POV report. A lineage-keyed miss on such a step is
  **not** a customer gap.
- **Variant steps** (`!! PLATFORM VARIANT`) ran a Windows command whose expected
  detections were authored against a different one (decision BEACON-01).
- **Pull mode never runs scenario cleanup.** The POV report lists the commands
  it did not run under "Cleanup Not Run". Remove the residue by hand.
- **A missing tool is now named on Windows** *(round 2)*: a step's
  `required_binaries` yield `TOOL_ABSENT`, and a missing or alias-only
  `requires_interpreters` entry blocks the step. No current Windows step
  declares either, so today this changes nothing you will see.
- **A step longer than 11,361 characters is refused** as
  `COMMAND_LINE_TOO_LONG` before launch *(round 2)*; the longest corpus step is
  3,439.
- **A chained run that is aborted or fails sweeps its Job Object** on Windows;
  a clean one leaves a step's background children running, as before
  *(round 2)*.
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
  egress host you own. `SIM-NDR-005` needs `CORTEXSIM_EXFIL_URL`, an http(s)
  receiver you own that the NGFW sees as external (its volume BIOC excludes
  RFC1918). When you run the card's pre-flight XQL, substitute your host for
  `exfil-receiver.testmynids.org` in its `dst_host` filter *(round 2)*.
- **Read the IDENTITY column.** 19 steps in 5 bundles (`SIM-EDR-005`,
  `SIM-EDR-013`, `SIM-EDR-017`, `SIM-MP-001`, `SIM-MP-020`) run as you, not as
  their declared service account, and read `DEGRADED`; the run ends
  `cortexsim-identity-summary degraded=N ran=M`. A detection keyed on the
  declared account will not match those steps *(round 2)*.
- **Managed hosts.** A Group Policy execution policy outranks
  `-ExecutionPolicy Bypass` for the bundle and every child step, and the run
  logs `cortexsim-policy-warning reason=EXECUTION_POLICY_GPO` when it is
  AllSigned or Restricted. AMSI or the endpoint agent can block the whole file
  before step 1, with no per-step attribution *(round 2)*.
- **Not run on Windows.** None of the 13 shipped bundles has executed on any
  OS. CI parsed all 13 under 5.1, the round-2 header and IDENTITY table
  included, at `e434efa` (run 43037077022), and ran a probe bundle whose table
  carried the IDENTITY column. The GPO policy warning has not fired on Windows:
  the runner's MachinePolicy and UserPolicy are Undefined.
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
| **Windows: green in CI only, no customer host** | — | The `windows-2022` job ran green at `d7f4f29` and on the round-2 code at `fb0f646` and `e434efa` (run 43037077022) on a hosted runner with no XDR and Defender real-time off. It is not a required check (DOCS-12). None of the 13 shipped bundles has run anywhere (§8.4). |
| **Abort evidence before round 2** | An aborted step's `step_outcomes` read `termination` / `rc` empty, on the Linux lab VM and in the `windows` job | Fixed on the server in round 2 (RG-RUNS-01): a late `/complete` on an aborted, delivered pull run now records `steps[]`. Pinned by a host test, and observed on Windows in run 43037077022 (`termination=aborted rc=130`); not re-run on the lab VM. A beacon that dies before its `/complete` still leaves the row unjudged, but since `271320d` no alert is credited to a step its host never reported: the row stays `pending`. |
| **Glued step frames before round 2** | `--- exit_code=0 duration=10ms ---=== STEP 2/4 …`, so anything matching `^=== STEP` saw only the first step | Fixed in the beacon (every frame ends with a newline). Runs recorded by an older beacon keep the glued text. |
| **`docker ps` healthy is liveness only** | `healthy` while `/api/health` reads degraded | The image `HEALTHCHECK` checks that the process answers, and Docker does not restart an unhealthy container. Read `/api/health` for faults. *(Authored; no container ran in this pass.)* |
| **Rollback is not symmetric** (OPS-17) | After a rollback to 1.1.0 or older, every launch is a 500 | Back up the volume before upgrading; roll back by restoring it (§8.5). |
| **Host test runs are not the image's stack** (OPS-20) | — | The repo venv has uvicorn 0.31.1 / httpx 0.27.2; the image pins 0.29.0 / 0.27.0. Trust the in-image backend job for server-lifecycle behaviour. |
| **Unsigned Windows beacon** (CI-06) | Quarantine / block | Exclusion plus published sha256 (§7.1). |
| **Correlation share is exactly at its floor** | — | 10.0% (137 of 1369). The next scenario without a Correlation row turns `coverage-strict` red. |
| **Install attempts reset on restart** | `GET /api/agents/install/attempts` empty after a restart | They live in memory (a deque of 100). Use `GET /api/agents` `registered_at`. |
| **MTTD is launch-to-alert** (OPS-07) | MTTD includes queue wait and earlier steps | Use `POST /api/runs/{id}/observations` with the alert's own timestamp, or reconcile. **Do not** read MTTD from `PUT /api/results/{id}/validate`: that records click latency (DOCS-04). |
| **Push runs read `pending` until scoped** (DOCS-03) | `UNSCOPED_MATCH` | Pass `?host=` / `host` (§7.6). |
| **Multi-endpoint pull liveness** (OPS-13, deferred) | A fan-out can hang | Rare in beta, and the failure is a hang, not a false claim. Single-endpoint pull is the beta path. A retried `/complete` no longer closes a fan-out early once the beacon names its task (round 2). |
| **Push bundles are not consent-gated** (PUSH-08) | `GET /download` hands out dual-use tooling with no prompt | Decision open. Treat a downloaded bundle as you would the tool itself. |
| **An agent row with work owed cannot be deleted** (OPS-08) | `409 AGENT_HAS_OPEN_RUNS` with `run_ids` | Abort those runs (`POST /api/runs/{id}/abort`, or Abort beside the row in the console). Nothing expires on its own: there is no queued-task TTL. |
| **The Windows system proxy is not read** (BEACON-19) | A beacon behind a proxy never polls | Set a machine-level `CORTEXSIM_AGENT_PROXY`; the installer does not yet write `--proxy` into the service. |
| **macOS pull of a Linux-only scenario** (RG-UI-01) | SimCore dispatches the Linux command to a macOS beacon unchanged, with `!! PLATFORM MISMATCH` on the run and in `warnings[]` (`77f80b4`) | The console's preflight blocks it; the server marks it and does not refuse it. A step's `macos` variant is still not preferred on a Mac (§8.6). |
| **Windows `SERVICE_START_FAILED` is a stop; POSIX is a warning** | — | The POSIX installer reports it and falls back to `nohup`; the Windows installer stops. The console renders each accordingly. |
| **PSScriptAnalyzer unpinned** | — | The green run installed 1.25.0. Pin it with `CORTEXSIM_PSSA_VERSION=1.25.0` (`TODO(pssa-pin)`). |
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
| CI-02, DOCS-02, UI-01 | Keep the stdlib SCM handler (vs a SYSTEM scheduled task)? | SCM handler; its start path ran in three green CI runs (Running 75 s after install, no SCM error events) | Keep; say "started and ran in CI", not "service works", until reboot and stop are observed |
| CI-06 | Authenticode-sign the Windows beacon before GA? | Unsigned; hashes + buildinfo published | Sign before GA |
| CI-11 | One Go toolchain for CI, Dockerfile and `go.mod`? | Mixed (1.21 / 1.22) | Pin a supported toolchain everywhere after a SHA-1 cert test |
| SEC-01 | Operator API authentication? | None; `api_auth` degraded, Host guard, CORS off, CIDR floor | Bearer token + split listeners |
| SEC-04 | Agent-plane auth? | None | Per-agent secret at enrollment |
| SEC-06 | Stop storing harvested credentials in run output? | Stored | Content discards secrets + server-side redactor |
| SEC-11 | Compose default `CORTEXSIM_ENV=production`? | `development` (placeholder secret refused in every env) | Yes |
| SEC-12 | Transport integrity over plain HTTP? | None (checksum is corruption-only) | Per-deploy CA, fingerprint pinned, before GA |
| OPS-07 | When does the MTTD clock start? | Launch time | `max(executed_at, delivered_at)` now |
| OPS-08 | Expire undelivered queued tasks? | No TTL; launch at a not-enrolled target refused; deleting an agent with work owed or in flight refused `409 AGENT_HAS_OPEN_RUNS` (round 2). Alternative not built: auto-cancel never-delivered runs on delete | TTL 30 min → `TASK_EXPIRED_UNDELIVERED` |
| DOCS-03 | Scope push-run reconcile to a host? | Unscoped → `pending / UNSCOPED_MATCH`; `?host=` / `host` override (ce9ed13); the alternative is reverting it to a docs-only note | Optional `target_host` at push launch + (c) |
| DOCS-04 | Should click-to-validate produce MTTD? | Yes (records click latency) | Exclude attested-only rows from MTTD |
| DOCS-12 | Gate B requires Windows evidence? | Job green three times (latest run 43037077022 at `e434efa`, the round-2 head); not a required check | Checkbox; require the job once first green (met at `d7f4f29`, and again at the round-2 head) |
| DOCS-15 | Publish beta scope, known issues and a feedback channel? | This section | `docs/release/BETA.md` + issue form, linked from README |
| UI-08 | Platform check keyed on server truth? | Both: `bundle_targets` consumed, 100 steps relabelled | Done |
| UI-11 | Elevation guidance in the console? | Honest trade-off text | Recommend elevation: the elevated path is the one the green job ran |
| UI-15 | Should the downloaded `.ps1` carry the token? | Tokenless; exact run line shown; the no-cradle path keeps it tokenless (round 2) | Keep |
| SEC-16 | How are enrollment tokens stored, and what are the mint ceilings? | Plaintext; up to 1000 uses and 30 days; `publish-agent.sh` refuses `--token` (round 2) | — |
| CONT-17 | Per-variant ATT&CK technique for a Windows variant step? | The `.ps1` names the technique as the primary's; the heatmap credits per scenario | Same schema question as BEACON-01 option (c) |
| RG-INSTALL-07 | Answer a non-Windows installer refusal with a shell script body instead of JSON? | Structured JSON, as for every route; the console keeps `curl -f` | — |
| INST-21 / SEC-12 | Give the beacon digest authenticity? | "Catches corruption, not tampering" in both installers (round 2) | Pinned CA, a signed beacon, or HTTPS |

### 8.3 Deferred findings

- **OPS-13**: multi-endpoint liveness needs the beacon to identify itself on
  `/control` and a per-(run, agent) delivery record. Deferred, because the
  failure is a hang, not a false claim.
- **SEC-03 (partial)**: the `docker.sock` mount is removed. A non-root `USER`
  in the image is deferred, because it cannot be verified without an image run.
- **CI-12**: closed in round 2. Every third-party action is pinned to a
  commit SHA (60 `uses:` lines, 13 actions), and Dependabot refreshes them. No
  runner has fetched a pinned action yet.
- **OPS-08 (partial)**: a launch at a not-enrolled target is refused, and so
  is deleting an agent with work owed or in flight (round 2). The queued-task
  TTL is still deferred (decision above).
- **OPS-16 (partial)**: the image has a liveness `HEALTHCHECK`. A memory or
  pids limit was declined, because SimCore's footprint has never been measured
  and a guessed cap would OOM-kill a run. Nothing restarts a hung SimCore.
- **Integration follow-ups:**
  - `make check-powershell` is now part of `make validate` / `make ci`
    (opt-out `CORTEXSIM_SKIP_POWERSHELL_GATE=1`, aggregates only).
  - `tests/tools/test_check_adapter_wiring.py::test_real_corpus_passes`
    shells out to `python3` and fails on a host whose system `python3` lacks
    PyYAML; put the repo venv first on `PATH`. It passed in the image in the
    backend job at `d7f4f29` and `e434efa`.
  - `scripts/check-adapter-sources.sh` reports `FAIL=2` in any worktree whose
    tier-2 submodules are not initialised (re-observed at `0b7a482`).

### 8.4 What the green `windows` runs proved, and what stays unproven

Run 43037077022 at `e434efa` (2026-11-11), the round-2 head, ran all 13 phases
green on a GitHub-hosted `windows-2022` runner: Windows Server 2022 Datacenter
build 20348, Windows PowerShell 5.1.20348 Desktop, FullLanguage, ANSI code page
1252 (OEM 65001), elevated `runneradmin`, execution policy unset at
MachinePolicy/UserPolicy, Defender antivirus on with real-time protection and
behaviour monitoring off. All nine CI jobs were green. Run 42933820773 at
`d7f4f29` had done the same on round 1's code. Quoted from the `e434efa` log and
its `windows-proof` artifact:

| Item | Observed in run 43037077022 |
|---|---|
| SimCore booting natively on Windows | yes: `boot` phase, degraded components `api_auth` and `payload_shelf` only (expected) |
| Beacon tests on 5.1 | "go vet ./... && go test ./... -count=1 exit 0 on native windows/amd64"; `pass=409 fail=0 skip=5`; "no TestPowerShell* test skipped on Windows"; all 43 distinct Windows-only test functions passed (round 2's teardown, step-length, `TOOL_ABSENT`, cache, shutdown and wrapper-note tests among them) |
| 5.1 parse of every rendered `.ps1` | `ParseFile` and `ParseInput` on 61 files: 13 bundles, 35 steps, 4 installers, the uninstaller, 5 beacon wrappers, 3 tracked scripts; the three control fixtures accepted or rejected as designed |
| PSScriptAnalyzer, 5.1 profiles | "62 file(s), 744 record(s), 0 failing" (1.25.0, two waivers, no `STALE WAIVER`) |
| Installer one-liner, elevated, under `iwr \| iex` | exit 0; "service CortexSimAgent is Running 75 s after install"; "no SCM 7000/7009/7011/7031/7034 events"; "last_seen advanced between the samples"; the beacon logged `mode=service` and its proxy route |
| Pull, exit codes and stderr | probe draft: rc 0, 0 with stderr, 3, then `not_run` `UPSTREAM_FAILED`; "stderr is plain text, not PowerShell CLIXML"; a cmdlet error rc 1, as text |
| The account a step runs as | every frame: `[cortexsim] executed as NT AUTHORITY\SYSTEM (elevated token)` |
| Step frames | every `=== STEP` header starts a line; none glued |
| Job Object tree-kill on abort | "within 15 s of POST /abort no ping.exe / powershell.exe remains in the beacon tree (1.3 s)"; a bystander `ping` survived; the aborted row read `termination=aborted rc=130` |
| `.ps1` push with `-File` | probe bundle exit 1; step-02 (exit 3) and step-03 (cmdlet error) not logged as successful; the table carried the IDENTITY column; `cortexsim-identity-summary degraded=0 ran=4` |
| Uninstaller, elevated | exit 0; "removed 2 of 2 item(s)"; service, `C:\ProgramData\CortexSim` and the process gone |
| cmd-dialect steps | the probe's two cmd steps ran on pull and push |

**Still unproven**, so attach evidence when you are the first to see one on a
real host: every **shipped** `.ps1` bundle (none of the 13 has executed on any
OS); every shipped scenario on Windows pull; reboot survival; the 60 s stop
deadline and `SERVICE_EXIT_CODE` readings; a real SCM stop during a run;
`taskkill /T` on a step timeout; the non-elevated refusal and the per-user
install; the uninstaller's other-profile sweep; the GPO execution-policy
warning; the Host guard admitting a Windows jumpbox's own (uppercase,
NetBIOS-style) name, since CI used `127.0.0.1`; an https SimCore under 5.1 TLS
defaults; a host with Cortex XDR or Defender in prevent mode (the unsigned
beacon, the `iwr | iex` cradle, AMSI); Server Core; code pages other than 1252,
including an OEM code page other than 65001; AppLocker / WDAC / Constrained
Language Mode; a domain-joined host; and any change after `e434efa` until the
job runs on it. Round-2 rows the phases do not reach (Windows teardown on
abandon, `TOOL_ABSENT`, the step-length guard) have run only as native unit
tests. Attach the installer's `stage=` / `code=` line, `agent.log`, `sc.exe qc`
/ `queryex` output and the bundle's outcome table to your report.
`tenant-verified` is still 0.

### 8.5 Upgrade, backup and rollback

`scripts/dev-up.sh` keeps an existing `.env` and the `simcore_db` volume, and
the columns added since 1.0.0 are added at boot by `database._migrate_*`. Do
**not** run the `ALTER TABLE … ADD COLUMN` lines older notes mention: SQLite
rejects a duplicate column (OPS-18). Rolling back is the risk. A database
created or migrated by current code has `runs.agent_profile_mode` and
`results.expected_outcome` as `NOT NULL` with no SQL default, so 1.1.0 or older
code fails every launch against it, with a 500 that does not name the schema.
There is no schema stamp (OPS-17, open).

1. **No run in flight.** `GET /api/runs?fields=summary` must show nothing
   `running` or `queued`. An upgrade mid-run loses the run (§3, OPS-03/04).
2. **Back up the volume** (the volume name is the compose project plus
   `_simcore_db`; `docker volume ls | grep simcore_db` shows it):
   ```bash
   docker run --rm -v cortex-pov-engine_simcore_db:/d -v "$PWD":/b alpine cp /d/cortexsim.db /b/cortexsim.db.bak
   ```
3. **Keep the same `.env`**, above all `CORTEXSIM_SECRET`; a new secret over a
   surviving volume makes every stored credential undecryptable
   (`MASTER_KEY_MISMATCH`). If the `.env` predates the Host guard, run
   `scripts/dev-up.sh --apply` to write `CORTEXSIM_ALLOWED_HOSTS` (the previous
   file is kept under `.env-backups/<UTC>/.env`); a bare `dev-up.sh` only
   admits this host's names for one bring-up.
4. **Upgrade:** `scripts/dev-up.sh`. Then check `/api/health`. Only
   `api_auth` should read degraded.
5. **Roll back** by stopping SimCore, restoring `cortexsim.db.bak` into the
   volume, and starting the older release. Do not point older code at the
   upgraded database.

*Authored from the code and from round 1's migration test (DBs built by
v0.1.0, v1.0.0, v1.0.2 and v1.1.0 code, then the current `init_db`); the
backup and restore commands were not run in this pass.*

### 8.6 Round 2: what closed, what stays open

Round 2 ran from `c852eac` to `0b7a482`. **Closed** (each commit body says
how it was verified; every Windows row's native tests passed in run
43037077022 at `e434efa`, and §8.4 says which ones its end-to-end phases also
reached): the CLIXML stderr on 5.1 (observed fixed in CI at `d7f4f29`); chain teardown on POSIX and the Windows abandon
sweep; a beacon stop that waited up to 62 s; the boot-registration retry; the
abort-evidence and duplicate `/complete` / `/output` defects; glued step
frames; the staging-refusal, orphan-delivery, un-crediting and retraction
defects in reconcile; agent delete with work owed; the long-poll handoff; the
POSIX uninstaller under sudo; the Windows other-profile delete; the TLS
setting the installer left behind; `jumpbox-01` in pre-enrollment telemetry;
Windows teardown in bash and K8s bundles; `CMD_PERCENT_CONTEXT`;
`SIM-NDR-005`'s third-party upload on Windows; `pushable_targets`; the
identity column; the GPO warning; `CORTEXSIM_TRUSTED_PROXIES` in `.env`; the
Host-guard trace and the `?server=` refusal; `BAD_INFRA_PARAM`; SHA-pinned
actions; and the refs, adapters and agent CI gaps.

**Stays open** (each is named in §8.1–8.3 or below):
- The SimCore single-worker lock (OPS-21): `--workers 1` is pinned in the
  image and the chart defaults to one replica, but nothing refuses a second
  worker started another way.
- A macOS beacon is sent a step's Linux primary even when the step declares a
  `macos` `platform_variants` entry (34 corpus steps, e.g. `SIM-EDR-003`'s
  `useradd` instead of `sysadminctl`). SimCore now marks a step whose labels
  exclude the beacon's OS (RG-UI-01, below), but preferring the variant
  changes what runs on a Mac and is left for a decision.
- The Windows installer does not write `--proxy` into the service
  (BEACON-19).
- The K8s manifest generator still renders a server name the Host guard will
  refuse (RG-GUARD-05; it lives on the container-agent branch).
- `SIM-NDR-005`'s Linux and macOS legs still upload to a hard-coded
  third-party host (CONT-16).
- An undefined `%NAME%` in a single-line cmd step reads differently on push
  and pull (PUSH-11 residual).
- A dead long-poll that no newer poll superseded can still be woken (OPS-09
  residual).
- The per-chunk "output appended" INFO line is not demoted (OPS-14, optional).
- Byte parity between host-built and image-built beacons (CI-16): the VCS
  stamp differs by design.

**Regression findings fixed on the branch.** These code findings were recorded
against round-2 commits and fixed: the elevated uninstaller's delete inside
another profile (`7d6187a`), the silent refused agent delete (`66084cb`), the
Windows `SERVICE_START_FAILED` shown as a warning (`33236a2`), the no-cradle
installer link baking the wrong address (`11c13ef`), the withheld POSIX
teardown lines with cmdlet-shaped arguments (`1aeb559`), and the IPv6 zone id
in `dc_ssh_cidr` (`e14dc03`).

**Closed after the round-2 head** (each re-checked as reproducing at
`7f8ae17` before the fix; each commit body records the test that failed
before it and passed after; host and httptest evidence only, no lab run):
- RG-UI-01, server half (`77f80b4`). A step whose `platforms` labels exclude
  a POSIX beacon's OS (a Linux-only step on a darwin beacon, `SIM-KOI-009` on
  a Linux one) now carries `!! PLATFORM MISMATCH` on `Run.output` and the
  launch `warnings[]`, read the way the console's preflight reads the labels,
  and the markdown report lists it. SimCore still does not refuse it and
  sends the command unchanged; the console's preflight still blocks it. The
  run-detail banner does not render this marker yet.
- RG-AGENT-04, residual (`46a827e`). `Control()` is bound to the shutdown
  context: a stop during a blocked `/control` poll returns at once (2.01 s in
  the in-step test, which is the poll interval) instead of after 30 s, and
  the run records "agent shutdown — task interrupted", not
  `CONTROL_CHANNEL_LOST`. Not observed under a real launchd or SCM stop.
- RG-RUNS-01, residual (`271320d`). On an aborted, delivered pull run an
  alert is credited only to a step the host reported (a `step_outcomes` row
  other than `not_run`, a step frame, or an in-process step). A dead or
  pre-`steps[]` beacon leaves the other rows `pending`, uncredited and never
  `missed`; the step that was running is credited once the late `/complete`
  arrives.
- RG-UI-05, residual (`b7e6e25`). `api_auth` publishes `local_host_names`
  and `local_fqdn_names` (`null` until the running guard has resolved its
  FQDN; health does no DNS). Get started admits the machine's own names and
  says POVengine *refuses* an unlisted name when both are known; with the
  FQDN unresolved it names the host name and says only the FQDN is unknown.

**Regression findings still open** (re-checked at the round-2 head):
- RG-AGENT-01, Windows half (decision). After a chained run that ends
  cleanly, Windows still leaves the steps' background processes alive, so a
  helper left by run N can answer run N+1. POSIX is fixed (`3527b70`).
- RG-INSTALL-07 part 1 (decision). A non-Windows installer refusal is still
  JSON, so `curl -fsSL … | bash` shows only curl's error and hides the 400
  code.
- RG-GUARD-05, K8s half. `core/engine/k8s_manifest.py` does not ask the Host
  guard, so a manifest can bake a `?server=` name SimCore will refuse. That
  file belongs to the container-agent branch. The installer half is fixed
  (`f529365`).
