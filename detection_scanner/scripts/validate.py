#!/usr/bin/env python3
"""
detection_scanner/scripts/validate.py — canonical validator for the TTP corpus.

Single source of truth for what "valid" means. CI, the cortex-pov-engine loader,
and human contributors all run this. Exits non-zero on any failure.

Checks performed:
  1. JSON well-formedness for every file under schema/, sources/, ttps/.
  2. The schema file itself is valid JSON Schema 2020-12.
  3. Every ttps/*.json validates against schema/ttp-entry.schema.json.
  4. ID uniqueness across ttps/ (no two entries share the same TTP-YYYY-NNNN).
  5. Filename matches id field (filename starts with id).
  6. Every metadata.source_refs[] entry resolves to a source-registry id.
  7. Every references[].publisher_id resolves to a source-registry id.
  8. publisher_id values in references[] are a subset of metadata.source_refs[].
  9. Exactly one references[] entry has primary: true.
 10. MITRE ATT&CK technique IDs follow Txxxx[.xxx] format.
 11. Use case ids match UC-<DOMAIN>-NNN pattern; test case ids match TC-<DOMAIN>-NNN[A-Z].
 12. Within a use case, sum of expected_score_weight is <= 1.0 (allows for unweighted tests).

Usage:
  python3 scripts/validate.py                 # full check, repo root
  python3 scripts/validate.py --strict        # additionally require weights sum to exactly 1.0
  python3 scripts/validate.py --quiet         # suppress per-file PASS lines
"""

import argparse
import glob
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

ID_PATTERN          = re.compile(r"^TTP-\d{4}-\d{4}$")
TECHNIQUE_PATTERN   = re.compile(r"^T\d{4}(\.\d{3})?$")
UC_PATTERN          = re.compile(r"^UC-[A-Z0-9]+-\d{3}$")
TC_PATTERN          = re.compile(r"^TC-[A-Z0-9]+-\d{3}[A-Z]?$")
SOURCE_ID_PATTERN   = re.compile(r"^SRC-[A-Z0-9-]+$")


class Report:
    def __init__(self, quiet=False):
        self.errors = []
        self.warnings = []
        self.passes = 0
        self.quiet = quiet

    def err(self, where, msg):
        self.errors.append(f"FAIL  {where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"WARN  {where}: {msg}")

    def ok(self, msg):
        self.passes += 1
        if not self.quiet:
            print(f"PASS  {msg}")

    def summary(self):
        print()
        print(f"--- {self.passes} pass, {len(self.warnings)} warn, {len(self.errors)} fail ---")
        for w in self.warnings:
            print(w)
        for e in self.errors:
            print(e)


def load_json(path, report):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        report.err(str(path), f"invalid JSON: {e}")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="weights must sum to exactly 1.0 per use case")
    ap.add_argument("--quiet", action="store_true", help="suppress per-file PASS output")
    args = ap.parse_args()

    report = Report(quiet=args.quiet)

    # ---- 1. JSON well-formedness ----
    all_jsons = sorted(
        glob.glob(str(ROOT / "schema" / "*.json"))
        + glob.glob(str(ROOT / "sources" / "*.json"))
        + glob.glob(str(ROOT / "ttps" / "*.json"))
    )
    parsed = {}
    for f in all_jsons:
        d = load_json(f, report)
        if d is not None:
            parsed[f] = d
            report.ok(f"json:wellformed {Path(f).relative_to(ROOT)}")

    schema_path = ROOT / "schema" / "ttp-entry.schema.json"
    schema = parsed.get(str(schema_path))
    if schema is None:
        report.err("schema", "schema/ttp-entry.schema.json missing or unparsable")
        report.summary()
        sys.exit(2)

    # ---- 2. Schema itself is valid 2020-12 ----
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        report.err("env", "Install jsonschema>=4.18: pip install 'jsonschema>=4.18'")
        report.summary()
        sys.exit(2)

    try:
        Draft202012Validator.check_schema(schema)
        report.ok("schema:meta-valid Draft 2020-12")
    except Exception as e:
        report.err("schema", f"meta-schema check failed: {e}")
        report.summary()
        sys.exit(2)

    validator = Draft202012Validator(schema)

    # ---- 3. Source registry sanity ----
    registry_path = ROOT / "sources" / "source-registry.json"
    registry = parsed.get(str(registry_path))
    if registry is None:
        report.err("sources", "source-registry.json missing")
        report.summary()
        sys.exit(2)
    registered_ids = {s["id"] for s in registry.get("sources", [])}
    for sid in registered_ids:
        if not SOURCE_ID_PATTERN.match(sid):
            report.err("source-registry", f"source id {sid} does not match {SOURCE_ID_PATTERN.pattern}")
    report.ok(f"source-registry: {len(registered_ids)} sources loaded")

    # ---- 4. TTP-by-TTP checks ----
    ttp_files = sorted(glob.glob(str(ROOT / "ttps" / "*.json")))
    seen_ids = {}

    for f in ttp_files:
        rel = str(Path(f).relative_to(ROOT))
        d = parsed.get(f)
        if d is None:
            continue

        # 4a. Schema validation
        errs = sorted(validator.iter_errors(d), key=lambda e: list(e.absolute_path))
        for e in errs:
            path = "/".join(str(p) for p in e.absolute_path) or "(root)"
            report.err(f"{rel}", f"schema @ {path}: {e.message[:200]}")
        if errs:
            continue

        # 4b. ID format + uniqueness
        ttp_id = d.get("id", "")
        if not ID_PATTERN.match(ttp_id):
            report.err(rel, f"id {ttp_id!r} does not match {ID_PATTERN.pattern}")
            continue
        if ttp_id in seen_ids:
            report.err(rel, f"duplicate id {ttp_id}; also in {seen_ids[ttp_id]}")
        seen_ids[ttp_id] = rel

        # 4c. Filename should start with id
        if not Path(f).name.startswith(ttp_id):
            report.err(rel, f"filename does not start with id {ttp_id}")

        # 4d. Source ref cross-reference
        refs = set(d.get("metadata", {}).get("source_refs", []))
        unknown_refs = refs - registered_ids
        if unknown_refs:
            report.err(rel, f"metadata.source_refs has unknown ids: {sorted(unknown_refs)}")

        pub_ids = {r.get("publisher_id") for r in d.get("references", []) if r.get("publisher_id")}
        unknown_pubs = pub_ids - registered_ids
        if unknown_pubs:
            report.err(rel, f"references[].publisher_id has unknown ids: {sorted(unknown_pubs)}")

        # 4e. references[].publisher_id should be a subset of source_refs[]
        not_in_meta = pub_ids - refs
        if not_in_meta:
            report.warn(rel, f"references[].publisher_id {sorted(not_in_meta)} not also in metadata.source_refs")

        # 4f. Exactly one primary reference
        primaries = [r for r in d.get("references", []) if r.get("primary")]
        if len(primaries) == 0:
            report.warn(rel, "no references entry has primary: true (recommended)")
        elif len(primaries) > 1:
            report.err(rel, f"{len(primaries)} references entries have primary: true (must be exactly 1)")

        # 4g. MITRE technique format
        for t in d.get("mitre_attack", {}).get("techniques", []):
            tid = t.get("technique_id", "")
            sid = t.get("subtechnique_id")
            if not TECHNIQUE_PATTERN.match(tid):
                report.err(rel, f"mitre technique_id {tid!r} does not match Txxxx")
            if sid and not TECHNIQUE_PATTERN.match(sid):
                report.err(rel, f"mitre subtechnique_id {sid!r} does not match Txxxx.xxx")
            if sid and not sid.startswith(tid + "."):
                report.err(rel, f"subtechnique {sid} not under technique {tid}")

        # 4h. Use case / test case IDs + weight sums
        for uc in d.get("panw_mapping", {}).get("use_cases", []):
            uc_id = uc.get("use_case_id", "")
            if not UC_PATTERN.match(uc_id):
                report.err(rel, f"use_case_id {uc_id!r} does not match {UC_PATTERN.pattern}")
            weight_sum = 0.0
            for tc in uc.get("test_cases", []):
                tc_id = tc.get("test_case_id", "")
                if not TC_PATTERN.match(tc_id):
                    report.err(rel, f"test_case_id {tc_id!r} does not match {TC_PATTERN.pattern}")
                weight_sum += float(tc.get("expected_score_weight", 0.0))
            if weight_sum > 1.0001:
                report.err(rel, f"use case {uc_id} weights sum to {weight_sum:.3f} > 1.0")
            elif args.strict and abs(weight_sum - 1.0) > 0.001:
                report.err(rel, f"--strict: use case {uc_id} weights sum to {weight_sum:.3f}, must == 1.0")
            elif weight_sum < 0.999 and not args.strict:
                report.warn(rel, f"use case {uc_id} weights sum to {weight_sum:.3f} (< 1.0)")

        # 4i. pov_engine.platforms intersection sanity
        plats = set(d.get("metadata", {}).get("pov_engine", {}).get("platforms", []))
        target = d.get("execution", {}).get("target_platform")
        if target and target != "cross-platform" and target not in plats:
            report.warn(rel, f"execution.target_platform={target!r} not in metadata.pov_engine.platforms {sorted(plats)}")

        report.ok(f"ttp {ttp_id}")

    report.summary()
    sys.exit(1 if report.errors else 0)


if __name__ == "__main__":
    main()
