#!/usr/bin/env python3
"""Regenerate docs/uc_tc_mapping/unscoreable-tcs.md from the index snapshot.

The list is derived, never hand-maintained — it moves whenever the index gains
a measurable threshold. Run inside the prod image so core/ imports resolve:

    docker run --rm -v "$PWD:/app" -w /app cortexsim:dev \
        python scripts/report_unscoreable_tcs.py
"""
import os
import sys, csv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "core"))
from engine.uctc_registry import registry, default_index_dir
registry.load(default_index_dir(REPO))

bound = set()
for x in csv.DictReader(open(os.path.join(REPO, "docs/uc_tc_mapping/crosswalk-v2.2.csv"))):
    bound.update(x["new_tc_refs"].split())

u = sorted(registry.unscoreable(), key=lambda t: (t.uc_id, t.tc_id))
hit = [t for t in u if t.tc_id in bound]
det = [t for t in registry.all_test_cases() if t.needs_detection_content]

L = []
L.append("# Unscoreable test cases\n")
L.append("Detection-backable (DET/HNT) test cases in the v2.2 master index that carry")
L.append("no machine-evaluable threshold — `Qualitative pass`, `TBD`, or blank.\n")
L.append("`engine/verifier.py` resolves these to **`not_applicable`** and never to")
L.append("`pass`. A silent pass on an unscoreable test case produces a green POV")
L.append("readout that means nothing, which is strictly worse than reporting no score.\n")
L.append(f"- **{len(u)} of {len(det)}** DET/HNT test cases are unscoreable.")
L.append(f"- **{len(hit)}** of them are bound by the current scenario corpus.\n")
L.append("Fixing one means giving it a measurable threshold upstream in the index, then")
L.append("re-exporting the snapshot. Until then the engine reports them honestly.\n")
L.append("Regenerate with `make unscoreable-report`.\n")
L.append("| Test case | Use case | Class | Tier | Threshold | Bound by corpus |")
L.append("|---|---|---|---|---|---|")
for t in u:
    thr = (t.threshold or "").strip() or "_(blank)_"
    L.append(f"| `{t.tc_id}` | {t.uc_id} | {t.validation_class} | {t.differentiation_tier} | {thr} | {'yes' if t.tc_id in bound else 'no'} |")
open(os.path.join(REPO, "docs/uc_tc_mapping/unscoreable-tcs.md"), "w").write("\n".join(L) + "\n")
print(f"wrote {len(u)} unscoreable test cases ({len(hit)} bound by the corpus)")
