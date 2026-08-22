# Production Readiness Review & Sprint Plan

**Status:** review + execution plan · **Date:** 2026-08-22
**Operating model:** `project-cadence` (archetype `software-repo`, all 7 phases)
**Companion:** [`caldera-parity-and-next-generation-strategy.md`](caldera-parity-and-next-generation-strategy.md) — the architecture thesis this plan sequences.

**Method.** Static audit of `origin/main` at `344c783`, GitHub issue/PR/CI state,
and market research on the 2026 validation category. Every number below is
reproducible; commands in §8.

---

## 0. Verdict in one paragraph

The engine is **further along than its own documentation claims** — 200 scenario
files, 170 detection cards, 92 tool adapters, 158 test modules, both CI workflows
green on main. It is not production-ready, and the blockers are not architectural.
They are: **an unauthenticated API that dispatches shell commands and holds a
credential vault**, a **public-facing story the repo cannot back** (README
advertises versioned container images and verified downloads; there are zero tags
and zero releases), and **documentation drift severe enough that every figure a
consultant quotes is wrong**. None of these is hard. All of them block shipping.

**Phase verdict: the project is in CI/CD (phase 5). The gate to RELEASE is
blocked** — and it has been *crossed in the README without being passed*, which is
exactly the public-facing-coherence failure the phase model exists to catch.

---

## 1. State of record vs reality

| Metric | Documented | Actual on `main` | Δ |
|---|---|---|---|
| Scenario YAML files | 118 (CLAUDE.md) | **200** | +69% |
| TTP detection cards | 89 (CLAUDE.md) | **170** | +91% |
| Tool adapter packs | 69 (CLAUDE.md) | **92** | +33% |
| Draft cards pending promotion | 35 (issue #70) | **0** | resolved |
| README per-plane scenario count | "5 scenarios" ×9 planes | 5–26 per plane | wrong everywhere |
| Test modules | — | 158 | — |
| Docs (`docs/**.md`) | — | 104 | — |
| Git tags / GitHub releases | README implies `:vX.Y.Z` | **0 / 0** | claim unbacked |
| CHANGELOG | — | **absent** | — |

README last touched 2026-08-19; CLAUDE.md 2026-08-05. Both are behind the content.
Issue #70's draft-card backlog appears **already resolved** (0 drafts remain) and
should be re-scoped or closed.

**CI health:** both `ci.yml` and `test.yml` green on `main` at `344c783`. The
shellcheck SC2034 failure that was red on `test.yml` since 2026-06-18 was fixed in
PR #89 (`e6a1654`). No standing CI debt.

---

## 2. Market frame — the category renamed itself, in our direction

Gartner has **retired "Breach and Attack Simulation" as a category**, consolidating
BAS and automated pentest/red-team into **Adversarial Exposure Validation (AEV)**,
now foundational to Continuous Threat Exposure Management (CTEM). The 2026 AEV
Market Guide names **detection rule validation** and **SIEM detection-rule
performance and hygiene** as first-class capabilities — identifying coverage gaps
before an incident does.

That is a direct external validation of the thesis in the Caldera strategy doc:
*plan toward detection coverage, not compromise.* We are not chasing the category;
the category moved onto our ground. The competitive set has repositioned
accordingly — SafeBreach on simulator-based production safety, AttackIQ on deep
ATT&CK customization plus agentless assessment for air-gapped labs, Cymulate on
SaaS breadth, Picus explicitly on detection-engineering integrations.

**What that means for "best of breed":** the differentiator is no longer *can you
execute the technique*. Everyone can. It is **can you prove what the defender's
detection stack did about it, and hand back the rule that closes the gap.** We are
the only engine with the detection corpus to do that — and we currently cannot
ship it into a customer environment because of §3.

---

## 3. Critical wins, ranked

### Tier 0 — blockers (nothing goes into a customer environment until true)

**T0-1 · The API has no authentication.** `core/main.py` mounts 15+ routers with no
auth dependency, behind `CORSMiddleware(allow_origins=["*"], allow_credentials=True)`.
Unauthenticated surfaces include `POST /api/agents/{id}/tasks` (dispatches shell
commands to beacons) and `/api/credentials/*` (the encrypted integration vault).
This is precisely the class of exposure that produced Caldera's unauthenticated RCE
(CVE-2025-27364) — a failure I flagged as a warning in the strategy doc, which
applies to us today. The `allow_origins=["*"]` + `allow_credentials=True`
combination is additionally invalid per the CORS spec and signals intent the code
should not have.

**T0-2 · No release surface behind a public claim.** README points at
`ghcr.io/hankthebldr/cortexsim` tagged `:vX.Y.Z` and a landing page with "verified
downloads." There are **no tags and no releases**. A consultant following the
README's install path is following a promise the repo does not keep.

**T0-3 · Dependency currency, vault-critical.** 8 open Dependabot PRs, four stale
since June. PR #83 bumps `cryptography` **43.0.1 → 50.0.0** — the library that
encrypts every stored credential. Seven major versions behind on the crypto
primitive under the vault is not acceptable in a tool handed to customers.

### Tier 1 — credibility (the POV fails in front of the customer without these)

**T1-1 · Placeholder XQL reaches the customer (issue #65).** AI Access BIOC bodies
still carry `// AUTO-GENERATED SKELETON` and `/* TODO: predicate matching the BIOC
name */`. The detection-body reveal + copy-to-clipboard panels put these directly in
front of an operator who pastes them into the customer's XSIAM Query Center. This is
the single most damaging possible POV failure mode: a Palo Alto consultant handing a
customer a TODO comment as detection content.

**T1-2 · Documentation truth.** Every count in README and CLAUDE.md is wrong, most
by 30–90%. A DC quoting "88 scenarios" understates the product by more than half.

**T1-3 · Drift cannot be allowed to recur.** The fix for T1-2 is not a one-time
edit — it is generated ground truth plus a CI gate, or the same drift returns
within two sprints.

### Tier 2 — the moat (post-blockers; sequenced from the Caldera analysis)

**T2-1 · Windows execution.** Zero Windows scenarios exist; the identity harness is
`runuser`/`sudo`/`su` only. Most Cortex XDR POVs are Windows-heavy. This is the
largest single content unlock available and it gates credibility in the majority of
real engagements.

**T2-2 · Closed-loop detection objective.** Runs that terminate when the *detection
goal* is satisfied. This is literally the capability the AEV guide names.

**T2-3 · Detection difficulty ladder.** Find the obfuscation level at which each
rule breaks; report it as a customer finding.

---

## 4. Phase gate: why RELEASE is blocked

Walking the phase-model RELEASE DoD against actual state:

| RELEASE DoD item | State |
|---|---|
| Version tagged (semver) | ✗ zero tags |
| CHANGELOG finalized for version | ✗ no CHANGELOG |
| README current (install, usage, status accurate) | ✗ counts wrong; install path unbacked |
| Pages / docs site updated | ◐ `docs/site/` exists, content stale |
| Release notes published | ✗ zero releases |
| Things: sprint closed, `phase/release` | ✗ no sprint tracked |
| Vault `_MOC` phase + sync sha | ✗ not mirrored |

**Do not advance.** Sprints 1–2 below exist to make this gate passable.

---

## 5. Sprint plan

Sizing per `cadence.md`: **1🍅 = 25 min, nothing over 4🍅 enters a sprint un-split.**
No rolling velocity exists for this project yet, so **capacity is declared, not
measured** — recalibrate from Sprint 1's actual completion before planning Sprint 2.

### ▶ Sprint 1 — "Safe to point at a customer environment"
**Goal:** the engine can be deployed in a customer lab without handing anyone
unauthenticated command execution. **Capacity: 16🍅 · Planned: 15🍅**

| # | Task (done = …) | 🍅 |
|---|---|---|
| S1-1 | a `require_api_token` FastAPI dependency exists, reads `CORTEXSIM_API_TOKEN`, unit-tested for accept/reject | 2 |
| S1-2 | every mutating router requires it; `/api/health` stays open; tests assert 401 on each router | 3 |
| S1-3 | agent routes authenticate via the existing enrollment-token machinery rather than being open | 3 |
| S1-4 | CORS reads an explicit allowed-origin list from config; wildcard+credentials removed | 1 |
| S1-5 | `cryptography` on 50.x, credential-vault tests green (PR #83) | 2 |
| S1-6 | remaining 7 Dependabot PRs each merged or closed with a stated reason | 2 |
| S1-7 | `SECURITY.md` published + a threat-model note for the task-dispatch surface | 2 |

**Gate:** no unauthenticated route can dispatch a task or read a credential.

### ▶ Sprint 2 — "The public story is true"
**Goal:** RELEASE DoD passable; nothing customer-visible is a placeholder.
**Capacity: 16🍅 · Planned: 15🍅**

| # | Task (done = …) | 🍅 |
|---|---|---|
| S2-1 | `scripts/inventory.py` emits counted ground truth (scenarios · cards · adapters · planes · tests) as JSON | 2 |
| S2-2 | CI job fails when README/CLAUDE.md counts drift from `inventory.json` | 2 |
| S2-3 | README plane table + all counts regenerated from inventory; install path matches reality | 2 |
| S2-4 | CLAUDE.md counts corrected against inventory | 1 |
| S2-5 | `CHANGELOG.md` exists with a populated `[Unreleased]` section | 2 |
| S2-6 | `v0.1.0` tagged; GitHub release published with notes; container tag matches | 2 |
| S2-7 | issue #65 — every active AI Access BIOC body carries a real XQL predicate | 3 |
| S2-8 | regression test blocks `AUTO-GENERATED SKELETON` / `TODO:` in active cards | 1 |

**Gate:** RELEASE DoD walks clean; issue #65 and #70 closed or re-scoped.

### ▶ Sprint 3 — "We run Windows"
**Goal:** first moat increment — the largest content unlock in the Caldera analysis.
**Capacity: 16🍅 · Planned: 15🍅**

| # | Task (done = …) | 🍅 |
|---|---|---|
| S3-1 | `spec/identity_harness.json` carries Windows identity modes alongside the Linux four | 2 |
| S3-2 | Go beacon reports platform and selects an executor per platform | 3 |
| S3-3 | a PowerShell executor runs a command and streams output back | 3 |
| S3-4 | scenario schema accepts per-platform executors; all 200 existing scenarios load unchanged | 3 |
| S3-5 | one Windows credential-theft scenario runs end-to-end with expected detections | 3 |
| S3-6 | a Go test guards the Windows harness against the shared spec (drift guard) | 1 |

**Gate:** a Windows scenario executes with identity causality, CI green.

---

## 6. Things 3 write-back (ready to replay)

Per the sync-contract: **tags, never sections**; every task via `add_todo_for` with
an explicit `list_id`. Anchors are updated in place, not recreated.

**Anchors to create/update:**
```
◆ PHASE: cicd                                    tags: phase/cicd, software-repo
▶ SPRINT 1: Safe to point at a customer env      tags: sprint/1   notes: planned 15🍅 / completed __ / rolled __
⏸ RESUME HERE
   STOPPED AT: production-readiness review complete; sprint plan drafted, not yet executed
   NEXT: done = require_api_token dependency exists with accept/reject unit tests (S1-1)
   OPEN: capacity is declared (16🍅), not measured — recalibrate after Sprint 1
```

**Tasks** — `add_todo_for("cortex-pov-engine", <title>, list_id=<explicit>, tags=[...])`:

| Title | Tags |
|---|---|
| S1-1 · API token dependency + unit tests (2🍅) | `phase/cicd`, `sprint/1` |
| S1-2 · Require token on all mutating routers; 401 tests (3🍅) | `phase/cicd`, `sprint/1` |
| S1-3 · Agent routes authenticate via enrollment token (3🍅) | `phase/cicd`, `sprint/1` |
| S1-4 · Lock CORS to configured origins (1🍅) | `phase/cicd`, `sprint/1` |
| S1-5 · Bump cryptography to 50.x, vault tests green (2🍅) | `phase/cicd`, `sprint/1` |
| S1-6 · Triage remaining 7 Dependabot PRs (2🍅) | `phase/cicd`, `sprint/1` |
| S1-7 · SECURITY.md + dispatch-surface threat model (2🍅) | `phase/cicd`, `sprint/1` |
| S2-1 · inventory.py counted ground truth (2🍅) | `phase/cicd`, `sprint/2` |
| S2-2 · CI gate on doc-count drift (2🍅) | `phase/cicd`, `sprint/2` |
| S2-3 · Regenerate README from inventory (2🍅) | `phase/cicd`, `sprint/2` |
| S2-4 · Correct CLAUDE.md counts (1🍅) | `phase/cicd`, `sprint/2` |
| S2-5 · Create CHANGELOG with [Unreleased] (2🍅) | `phase/cicd`, `sprint/2` |
| S2-6 · Tag v0.1.0 + publish release (2🍅) | `phase/release`, `sprint/2` |
| S2-7 · Real XQL for AI Access BIOCs — issue #65 (3🍅) | `phase/cicd`, `sprint/2` |
| S2-8 · Regression test blocking placeholder bodies (1🍅) | `phase/cicd`, `sprint/2` |
| S3-1 … S3-6 · Windows execution (15🍅 total) | `phase/implementation`, `sprint/3` |

---

## 7. Vault write-back (ready to replay)

**`_MOC cortex-pov-engine.md` frontmatter** (mirror keys only — Things stays
authoritative for live state):

```yaml
archetype: software-repo
phase: cicd
phase-entered: 2026-08-22
phases-applicable: [init, plan, research, design, implementation, cicd, release]
velocity: null            # no completed sprint yet — set after Sprint 1
```

**Notes to file:**
- `_projects/cortex-pov-engine/` ← this document (planning/scope for the release push)
- `_research/` ← the AEV category shift (BAS retired → AEV; detection-rule
  validation named as a core capability) as a `[research]` note, with an
  ADR-style `[decision]` recording that the engine targets AEV detection-rule
  validation rather than BAS execution breadth.

---

## 8. Reproducing the audit

```bash
git ls-tree -r --name-only origin/main scenarios/ | grep '\.yml$' | grep -v _schema | wc -l   # 200
git ls-tree -r --name-only origin/main detection_scanner/ttps/ | grep -c '\.json$'            # 170
git ls-tree -r --name-only origin/main tools/packs/ | grep -c '\.yml$'                        # 92
git ls-tree -r --name-only origin/main tests/ | grep -c 'test_.*\.py'                         # 158
git show origin/main:core/main.py | grep -n "allow_origins"                                   # ["*"]
git show origin/main:core/main.py | grep -c "Depends"                                          # 0 app-level auth
git tag | wc -l                                                                                # 0
```

## 9. Sources

- [2026 Gartner Market Guide for Adversarial Exposure Validation](https://www.picussecurity.com/resource/report/2026-gartner-market-guide-for-automated-exposure-validation)
- [Gartner AEV Market Guide — exposure validation use cases](https://cymulate.com/blog/gartner-2026-aev-market-guide-exposure-validation-use-cases/)
- [Gartner Peer Insights — Adversarial Exposure Validation](https://www.gartner.com/reviews/market/adversarial-exposure-validation)
- [BAS / AEV tool landscape 2026](https://scythe.io/library/best-breach-and-attack-simulation-bas-aev-tools-2026)
- [Caldera CVE-2025-27364 advisory](https://github.com/mitre/caldera/security/advisories)
