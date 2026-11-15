# Runs, Results, Reports: one job per page

**Status:** approved by Henry in chat, 2026-11-15 ("Yes, build it"). Items marked *(call)* are
the builder's decisions, not Henry's words; he can veto them.

## Why

Henry, testing v1.2.0 locally: "We need to simplify the whole runs vs results. The runs shows
the most detail that should be in the results, but it doesn't appear that any of the data is
looking live and we're not getting attempted runtime logs." And: "'reports' doesn't need the
compare runs, but needs to be able to do a rollup/mappings of the executions fired throughout
the deployment of the engine."

Today the pages overlap:

- **Runs › run detail** carries six tabs: Live, Evidence, Storyline, Techniques, Topology,
  Causality, plus the case panel. Five of them are about what Cortex saw, not what ran.
- **Results** (`#/proof`, headed "Report") shows the Result-of bar, the tenant evidence panel
  and the same Evidence view the run detail has.
- **Dashboards** (`#/coverage`) charts the corpus. Nothing rolls up what was actually executed
  on this engine.
- The Live tab shows nothing for a finished run ("waiting for agent…"), prints a step's output
  only after the step ends, and never shows the command that was attempted.

## The split

| Page | Its one job | Shows |
|---|---|---|
| **Runs** | What was attempted | Run list. Per run: lifecycle strip, the step chain with live state, a runtime log per step, Abort, Review. |
| **Results** | What Cortex saw, for one reviewed run | Result-of bar and switcher, the verdict, detections per step with their state, Evidence, Storyline, Techniques, Topology, Causality, case and tenant evidence, the report downloads. |
| **Reports** | Everything fired on this engine | A rollup of every execution, mapped scenario → technique → UC/TC → plane → detection source → outcome. The three existing dashboards stay as further tabs. |

Rules that hold across all three:

1. **Runs never shows a detection verdict.** A run row and a run page say Ran / Failed /
   Did not run / Aborted / Staged, and whether the run has been reviewed. Nothing more.
2. **Results never shows without a run**, and says "not reviewed yet" with a **Review** action
   until the run has been read back. An unreviewed run's detections are "not reviewed", never
   "missed".
3. **Reports counts executions, not the corpus.** A scenario that never ran reads "never run",
   not 0 %. An empty engine shows an empty state, not zeros dressed as coverage.
4. **Compare runs** stays a secondary action on the Runs list only. It is not on Results or
   Reports. *(call: Henry said Reports does not need it; it is left where it is.)*
5. The action that reads a run back is named **Review** everywhere (renamed from "Read back"
   in the same release train).

## Runs: the runtime log

Run detail keeps: the lifecycle strip, `RunChain`, Abort, and one panel, the **runtime log**.
The Evidence, Storyline, Techniques, Topology and Causality tabs and the case panel leave.
Old links `#/runs?run=<id>&tab=evidence|storyline|techniques|topology|causality` redirect to
`#/proof?run=<id>&tab=<same>`.

The runtime log is one block per step, in order:

- step number, name, technique, and the account it ran as;
- **the command that was attempted** (from the scenario step; the beacon's own record of what
  it executed once the beacon reports it);
- state with an icon and a word: Queued · Running · Ran (exit 0) · Failed (exit N) ·
  Timed out · Did not run (with the reason) · Aborted;
- duration, stdout and stderr.

For a finished run the log is built from the stored `Run.output` and `step_outcomes`. For a
live run it follows the SSE stream. It never says "waiting for agent…" for a run that has
ended.

A run that has not been reviewed shows one primary **Review**; once reviewed, the primary is
**Open results**.

### Live, phase 2 (beacon change, its own patch)

Today the beacon posts a step's output after the step ends, so a three-minute step is silent
for three minutes. Phase 2 has the beacon (a) report each step's start with the command it is
about to run, and (b) stream output chunks while the step runs. That needs a new beacon binary
on each target. Phase 1 (above) ships first and needs no beacon change. *(call)*

## Results

`#/proof?run=<id>&tab=<tab>`; heading "Results" (not "Report").

- Result-of bar with the run switcher (exists).
- Verdict line: the run's `tc_verdict` in words, with its reason.
- Tabs *(call on order)*: **Detections** (the Evidence view: each step's expected detections
  with detected / missed / pending / not executed / not reached / not reviewed) · Storyline ·
  Techniques · Topology · Causality · Tenant evidence (case ledger and the tenant reader's
  panel).
- Report downloads (Markdown, matrix CSV, Navigator layer, bundle) as secondary actions.
- Not reviewed: the Review notice and action; the tabs render their "not reviewed" state.

## Reports

`#/coverage` keeps its route; the rail label and heading become **Reports**. *(call: keep the
route so deep links survive.)*

First tab, default: **Executions**. Source: a new read-only endpoint
`GET /api/reports/executions` (SQL aggregates; it must never load `Run.output`).

- Filters: time range, plane, run status, agent; drafts (`SIM-DRAFT-*`) are left out unless a
  toggle includes them.
- Totals: runs by status; scenarios exercised (of the corpus count); techniques exercised;
  test cases touched; steps ran / failed / did not run; expected detections by outcome, with
  "not reviewed" as its own bucket.
- Table, one row per scenario that ran: plane, techniques, UC/TC, detection sources (doors),
  runs, first and last run, last status, reviewed runs, and the detection outcome counts of its
  most recent reviewed run. Each row opens that run in Results.
- Second view of the same data by technique and by test case (mapping view).
- Download as CSV and JSON.

The three existing tabs (Detection source × Use case, MITRE ATT&CK heatmap, Planes × doors)
follow unchanged.

## Not in this change

- No scoring change. Outcomes, verdicts and the matcher are untouched.
- No change to the report export formats.
- Compare runs is not redesigned.
- Auto-review stays opt-in (`CORTEXSIM_AUTO_RECONCILE`).

## Slices

1. **Reports rollup**: endpoint + Executions tab + label. Independent of the rest.
2. **Runs = execution, Results = evidence**: move the tabs, runtime log from stored output and
   SSE, redirects. Builds on the "Review" rename and the stored-output fix (#340).
3. **Beacon live log**: step-start with command, streamed chunks; console renders them.

Each slice lands as its own PR into `dev` with tests that fail without it.
