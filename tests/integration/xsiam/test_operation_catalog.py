# tests/integration/xsiam/test_operation_catalog.py
"""Loader/schema tests for the XSIAM operation catalog."""
from __future__ import annotations

import os

from integrations.xsiam.operations import catalog as catmod
from integrations.xsiam.operations.catalog import OperationCatalog
from integrations.xsiam.operations.schema import XsiamOperationSchema

PACKS = os.path.join(os.path.dirname(catmod.__file__), "packs")


def test_shipped_packs_load_clean():
    cat = OperationCatalog()
    n = cat.load(PACKS)
    assert n >= 100  # 116 today; guard against a pack disappearing
    # every op has a resolvable, well-formed record
    for op in cat.all():
        assert op.op_id.startswith("OP-")
        assert op.path.startswith("/public_api/")
        assert op.access_class in ("read", "write", "destructive")


def test_known_operations_resolve():
    cat = OperationCatalog()
    cat.load(PACKS)
    # confirmed-path ops from the authoritative CSV
    assert cat.find("OP-QUARANTINE-STATUS").path == "/public_api/v1/quarantine/status"
    assert cat.find("OP-ACTIONS-FILE-RETRIEVAL-DETAILS").access_class == "read"
    assert cat.find("OP-INDICATORS-GET").access_class == "read"
    assert cat.find("OP-BIOC-DELETE").access_class == "destructive"


def test_path_params_are_derived():
    op = XsiamOperationSchema(
        op_id="OP-TEST-PARAM", category="Test", name="x", method="POST",
        path="/public_api/v1/asset-groups/update/{group_id}", access_class="write",
    )
    assert op.path_params == ["group_id"]
    assert op.consent == "write"  # derived default from access_class


def test_consent_defaults_by_access_class():
    mk = lambda ac: XsiamOperationSchema(
        op_id="OP-X", category="c", name="n", method="POST",
        path="/public_api/v1/x", access_class=ac,
    ).consent
    assert mk("read") == "none"
    assert mk("write") == "write"
    assert mk("destructive") == "destructive"


def test_bad_row_is_rejected_not_raised(tmp_path):
    good = tmp_path / "good.yml"
    good.write_text(
        "category: Good\noperations:\n"
        "  - op_id: OP-GOOD-ONE\n    name: ok\n    method: POST\n"
        "    path: /public_api/v1/ok\n    access_class: read\n"
    )
    bad = tmp_path / "bad.yml"
    bad.write_text(
        "category: Bad\noperations:\n"
        "  - op_id: not-a-valid-id\n    name: nope\n    method: DELETE\n"
        "    path: /wrong/prefix\n    access_class: sideways\n"
    )
    cat = OperationCatalog()
    n = cat.load(str(tmp_path))  # must not raise despite the bad row
    assert n == 1
    assert cat.find("OP-GOOD-ONE") is not None


def test_no_duplicate_op_ids_in_shipped_packs():
    cat = OperationCatalog()
    cat.load(PACKS)
    ids = [o.op_id for o in cat.all()]
    assert len(ids) == len(set(ids))
