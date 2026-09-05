from engine.uctc_coverage_sheet import COLUMNS, build_rows, rows_to_csv_text


def _specs():
    return {
        "TC-EDR-05": {"validation_class": "DET", "target_dataset": "xdr_data", "uc_id": "UC-EDR"},
        "TC-EDR-07": {"validation_class": "DET", "target_dataset": "xdr_data", "uc_id": "UC-EDR"},
        "TC-CSPM-02": {"validation_class": "POS", "target_dataset": "posture_findings", "uc_id": "UC-CSPM"},
    }


def test_build_rows_sorted_and_classified():
    rows = build_rows(_specs(), {"TC-EDR-05"}, {"TC-CSPM-02"})
    ids = [r["tc_id"] for r in rows]
    assert ids == ["TC-CSPM-02", "TC-EDR-05", "TC-EDR-07"]  # sorted
    by = {r["tc_id"]: r for r in rows}
    assert by["TC-EDR-05"]["status"] == "authored"
    assert by["TC-EDR-05"]["evidenced_by"] == "scenario"
    assert by["TC-CSPM-02"]["status"] == "authored"      # evidenced by assertion
    assert by["TC-CSPM-02"]["evidenced_by"] == "assertion"
    assert by["TC-EDR-07"]["status"] == "open"


def test_rows_to_csv_text_is_deterministic():
    rows = build_rows(_specs(), {"TC-EDR-05"}, {"TC-CSPM-02"})
    a = rows_to_csv_text(rows)
    b = rows_to_csv_text(build_rows(_specs(), {"TC-EDR-05"}, {"TC-CSPM-02"}))
    assert a == b
    assert a.splitlines()[0] == ",".join(COLUMNS)
    assert a.endswith("\n")
import pytest
from engine.uctc_coverage_sheet import scoreboard_markdown, write_xlsx


def test_scoreboard_markdown_counts_and_is_deterministic():
    rows = build_rows(_specs(), {"TC-EDR-05"}, {"TC-CSPM-02"})
    md = scoreboard_markdown(rows)
    assert md == scoreboard_markdown(rows)          # deterministic
    assert "| DET |" in md
    assert "authored" in md
    assert "tenant-verified" in md.lower()


def test_write_xlsx_roundtrip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    rows = build_rows(_specs(), {"TC-EDR-05"}, set())
    out = tmp_path / "sheet.xlsx"
    write_xlsx(str(out), rows)
    wb = openpyxl.load_workbook(out, read_only=True)
    ws = wb["Engine Coverage v2.3"]
    header = [c.value for c in next(ws.iter_rows(max_row=1))]
    assert header == COLUMNS
