"""Projections of the 266 index rows into the engine-coverage sheet + scoreboard.

Pure (except write_xlsx). The CSV projection is the CI-gated source of truth;
the xlsx is a best-effort convenience. See design spec sections 5 and 8.
"""
from __future__ import annotations

import csv
import io

from engine.uctc_mechanism import binding_record

COLUMNS: list[str] = [
    "tc_id", "uc_id", "validation_class", "mechanism", "data_source",
    "platform_category", "evidenced_by", "authored", "negative_control",
    "tenant_verified", "status",
]


def build_rows(specs: dict, evidenced_scenario: set, evidenced_assertion: set) -> list[dict]:
    """One binding record per index TC, sorted by tc_id. Scenario evidence wins
    over assertion evidence when a TC appears in both."""
    rows = []
    for tc_id in sorted(specs):
        if tc_id in evidenced_scenario:
            ev = "scenario"
        elif tc_id in evidenced_assertion:
            ev = "assertion"
        else:
            ev = ""
        rows.append(binding_record(tc_id, specs[tc_id], ev))
    return rows


def rows_to_csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r[k] for k in COLUMNS})
    return buf.getvalue()
