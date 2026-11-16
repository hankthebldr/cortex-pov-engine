# Cloud posture and the IaC generator: target 1.4.x

This branch, `feature/scenarios-cloud-posture`, holds the cloud posture work.
It was taken off `dev` and `main` on 2026-11-15 so it can be developed here
and ship in a 1.4.x release. Nothing on this list is in 1.2.x or 1.3.x.

## What lives here

- **Planes:** CSPM (5 scenarios), ASM (6) and AI_SPM (7), with their 18 TTP
  cards and generated exports.
- **IaC topology generator:** `infra/` (11 AWS modules and the templates),
  `core/engine/infra_{generator,catalog,models}.py`, `core/api/infra.py`
  (`/api/infra/*`), `GET /api/scenarios/{id}/infra-hints`,
  `core/content_loader.py` and `scripts/jumpbox/install-content.sh`.
- **Console:** the Lab page (`LabView`), the legacy Deploy toggle
  (`InfraGenerator`) and the "Lab environments" column on Agents.
- **Assertions:** the 14 whose stimulus is a Terraform module: POS-CSPM-001..003,
  POS-DSPM-001..004, POS-AISP-001/002/003/005, POS-ASM-001/002 and PLT-XTI-001.

## Keeping this branch current

`dev` deleted these files and this branch never touched them. So a plain
`git merge dev` into this branch takes the deletions and removes the work here
as well. To bring `dev` in without losing anything:

1. `git merge origin/dev` on this branch.
2. `git revert -m 1 caa06f5e`. That is the merge commit of PR #380, which
   landed `chore/scenarios-pull-cloud-posture` on `dev` on 2026-11-15.

After step 2 the files are back, on top of current `dev`. Merge `dev` in that
way before each round of work. When the work is ready for 1.4.x, open the PR
from this branch into `dev` as usual.

## Gates that change when it returns

- `detection_scanner/scripts/coverage_report.py` carries
  `tactic_floor_overrides: {"TA0043": 1}` on `dev`, because Reconnaissance had
  one scenario left without ASM. Remove the override when ASM is back.
- The corpus pins (`tests/engine/test_corpus_refs_strict.py`, the door and
  signal-path counts, the golden push digests) were re-pinned for 187 scenarios
  on `dev`. Regenerate them, and `make ground-truth`, after the revert.
