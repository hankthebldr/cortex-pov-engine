"""Door registry: the ways data reaches a tenant, and what proves each one."""

import dataclasses

import pytest

from engine import ingestion_doors as doors
from engine.ingestion_doors import (
    CLOUD_PROVIDER_DOORS,
    DOORS,
    UnknownDoorError,
    door_key,
    get_door,
)


def test_ids_unique():
    ids = [d.id for d in DOORS]
    assert len(ids) == len(set(ids))
    assert len(ids) == 27


def test_every_door_has_https_or_repo_evidence():
    for d in DOORS:
        assert d.evidence.startswith("https://"), d.id


def test_cloud_doors_carry_a_provider_filter():
    with_filter = {d.id for d in DOORS if d.provider_filter is not None}
    assert with_filter == set(CLOUD_PROVIDER_DOORS)
    assert get_door("cloud_aws").provider_filter == "AWS"
    assert get_door("cloud_gcp").provider_filter == "GCP"
    assert get_door("cloud_azure").provider_filter == "AZURE"


def test_get_door_unknown_raises():
    with pytest.raises(UnknownDoorError):
        get_door("no_such_door")
    assert issubclass(UnknownDoorError, ValueError)


def test_door_key_tenant_vs_scoped():
    assert door_key("saas_okta", None) == "saas_okta"
    assert door_key("xdr_agent", "a1") == "xdr_agent:a1"
    with pytest.raises(ValueError):
        door_key("xdr_agent", None)
    with pytest.raises(UnknownDoorError):
        door_key("nope", None)


def test_registry_is_frozen():
    assert isinstance(DOORS, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        get_door("saas_okta").label = "x"
    assert isinstance(get_door("saas_o365").verify_datasets, tuple)


def test_table_values_verbatim():
    assert get_door("xdr_agent_xth").needs_attestation_of == "xth"
    assert get_door("ngfw_traffic").verify_datasets == ("panw_ngfw_traffic_raw",)
    assert get_door("ngfw_stitch").scope == "agent"
    assert get_door("ngfw_stitch").wizard == "firewall"
    assert get_door("http_collector").scope == "target"
    assert get_door("http_collector").verify_datasets == ()
    assert get_door("http_collector").wizard == "tenant"
    assert get_door("xpanse").verify_datasets == ()
    assert get_door("saas_google_workspace").verify_datasets == (
        "google_workspace_login_raw", "google_workspace_admin_console_raw")


def test_validate_rejects_malformed(monkeypatch):
    good = get_door("saas_okta")
    malformed = (
        dataclasses.replace(good, id="x_scope", scope="galaxy"),
        dataclasses.replace(good, id="x_filter", provider_filter="AWS"),
        dataclasses.replace(good, id="x_evidence", evidence="http://x"),
        dataclasses.replace(good, label="duplicate id"),
    )
    for bad in malformed:
        monkeypatch.setattr(doors, "DOORS", (good, bad))
        with pytest.raises(ValueError):
            doors._validate()


#: Pack folders the 2026-11-10 tenant-setup research states (marketplace appendix).
RESEARCH_PACK_FOLDERS = frozenset({
    "CiscoASA", "VMwareESXi", "MicrosoftWindowsEvents", "Kubernetes",
    "IvantiConnectSecure", "Office365", "MicrosoftEntraID", "ProofpointTAP", "Koi",
})
_PACK_PREFIX = "https://github.com/demisto/content/tree/master/Packs/"


def test_pack_evidence_folders_are_research_stated():
    folders = {d.evidence[len(_PACK_PREFIX):] for d in DOORS
               if d.evidence.startswith(_PACK_PREFIX)}
    assert folders, "expected pack evidence on some doors"
    assert folders <= RESEARCH_PACK_FOLDERS, folders - RESEARCH_PACK_FOLDERS
