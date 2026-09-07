# UC/TC P0 Coverage Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every open v2.2 test case a recorded closure mechanism and regenerate a UC/TC-keyed engine-coverage sheet + scoreboard from live engine state, so all later phases report through one machine-checked source.

**Architecture:** Two new pure modules under `core/engine/` compute a deterministic per-TC closure mechanism (from `detection_spec_v2.2.csv`) and project the 266 index rows into a coverage sheet. The existing crosswalk script gains an `--emit-xlsx` mode that writes a deterministic CSV (the CI-gated source of truth), an `.xlsx` twin (best-effort, needs openpyxl), and a markdown scoreboard. A `--emit-xlsx --check` mode fails closed when the committed sheet is stale, mirroring the repo's existing export-determinism gates.

**Tech Stack:** Python 3.11, stdlib `csv`/`io`, `openpyxl` (optional, host has 3.1.5), pytest. Modules live in the importable `engine` package (conftest puts `core/` on `sys.path`); the script is imported in tests via `importlib.util.spec_from_file_location` (precedent: `tests/eal_simulator/test_cli.py`).

**Spec:** [`docs/superpowers/specs/2026-09-04-uctc-full-coverage-design.md`](../specs/2026-09-04-uctc-full-coverage-design.md)

## Global Constraints

- **Mechanism vocabulary is exactly** `M1 | M2-quick | M2-longterm | M3 | M4 | M5`. No other value may be emitted.
- **An unrecognized `validation_class` raises**, never returns empty (spec §10 / repo rule "tolerance hides bugs").
- **Three-state, never merged:** `authored` (bool), `negative_control` (`unknown|true|false`, defaults `unknown` in P0), `tenant_verified` (bool, always `False` in P0). Closure counts `authored` in P0; `negative_control` is filled by later phases; `tenant_verified` only ever moves on a live-tenant run.
- **Never mutate the DC's original workbook.** All generated artifacts are new files under `docs/uc_tc_mapping/_v2.2-source/` (CSV + a `_v2.3_` xlsx) and `docs/uc_tc_mapping/` (scoreboard).
- **The CSV is the gate; the xlsx is a convenience.** The determinism check compares CSV + scoreboard text (stdlib, deterministic). The xlsx step is guarded by `import openpyxl` and skips gracefully when absent.
- **M3 (posture) open rows carry `status: blocked(laab)`** — authored-when-evidenced, otherwise blocked on LaaB, never plain `open`.
- Data source of truth: `docs/uc_tc_mapping/_v2.2-source/detection_spec_v2.2.csv` — 266 rows, one per `tc_id`, columns include `validation_class`, `target_dataset`, `uc_id`.

---

## File Structure

- **Create** `core/engine/uctc_mechanism.py` — the deterministic decision procedure (dataset→family→mechanism, platform category, per-TC binding record). Pure, no I/O.
- **Create** `core/engine/uctc_coverage_sheet.py` — projections of the 266 rows: `build_rows`, `rows_to_csv_text`, `scoreboard_markdown`, `write_xlsx`. Pure except `write_xlsx`.
- **Create** `tests/engine/test_uctc_mechanism.py` — unit tests for the decision procedure.
- **Create** `tests/engine/test_uctc_coverage_sheet.py` — unit tests for the projections.
- **Create** `tests/engine/test_uctc_emit_xlsx.py` — integration test driving the script's `--emit-xlsx --check` in a tmp tree.
- **Modify** `scripts/uctc_crosswalk_v2.2.py` — add `--emit-xlsx` / `--check` handling that calls the two modules.
- **Create (generated, committed)** `docs/uc_tc_mapping/_v2.2-source/engine_coverage_v2.3.csv`, `docs/uc_tc_mapping/_v2.2-source/CortexUCTCIndex_v2.3_engine-coverage.xlsx`, `docs/uc_tc_mapping/scoreboard.md`.
- **Modify** `Makefile` — add `uctc-sheet` (generate) and `check-uctc-sheet` (gate), and fold the gate into the existing `check-refs`/`validate` chain.

---

### Task 1: Decision procedure — dataset family → mechanism

**Files:**
- Create: `core/engine/uctc_mechanism.py`
- Test: `tests/engine/test_uctc_mechanism.py`

**Interfaces:**
- Produces: `dataset_family(target_dataset: str) -> str`, `mechanism_for(validation_class: str, target_dataset: str) -> str`, `platform_category(validation_class: str, target_dataset: str) -> str`. Mechanism return is one of the six vocabulary values; `mechanism_for` raises `ValueError` on an unknown class.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_uctc_mechanism.py
import pytest
from engine.uctc_mechanism import dataset_family, mechanism_for, platform_category


def test_dataset_family_splits_on_middot_and_takes_first_known():
    assert dataset_family("xdr_data") == "endpoint"
    assert dataset_family("cloud_audit_logs · container_events") == "cloud_audit"
    assert dataset_family("cloud_inventory · posture_findings") == "posture"
    assert dataset_family("panw_ngfw_traffic_raw · panw_ngfw_threat_raw") == "network"
    assert dataset_family("") == "other"
    assert dataset_family("some_unregistered_source") == "other"


def test_mechanism_for_maps_class_and_dataset():
    assert mechanism_for("DET", "xdr_data") == "M1"
    assert mechanism_for("DET", "panw_ngfw_traffic_raw · panw_ngfw_threat_raw") == "M2-quick"
    assert mechanism_for("DET", "incidents") == "M2-quick"
    assert mechanism_for("DET", "cloud_audit_logs · container_events") == "M2-longterm"
    assert mechanism_for("HNT", "xdr_data") == "M1"
    assert mechanism_for("POS", "posture_findings") == "M3"
    assert mechanism_for("PLT", "anything") == "M4"
    assert mechanism_for("AUT", "incidents") == "M5"


def test_mechanism_for_raises_on_unknown_class():
    with pytest.raises(ValueError):
        mechanism_for("BOGUS", "xdr_data")


def test_platform_category():
    assert platform_category("DET", "xdr_data") == "none"
    assert platform_category("DET", "cloud_audit_logs · container_events") == "cloud"
    assert platform_category("POS", "posture_findings") == "cloud"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_mechanism.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.uctc_mechanism'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/engine/uctc_mechanism.py
"""Deterministic closure-mechanism decision procedure for v2.2 test cases.

The mechanism is a property of the test case, computed from its index row
(`validation_class` + `target_dataset`), not guessed at authoring time. See
docs/superpowers/specs/2026-09-04-uctc-full-coverage-design.md sections 3-4.
"""
from __future__ import annotations

# Middle-dot-joined tokens in `target_dataset` -> a coarse signal family.
FAMILY_BY_DATASET: dict[str, str] = {
    "xdr_data": "endpoint",
    "incidents": "incidents",
    "network_story": "network",
    "pan_dns": "network",
    "panw_ngfw_traffic_raw": "network",
    "panw_ngfw_threat_raw": "network",
    "cloud_audit_logs": "cloud_audit",
    "container_events": "cloud_audit",
    "cloud_inventory": "posture",
    "posture_findings": "posture",
    "asm_assets": "asm",
    "asm_issues": "asm",
    "okta_sso": "identity_saas",
    "saas_okta_raw": "identity_saas",
    "msft_azure_ad_signin": "identity_saas",
    "msft_o365": "email",
    "msft_o365_audit": "email",
    "proofpoint_tap_raw": "email",
}

# DET/HNT family -> mechanism (+ controllability suffix on M2).
_DETHNT_MECHANISM_BY_FAMILY: dict[str, str] = {
    "endpoint": "M1",
    "network": "M2-quick",
    "incidents": "M2-quick",
    "cloud_audit": "M2-longterm",
    "identity_saas": "M2-longterm",
    "email": "M2-longterm",
    "posture": "M2-longterm",
    "asm": "M2-longterm",
    "other": "M2-longterm",
}

_CATEGORY_BY_FAMILY: dict[str, str] = {
    "endpoint": "none",
    "network": "none",
    "incidents": "none",
    "cloud_audit": "cloud",
    "posture": "cloud",
    "identity_saas": "identity",
    "email": "email",
    "asm": "external-surface",
    "other": "unknown",
}


def dataset_family(target_dataset: str) -> str:
    """Return the family of the first recognized dataset token, else 'other'."""
    if not target_dataset:
        return "other"
    for tok in (t.strip() for t in target_dataset.split("·")):
        fam = FAMILY_BY_DATASET.get(tok)
        if fam:
            return fam
    return "other"


def mechanism_for(validation_class: str, target_dataset: str) -> str:
    """Assign exactly one closure mechanism. Raise on an unknown class."""
    vc = (validation_class or "").strip().upper()
    if vc == "POS":
        return "M3"
    if vc == "PLT":
        return "M4"
    if vc == "AUT":
        return "M5"
    if vc in ("DET", "HNT"):
        return _DETHNT_MECHANISM_BY_FAMILY[dataset_family(target_dataset)]
    raise ValueError(f"unknown validation_class: {validation_class!r}")


def platform_category(validation_class: str, target_dataset: str) -> str:
    """Coarse external-platform category (refined per-TC by the runbook)."""
    vc = (validation_class or "").strip().upper()
    fam = dataset_family(target_dataset)
    if vc == "POS":
        return _CATEGORY_BY_FAMILY.get(fam, "cloud")
    return _CATEGORY_BY_FAMILY[fam]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_mechanism.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add core/engine/uctc_mechanism.py tests/engine/test_uctc_mechanism.py
git commit -m "feat(uctc): deterministic closure-mechanism decision procedure"
```

---

### Task 2: Per-TC binding record

**Files:**
- Modify: `core/engine/uctc_mechanism.py`
- Test: `tests/engine/test_uctc_mechanism.py`

**Interfaces:**
- Consumes: `mechanism_for`, `platform_category` from Task 1.
- Produces: `binding_record(tc_id: str, spec_row: dict, evidenced_by: str) -> dict` with keys `tc_id, uc_id, validation_class, mechanism, data_source, platform_category, evidenced_by, authored, negative_control, tenant_verified, status`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/engine/test_uctc_mechanism.py
from engine.uctc_mechanism import binding_record


def _row(vc, ds, uc="UC-EDR"):
    return {"validation_class": vc, "target_dataset": ds, "uc_id": uc}


def test_binding_record_open_endpoint_det():
    r = binding_record("TC-EDR-07", _row("DET", "xdr_data"), "")
    assert r["mechanism"] == "M1"
    assert r["authored"] is False
    assert r["negative_control"] == "unknown"
    assert r["tenant_verified"] is False
    assert r["status"] == "open"


def test_binding_record_authored_sets_status_authored():
    r = binding_record("TC-EDR-05", _row("DET", "xdr_data"), "scenario")
    assert r["authored"] is True
    assert r["evidenced_by"] == "scenario"
    assert r["status"] == "authored"


def test_binding_record_open_pos_is_laab_blocked():
    r = binding_record("TC-CSPM-02", _row("POS", "posture_findings", "UC-CSPM"), "")
    assert r["mechanism"] == "M3"
    assert r["status"] == "blocked(laab)"


def test_binding_record_authored_pos_is_authored_not_blocked():
    r = binding_record("TC-KSPM-03", _row("POS", "posture_findings", "UC-KSPM"), "assertion")
    assert r["status"] == "authored"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_mechanism.py::test_binding_record_open_endpoint_det -v`
Expected: FAIL — `ImportError: cannot import name 'binding_record'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to core/engine/uctc_mechanism.py

def binding_record(tc_id: str, spec_row: dict, evidenced_by: str) -> dict:
    """Assemble the durable per-TC binding record (spec section 5).

    `evidenced_by` is 'scenario', 'assertion', or '' (not yet authored).
    """
    vc = spec_row.get("validation_class", "")
    ds = spec_row.get("target_dataset", "")
    mech = mechanism_for(vc, ds)
    authored = bool(evidenced_by)
    if authored:
        status = "authored"
    elif mech == "M3":
        status = "blocked(laab)"
    else:
        status = "open"
    return {
        "tc_id": tc_id,
        "uc_id": spec_row.get("uc_id", ""),
        "validation_class": (vc or "").strip().upper(),
        "mechanism": mech,
        "data_source": ds,
        "platform_category": platform_category(vc, ds),
        "evidenced_by": evidenced_by,
        "authored": authored,
        "negative_control": "unknown",
        "tenant_verified": False,
        "status": status,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_mechanism.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add core/engine/uctc_mechanism.py tests/engine/test_uctc_mechanism.py
git commit -m "feat(uctc): per-TC binding record with three-state honesty columns"
```

---

### Task 3: Coverage-sheet rows + deterministic CSV projection

**Files:**
- Create: `core/engine/uctc_coverage_sheet.py`
- Test: `tests/engine/test_uctc_coverage_sheet.py`

**Interfaces:**
- Consumes: `binding_record` from Task 2.
- Produces: `COLUMNS: list[str]`, `build_rows(specs: dict[str, dict], evidenced_scenario: set[str], evidenced_assertion: set[str]) -> list[dict]` (sorted by `tc_id`), `rows_to_csv_text(rows: list[dict]) -> str` (`\n` line terminator, header + `COLUMNS` order).

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_uctc_coverage_sheet.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_coverage_sheet.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.uctc_coverage_sheet'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/engine/uctc_coverage_sheet.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_coverage_sheet.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add core/engine/uctc_coverage_sheet.py tests/engine/test_uctc_coverage_sheet.py
git commit -m "feat(uctc): coverage-sheet rows + deterministic CSV projection"
```

---

### Task 4: Scoreboard markdown + xlsx twin

**Files:**
- Modify: `core/engine/uctc_coverage_sheet.py`
- Test: `tests/engine/test_uctc_coverage_sheet.py`

**Interfaces:**
- Consumes: `build_rows`, `COLUMNS` from Task 3.
- Produces: `scoreboard_markdown(rows: list[dict]) -> str` (deterministic; counts by validation_class × status and by mechanism), `write_xlsx(path: str, rows: list[dict]) -> None` (raises `ImportError` if openpyxl absent).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/engine/test_uctc_coverage_sheet.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_coverage_sheet.py::test_scoreboard_markdown_counts_and_is_deterministic -v`
Expected: FAIL — `ImportError: cannot import name 'scoreboard_markdown'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to core/engine/uctc_coverage_sheet.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_coverage_sheet.py -v`
Expected: PASS (4 tests; xlsx test runs since openpyxl 3.1.5 is installed)

- [ ] **Step 5: Commit**

```bash
git add core/engine/uctc_coverage_sheet.py tests/engine/test_uctc_coverage_sheet.py
git commit -m "feat(uctc): scoreboard markdown + xlsx coverage-sheet twin"
```

---

### Task 5: Wire `--emit-xlsx` / `--check` into the crosswalk script

**Files:**
- Modify: `scripts/uctc_crosswalk_v2.2.py` (argparse block near line 918; add an emit branch before the `--report` return)
- Test: `tests/engine/test_uctc_emit_xlsx.py`

**Interfaces:**
- Consumes: `build_rows`, `rows_to_csv_text`, `scoreboard_markdown`, `write_xlsx` (Tasks 3-4); the script's existing `CROSSWALK`, `PLT_ASSERTION_CROSSWALK`, `POS_ASSERTION_CROSSWALK`, and `spec` (from `load_index`).
- Produces: writes `docs/uc_tc_mapping/_v2.2-source/engine_coverage_v2.3.csv`, `.../CortexUCTCIndex_v2.3_engine-coverage.xlsx`, and `docs/uc_tc_mapping/scoreboard.md`. `--emit-xlsx --check` returns exit 1 when the committed CSV or scoreboard is stale.

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_uctc_emit_xlsx.py
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "scripts", "uctc_crosswalk_v2.2.py")


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, cwd=REPO)


def test_emit_xlsx_writes_csv_and_scoreboard(tmp_path, monkeypatch):
    # Generate into the real tree, then assert the artifacts exist and --check passes.
    r = _run("--emit-xlsx")
    assert r.returncode == 0, r.stderr
    csv_path = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "engine_coverage_v2.3.csv")
    board = os.path.join(REPO, "docs", "uc_tc_mapping", "scoreboard.md")
    assert os.path.exists(csv_path)
    assert os.path.exists(board)
    # 266 data rows + header
    assert len(open(csv_path).read().splitlines()) == 267


def test_check_passes_when_in_sync():
    _run("--emit-xlsx")                    # regenerate
    r = _run("--emit-xlsx", "--check")     # then check
    assert r.returncode == 0, r.stderr


def test_check_fails_when_stale(tmp_path):
    csv_path = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source", "engine_coverage_v2.3.csv")
    _run("--emit-xlsx")
    original = open(csv_path).read()
    try:
        open(csv_path, "w").write(original + "TC-BOGUS,,,,,,,,,,\n")
        r = _run("--emit-xlsx", "--check")
        assert r.returncode == 1
    finally:
        open(csv_path, "w").write(original)   # restore
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_emit_xlsx.py -v`
Expected: FAIL — script exits nonzero / unknown argument `--emit-xlsx`

- [ ] **Step 3: Write minimal implementation**

In `scripts/uctc_crosswalk_v2.2.py`, add the two arguments after the existing ones (~line 922):

```python
    ap.add_argument("--emit-xlsx", action="store_true",
                    help="regenerate the UC/TC-keyed engine coverage sheet + scoreboard")
    ap.add_argument("--check", action="store_true",
                    help="with --emit-xlsx: fail if the committed sheet is stale")
```

Update the default-mode guard so `--emit-xlsx` alone does not fall back to `--report`:

```python
    if not (args.report or args.emit or args.apply or args.emit_xlsx):
        args.report = True
```

Add this branch immediately AFTER `tc, spec, lib = load_index()` and `files = scenario_files()` and the missing/extra/bad validation block (i.e. after the early `return 1` guards, before `rows = []`):

```python
    if args.emit_xlsx:
        sys.path.insert(0, os.path.join(REPO, "core"))
        from engine.uctc_coverage_sheet import (
            build_rows, rows_to_csv_text, scoreboard_markdown, write_xlsx,
        )
        evidenced_scen = {r for refs, _, _ in CROSSWALK.values() for r in refs}
        evidenced_assert = (
            {r for refs, _, _ in PLT_ASSERTION_CROSSWALK.values() for r in refs}
            | {r for refs, _, _ in POS_ASSERTION_CROSSWALK.values() for r in refs}
        )
        srows = build_rows(spec, evidenced_scen, evidenced_assert)
        csv_text = rows_to_csv_text(srows)
        board_text = scoreboard_markdown(srows)
        csv_path = os.path.join(IDX, "engine_coverage_v2.3.csv")
        board_path = os.path.join(OUT, "scoreboard.md")
        if args.check:
            cur_csv = open(csv_path).read() if os.path.exists(csv_path) else ""
            cur_board = open(board_path).read() if os.path.exists(board_path) else ""
            if cur_csv != csv_text or cur_board != board_text:
                print("ERROR: engine coverage sheet/scoreboard is stale — "
                      "run `make uctc-sheet` and commit", file=sys.stderr)
                return 1
            print("engine coverage sheet: in sync")
            return 0
        with open(csv_path, "w") as fh:
            fh.write(csv_text)
        with open(board_path, "w") as fh:
            fh.write(board_text)
        try:
            write_xlsx(os.path.join(IDX, "CortexUCTCIndex_v2.3_engine-coverage.xlsx"), srows)
            xlsx_note = "csv + xlsx + scoreboard"
        except ImportError:
            xlsx_note = "csv + scoreboard (openpyxl absent — xlsx skipped)"
        print(f"wrote {xlsx_note}: {csv_path}")
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_emit_xlsx.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit the code AND the generated artifacts**

```bash
python3 scripts/uctc_crosswalk_v2.2.py --emit-xlsx
git add scripts/uctc_crosswalk_v2.2.py tests/engine/test_uctc_emit_xlsx.py \
        docs/uc_tc_mapping/_v2.2-source/engine_coverage_v2.3.csv \
        docs/uc_tc_mapping/_v2.2-source/CortexUCTCIndex_v2.3_engine-coverage.xlsx \
        docs/uc_tc_mapping/scoreboard.md
git commit -m "feat(uctc): --emit-xlsx coverage sheet + scoreboard, CI-gated by --check"
```

---

### Task 6: Makefile targets + fold the gate into `check-refs`

**Files:**
- Modify: `Makefile` (near the `check-refs` / `crosswalk-report` targets, ~line 207-228)
- Test: `tests/engine/test_uctc_emit_xlsx.py` (add an in-sync assertion so CI proves the committed sheet matches the tree)

**Interfaces:**
- Consumes: the script's `--emit-xlsx --check` from Task 5.
- Produces: `make uctc-sheet` (generate), `make check-uctc-sheet` (gate), and `check-uctc-sheet` added to the `validate` chain.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/engine/test_uctc_emit_xlsx.py
def test_committed_sheet_is_in_sync_on_head():
    """The committed engine_coverage_v2.3.csv must match a fresh regeneration.
    This is the CI honesty gate: a merged change that shifts coverage without
    regenerating the sheet fails here."""
    r = _run("--emit-xlsx", "--check")
    assert r.returncode == 0, (
        "engine coverage sheet is stale on HEAD — run `make uctc-sheet` and commit\n"
        + r.stderr
    )
```

- [ ] **Step 2: Run test to verify it fails or passes honestly**

Run: `cd core && python -m pytest ../tests/engine/test_uctc_emit_xlsx.py::test_committed_sheet_is_in_sync_on_head -v`
Expected: PASS if Task 5's artifacts were committed; FAIL with the stale message otherwise (proving the gate bites).

- [ ] **Step 3: Add the Makefile targets**

```makefile
uctc-sheet: ## regenerate the UC/TC-keyed engine coverage sheet + scoreboard
	python3 scripts/uctc_crosswalk_v2.2.py --emit-xlsx

check-uctc-sheet: ## gate: committed engine coverage sheet matches the tree (fail-closed)
	python3 scripts/uctc_crosswalk_v2.2.py --emit-xlsx --check
```

Add `check-uctc-sheet` to the existing `validate` aggregate target's prerequisite list (line ~149), immediately after `check-refs`:

```makefile
validate: validate-detection check-refs check-uctc-sheet check-adapters check-streamer check-agent-shelf check-ground-truth ## ... (existing comment)
```

- [ ] **Step 4: Run the gate**

Run: `make check-uctc-sheet`
Expected: prints `engine coverage sheet: in sync`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add Makefile tests/engine/test_uctc_emit_xlsx.py
git commit -m "build(uctc): make uctc-sheet + check-uctc-sheet folded into validate"
```

---

## Self-Review

**1. Spec coverage.** Spec §3 (mechanisms) → Task 1. §4 (decision procedure) → Tasks 1-2. §5 (binding record) → Task 2. §8 (`--emit-xlsx` sheet, copy not original, CI-guarded) → Tasks 3-6. §9 (three-state done, M3 LaaB-blocked) → Task 2 status logic + Task 4 scoreboard. §10 (ground-truth loop, fold into existing job) → Task 6. §6 (log-sim L0 matrix) is **out of scope for this plan** — it is a different subsystem (`eal_simulator`) needing external catalog data, tracked as the immediate follow-on plan. §7 (runbooks) and §11-12 are later phases. No in-scope requirement is unimplemented.

**2. Placeholder scan.** No TBD/TODO/vague steps; every code step carries runnable code and an exact command with an expected result.

**3. Type consistency.** `COLUMNS` defined once in Task 3 and reused in Tasks 4-5. `build_rows(specs, evidenced_scenario, evidenced_assertion)` signature is identical in Tasks 3, 5. `write_xlsx(path, rows)` identical in Tasks 4, 5. `binding_record(tc_id, spec_row, evidenced_by)` identical in Tasks 2, 3. Mechanism vocabulary matches the global constraint in every table and mapping.

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** — one fresh subagent per task, review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
