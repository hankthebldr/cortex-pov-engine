# Staged detection library (drafts)

Generated TTP cards and scenarios that are **not part of the corpus**. They were
written by a local model through the `scripts/detdev/` pipeline and have not been
reviewed or run. They are kept here, in the open, so each one can be reviewed and
promoted on its own merits instead of landing as one unreviewable change.

`INDEX.md` lists every pair with what its content shows. `MANIFEST.json` is the
same data for tools. Both are generated: `python3 scripts/detdev/manifest.py --apply`.

## What staging means

| | corpus (`scenarios/`, `detection_scanner/ttps/`) | this directory |
|---|---|---|
| Loaded by SimCore at boot | yes | no |
| Shown in the console, launchable | yes | no |
| Counted by coverage, ground truth, the UC/TC index | yes | no |
| Read by the validators and the corpus pins | yes | no |
| Shipped in the image | yes | no |
| Ids | `SIM-<PLANE>-NNN`, `TTP-2026-NNNN` | provisional: `SIM-DRAFT-NNN`, `TTP-0000-NNNN` |

A provisional id is a position in this directory, nothing more. A pair gets its
real ids when it is promoted.

## Layout

```
drafts/detection-library/
  cards/TTP-0000-NNNN-<slug>.json         one draft card per pair
  scenarios/sim-draft-NNN-<slug>.yml      its draft scenario
  INDEX.md, MANIFEST.json                 generated: what each pair's content shows
  ORIGIN.json                             where each rebuilt pair came from
```

## How a pair is built

`scripts/detdev/library.py` builds both files from two inputs only: the model's
fragment and the pair's recorded source. Nothing is copied from a live card or
scenario.

- **Source.** The Cortex analytics alert the fragment was written for, taken from
  the pipeline's target lists (`scripts/detdev/targets-*-analytics.txt`). It is the
  card's primary reference and the scenario's `threat_report_url`. A pair whose
  alert is not in the lists says "not recorded" and cites nothing.
- **Indicators.** A hash is carried only with the `source_url` that published it.
- **Platform.** Every step is labelled with the card's platform. Windows steps run
  as `direct`, Linux and macOS steps as `root`; the model's own choice is not used.
- **Tools.** A tool declaration is kept only when a step runs that tool.
- **Not written at all:** threat actors, remediation text, UC/TC refs, a
  measurement contract, a cleanup. The scenario's cleanup is a placeholder line
  that says so; what the model proposes for cleanup is recorded on the card as
  untested.

The first 366 pairs were rebuilt this way from the output of PR #229, whose
packager cloned a live pair and so stamped that pair's source, refs, tool,
identity and cleanup on everything. `ORIGIN.json` maps each provisional id to the
card and scenario it replaces there.

## What every pair still needs

`INDEX.md` lists per-pair findings read from the content. Independent of those,
no pair here has had any of the following:

1. A review of the commands: real, runnable, confined to a scratch directory, no
   traffic to a host outside the lab. The scratch directory is created exclusively
   (`rm -rf` then `mkdir -m 700` with no `-p`) and every later step checks in one
   `stat` lookup that it is a directory the running user owns; `tests/engine/test_scenario_sandbox_ownership.py`
   shows the two lines.
2. A cleanup that was written and run.
3. A check of each declared row against what a tenant can raise. Most rows are
   `BIOC`, which exist only if the rule is deployed to the tenant. `Analytics` and
   `ABIOC` rows name built-in detectors whose activation and training periods are
   on the alert's reference page. No pair declares a `Correlation` row.
4. UC/TC refs and a measurement contract.
5. A run on a lab host. The lab has no Windows host, so the Windows pairs cannot
   be lab-run today.

## Promoting a pair

1. Pick it from `INDEX.md`. The pairs with no content finding are the cheapest start.
2. Author it properly: the `ttp-engineer` agent writes the scenario and card from
   the draft and the alert's reference page, and `detection-corpus-reviewer`
   reviews the result. `docs/workflows/unit42-backlog-build.workflow.js` is that
   loop as a workflow.
3. Give it the next free real ids. Check open pull requests as well as `dev`.
4. Move the two files into `scenarios/<plane>/` and `detection_scanner/ttps/`, add
   the crosswalk row, and update the corpus pins the new scenario moves.
5. Run the corpus gates (`make validate-detection check-refs coverage-strict
   check-ground-truth check-lab-ready`).
6. Remove the promoted pair from this directory and regenerate the manifest.

## Adding drafts

```
python3 scripts/detdev/genfrag.py FRAG.json edr "<brief>" "" --platform windows --alert "<exact alert name>"
python3 scripts/detdev/package.py FRAG.json            # preview
python3 scripts/detdev/package.py FRAG.json --apply    # writes the next free pair here
python3 scripts/detdev/manifest.py --apply
```

`scripts/detdev/TASK.md` has the pipeline's conventions.
