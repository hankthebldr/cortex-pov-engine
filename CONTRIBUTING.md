# Contributing to CortexSim

CortexSim generates controlled detection signal into **customer** Cortex
environments. A defect here does not produce a broken build — it produces a
**false claim about a customer's security coverage**, in a document a Domain
Consultant shows that customer. The branching model and QA process below exist
for that reason and no other.

The governing principle everywhere in this repo:

> **Authored is not proven.** A gate that cannot fail proves nothing. If a check
> would pass on a build where the feature is absent, it is not a check.

---

## 1. Branching model

Three permanent tiers. Work always flows **up**, never sideways.

```
  feature/*  fix/*  docs/*  chore/*      <- short-lived, one task each
        |
        |  PR  ==>  QA Gate A (automated + review)
        v
      dev                                 <- integration trunk, always green
        |
        |  PR  ==>  QA Gate B (release readiness)
        v
      main                                <- releasable. Tagged. Never pushed to directly.
```

| Branch | Cut from | Merges to | Lifetime | Who pushes |
|---|---|---|---|---|
| `main` | — | — | permanent | **nobody directly** — merge from `dev` only |
| `dev` | `main` | `main` | permanent | merge from topic branches only |
| `feature/*` etc. | `dev` | `dev` | hours to days | the author |
| `hotfix/*` | `main` | `main` **and** `dev` | hours | the author, with sign-off |

### Why `main` is never pushed to directly

`main` is what a DC deploys into a customer lab. The moment someone pushes to it
directly, "what is on `main`" and "what passed the gate" stop being the same
question — and the whole model becomes decoration. The single exception is
`hotfix/*` (§5), which is still a PR, just an expedited one.

### Naming

```
feature/<area>-<short-slug>     feature/api-bearer-auth
fix/<area>-<short-slug>         fix/docker-ships-assertions
docs/<area>-<short-slug>        docs/reference-count-reconciliation
chore/<area>-<short-slug>       chore/deps-cryptography-bump
hotfix/<area>-<short-slug>      hotfix/vault-key-rotation
```

`<area>` is the part of the tree you touch: `api`, `agent`, `ui`, `scenarios`,
`ttp`, `adapters`, `shelf`, `infra`, `docker`, `ci`, `docs`, `deps`.

**One branch, one task.** If your branch needs the word "and" to describe it, it
is two branches.

### Keeping current

Topic branches **rebase** onto `dev` (linear history, readable diffs). `dev` ->
`main` **merges** (the integration point is worth a commit). Never rebase `dev`
or `main` — other people's branches are cut from them.

```bash
git fetch --prune
git rebase origin/dev          # on your topic branch
```

> **Squash-merge trap.** This repo has been bitten by it: when a PR is
> squash-merged, your local branch still shows dozens of "unmerged" commits whose
> content is already on the target. Before concluding a branch has unmerged work,
> compare **content**, not commit counts:
> `git diff <target>..<branch> --diff-filter=A --name-only` and check whether the
> files it adds actually exist on the target. Commit counts lie; blobs do not.

---

## 2. Tasks and subtasks

A **task** is one topic branch and one PR. It is sized so a reviewer can hold the
whole change in their head — roughly 400 changed lines of substance or fewer,
excluding generated files and fixtures.

A **subtask** is a single commit inside that branch. Commits are the unit of
review; the PR is the unit of merge.

- Stage **by name** — `git add path/to/file`. Never `git add -A`, `git add .`, or
  `git commit -a`. If `git status` shows files you did not touch for this task,
  they belong to a different branch.
- One coherent change per commit. A release prep touching version + CHANGELOG +
  README + CI + packaging is **6-8 commits**, not one and not forty.
- First line 72 chars or fewer, imperative mood, `type(scope): summary`.

```
fix(docker): ship assertions/ and spec/, and make their absence visible
feat(api): require a bearer token on every mutating route
docs(reference): regenerate counts from the loader, not by hand
```

The body carries the part a reviewer cannot reconstruct from the diff: **what was
silently wrong, and what would have caught it.**

### Splitting a task that grew

If a branch outgrows one reviewable PR, cut the *next* slice as a new branch off
the first one and stack the PRs (`feature/x-1` -> `feature/x-2` -> `dev`), merging
in order. Do not let one PR grow to 2,000 lines; a 2,000-line review is not a
review.

---

## 3. QA Gate A — `feature/*` -> `dev`

This is the substantive gate. Everything below is required.

### A1 · Automated (CI, blocking)

`ci.yml` runs on every PR. All jobs must be green:

| Job | Proves |
|---|---|
| `backend` | Python suite inside the **prod image** (not the host venv) |
| `agent` | Go beacon build + vet + `test -race`, cross-compile linux/darwin/**windows** |
| `ui` | vitest + `vite build` |
| `detection` | corpus validator + deterministic export regeneration (`sha256sum -c`) |
| `refs` | every scenario through the real loader under `CORTEXSIM_STRICT_REFS=true` |
| `adapters` | tier-2 source trees present; adapter wiring check |
| `rust-dist` | static-musl builds of the three Rust tools |
| `e2e-isolated` | Tier-C pure assertion suite |

A red job is never merged around. A **skipped** job reads exactly like a passing
one — if you add a gate, it must run on the PR, not behind a label.

### A2 · The author's own evidence (in the PR body, required)

CI proves the tree is consistent. It does not prove your change does what you
say. State, concretely:

1. **What was wrong**, in terms of observable behavior — not "X was missing" but
   "the API returned `count: 0` while 18 files sat on disk."
2. **How you verified the fix**, with the command and its actual output.
3. **How you verified the guard fails without the fix.** Delete the fix, watch the
   new test go red, put it back. If you did not do this, say so explicitly.

> A test written after the fix, never observed failing, is an assumption with
> good syntax.

### A3 · Content changes (scenarios, TTP cards, adapters)

Detection content has its own truth conditions beyond "it parses":

- `make validate` green, `make check-refs` green, `make check-adapter-wiring` green.
- Every `ttp_ref`, `adapter_ref`, `detection_id`, `uc_ref`, `tc_ref`,
  `pov_scenario_id` resolves. The loader enforces this; do not disable strict mode.
- New detections must be able to go **red**. A detection that fires on any input,
  or a threshold like `expected_rows_min: 0`, proves nothing and will be rejected
  by the `A-17` guard — by design.
- **Do not "fix" the deliberate warnings.** The 100 `S-13` tier disagreements and
  13 `S-14` posture bindings are positioning calls
  (`docs/uc_tc_mapping/index-gaps-v2.2.md`). Silencing them is a regression.
- If a count changes, regenerate the counted ground truth — do not hand-edit a
  number in a doc. `python3 scripts/uctc_crosswalk_v2.2.py --report` wins over
  any figure written in prose.

### A4 · Review

One reviewer other than the author. The reviewer's job is not to re-run CI — CI
did that. It is to answer three questions:

1. Does the change do what the PR body claims?
2. Could this ship a **confident wrong answer** to a customer? (An absent
   detection read as "Cortex missed it"; a count quoted in a POV report; a green
   that came from an injected transport rather than a tenant.)
3. Is the new guard capable of failing?

### A5 · Honesty checks (blocking, and not automatable)

- **Never conflate authored with proven.** `tenant-verified` is `0` and stays `0`
  until a run executes against a live Cortex tenant. No test, console surface, PR
  body, or doc may report authored coverage as proven coverage.
- **A zero is degraded, not ok.** If a component loads 0 of something, the health
  surface says degraded and names the fix. "None shipped" and "none authored"
  must never render as the same response.
- **No writes to Cortex.** `CORTEXSIM_XSIAM_ALLOW_WRITE` and
  `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE` stay default-off. Any PR touching that is a
  design conversation before it is a diff.
- **Tolerance hides bugs.** If an unrecognized shape can be silently read as
  empty, raise instead. The tolerant fix keeps the bug and greens the test.

---

## 4. QA Gate B — `dev` -> `main`

`dev` is already green per Gate A, so Gate B asks a different question: **is this
releasable into a customer environment?**

- [ ] Full CI green on the merge commit, not just on the last topic PR.
- [ ] **Image parity** — build the image and assert it *ships* what the tree has.
      CI proving code compiles is not proof the image contains it
      (`make check-agent-shelf`, `tests/tools/test_image_ships_runtime_content.py`).
      This repo shipped an image missing 20 assertions and one agent target while
      every gate was green.
- [ ] **Counted ground truth reconciled** — scenarios, TTP cards, adapters, planes,
      routes. Every number in `README.md` and `CLAUDE.md` regenerated, not edited.
- [ ] `CHANGELOG.md` updated with the user-visible changes.
- [ ] **Public claims are backed.** If the README advertises a tag, image, or
      download, it must exist at merge time.
- [ ] Deploy the image locally (`scripts/dev-up.sh`) and confirm `/api/health` —
      including `degraded_components` and `not_checked[]` — reads as expected.
- [ ] Migrations: any new ORM column has its `ALTER TABLE` documented for
      existing deployments.

Tagging (`vX.Y.Z`) happens on `main` after the merge and triggers `release.yml`.

---

## 5. Hotfixes

For a defect on `main` that cannot wait for the `dev` train — an exposed
credential path, an image that will not boot, a detection that reports a
customer's coverage wrongly.

```bash
git checkout -b hotfix/<area>-<slug> main
# fix + a guard that fails without the fix
# PR -> main (Gate A automated + one reviewer; Gate B checklist abbreviated)
# then immediately:
git checkout dev && git merge main
```

**Merging the hotfix back down into `dev` is part of the hotfix, not a follow-up.**
A hotfix that lives only on `main` gets silently reverted by the next `dev` merge.

---

## 6. Local setup

```bash
cp .env.example .env      # set CORTEXSIM_SECRET
./scripts/dev-up.sh       # build + up + poll /api/health
```

`core/` is **not** bind-mounted — only `scenarios/`, `detection_scanner/`,
`tools/`, `docs/uc_tc_mapping/`, `sources/`, `scripts/`. Editing Python requires
`docker compose up -d --build`. A stale container reports current content counts
from the mounts while running month-old code; the tell is a component count that
disagrees with the tree (e.g. `eal.plugins: 14` against 21 files on disk).

Run the Python suite **inside the prod image** — the host Python is a different
version and the venv is not authoritative:

```bash
make ci          # enumerate local gate equivalents (make -n ci to preview)
make validate    # detection corpus
make check-refs  # UC/TC ref integrity under strict mode
```

---

## 7. What does not need a PR

Nothing. Every change to tracked files reaches `main` through `dev` via a PR.
Commit early and often on your topic branch — a full working tree is fragile,
and frequent named commits are the safety net — but the merge is always a PR.
