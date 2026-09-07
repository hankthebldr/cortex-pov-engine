#!/usr/bin/env python3
"""Find index rows whose `success_criteria` was copy-pasted from another row.

Why this matters more here than it would in a spreadsheet
---------------------------------------------------------
`success_criteria` is not decoration in this engine. It is the sentence an
artifact's claim is measured against, and it travels with `primary_kpi` and
`threshold` into `verifier.score_run`. When a row's criteria describe a
different capability than its title, two failures follow and both are silent:

1. An artifact authored from the TITLE is scored against the wrong claim.
2. An artifact authored from the CRITERIA "satisfies" a test case whose title
   promises something it never proved — which is how a POV report ends up
   claiming coverage the engine does not have.

The mechanical signal is exact `success_criteria` reuse ACROSS use cases. Reuse
*within* one use case is normal (two motions against one capability); reuse
across a UC boundary means text was pasted from a row about something else.

Detection is mechanical; the verdict is not — "is this criteria about the same
capability as this title" needs judgement, so `VERDICT` below is hand-authored
in the same spirit as `scripts/uctc_crosswalk_v2.2.py`. The script's job is to
prove the GROUPS still exist and to fail when the index changes underneath them.

Usage::

    python3 scripts/check_index_criteria_reuse.py            # report
    python3 scripts/check_index_criteria_reuse.py --check    # non-zero on drift
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "tc_index_v2.2.csv")

# Rows where the shared criteria describe a DIFFERENT capability than the row's
# own title. Each value is the capability the title promises, followed by the
# capability the pasted criteria actually measure.
DEFECTS: dict[str, tuple[str, str]] = {
    "TC-IR-06": (
        "malware auto-containment isolates the endpoint within an SLA",
        "malware DETECTION pre-execution and false-positive rate — the row's "
        "primary_kpi is 'False Positive Rate', which matches the pasted "
        "criteria and not its own title, confirming the direction of the paste",
    ),
    "TC-EDR-04": (
        "auto-containment triggers on confirmed malware execution",
        "malware detection across methods — containment is never measured",
    ),
    "TC-SOT-03": (
        "the full lifecycle loop: detection tuning -> enrichment -> response -> feedback",
        "enrichment only — the tuning and feedback legs that make it a loop are absent",
    ),
    "TC-NDR-06": (
        "EDL policy management and enforcement (network block lists)",
        "endpoint protection-policy deployment and exceptions",
    ),
    "TC-ERV-08": (
        "compliance catalog with custom policy configurability",
        "endpoint protection-policy deployment and exceptions",
    ),
    "TC-CDR-03": (
        "CSPM policy scanning detects misconfigurations across multi-cloud",
        "endpoint protection-policy deployment and exceptions",
    ),
    "TC-CDR-04": (
        "CIEM identifies over-permissioned cloud identities and recommends "
        "least-privilege",
        "multi-IdP identity ingest and cross-provider user correlation — a "
        "different capability, and verbatim the claim TC-ITDR-03/06 already own",
    ),
    "TC-TIM-02": (
        "automated IOC-to-EDL push for real-time blocking at the NGFW (outbound)",
        "NGFW log ingest and correlation into incidents (inbound)",
    ),
}

# Groups where cross-UC reuse is legitimate: two motions genuinely measuring one
# capability. Listed explicitly so a reviewer can see they were considered and
# not merely missed, and so a NEW group cannot hide among them.
BENIGN_ANCHORS = {
    "TC-IR-01", "TC-SOT-02",            # auto-triage, two motions
    "TC-IR-07", "TC-SOAR-02",           # playbook automation
    "TC-TH-05", "TC-NDR-03",            # EAL false-positive reduction
    "TC-EDR-05", "TC-NDR-01", "TC-NDR-02",  # causality stitching
    "TC-EDR-03",                        # the detection row the group B text fits
    "TC-EDR-01", "TC-EDR-02",           # the endpoint-policy rows group G fits
    "TC-ITDR-03", "TC-ITDR-06",         # the IdP rows group I fits
    "TC-ERV-02",                        # the NGFW-ingest row group J fits
    "TC-IR-11", "TC-SOAR-01",           # enrichment
    "TC-IR-09", "TC-ITDR-05", "TC-ERV-06",  # generic risk-scoring text
}


def groups() -> list[tuple[str, list[dict]]]:
    """Criteria strings shared by rows spanning more than one use case."""
    with open(IDX) as fh:
        rows = list(csv.DictReader(fh))
    by_criteria: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        text = (row.get("success_criteria") or "").strip()
        if text:
            by_criteria[text].append(row)
    return sorted(
        ((text, rs) for text, rs in by_criteria.items()
         if len({r["uc_id"] for r in rs}) > 1),
        key=lambda kv: -len(kv[1]),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the index drifted from the verdicts")
    args = ap.parse_args()

    found = groups()
    affected = {r["tc_id"] for _t, rs in found for r in rs}
    classified = set(DEFECTS) | BENIGN_ANCHORS

    untriaged = sorted(affected - classified)
    vanished = sorted(classified - affected)

    for tc in untriaged:
        print(f"UNTRIAGED: {tc} shares success_criteria across a UC boundary and "
              f"has no verdict", file=sys.stderr)
    for tc in vanished:
        print(f"STALE: {tc} carries a verdict but no longer shares criteria "
              f"across a UC boundary", file=sys.stderr)

    if args.check:
        if untriaged or vanished:
            print(f"\n{len(untriaged)} untriaged, {len(vanished)} stale",
                  file=sys.stderr)
            return 1
        print(f"index-criteria-reuse: {len(found)} cross-UC groups covering "
              f"{len(affected)} rows, all triaged ({len(DEFECTS)} defects)")
        return 0

    print(f"# success_criteria reused across use-case boundaries\n")
    print(f"{len(found)} groups · {len(affected)} rows · "
          f"{len(DEFECTS)} judged DEFECTS\n")
    for text, rs in found:
        ucs = sorted({r["uc_id"] for r in rs})
        print(f"\n## {len(rs)} rows across {len(ucs)} UCs — {', '.join(ucs)}")
        print(f"    criteria: {text[:150]}")
        for r in sorted(rs, key=lambda r: r["tc_id"]):
            mark = "DEFECT " if r["tc_id"] in DEFECTS else "ok     "
            print(f"    {mark} {r['tc_id']:14s} {r['test_case_title'][:78]}")
    print(f"\n\n# The {len(DEFECTS)} defects, by what the row promises vs measures\n")
    for tc, (promises, measures) in sorted(DEFECTS.items()):
        print(f"{tc}")
        print(f"    title promises : {promises}")
        print(f"    criteria measure: {measures}\n")
    return 1 if untriaged or vanished else 0


if __name__ == "__main__":
    raise SystemExit(main())
