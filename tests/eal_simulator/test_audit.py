"""Audit logger / ECS event format tests."""
from __future__ import annotations

import json
from pathlib import Path

from eal_simulator import AuditLogger, ecs_event


def test_ecs_event_has_required_fields():
    ev = ecs_event(
        action="campaign_started",
        outcome="success",
        message="hello",
        campaign_id="CMP-X-001",
        run_id="run-1",
        plugin="c2_http_beacon",
        target="example.test",
    )
    assert ev["@timestamp"]
    assert ev["ecs"]["version"]
    assert ev["event"]["action"] == "campaign_started"
    assert ev["event"]["outcome"] == "success"
    assert ev["service"]["name"] == "cortexsim-eal-simulator"
    assert ev["cortexsim"]["campaign_id"] == "CMP-X-001"
    assert ev["cortexsim"]["plugin"] == "c2_http_beacon"


def test_ecs_event_drops_none_inside_cortexsim_namespace():
    ev = ecs_event(action="x", campaign_id=None, run_id="r")
    assert "campaign_id" not in ev["cortexsim"]
    assert ev["cortexsim"]["run_id"] == "r"


def test_audit_logger_writes_one_line_per_event(tmp_path: Path):
    log_file = tmp_path / "audit.json"
    audit = AuditLogger(file_path=str(log_file))
    audit.emit(ecs_event(action="a", campaign_id="x"))
    audit.emit(ecs_event(action="b", campaign_id="x"))
    audit.close()

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event"]["action"] == "a"
    assert parsed[1]["event"]["action"] == "b"


def test_audit_logger_safe_close_idempotent():
    audit = AuditLogger(file_path=None)
    audit.close()
    audit.close()  # should not raise
