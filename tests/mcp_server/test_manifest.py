"""The P-2 manifest export (spec test 12)."""
from __future__ import annotations

import copy

import jsonschema
import pytest

from mcp_server.manifest import build_manifest, validate_manifest
from mcp_server.naming import CANONICAL_IDS, wire_name
from mcp_server.tools import cortexsim, xsiam

NOT_BACKED = {"xsiam.modeling_rule.list", "xsiam.parser.list", "xsiam.content.list",
              "xsiam.ingest.status"}


def _doc() -> dict:
    return build_manifest(cortexsim.specs() + xsiam.specs())


def test_manifest_validates_against_local_p2_schema_and_lists_41_tools():
    doc = _doc()
    validate_manifest(doc)
    assert (doc["manifest_version"], doc["kind"], doc["id"], doc["target"]) == (
        2, "mcp-server", "core/xsiam", "xsiam")
    assert doc["server"] == {"name": "cortexsim", "sdk": "mcp==1.30.0"}
    assert [t["id"] for t in doc["tools"]] == list(CANONICAL_IDS)
    assert len(doc["tools"]) == 41
    for t in doc["tools"]:
        assert t["wire"] == wire_name(t["id"])
    by_id = {t["id"]: t for t in doc["tools"]}
    assert by_id["xsiam.xql.query"]["scopes"] == ["xql.execute"]
    assert by_id["xsiam.xql.query"]["effect"] == "tenant"
    assert by_id["cortexsim.run.abort"]["class"] == "destructive"


def test_not_backed_tools_are_marked_backed_false():
    by_id = {t["id"]: t for t in _doc()["tools"]}
    assert {i for i, t in by_id.items() if t["backed"] is False} == NOT_BACKED


@pytest.mark.parametrize("mutate", [
    lambda d: d["tools"][0].update(scopes=["xql.write"]),
    lambda d: d["tools"][0].update(**{"class": "admin"}),
    lambda d: d.update(manifest_version=1),
    lambda d: d["tools"][0].pop("backed"),
    lambda d: d["tools"].append(copy.deepcopy(d["tools"][0])),
    lambda d: d["tools"][0].update(wire="other_name"),
])
def test_the_schema_can_fail(mutate):
    doc = _doc()
    mutate(doc)
    with pytest.raises((jsonschema.ValidationError, ValueError)):
        validate_manifest(doc)
