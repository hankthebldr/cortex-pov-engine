# Release pipeline and environment gates — design

**Date:** 2026-09-07
**Status:** proposed
**Issues:** #113 #114 #115 #116 #117 #118 #119 #120 #121 #122

> **Update 2026-09-08.** Two of the observations below were overtaken by manual
> remediation while this was being written: v1.0.0 and v1.0.1 have both now
> released successfully with real artifacts (§9), and `origin/dev` is restored
> (§5.4). Section 1 is preserved as the evidence that motivated the design —
> read it in the past tense. **Every structural cause it identifies is still
> open.** The pipeline was fixed by hand; it was not gated.

---

## 1. Problem

Three failures, one shape.

**The release pipeline has never succeeded.** Every `Release` run this repository
has ever had, for every tag, failed on the same three shellcheck findings in
`install.sh` — twice for `v0.1.0` on 2026-08-31, once for `v1.0.0` on
2026-09-07, seven days apart:

| run | tag | failed job | downstream |
|---|---|---|---|
| 33449519011 | v0.1.0 | `lint-shell` | `build-image` / `manifest` / `release` skipped at 0s |
| 33449619749 | v0.1.0 | `lint-shell` | same |
| 34135434023 | v1.0.0 | `lint-shell` | same |

`gh release list` is empty. No GHCR image, no `install.sh` asset, no
`SHA256SUMS`, no `manifest.json` has ever been published, while `README.md:157`
advertises a prebuilt image as "the intended fast path".

**Nothing noticed for seven days**, because `tag-on-main.yml` decides whether to
release by asking whether the *tag* exists. Once `v1.0.0` was tagged, a failed
release became indistinguishable from a successful one, and the workflow emits
`"nothing to release"` — which reads like success.

**The gate that caused it runs nowhere else.** `lint-shell` exists only in
`release.yml`, on tag push and manual dispatch. Fifteen CI jobs run on every PR;
the path between a merge and a shippable artifact runs on none of them. A gate
that runs on exactly one trigger can only ever first fail on that trigger — at
the most expensive moment, after the tag is public.

### 1.1 The generalisation

The same inversion runs through the whole pipeline:

```
ci.yml    backend · agent · ui · detection · refs · adapters · rust-dist    HARD
          e2e-isolated  (Tier-C assertion suite)                            HARD
          e2e-isolated  (Tier-C docker detonation)   if: label tier-c-e2e   SKIPPABLE
test.yml  python · go · vitest · ttp-static · ui-layout · installer         HARD
          e2e-stack     (compose up + smoke + playwright)  continue-on-error  SOFT
release.yml  lint-shell -> image -> manifest -> release      tag-only, 0-for-3
```

No workflow carries a `schedule:`, so anything too slow for PR CI runs never.

**Every static gate is hard. Every environment gate is soft.** The two rungs
where execution defects actually live are precisely the two that cannot block a
merge, while corpus linting and unit tests are hard gates. Gating is inverted
relative to risk.

Three distinct mechanisms render *"did not run"* as *"passed"*, and the repo's
own comments already record two of them:

- `continue-on-error` — `test.yml:132`; the comment at `test.yml:214` says
  *"a soft-failing check reads exactly like a passing one"*.
- label gating — `ci.yml:420`; `CLAUDE.md` already states *"a job that can be
  skipped reads exactly like a passing one."*
- `needs:`-skip — `test.yml:150` records this having already bitten, when a
  missing `pyyaml` went unnoticed because the job that would have caught it was
  skipped by a failing dependency.

### 1.2 Why this is the repo's own rule, inverted

`CLAUDE.md` Gate A5: **"A zero is degraded, not ok. 'None shipped' and 'none
authored' must never render as the same response."** `/api/health` enforces it —
booting without `tools/` reports `degraded`, never `{status: ok, count: 0}` —
and publishes `not_checked[]`, the claims it deliberately does *not* make.

The pipeline commits the defect the product refuses to commit, at three separate
points. This design applies the product's own honesty grammar to the pipeline
that ships it.

---

## 2. The environment ladder

For this product the ladder is not dev/test/prod. It is **a ladder of what is
real**, and it should mirror the `AUTHORED · CONFIGURED · REACHABLE · VERIFIED`
grammar the Readiness console already ships.

| Rung | What is real | What it proves | What it **cannot** prove | Trigger | Gate |
|---|---|---|---|---|---|
| **L0** local | nothing; injected transport | code compiles, logic holds | anything about a host | `make ci` pre-push | advisory |
| **L1** CI | injected transport, network-denied (#109) | corpus loads, refs resolve, image builds | that anything executes | every PR | **hard** ✅ |
| **L2** integration | real compose stack, real beacon, sinkholed detonation (Tier-C/D) | the lifecycle runs; identity harness resolves | behaviour on a customer-shaped host | PR + nightly | **hard** ❌ today |
| **L3** UAT / lab | real targets, default-deny egress, real push bundles + payload shelf | `lab_readiness` GREEN is *proven*, not computed | that a detection fired | release candidate | **hard** — does not exist |
| **L4** tenant | a live Cortex tenant | `tenant-verified` moves off **0** | — | manual, opt-in | reported, never gated |

**L4 is never a CI gate.** A customer tenant is not a test fixture. It is
reported into Readiness and nothing else.

### 2.1 Scope of this design

**In scope — L1 and L2.** Sections 3–6.

**Out of scope — L3 and L4.** L3 (a `lab-verify` harness that executes
`lab_readiness.py`'s GREEN set against provisioned targets and emits
`classify.py` verdicts) is its own program and gets its own spec. It is named
here only so the ladder is legible; nothing in this design depends on it.

---

## 3. Component: `release-core.yml` — release rehearsal

Closes #115, #117.

Extract `release.yml`'s body into a reusable workflow with a `publish` input.
One definition, two modes.

```
.github/workflows/release-core.yml
  on: workflow_call
  inputs:
    tag:     string   — the version under test ("v0.0.0-pr" in rehearsal)
    publish: boolean  — false: build and package only. true: push and release.

  ci.yml      -> release-core.yml  publish: false   # every PR
  release.yml -> release-core.yml  publish: true    # tag push / dispatch
```

| stage | `publish: false` | `publish: true` |
|---|---|---|
| `lint-shell` | run | run |
| version coherence (§4) | run | run, plus tag equality |
| image build | build, single-arch, **no push** | build, multi-arch, push to GHCR |
| stage2 packaging | package, **no upload** | package, upload |
| `manifest.json` | generate, assert well-formed | generate, upload |
| `verify-release` (§6) | skipped | run |

The release path becomes exercised on every PR **by construction**, not by
remembering to duplicate a check.

**Rejected alternative — a separate `release-preflight` job** duplicating the
cheap checks. It is a copy, and copies drift. That failure mode is already
demonstrated in this very workflow: `release.yml` globs
`installer/bootstrap/*.sh` and `installer/stage2/{linux,windows}/`, **none of
which exist**, so `lint-shell` lints 2 files while claiming to cover "installer
+ bootstrap scripts", and the Windows installer asset silently never ships.
This is the same principle as `payload_shelf.py::compose()` being the one
resolver and `queries.py::result_rows` being the one unwrapper.

### 3.1 Two guards folded into the same change

Both are properties of this gate, not separate work.

- **Pin shellcheck.** `apt-get install -y shellcheck` on `ubuntu-22.04` resolves
  to whatever the runner image ships (0.8.0 today). A runner-image bump can fail
  a release with zero code change.
- **Fail on an empty glob.** Every path `release.yml` globs must resolve to ≥1
  file or the job errors. Same rule as the corpus loaders: *an unrecognised
  shape must raise, not read as empty.* `nullglob` currently swallows three of
  four patterns in silence.

**Explicitly not in scope: lowering the severity floor.** PR #111 considered and
correctly rejected `shellcheck -S error` — it would green the build by silencing
every future real warning in these scripts. The fix there (three targeted
`# shellcheck disable=` directives, each carrying its reason) is the right shape
and stands.

---

## 4. Component: `check-version-coherence.sh`

Closes #116.

The version is declared in **six** places. A coherence gate exists, covers
**two**, and runs in no workflow.

| # | site | covered today |
|---|---|---|
| 1 | `CHANGELOG.md` first `## [X.Y.Z]` — drives the tag (`tag-on-main.yml:39`) | ✅ |
| 2 | `core/main.py:260` `version="…"` | ✅ |
| 3 | `.env.example:65` `CORTEXSIM_VERSION=` | ❌ |
| 4 | `docker-compose.yml:12` `${CORTEXSIM_VERSION:-…}` | ❌ |
| 5 | `docker-compose.yml:13` `${CORTEXSIM_VERSION:-…}` | ❌ |
| 6 | `ui/package.json:3` `"version"` | ❌ |
| — | git tag | ❌ |

`core/config.py:50` defaults to `"unknown"` and is env-supplied. That is correct
and stays — it is the one site that must not carry a literal.

The existing check lives at `scripts/launch-preflight.sh:148` and compares only
sites 1 and 2. It would have caught neither #107 nor #110, the two
`CORTEXSIM_VERSION` fix PRs already merged in a single day, because neither
touched `core/main.py`.

**The silent failure it must catch:** `docker-compose.yml` carries the literal
`1.0.0` twice as a `:-` fallback. Bump `.env.example` to `1.1.0`, forget compose,
and a developer without a local `.env` builds 1.1.0 code into an image **tagged
1.0.0**, with a matching container name. Nothing errors. The artifact is
mislabelled — the worst outcome for a tool whose thesis is that authored is not
proven.

**Interface.** Lift the logic *out* of `launch-preflight.sh` into
`scripts/check-version-coherence.sh` and have `launch-preflight.sh` call it, so
there is one definition rather than two that drift.

```
scripts/check-version-coherence.sh [--tag vX.Y.Z]

  exit 0   all six agree (and equal --tag when given)
  exit 1   mismatch — EVERY disagreeing site named, not just the first
```

Reporting every site in one run is the same discipline as
`POST /api/connectors/{kind}/preflight`: every stage reported even when an
earlier one degraded.

Consider in the same change: delete the `:-1.0.0` fallbacks so compose fails
loudly on an unset var rather than defaulting to a stale literal.

---

## 5. Component: environment gates go hard

Closes #118, and #114 for the release-state half.

### 5.1 Un-soften what exists

1. **Remove `continue-on-error` from `e2e-stack`.** If the legacy Playwright
   specs still fail after the Mission Ops Console migration, `test.skip` the
   specific stale specs with a named tracking issue. A skipped named spec is
   honest; a soft-failing job is not. The toggle was introduced as temporary —
   *"the follow-up spec-migration PR will remove this toggle in the same change
   set"* — and was not removed.
2. **Promote the Tier-C docker detonation** from label-gated to unconditional on
   the nightly schedule, retaining the `tier-c-e2e` label as a way to opt *in*
   on a PR.
3. **Add job-status aggregation.** An `if: always()` gate job that fails the
   check run when a required job was *skipped*, so a skipped job is
   distinguishable from a passing one.

### 5.2 Add the nightly

```
.github/workflows/nightly.yml
  on: schedule: cron nightly, workflow_dispatch
  jobs:
    l2-detonation    Tier-C docker end-to-end, unconditional
    gate-b           make launch-preflight  (§5.4)
    release-rehearse release-core.yml  publish: false, multi-arch
```

Anything too slow for PR CI runs here rather than never.

### 5.3 Release state becomes a first-class state

`tag-on-main.yml` keys idempotence on **release existence**, not tag existence:

```
tag missing         -> tag it, dispatch release.yml
tag + release       -> no-op       "v1.0.0 already released"
tag, NO release     -> DISPATCH    "v1.0.0 tagged but not released — retrying"
```

`gh release view "$TAG"` is the probe. A tagged-but-unreleased version is a
**degraded** state the workflow announces and acts on, never a silent skip.
Three states, three distinct log lines.

### 5.4 The three-outcome contract

Closes #121. `scripts/launch-preflight.sh` reports three outcomes; GitHub
Actions has two. The mapping must not lose the third:

```
exit 0  -> job passes
exit 1  -> job fails
exit 2  -> job FAILS, with an explicit "verdict withheld" annotation
           — never neutral, never skipped, because those render as passing
```

Placement is a `gate-b` job on release-bound PRs plus the nightly — **not** on
every topic-branch PR. The script's own header says several checks are
meaningless off a release merge, and a check that is meaningless where it runs
teaches people to ignore it.

**#119 — restored 2026-09-08, guard still owed.** `origin/dev` was missing when
this was written, which would have made the branch-topology and
`version-advances` checks exit 2 by construction. It is now back
(`refs/heads/dev` @ `8e9c18d`), so `gate-b` is unblocked.

The restoration was manual, so the half of #119 that matters is still open: **a
check that every branch `CONTRIBUTING.md:35` calls permanent actually exists on
the remote.** `dev` went missing for four days across five direct-to-`main`
merges without a single failing job. Restoring it by hand does not stop that
recurring — a documented invariant with no check is exactly how it vanished.

---

## 6. Component: `verify-release`

Closes #120.

`release.yml` ends at `gh release create`. Nothing verifies the published
artifact. Both existing parity gates — `check-agent-shelf`, `check-ui-shelf` —
assert the **locally built** image; neither pulls from GHCR.

`CLAUDE.md` records how that gap behaves: the Dockerfile agent-builder listed 4
targets while `build-agent-dist.sh` emitted 5, so *"`make agent-dist` on a dev
box emitted 5 while every deployed image shipped 4. CI never noticed because the
`agent` job only proves the beacon compiles for Windows, never that the image
ships it."* Same shape, one level up: CI proves the image the **tree** builds is
correct; nothing proves the image the **registry** serves is that image.

A terminal job in `release-core.yml`, `publish: true` only:

```
docker pull ghcr.io/<owner>/cortexsim:<tag>          # both platforms
docker run  -d …
assert GET /api/health          -> ok, degraded_components empty
assert route count              == the tree's expected count
assert GET /api/agents/binary?os=windows&arch=amd64 -> 200, PE32+
assert GET /api/scenarios       -> the counted corpus size
sha256 every release asset      == its manifest.json entry, BY RE-DOWNLOAD
```

Two rules are load-bearing:

- **Re-download to verify.** `manifest.json` is generated from the same bytes it
  describes, so today it is self-consistent by construction and proves nothing
  about what GitHub actually stored.
- **A zero is degraded, not ok.** `GET /api/scenarios` returning `[]` fails this
  job. It must not pass, for the same reason `/api/health` refuses to report
  `{status: ok, count: 0}`.

---

## 7. Verdict vocabulary for L2 and above

`deploy/tier-d/classify.py` already implements the right taxonomy, for the
product under test. It should become the **pipeline's** vocabulary at L2+:

```
ENGINE       a real CortexSim defect      -> fails the gate
ENVIRONMENT  the target could not run it  -> does NOT fail; is RECORDED
TTP          ran, legitimately negative   -> signal
INCONCLUSIVE cannot prove what happened   -> fails, verdict withheld
```

This four-way split is what makes environment-level automation tractable at all.
Most pipelines cannot gate above L1 because they only have pass/fail, so a flaky
lab host and a real regression look identical, and the suite gets marked
`continue-on-error` — which is exactly what happened to `e2e-stack`.

---

## 8. The demotion loop

The discipline that makes this hold, and the part that is not tooling:

```
defect found at rung N
  -> classify: ENGINE | ENVIRONMENT | INCONCLUSIVE
  -> write the regression at the LOWEST rung that can express it
  -> if it cannot be demoted below N, record it in a "provable only at L{N}"
     register — never silently; an undemotable bug is a known blind spot,
     not a closed ticket
  -> the register IS the spec for what L{N} automation must cover
```

All ten defects filed this session demote to **L1**:

| issue | demoted regression |
|---|---|
| #113 | (recovery, §9) — prevention is #115 |
| #114 | guard test: tagged-but-unreleased dispatches |
| #115 | `release-core.yml` rehearsal on every PR |
| #116 | `check-version-coherence.sh` |
| #117 | empty-glob guard |
| #118 | hard gates + nightly + skip aggregation |
| #119 | permanent-branch existence check |
| #120 | `verify-release` |
| #121 | `gate-b` job, exit 2 fails |
| #122 | action version bump |

---

## 9. Recovery of v1.0.0 — DONE 2026-09-08

Closes #113. Recorded here as history; no action remains.

### 9.1 What was wrong

The fix (`021d077`, merged `99665f0`) landed **two commits after** the tag
(`v1.0.0` -> `9c2b78a`). `release.yml` checks out the *tag's* tree, so
re-dispatching at `v1.0.0` reproduced the same three findings. PR #111 unblocked
v1.0.1+, not v1.0.0.

Dispatching at `--ref main` instead would **not** have been equivalent: it builds
the image from `99665f0` while creating the Release against tag ref `9c2b78a`,
baking an image/tag mismatch into `manifest.json`.

### 9.2 What was done — verified, not asserted

Both recovery paths were taken. Measured 2026-09-08:

```
$ git ls-remote --tags origin refs/tags/v1.0.0
99665f09f6dcada6f1b3a5013d12904a0af077d5    refs/tags/v1.0.0      # moved onto main

$ gh release list
CortexSim v1.0.1          v1.0.1   2026-09-07T19:57:54Z
CortexSim v1.0.0  Latest  v1.0.0   2026-09-08T13:50:09Z

$ gh run list --workflow=release.yml
success  Release  v1.0.0  push               34232499912  18m39s
success  Release  v1.0.1  workflow_dispatch  34156539870  16m29s
```

Both releases carry real assets — `install.sh` (29 310 B), `manifest.json`,
`SHA256SUMS`, `stage2-linux.tar.gz`, `stage2-windows.zip`.

**The release pipeline has now succeeded twice.** The 0-for-3 record in §1 is
history.

### 9.3 What this does NOT resolve

The recovery was manual. Every structural cause in §1.1 stands untouched:
`lint-shell` still runs only at release time (#115), `tag-on-main.yml` still keys
idempotence on tag existence (#114), and nothing verifies the published artifact
(#120) — the assets above were confirmed by hand, by this spec's author, not by a
gate.

**A green release is not a gated release.** Two successes prove the tree is
currently fixable, not that the next regression will be caught.

---

## 10. Testing

Every gate in this design must be proven to fail without its fix. This is Gate A
in `CONTRIBUTING.md`: *"a test never observed failing is an assumption with good
syntax."*

| gate | the failure it must be observed to catch |
|---|---|
| `release-core` rehearsal | a PR that breaks `install.sh` shellcheck fails CI |
| empty-glob guard | a PR that deletes a globbed directory fails CI |
| version coherence | bumping `.env.example` alone fails, naming the other five sites |
| release-state idempotence | a tagged-but-unreleased version is re-dispatched |
| `e2e-stack` hard | a deliberately broken smoke test fails the PR |
| skip aggregation | a skipped required job fails the check run |
| `verify-release` | a deliberately broken published image fails the job |
| permanent-branch check | deleting `dev` fails CI |

---

## 11. Sequencing

```
0.  #119  restore or amend the branch model        (blocks #121)
1.  #118  un-soften environment gates + nightly     cheapest, highest value
2.  #116  check-version-coherence.sh
3.  #115  release-core.yml + #117 glob guard
4.  #120  verify-release
5.  #121  wire launch-preflight as gate-b
6.  #114  release-state idempotence
7.  #122  action bumps
```

Step 1 first because it is the precondition for trusting any result above L1:
until the environment gates are hard, a green run at L2 means nothing.

`#113` (v1.0.0 recovery) runs independently, as soon as step 2 of §9 is
authorised.

---

## 12. Out of scope

- **L3 lab verification** — its own spec.
- **L4 tenant verification** — reported, never gated. `tenant-verified` is 0 and
  nothing in this design changes that.
- **Semver upgrade-path and N-1 image compatibility testing** — premature. There
  are zero published versions; there is no upgrade path to test. Revisit at v1.2.
- **Lowering the shellcheck severity floor** — considered and rejected in #111.
