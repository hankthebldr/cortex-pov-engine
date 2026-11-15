# Runs surface rebuild — design (2026-11-02)

Follows the UX review `docs/design/2026-11-02-minimal-console-ux-review.md`
(§A findings 3 and 6, §B, §C). Branch `feature/ui-runs-rebuild`, cut from `dev`.

## Intent

A DC launches a chain from the Composer and then has to leave the canvas to
learn what happened; the Runs page they land on is 86 rows of
`scenario · uuid · status`. Both surfaces exist and both are thin. This rebuilds
them on **live run records** — `Run`, `Result`, `StepOutcome`, the SSE stream —
to the standard the Enclave console sets, and rebuilds the two run-detail
*designs* PR #135 deleted (Techniques, Topology) on those records rather than
on the `povdata/` seed catalogs that made deleting them right.

## Goals

1. A Runs row answers *what, when, where, how long, how much proved* without
   opening it, and 86+ rows are navigable: filter by scenario and status,
   consecutive repeats grouped, paged.
2. The Composer's Active run pane tails per-step rows inline and switches
   itself on when a launch starts from the canvas. History lists every run and
   opens one in the workstream without leaving the Composer.
3. Run detail gains **Techniques** (kill-chain strip with empty stages dimmed,
   per-technique verdict table) and **Topology** (lane bands from `laneOf`,
   Engine pinned terminal).

## Non-goals

- Server-side paging of `/api/runs`. 86 rows is small; the list is paged
  client-side. Keyset paging is a follow-up for >~1000 runs.
- Re-adding `RunTopology` / `RunTechniques` / anything #135 deleted. New
  components, new names, new data.
- Changing the run lifecycle, the scorer, or any Cortex read-back path.

## Anti-goals (what would make this a failure even if it works)

- A row or a strip that renders a failed run's zero as a detection miss.
  `isRunUnproven` already encodes the rule; every new surface obeys it.
- A number the API did not return, rendered as a fact ("0 detections" on a run
  whose results never loaded). Unknown renders as a dash with a title.
- Any surface implying tenant verification. `tenant-verified` is 0; counts come
  from this SimCore's `Result` rows and say so.
- A second derivation of "which lane is this step in". Topology calls
  `composerLanes.js::laneOf` and nothing else.

## Constraints

- Every row field comes from `GET /api/runs` / `GET /api/runs/{id}`; fields are
  added to `core/api/runs.py` only where the row cannot be built otherwise.
- Vitest green; a Playwright spec for the Runs row fields and the Composer
  auto-switch; verified in the baked image on :8888, not jsdom.
- Commits at boundaries, staged by path, Gate A note in each body. No PR.

## Decisions

### D1 — `GET /api/runs` gains two aggregates, one grouped query

`expected_detections` (count of `Result` rows for the run) and
`detected_count` (rows where `is_hit(outcome, observed)`), computed in **one**
`GROUP BY run_id` query over `Result`, never N+1. The hit rule is mirrored in
SQL from `engine.outcome.row_status`: `outcome IN ('detected','prevented')`, or
`outcome IS NULL AND observed`. A run with no `Result` rows gets `0 / 0`, which
the UI renders as a dash, not as `0 / 0`.

`LastRunCard` already reads `detected_count` / `expected_detections` and
printed a dash because they never existed; it starts working without a change.
The agent is the existing `target` column (`LastRunCard` read two names that
never existed; it is pointed at `target`). Nothing else is added: started-at,
completed-at, status, mode, tc_verdict already ship.

Guard: `tests/api/test_runs_api.py` asserts both fields on a seeded run with
one hit of two. Removing the aggregate makes the test fail on a missing key.

### D2 — Runs list rows and controls (`ui/src/app/destinations.jsx::RunList`)

Row: `scenario_id` · started (relative, `title` = absolute UTC ISO) · agent
(`target`, or `push bundle` for `mode=push`) · duration · detections · status
chip. Pure helpers in a new `ui/src/components/console/runRows.js`:

- `relativeTime(iso, now)` and `absoluteTime(iso)` via `parseServerTime`
  (naive timestamps are UTC — the non-UTC bug class).
- `runDuration(run, now)`: `completed_at − started_at`; live elapsed while
  `running`; `—` for `staged` / no `started_at`.
- `detectionCell(run)`: `d / n` for complete and running runs; for
  `failed`/`aborted` with `d === 0` returns `{ text: 'unproven', ... }` — a run
  that died has no coverage number; for `staged` returns a dash.
- `filterRuns(runs, { scenario, status })`, `groupRuns(rows, min = 3)`
  (consecutive same-scenario rows ≥ 3 collapse into one group showing the
  newest row and `+N more`), `pageOf(rows, page, size = 25)`.

Filters live in the URL (`?scenario=&status=`) via the surface's `setParams`,
so a filtered list is a deep link. The pager prints `Page N of M · K runs`
over the filtered set; a filtered-to-empty list says so rather than rendering
nothing. Relative times tick on a 30 s interval, 1 s while any row is running.

### D3 — Composer workstream (`ComposerView.jsx`)

- A new hook `ui/src/components/console/useRunStepTail.js` takes a run id and
  the scenario's steps and returns one row per step: `id · identity · status ·
  rc · duration`. Status/rc/termination come from `GET /api/runs/{id}`
  (`step_outcomes`, authoritative); the pane refetches on every `run.status`
  SSE frame and polls every 3 s while the run is not terminal. Identity and
  duration come from the beacon's own transcript in `Run.output`, whose
  per-step grammar is fixed in `agent/beacon/client.go`:
  `=== STEP i/N · <step> · <technique> · identity=<who> ===` …
  `--- exit_code=<rc> duration=<d> ---`. The parser is pure
  (`parseStepTranscript(output)`) and pinned by a test on those exact strings.
  A step with no transcript and no outcome row is `queued`; a transcript header
  with no footer is `running`; an `IDENTITY NOT HONOURED` marker is surfaced
  verbatim on the row.
- `ActivePane` shows the **followed** run: the run a canvas launch just
  created, else a run picked in History, else the provider's `activeRun`.
  On `launch.launch()` resolving to a run, ComposerView sets
  `followRunId`, switches `wsTab` to `active`, opens the dock, and calls
  `refreshRuns()`. `Follow ▸` stays the deep link to Runs.
- `HistoryPane` lists every run (no `slice(0, 8)`), newest first, scoped by
  default to the draft's origin scenario with an `all runs` toggle, and a row
  click sets `followRunId` (inspect in place) instead of navigating away.

### D4 — Techniques tab (`ui/src/components/console/RunTechniquesView.jsx`)

Data: `GET /api/results/{run_id}` (rows + coverage), the run (`tc_verdict`,
`tc_verdict_detail`), and the TTP cards the rows reference
(`GET /api/ttps/{ttp_ref}` for each distinct `ttp_ref`) for
`mitre_attack.techniques[].tactic_ids`. `GET /api/mitre/coverage` is **not**
used: it assigns the scenario-level tactic to every step technique
(`_ensure` first-sighting), so it would place a Discovery step under
Credential Access.

- Tactic order is one table, `ui/src/components/console/attackTactics.js`
  (14 tactics, kill-chain order); `exportNavigatorLayer.js` derives its
  shortname map from it so there is one definition.
- Kill-chain strip: 14 stages in order. A stage holds the techniques whose
  card maps them there (sub-technique id, then parent id). A stage with no
  technique is drawn dimmed. A stage's tone is the worst of its rows:
  `detected`/`prevented` → hit; `missed` → miss, **only when the run is
  proven**; otherwise pending. A technique no card places is listed under
  `unplaced` with the reason, never guessed from the scenario's single tactic.
- Verdict table: one row per technique — id, name (card), tactic, steps,
  `hits / rows`, outcome summary, `kpi_verdict` summary. Header carries the run
  `tc_verdict` and the sentence "Evidence is this SimCore's Result rows; a
  detection counts only once reconciled from the tenant or validated by the DC.
  Authored is not proven."

### D5 — Topology tab (`ui/src/components/console/RunTopologyView.jsx`)

Lane bands are `LANE_BANDS` (LAUNCH · ENDPOINT · CLOUD · NETWORK · DATA
STREAMS · ANALYTICS · PROOF). Columns are the run's scenario steps in order;
each step is placed by `laneOf(step)` with its host outcome (`rc`,
termination) and `hits / rows` from results. The **Engine** (ANALYTICS) lane is
pinned as the terminal column: correlation/analytics rows and the run
`tc_verdict` land there regardless of step order. Empty lanes are drawn
dimmed with "no step launched through this door". No new lane derivation.

### D6 — Run detail sub-tabs

`RUN_SUBTABS` gains `techniques` and `topology`; `?tab=` deep links work.
Live · Evidence · Storyline · Causality are untouched.

## Honesty rules carried by every new surface

- Unproven run ⇒ no miss reading anywhere (row, strip, table, topology).
- A count never renders before its fetch resolves; a failed fetch says
  "could not load", never `0`.
- `tenant-verified: 0` is stated verbatim where a verdict is shown.

## Tests

- vitest: `runRows.test.js` (relative/absolute time across the UTC seam,
  duration, detection cell truthfulness, filter/group/page), `RunList` render,
  `useRunStepTail` transcript grammar, ComposerView auto-switch (mock
  `POST /api/runs` → `ws-tab-active` selected, step rows render),
  `RunTechniquesView` (dimmed empty stages; failed run never "missed";
  unplaced bucket), `RunTopologyView` (lanes via `laneOf`, Engine terminal).
- pytest: list aggregates (D1).
- Playwright `ui/tests/e2e/10-runs-rebuild.spec.ts`: row fields present with
  the absolute-time title, filter narrows, pager label; Composer launch (pull,
  seeded agent) auto-switches to Active with queued step rows.
- Baked image on :8888 (`scripts/dev-up.sh` with a distinct
  `CORTEXSIM_VERSION` tag so the 1.1.0 image is not re-tagged).

## Left to the builder

Exact copy, chip tones, and column widths; whether the group header shows a
status breakdown (`83 failed`) — do it if it costs one reduce.
