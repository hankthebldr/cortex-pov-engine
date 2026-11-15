# Workflow scripts

Scripts for the Workflow tool. Run one with
`Workflow({ scriptPath: "docs/workflows/<file>", args: { ... } })`.

| Script | What it does | Args |
|---|---|---|
| `finalize-slice.workflow.js` | Finalization NOW slice (2026-07-11). Prepared, see its header. | none |
| `unit42-backlog-triage.workflow.js` | Read-only. Fetches the uncited Unit 42 index posts and returns one schema-checked verdict per post (`build_now`, `build_authored_only`, `not_scenario_material`, `needs_henry`). | none. The 18 posts are hard-coded in `BATCHES`; edit that list to triage others. |
| `unit42-backlog-build.workflow.js` | Builds triaged posts into scenarios: research brief (or a refresh of a saved one), author in its own worktree (`ttp-engineer`), adversarial review (`detection-corpus-reviewer`), one fix round, then optional serial integration and gates. | see below |

The triage script is a byte-for-byte copy of the script run `wf_62977aa9-f3d`
used on 2026-11-11 (12 agents; its verdicts are recorded in
`docs/reference/unit42-backlog-triage.md`).

The build script is the one run `wf_894ea860-faa` used on 2026-11-12/13
(4 agents; it landed SIM-CDR-030 / TTP-2026-0550). It is the 2026-11-11 script
(run `wf_abbf06d1-1b7`, which landed SIM-KOI-012 / TTP-2026-0203; that version
is in this file's git history) with these changes: the landing branch is an
argument, a saved research brief can be refreshed against the current checkout
in place of new web research, authored-only builds get their own declaration
rules and an emitter-fit review check, the integration step updates the
door-corpus pins, and the isolated-execution gate is `make e2e-tierc` only.

## `unit42-backlog-build` args

The script throws unless `main`, `venv_bin`, `base_sha` and at least one
candidate are given.

| Arg | Meaning | 2026-11-11 value |
|---|---|---|
| `main` | absolute path of the worktree that integration commits into | the landing branch's worktree |
| `land_branch` | branch that worktree must be on; required when `integrate` is true | `feature/scenarios-unit42-cloud-logging-cdr-030` |
| `venv_bin` | the repo venv's `bin`, put first on `PATH` for every gate | `<repo>/.venv/bin` |
| `base_sha` | commit every author worktree must descend from | `23d8d13c` |
| `image` | existing local SimCore image for the container run and the `make` gates; never built or pulled | `cortex-pov-engine-simcore:1.1.1` |
| `today` | date written into the corpus-size pin comment | `2026-11-12` |
| `integrate` | `true` lands the reviewed scenarios on `main`; otherwise they stay on their author branches | `true` |
| `pytest_baseline` | the host-suite result on `base_sha`, or where to read it; quoted to the integration step | a log file path |
| `candidates[]` | one object per post, below | one (`cloud-logging`) |

Each candidate: `key`, `priority`, `post_title`, `post_url`, `mode`
(`lab_runnable` or `authored_only`), `plane`, `scenario_id`, `scenario_path`,
`ttp_id`, `card_path`, `short_name`, `notes` (orchestrator notes handed to the
researcher and the author), and `triage` (the triage record for that post).
Optional: `brief_file` (a research brief saved by an earlier run; the first
stage then checks it against the current checkout and does not fetch the post
again), `extra_files[]` (more inputs for that stage) and `emitter_plugin` (the
plugin an authored-only build emits through; the reviewer renders its records
and checks every scored filter against them).

Before re-running the build script:

- Assign ids that are free on `dev` **and** on every open pull request. On
  2026-11-12 the detection-library pull request (#229) already claimed
  SIM-CDR-029 and TTP-2026-0204 to TTP-2026-0549; different file names mean
  git would not have reported the clash.
- The session scratchpad does not survive a restart. Keep the research brief
  and any edited copy of the script somewhere that does.
- The `LAB_FACTS` block describes the lab as read back on 2026-11-10 and
  2026-11-11. Re-check it against the tenant before reusing it.
- The commit trailer written into the author, fix and integration prompts is
  the one the 2026-11-12 run used. Replace it with the current session's
  trailer.
- The integration step runs `make e2e-tierc`, never the whole
  `tests/e2e_isolated` directory and never `tests/smoke`: some of those tests
  drive whatever SimCore is listening on this machine.
