"""Tests for detection_scanner/scripts/export_artifacts.py.

The exporter must:

* Produce one artifact set per active TTP entry.
* Emit valid YAML / JSON the customer can paste straight into their SOC.
* Stay deterministic — re-running over the same corpus produces byte-identical
  output (so CI can guard against drift with ``git diff --exit-code``).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "detection_scanner" / "scripts" / "export_artifacts.py"
TTPS_DIR = REPO_ROOT / "detection_scanner" / "ttps"


def _load_exporter():
    spec = importlib.util.spec_from_file_location("export_artifacts", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["export_artifacts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def exporter():
    return _load_exporter()


def _active_ttps() -> list[dict]:
    out: list[dict] = []
    for p in sorted(TTPS_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("status") == "active":
            out.append(data)
    return out


def test_active_corpus_non_empty():
    assert _active_ttps(), "no active TTP entries to export — exporter test is meaningless"


def test_sigma_renders_for_every_ttp_with_biocs(exporter):
    for ttp in _active_ttps():
        if not (ttp.get("detections") or {}).get("biocs"):
            continue
        body = exporter.render_sigma(ttp)
        assert body, f"{ttp['id']}: sigma render returned empty"
        docs = list(yaml.safe_load_all(body))
        assert docs, f"{ttp['id']}: sigma multi-doc parse returned no docs"
        for doc in docs:
            assert isinstance(doc, dict)
            for key in ("title", "id", "description", "tags", "logsource", "detection", "level"):
                assert key in doc, f"{ttp['id']}: sigma doc missing '{key}'"
            assert doc["level"] in {"informational", "low", "medium", "high", "critical"}


def test_xql_paste_buffer_contains_every_bioc_logic(exporter):
    for ttp in _active_ttps():
        biocs = (ttp.get("detections") or {}).get("biocs") or []
        if not biocs:
            continue
        body = exporter.render_xql(ttp)
        assert body
        for b in biocs:
            logic = (b.get("logic") or "").strip()
            if not logic:
                continue
            # First non-comment line of each BIOC logic block must appear
            # verbatim in the paste buffer (no XQL transformation allowed).
            first_real_line = next((l for l in logic.splitlines() if l.strip()), "")
            assert first_real_line in body, (
                f"{ttp['id']}: BIOC '{b.get('name')}' logic not preserved verbatim"
            )


def test_correlation_json_is_parsable_and_has_rules(exporter):
    for ttp in _active_ttps():
        rules = (ttp.get("detections") or {}).get("correlation_rules") or []
        if not rules:
            continue
        body = exporter.render_correlation(ttp)
        parsed = json.loads(body)
        assert parsed["ttp_ref"] == ttp["id"]
        assert len(parsed["rules"]) == len(rules)
        for r in parsed["rules"]:
            assert "rule_id" in r and "logic" in r


def test_xsoar_playbook_yaml_parses(exporter):
    for ttp in _active_ttps():
        panw = ttp.get("panw_mapping") or {}
        products = panw.get("products") or []
        has_xsoar = any(isinstance(p, dict) and p.get("module") == "cortex-xsoar"
                        for p in products)
        body = exporter.render_xsoar_playbook(ttp)
        if not has_xsoar:
            assert body is None
            continue
        doc = yaml.safe_load(body)
        assert isinstance(doc, dict)
        assert doc["output"]["ttp_ref"] == ttp["id"]
        # Either correlation triggers or playbook rule id triggers must exist.
        triggers = doc.get("triggers") or []
        assert triggers, f"{ttp['id']}: xsoar playbook has no triggers"


def test_exporter_output_is_deterministic(exporter):
    """Calling each render fn twice on the same dict yields identical bytes.

    Guards the ``git diff --exit-code`` CI pattern.
    """
    for ttp in _active_ttps():
        for fn in (
            exporter.render_sigma,
            exporter.render_xql,
            exporter.render_correlation,
            exporter.render_xsoar_playbook,
        ):
            a = fn(ttp)
            b = fn(ttp)
            assert a == b, f"{ttp['id']}: {fn.__name__} non-deterministic"
