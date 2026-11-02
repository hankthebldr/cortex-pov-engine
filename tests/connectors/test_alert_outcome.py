"""ObservedAlert carries the tenant's action and the derived outcome."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from connectors.base import ConnectorConfig, ObservedAlert
from connectors.xsiam import XsiamConnector

T0 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_observed_alert_classifies_outcome_from_action_pretty():
    prevented = ObservedAlert(source="xsiam", observed_at=T0,
                              action_pretty="Prevented (Blocked)", action="BLOCKED")
    detected = ObservedAlert(source="xsiam", observed_at=T0,
                             action_pretty="Detected (Reported)")
    bare = ObservedAlert(source="xsiam", observed_at=T0)
    assert prevented.outcome == "prevented"
    assert detected.outcome == "detected"
    assert bare.outcome == "unknown" and bare.action is None and bare.action_pretty is None
    d = prevented.to_dict()
    assert d["action"] == "BLOCKED"
    assert d["action_pretty"] == "Prevented (Blocked)"
    assert d["outcome"] == "prevented"


def test_from_dict_reads_action_fields_and_tolerates_absence():
    a = ObservedAlert.from_dict({"observed_at": T0.isoformat(), "action": "BLOCKED"})
    assert a.action == "BLOCKED" and a.action_pretty is None and a.outcome == "prevented"
    b = ObservedAlert.from_dict({"observed_at": T0.isoformat(),
                                 "action_pretty": "Detected (Reported)"})
    assert b.outcome == "detected"


def test_xsiam_normalize_reads_action_fields():
    conn = XsiamConnector(fetcher=lambda *a, **k: (200, json.dumps({})))
    alert = conn._normalize_alert({
        "alert_id": "9", "name": "x", "source": "XDR Agent",
        "detection_timestamp": int((T0 + timedelta(seconds=1)).timestamp() * 1000),
        "action": "BLOCKED", "action_pretty": "Prevented (Blocked)",
    })
    assert alert.action == "BLOCKED"
    assert alert.action_pretty == "Prevented (Blocked)"
    assert alert.outcome == "prevented"
    plain = conn._normalize_alert({
        "alert_id": "10", "detection_timestamp": int(T0.timestamp() * 1000)})
    assert plain.outcome == "unknown"
