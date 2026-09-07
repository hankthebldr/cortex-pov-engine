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
