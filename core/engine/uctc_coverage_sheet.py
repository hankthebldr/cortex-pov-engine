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
from collections import Counter

_CLASSES = ["DET", "HNT", "POS", "PLT", "AUT"]
_MECHANISMS = ["M1", "M2-quick", "M2-longterm", "M3", "M4", "M5"]


def scoreboard_markdown(rows: list[dict]) -> str:
    authored = Counter(r["validation_class"] for r in rows if r["authored"])
    total = Counter(r["validation_class"] for r in rows)
    blocked = Counter(r["validation_class"] for r in rows if r["status"].startswith("blocked"))
    proven = Counter(r["validation_class"] for r in rows if r["tenant_verified"])
    mech = Counter(r["mechanism"] for r in rows if not r["authored"])

    out = ["# UC/TC Engine Coverage Scoreboard", "",
           "> Regenerated from live engine state by `make uctc-sheet`. Do not hand-edit.",
           "", "## Coverage by validation class", "",
           "| class | total | authored | open | blocked(laab) | tenant-verified |",
           "|---|---:|---:|---:|---:|---:|"]
    for c in _CLASSES:
        t = total.get(c, 0)
        a = authored.get(c, 0)
        b = blocked.get(c, 0)
        out.append(f"| {c} | {t} | {a} | {t - a} | {b} | {proven.get(c, 0)} |")
    ta, tt = sum(authored.values()), sum(total.values())
    out.append(f"| **all** | **{tt}** | **{ta}** | **{tt - ta}** | "
               f"**{sum(blocked.values())}** | **{sum(proven.values())}** |")
    out += ["", "## Open TCs by mechanism", "",
            "| mechanism | open count |", "|---|---:|"]
    for m in _MECHANISMS:
        out.append(f"| {m} | {mech.get(m, 0)} |")
    out += ["", "_tenant-verified is a separate column; authored is not proven._", ""]
    return "\n".join(out)


def write_xlsx(path: str, rows: list[dict]) -> None:
    import openpyxl  # raises ImportError when absent — caller degrades to CSV

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Engine Coverage v2.3"
    ws.append(COLUMNS)
    for r in rows:
        ws.append([r[k] for k in COLUMNS])
    wb.save(path)
