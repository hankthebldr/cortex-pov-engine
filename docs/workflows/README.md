# Workflow scripts

Scripts for the Workflow tool. Run one with
`Workflow({ scriptPath: "docs/workflows/<file>", args: { ... } })`.

| Script | What it does | Args |
|---|---|---|
| `finalize-slice.workflow.js` | Finalization NOW slice (2026-07-11). Prepared, see its header. | none |
| `unit42-backlog-triage.workflow.js` | Read-only. Fetches the uncited Unit 42 index posts and returns one schema-checked verdict per post (`build_now`, `build_authored_only`, `not_scenario_material`, `needs_henry`). | none. The 18 posts are hard-coded in `BATCHES`; edit that list to triage others. |
| `unit42-backlog-build.workflow.js` | Builds triaged posts into scenarios: research brief, author in its own worktree (`ttp-engineer`), adversarial review (`detection-corpus-reviewer`), one fix round, then optional serial integration and gates. | see below |

Both Unit 42 scripts are byte-for-byte copies of the scripts the 2026-11-11
runs used: triage run `wf_62977aa9-f3d` (12 agents; its verdicts are recorded
in `docs/reference/unit42-backlog-triage.md`) and build run `wf_abbf06d1-1b7`
(4 agents; it landed SIM-KOI-012 / TTP-2026-0203 on
`feature/scenarios-unit42-backlog`).

## `unit42-backlog-build` args

The script throws unless `main`, `venv_bin`, `base_sha` and at least one
candidate are given.

| Arg | Meaning | 2026-11-11 value |
|---|---|---|
| `main` | absolute path of the worktree that integration commits into | the `feature/scenarios-unit42-backlog` worktree |
| `venv_bin` | the repo venv's `bin`, put first on `PATH` for every gate | `<repo>/.venv/bin` |
| `base_sha` | commit every author worktree must descend from | `88fd1c0f` |
| `image` | existing local SimCore image for the container run and the `make` gates; never built or pulled | `cortex-pov-engine-simcore:1.1.0` |
| `today` | date written into the corpus-size pin comment | `2026-11-11` |
| `integrate` | `true` lands the reviewed scenarios on `main`; otherwise they stay on their author branches | `true` |
| `candidates[]` | one object per post, below | one (`axios`) |

Each candidate: `key`, `priority`, `post_title`, `post_url`, `mode`
(`lab_runnable` or `authored_only`), `plane`, `scenario_id`, `scenario_path`,
`ttp_id`, `card_path`, `short_name`, `notes` (orchestrator notes handed to the
researcher and the author), and `triage` (the triage record for that post).

Before re-running the build script:

- The integration prompt requires `git branch --show-current` in `main` to
  print `feature/scenarios-unit42-backlog`. Change that string for another
  branch.
- The `LAB_FACTS` block describes the lab as read back on 2026-11-10 and
  2026-11-11. Re-check it against the tenant before reusing it.
- The commit trailer written into the author, fix and integration prompts is
  the one the 2026-11-11 run used. Replace it with the current session's
  trailer.
