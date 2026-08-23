# CortexSim — Sprint Plan & Cadence Baseline

> **Created:** 2026-08-23 · **Branch:** `claude/project-init-3gmg7n` ·
> **Owner:** hankthebldr · **Managed via:** project-cadence
>
> This is the **handoff artifact** for the `project-cadence init` run. It was
> authored from a Claude Code *web* session (a cloud Linux VM with only the repo
> cloned — no reach to the local Obsidian vault / Things). Import it into the
> vault-os project home from the **BD790i workstation**, where `docsync` +
> `sprint-run` can bind Things ↔ vault. The paste-in for that is at the bottom.

---

## Baseline (INIT gate — met)

| Field | Value |
|-------|-------|
| **Repo / project / vault key** | `cortex-pov-engine` (byte-identical) |
| **Archetype** | `software-repo` → all 7 phases apply |
| **Current phase** | **IMPLEMENT ↔ CI/CD** (mature: 79 PRs, green 6-job CI, 88 scenarios / 15 planes) |
| **North star** | Best BAS tool for proving Cortex value — a DC runs CortexSim in a customer lab and within 90 min the security architect has a deck-ready coverage visualization. |
| **Success criteria** | Detection efficacy measured against verifiable benchmarks across detect / stitch / respond; no Cortex write path (Phase-1 rule; opt-in read-only health/metrics from Phase 9). |

The scaffold DoD (git, README, CI, LICENSE) closed long ago — this is not a
fresh init. The remaining work is the tail of the consolidated gap backlog
(`docs/reference/GAP-ANALYSIS.md`).

---

## Shipped this session (2026-08-23)

Two of the four open backlog items closed; CI matrix 5 → 6 jobs. See the
`2026-08-23 pass` block in `GAP-ANALYSIS.md`.

- ✅ **De-hand-rolling CI gate** — `scripts/check-adapter-wiring.py` + `adapters`
  job step + `make check-adapters`. Fixed the one real gap (`deepce` in
  `cdr-003`). Green at **0 candidates · 11 redundant · 70 generic**.
- ✅ **Tier-C isolated-execution gate** — pure assertion suite as a dedicated
  `e2e-isolated` CI job + `make e2e-tierc` (53 pass / 4 skip, no docker). Docker
  detonation e2e wired as an opt-in `tier-c-e2e` label step.

---

## Remaining backlog (sized in pomodoros 🍅; split anything > 4🍅)

Phase tag `phase/implement` unless noted. IDs trace to `GAP-ANALYSIS.md`.

| # | Task | 🍅 | Notes / DoD |
|---|------|----|-------------|
| B1 | Migrate **CDR** hand-rolled `command:` blocks → `adapter_ref` (per scenario) | 2 ×5 | ~96% hand-rolled. One task per CDR scenario; each: swap to `adapter_ref`, re-verify expected detections still map, `make check-adapter-wiring`. |
| B2 | Migrate **NDR** hand-rolled `command:` blocks → `adapter_ref` (per scenario) | 2 ×7 | ~89% hand-rolled. Same per-scenario pattern; NDR leans on EAL plugins so confirm no double-drive. |
| B3 | Headless push-bundle generation script (`SIM-EDR-001`) | 3 | Prereq for B4. Boot SimCore headless or a thin generator CLI over `push_generator.generate_bash`; emit a bundle file without a live server round-trip. |
| B4 | Promote **Tier-C docker e2e** to a default (path-filtered) hard gate | 3 | Depends on B3. Validate `run-tier-c.sh` end-to-end on a real runner, then drop the `tier-c-e2e` label guard → default gate. |
| B5 | Reconcile **scenario/card count drift** across docs | 2 | Loader ground-truth recount; align `CLAUDE.md` (88/89), `docs/reference/README.md`, `scenario-catalog.md`. |
| B6 | Opportunistic **GAP-ADAPT-02** residual wiring (~21 adapters) | 1 ×N | Low priority; wire when a scenario naturally needs one. Not a sprint focus. |
| P1 | Live-tenant **poll-cadence / back-pressure tuning** (auto-reconcile loop) | — | **Parked — needs a real XSIAM tenant.** RESEARCH-phase spike; time-box when a tenant is available. |

---

## Sprint 1 — proposed

> **Goal:** *Fully de-hand-roll the CDR plane and stop the doc-count drift — a
> coherent, verifiable slice needing no live tenant.*

| Task | 🍅 |
|------|----|
| B1 × 5 (CDR scenarios → `adapter_ref`, re-verified) | 10 |
| B5 (count reconciliation) | 2 |
| **Sprint capacity** | **12🍅** |

**Sprint-done definition:** all 5 CDR scenarios reference adapters via
`adapter_ref` with expected detections re-verified; `make check-adapter-wiring`
still green; doc counts reconciled; branch green in the 6-job CI; velocity logged
(pomodoros completed) to the `_MOC`.

**Sprint 2 candidate:** B3 → B4 (Tier-C docker e2e to a hard gate) — the highest
remaining CI-confidence win, deferred to Sprint 2 because it needs live-CI
iteration (docker-compose + bundle generation) rather than repo-local edits.

---

## BD790i handoff — bind to vault-os

Run from Claude Code **on BD790i** (local terminal — confirm `pwd` is under
`/home/...`/`/Users/...`, not `/home/user/cortex-pov-engine` on a cloud VM):

```
/project-cadence init cortex-pov-engine
```

`_MOC cortex-pov-engine.md` frontmatter:

```yaml
---
phase: implement
archetype: software-repo
repo: cortex-pov-engine
---
```

Then emit Sprint 1 to Things via docsync (explicit `list_id`, never silent-Inbox):

```
add_todo_for("cortex-pov-engine", "B1: CDR cdr-00X → adapter_ref (re-verify detections)", tags=["phase/implement","sprint/1"], est="2p")   # ×5
add_todo_for("cortex-pov-engine", "B5: reconcile scenario/card count drift across docs", tags=["phase/implement","sprint/1"], est="2p")
```

Or drive the whole sprint through the harness: `sprint-run cortex-pov-engine`.
