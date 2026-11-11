"""The P-2 manifest export (spec section 11, test 12).

``cortex-pov-agent`` names an ``xsiam.*`` MCP server as prerequisite P-2 and
vendors a ``manifest.yaml`` of shape ``manifest_version: 2, kind: mcp-server``.
That repo is not a dependency, so its fields are copied into
:data:`MANIFEST_SCHEMA` here. The document is generated from the same
``ToolSpec`` list the server registers — never hand-maintained — so the
manifest cannot list a tool the server does not have, or miss one it does.
"""
from __future__ import annotations

import importlib.metadata
from typing import Any, Iterable

import jsonschema

from .naming import WIRE_PATTERN, wire_name
from .registry import ToolSpec

#: The consumer's scope vocabulary. A tool may hold none of them (SimCore-local).
SCOPES = ("dataset.read", "xql.execute", "content.read", "alert.read")
CLASSES = ("read", "write", "destructive")
EFFECTS = ("none", "simcore", "tenant", "host")

MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "P-2 mcp-server manifest (local copy of the cortex-pov-agent shape)",
    "type": "object",
    "additionalProperties": False,
    "required": ["manifest_version", "kind", "id", "target", "server", "tools"],
    "properties": {
        "manifest_version": {"const": 2},
        "kind": {"const": "mcp-server"},
        "id": {"const": "core/xsiam"},
        "target": {"const": "xsiam"},
        "server": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "sdk"],
            "properties": {
                "name": {"const": "cortexsim"},
                "sdk": {"type": "string", "pattern": r"^mcp==\d+\.\d+\.\d+$"},
            },
        },
        "tools": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "wire", "class", "effect", "scopes", "backed"],
                "properties": {
                    "id": {"type": "string", "pattern": r"^(cortexsim|xsiam)(\.[a-z0-9_]+)+$"},
                    "wire": {"type": "string", "pattern": WIRE_PATTERN.pattern},
                    "class": {"enum": list(CLASSES)},
                    "effect": {"enum": list(EFFECTS)},
                    "scopes": {"type": "array", "uniqueItems": True,
                               "items": {"enum": list(SCOPES)}},
                    "backed": {"type": "boolean"},
                },
            },
        },
    },
}


def _sdk() -> str:
    return f"mcp=={importlib.metadata.version('mcp')}"


def build_manifest(specs: Iterable[ToolSpec]) -> dict:
    return {
        "manifest_version": 2,
        "kind": "mcp-server",
        "id": "core/xsiam",
        "target": "xsiam",
        "server": {"name": "cortexsim", "sdk": _sdk()},
        "tools": [
            {"id": s.id, "wire": wire_name(s.id), "class": s.klass, "effect": s.network,
             "scopes": list(s.scopes), "backed": s.backed}
            for s in specs
        ],
    }


def validate_manifest(doc: dict) -> None:
    """``jsonschema.ValidationError`` on a shape violation; ``ValueError`` when
    the tool list repeats an id or a wire name is not the one derived from it."""
    jsonschema.Draft202012Validator(MANIFEST_SCHEMA).validate(doc)
    ids = [t["id"] for t in doc["tools"]]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"manifest lists a tool more than once: {', '.join(dupes)}")
    wrong = [t["id"] for t in doc["tools"] if t["wire"] != wire_name(t["id"])]
    if wrong:
        raise ValueError(f"wire name is not the derived one for: {', '.join(wrong)}")
