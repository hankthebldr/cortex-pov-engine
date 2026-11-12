#!/usr/bin/env python3
"""
scripts/check_grouping_keys.py
------------------------------

Corpus gate: do each scenario's detections share a native XSIAM case-grouping
key? Runs ``engine.grouping_keys.analyze_scenario`` over every scenario
(found the way ``scripts/generate_ground_truth.py`` finds them) against the
TTP card corpus, and prints a per-code table.

Exit codes:
    0   no G-03 (every other code is reported, never gated)
    1   at least one G-03: a correlation rule maps the declared stitching_key
        to a custom destination XSIAM does not group on
    2   bad input: a scenario or card shape the analysis cannot read

Usage:
    python3 scripts/check_grouping_keys.py            # table
    python3 scripts/check_grouping_keys.py --json     # {counts_by_code, findings}
    python3 scripts/check_grouping_keys.py --base DIR # another corpus root
    make check-grouping
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "core") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "core"))

import yaml  # noqa: E402

from engine.grouping_keys import (  # noqa: E402
    G01_NO_SHARED_KEY, G02_STITCH_LOW_ONLY, G03_STITCH_KEY_CUSTOM, G04_KEY_UNKNOWN,
    G05_BRIDGEABLE, LEVELS, analyze_scenario,
)
from engine.ttp_catalog import TtpCatalog, default_corpus_dir  # noqa: E402

CODES = (G01_NO_SHARED_KEY, G02_STITCH_LOW_ONLY, G03_STITCH_KEY_CUSTOM,
         G04_KEY_UNKNOWN, G05_BRIDGEABLE)
OUTCOMES = {
    G01_NO_SHARED_KEY: "no shared native key (Medium+)",
    G02_STITCH_LOW_ONLY: "stitched step below Medium only",
    G03_STITCH_KEY_CUSTOM: "stitching_key to custom destination",
    G04_KEY_UNKNOWN: "GROUPING_KEY_UNKNOWN",
    G05_BRIDGEABLE: "BRIDGEABLE_IP_HOST",
}
_SKIP_SCENARIO_DIRS = {"probes", "packages", "campaigns"}


class CorpusShapeError(RuntimeError):
    """A scenario or card the analysis cannot read."""


def find_scenario_yaml_files(scenarios_dir: Path) -> list[Path]:
    """Same walk as scripts/generate_ground_truth.py (and the loader)."""
    found: list[Path] = []
    for root, dirs, files in os.walk(scenarios_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_SCENARIO_DIRS]
        for fname in files:
            if fname.endswith(".yml") and fname != "_schema.yml":
                found.append(Path(root) / fname)
    return sorted(found)


def corpus_report(base: Path) -> dict[str, Any]:
    """``{scenarios_analyzed, step_detections_analyzed, counts_by_code, findings}``
    for the corpus under ``base``. Every code appears in ``counts_by_code``,
    zeros included."""
    catalog = TtpCatalog()
    catalog.load(default_corpus_dir(str(base)))
    findings: list[dict[str, Any]] = []
    scenarios = step_detections = 0
    for path in find_scenario_yaml_files(base / "scenarios"):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("not a YAML mapping")
            out = analyze_scenario(doc, catalog)
        except (yaml.YAMLError, ValueError) as exc:
            raise CorpusShapeError(f"{path}: {exc}") from exc
        scenarios += 1
        step_detections += sum(len(s.get("expected_detections") or [])
                               for s in doc.get("steps") or [])
        findings.extend(asdict(f) for f in out)
    findings.sort(key=lambda f: (f["scenario_id"], f["step_id"] or "", f["code"], f["detail"]))
    counts = {c: 0 for c in CODES}
    for f in findings:
        counts[f["code"]] += 1
    return {"scenarios_analyzed": scenarios, "step_detections_analyzed": step_detections,
            "counts_by_code": counts, "findings": findings}


def render_table(report: dict[str, Any]) -> str:
    lines = [f"{'code':<6} {'level':<6} {'outcome':<38} {'count':>6}"]
    for c in CODES:
        lines.append(f"{c:<6} {LEVELS[c]:<6} {OUTCOMES[c]:<38} {report['counts_by_code'][c]:>6}")
    lines.append(f"scenarios {report['scenarios_analyzed']} · step-detections "
                 f"{report['step_detections_analyzed']}")
    for f in report["findings"]:
        if f["level"] in ("ERROR", "WARN"):
            lines.append(f"{f['level']} {f['code']} {f['scenario_id']} "
                         f"{f['step_id'] or '-'}: {f['detail']}")
    lines.append("class-level analysis (never value-level): advisory except G-03, "
                 "which fails the gate")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="emit {counts_by_code, findings}")
    ap.add_argument("--base", default=str(REPO_ROOT),
                    help="corpus root holding scenarios/ and detection_scanner/ttps/")
    args = ap.parse_args(argv)
    try:
        report = corpus_report(Path(args.base))
    except CorpusShapeError as exc:
        print(f"check_grouping_keys: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"counts_by_code": report["counts_by_code"],
                          "findings": report["findings"]}, indent=2, sort_keys=True))
    else:
        print(render_table(report))
    return 1 if report["counts_by_code"][G03_STITCH_KEY_CUSTOM] else 0


if __name__ == "__main__":
    raise SystemExit(main())
