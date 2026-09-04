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
