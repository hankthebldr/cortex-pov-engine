"""Drift guard: the vocabularies and note codes docs/reference/dataset-requirements.md prints
equal the ones the code enforces. A value added on one side only fails here."""
from __future__ import annotations

import re
from pathlib import Path

from engine.dataset_requirements import CORTEX_SIDE_KINDS, PRODUCED_BY, SOURCE_REQUIREMENTS
from engine.prerequisites import NOTE_CODES

DOC = (Path(__file__).resolve().parents[2] / "docs" / "reference" / "dataset-requirements.md").read_text(
    encoding="utf-8")
_TICK = re.compile(r"`([^`]+)`")


def _paragraph(starts_with: str) -> str:
    lines = DOC.splitlines()
    (i,) = [n for n, line in enumerate(lines) if line.startswith(starts_with)]
    out = []
    for line in lines[i:]:
        if not line.strip():
            break
        out.append(line)
    return "\n".join(out)


def _table_after(heading_text: str) -> list[list[str]]:
    lines = DOC.splitlines()
    (i,) = [n for n, line in enumerate(lines) if heading_text in line]
    rows, started = [], False
    for line in lines[i + 1:]:
        if line.startswith("|"):
            started = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not set("".join(cells)) <= set("-: "):
                rows.append(cells)
        elif started:
            break
    return rows[1:]  # drop the header row


def test_produced_by_list_matches_code():
    text = _paragraph("`produced_by` and `source_side[].product`")
    assert set(_TICK.findall(text)) - {"produced_by", "source_side[].product"} == set(PRODUCED_BY)
    assert f"({len(PRODUCED_BY)} values)" in text


def test_cortex_side_kinds_match_code():
    text = _paragraph("`cortex_side[].kind`:")
    head = text.split("Today")[0]
    assert set(_TICK.findall(head)) - {"cortex_side[].kind"} == set(CORTEX_SIDE_KINDS)


def test_source_requirement_table_matches_code():
    rows = _table_after("`source_side[].requirement` depends on the product")
    listed = {}
    other = None
    for product, reqs in rows:
        allowed = set(_TICK.findall(reqs))
        if product == "every other product":
            other = allowed
        else:
            listed[_TICK.findall(product)[0]] = allowed
    assert other is not None
    for product, allowed in SOURCE_REQUIREMENTS.items():
        assert set(allowed) == listed.get(product, other), product
    assert set(listed) <= set(SOURCE_REQUIREMENTS)


def test_note_code_table_matches_code():
    rows = _table_after("Absence of evidence is a note, never an empty answer.")
    assert [_TICK.findall(code)[0] for code, _ in rows] == list(NOTE_CODES)
