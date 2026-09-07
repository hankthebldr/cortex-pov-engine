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

### Blocked here — must run elsewhere

| gate | why blocked |
|---|---|
| `check-refs` | runs inside the prod image |
| `check-agent-shelf` | asserts the BUILT image serves all 5 beacon targets |
| `make build` / image parity | Docker Hub blob CDN denied by egress policy |
| `check-rust-shelf` / `check-rust-exec` | same |
| docker-gated Tier-C e2e | same |

These are **not** failures. They are the Gate B image-parity requirement —
"the built image ships what the tree has" — and it is the one requirement this
sandbox structurally cannot satisfy. See §5.

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

# 2. Image parity — REQUIRED, and cannot be done in the cloud sandbox
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
  beacon or the installer.
- **The Rust tool matrix is baked, not servable.** There is no
  `/api/tools/binary/{tool}` route; `docker cp` is still the only way off the
  image.
