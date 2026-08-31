<!--
  Target `dev` for normal work. Target `main` only for a `hotfix/*` or a
  `dev` -> `main` release merge. See CONTRIBUTING.md §1.
-->

## What was wrong

<!-- Observable behavior, not "X was missing".
     Good: "GET /api/assertions returned count:0 while 18 YAMLs sat on disk,
            so 'none shipped' and 'none authored' were the same response."
     Weak: "assertions/ was not copied into the image." -->

## What this changes

<!-- One paragraph. If you need the word "and", consider splitting the PR. -->

## Evidence (QA Gate A2 — required)

**How I verified the fix** — command and its actual output:

```
```

**How I verified the guard fails without the fix** — I removed the fix and
watched the new test go red:

```
```

<!-- If you did NOT do this, say so explicitly here and why. A test written
     after the fix and never observed failing is an assumption with good syntax. -->

## Checklist

- [ ] Branch cut from `dev` and rebased onto current `origin/dev`
- [ ] Files staged **by name** — no `git add -A` / `git add .` / `git commit -a`
- [ ] All CI jobs green (`backend` `agent` `ui` `detection` `refs` `adapters` `rust-dist` `e2e-isolated`)
- [ ] New guard is capable of failing (see Evidence above)
- [ ] No number was hand-edited into a doc — counts regenerated from the loader / crosswalk

**Content changes only** (scenarios, TTP cards, adapters) — delete if N/A:

- [ ] `make validate`, `make check-refs`, `make check-adapter-wiring` green
- [ ] Every `ttp_ref` / `adapter_ref` / `detection_id` / `uc_ref` / `tc_ref` / `pov_scenario_id` resolves
- [ ] Did **not** silence deliberate `S-13` / `S-14` warnings

**Honesty checks (Gate A5)** — these are blocking and not automatable:

- [ ] Nothing here reports **authored** coverage as **proven** coverage
      (`tenant-verified` is still 0 unless a live tenant answered)
- [ ] A component loading zero of something reports **degraded**, not ok
- [ ] No write path to Cortex introduced or enabled by default
- [ ] No new tolerance that silently reads an unrecognized shape as empty

## Risk to a customer engagement

<!-- Could this ship a confident wrong answer into a POV report? An absent
     detection read as "Cortex missed it"? A count a DC would quote?
     "None" is a valid answer — say it deliberately, not by omission. -->
