"""engine.door_resolution: which ingestion door each expected detection needs.

Spec: docs/superpowers/specs/2026-11-11-ingestion-doors-design.md §5. The cards
and the registry here are tiny and in memory, so each rule is pinned on its own.
"""
from __future__ import annotations

import json

import pytest

from engine.dataset_requirements import DatasetRequirement, DatasetRequirementsError
from engine.door_resolution import (
    DoorRequirement,
    DoorResolutionError,
    detection_requirement,
    scenario_readiness,
)
from engine.ttp_catalog import TtpCatalog

ENDPOINT_STEP = {"id": "step-01", "command": "cat /etc/shadow"}
COLLECTOR_STEP = {"id": "step-02", "channel": "eal", "eal": {"plugin": "idp_signin_emulator"}}


def _row(name: str, doors=(), unknown: str | None = None) -> DatasetRequirement:
    return DatasetRequirement(name, "cortex-xsiam", (), (), (), tuple(doors), unknown)


REGISTRY = {
    r.dataset: r for r in (
        _row("xdr_data", ["xdr_agent"]),
        _row("panw_ngfw_traffic_raw", ["ngfw_traffic"]),
        _row("msft_windows_security", ["windows_events", "xdr_agent_xth"]),
        _row("okta_sso", ["saas_okta"]),
        _row("cloud_audit_logs", ["cloud_aws", "cloud_gcp", "cloud_azure"]),
        _row("kubernetes_audit_logs", ["syslog_kubernetes_kubernetes", "cloud_aws"]),
        _row("pan_dns", unknown="pan_dns is not a documented tenant dataset"),
    )
}


def _card(ttp: str, detections: dict, tags=None) -> dict:
    card = {"id": ttp, "status": "active", "detections": detections}
    if tags is not None:
        card["metadata"] = {"tags": tags}
    return card


@pytest.fixture
def catalog(tmp_path):
    cards = [
        _card("TTP-EDR", {
            "biocs": [
                {"name": "LSASS read", "detection_id": "BIOC-XDR", "logic": "dataset = xdr_data | filter a = 1"},
                {"name": "Kerberoast", "detection_id": "BIOC-WIN",
                 "logic": "dataset = msft_windows_security | filter event_id = 4769"},
                {"name": "DNS hunt", "detection_id": "BIOC-DNS", "logic": "dataset = pan_dns | filter q = 1"},
                {"name": "DNS joined", "detection_id": "BIOC-DNS-JOIN",
                 "logic": "dataset = xdr_data | join (dataset = pan_dns) as d d.x = x"},
                {"name": "No anchor", "detection_id": "BIOC-NONE", "logic": "filter a = 1"},
            ],
            "correlation_rules": [
                {"name": "Endpoint to firewall", "rule_id": "CR-JOIN",
                 "logic": "dataset = xdr_data | join (dataset = panw_ngfw_traffic_raw) as n n.ip = ip"},
            ],
            "xql_queries": [
                {"name": "Traffic", "detection_id": "XQL-NGFW", "dataset": "panw_ngfw_traffic_raw", "query": "x"},
                {"name": "Nothing declared", "detection_id": "XQL-NONE", "query": "x"},
            ],
            "iocs": [{"ioc_type": "ip", "value": "10.0.0.1", "detection_id": "IOC-1"}],
        }),
        _card("TTP-IDP", {
            "biocs": [{"name": "Okta brute", "detection_id": "BIOC-OKTA", "logic": "dataset = okta_sso | filter x"}],
            "xql_queries": [{"name": "Okta hunt", "detection_id": "XQL-OKTA", "dataset": "okta_sso", "query": "x"}],
            "abiocs": [{"name": "Okta anomaly", "detection_id": "ABIOC-OKTA", "logic": "dataset = okta_sso | x"}],
        }),
        _card("TTP-CLOUD-TAGGED", {
            "xql_queries": [
                {"name": "No filter", "detection_id": "XQL-CL", "dataset": "cloud_audit_logs", "query": "x"},
                {"name": "GCP filter", "detection_id": "XQL-CL-GCP", "dataset": "cloud_audit_logs",
                 "query": 'dataset = cloud_audit_logs | filter cloud_provider = "gcp"'},
                {"name": "Two providers", "detection_id": "XQL-CL-IN", "dataset": "cloud_audit_logs",
                 "query": 'dataset = cloud_audit_logs | filter cloud_provider in ("AWS", "AZURE")'},
            ],
        }, tags=["cloud", "aws"]),
        _card("TTP-CLOUD-BARE", {
            "xql_queries": [
                {"name": "No filter", "detection_id": "XQL-CL", "dataset": "cloud_audit_logs", "query": "x"},
                {"name": "K8s audit", "detection_id": "XQL-K8S", "dataset": "kubernetes_audit_logs", "query": "x"},
                {"name": "Not a provider", "detection_id": "XQL-CL-K8S", "dataset": "cloud_audit_logs",
                 "query": 'dataset = cloud_audit_logs | filter cloud_provider = "kubernetes"'},
            ],
        }, tags=["cloud"]),
        _card("TTP-BAD-TAGS", {
            "xql_queries": [{"name": "No filter", "detection_id": "XQL-CL", "dataset": "cloud_audit_logs",
                             "query": "x"}],
        }, tags="aws"),
        _card("TTP-BAD-LOGIC", {
            "biocs": [{"name": "Malformed", "detection_id": "BIOC-BAD", "logic": None}],
        }),
        _card("TTP-UNREG", {
            "xql_queries": [{"name": "Unknown", "detection_id": "XQL-UNREG", "dataset": "made_up_raw", "query": "x"}],
        }),
    ]
    for c in cards:
        (tmp_path / f"{c['id']}.json").write_text(json.dumps(c), encoding="utf-8")
    cat = TtpCatalog()
    assert cat.load(str(tmp_path)) > 0
    return cat


def _det(type_: str, ttp_ref: str | None, detection_id: str | None) -> dict:
    d = {"type": type_, "plane": "EDR", "description": "d"}
    if ttp_ref is not None:
        d["ttp_ref"] = ttp_ref
    if detection_id is not None:
        d["detection_id"] = detection_id
    return d


def _req(catalog, det, step=ENDPOINT_STEP, **kw) -> DoorRequirement:
    return detection_requirement(step, det, catalog=catalog, registry=REGISTRY, **kw)


# ---------------------------------------------------------------------------
# Rule 2: the card's datasets decide, AND across datasets, OR within one
# ---------------------------------------------------------------------------


def test_xdr_data_bioc_needs_xdr_agent(catalog):
    req = _req(catalog, _det("BIOC", "TTP-EDR", "BIOC-XDR"))
    assert req == DoorRequirement(all_of=(("xdr_agent",),))
    assert req.route is None and req.underived is None and req.needs_analytics is False


def test_two_dataset_join_is_and(catalog):
    req = _req(catalog, _det("Correlation", "TTP-EDR", "CR-JOIN"))
    assert req.all_of == (("xdr_agent",), ("ngfw_traffic",))
    assert req.underived is None


def test_windows_or_set_preserved(catalog):
    req = _req(catalog, _det("BIOC", "TTP-EDR", "BIOC-WIN"))
    assert req.all_of == (("windows_events", "xdr_agent_xth"),)


def test_xql_dataset_field_is_read(catalog):
    assert _req(catalog, _det("XQL", "TTP-EDR", "XQL-NGFW")).all_of == (("ngfw_traffic",),)


def test_analytics_types_need_analytics(catalog):
    assert _req(catalog, _det("ABIOC", "TTP-IDP", "ABIOC-OKTA")).needs_analytics is True
    assert _req(catalog, _det("Analytics", "TTP-IDP", "ABIOC-OKTA")).needs_analytics is True
    assert _req(catalog, _det("BIOC", "TTP-IDP", "BIOC-OKTA")).needs_analytics is False


# ---------------------------------------------------------------------------
# Rule 1: a collector step is judged by its route, not the card's dataset
# ---------------------------------------------------------------------------


def test_collector_bioc_route_blocked(catalog):
    req = _req(catalog, _det("BIOC", "TTP-IDP", "BIOC-OKTA"), step=COLLECTOR_STEP,
               collector_target="okta-col", collector_dataset="okta_sso")
    assert req.route == "collector"
    assert req.all_of == (("http_collector",),)
    assert req.collector_target == "okta-col"
    assert req.route_blocked == "DOOR_ROUTE_CANNOT_FEED"


def test_collector_ioc_route_blocked(catalog):
    req = _req(catalog, _det("IOC", "TTP-EDR", "IOC-1"), step=COLLECTOR_STEP,
               collector_target="c", collector_dataset="xdr_data")
    assert req.route_blocked == "DOOR_ROUTE_CANNOT_FEED"
    assert req.underived is None


def test_collector_xql_dataset_mismatch_blocked(catalog):
    det = _det("XQL", "TTP-IDP", "XQL-OKTA")
    mismatch = _req(catalog, det, step=COLLECTOR_STEP, collector_target="c", collector_dataset="okta_okta_raw")
    assert mismatch.route_blocked == "DOOR_ROUTE_CANNOT_FEED"
    unresolved = _req(catalog, det, step=COLLECTOR_STEP, collector_target="c", collector_dataset=None)
    assert unresolved.route_blocked == "DOOR_ROUTE_CANNOT_FEED"


def test_collector_xql_matching_dataset_scorable(catalog):
    req = _req(catalog, _det("XQL", "TTP-IDP", "XQL-OKTA"), step=COLLECTOR_STEP,
               collector_target="okta-col", collector_dataset="OKTA_SSO")
    assert req == DoorRequirement(all_of=(("http_collector",),), route="collector",
                                  collector_target="okta-col")


def test_collector_analytics_needs_xdm(catalog):
    det = _det("ABIOC", "TTP-IDP", "ABIOC-OKTA")
    kw = dict(step=COLLECTOR_STEP, collector_target="c", collector_dataset="okta_sso")
    unattested = _req(catalog, det, **kw)
    assert unattested.route_blocked == "DOOR_ROUTE_CANNOT_FEED"
    assert unattested.needs_analytics is True
    attested = _req(catalog, det, xdm_attested=True, **kw)
    assert attested.route_blocked is None and attested.needs_analytics is True


def test_collector_route_wins_over_a_missing_card(catalog):
    """A collector step's detection is judged by the route even with no card behind it."""
    req = _req(catalog, _det("XQL", "TTP-NOPE", "XQL-1"), step=COLLECTOR_STEP,
               collector_target="c", collector_dataset="okta_sso")
    assert req.route == "collector" and req.underived is None
    assert req.route_blocked == "DOOR_ROUTE_CANNOT_FEED"


# ---------------------------------------------------------------------------
# Rule 3: cloud_audit_logs is split by provider
# ---------------------------------------------------------------------------


def test_cloud_provider_from_filter(catalog):
    # The filter wins over the card's ``aws`` tag, and its value is read case-blind.
    req = _req(catalog, _det("XQL", "TTP-CLOUD-TAGGED", "XQL-CL-GCP"))
    assert req.all_of == (("cloud_gcp",),)


def test_cloud_provider_in_filter_keeps_every_named_provider(catalog):
    req = _req(catalog, _det("XQL", "TTP-CLOUD-TAGGED", "XQL-CL-IN"))
    assert req.all_of == (("cloud_aws", "cloud_azure"),)


def test_cloud_provider_from_tag(catalog):
    req = _req(catalog, _det("XQL", "TTP-CLOUD-TAGGED", "XQL-CL"))
    assert req.all_of == (("cloud_aws",),)


def test_cloud_without_provider_underived(catalog):
    for det_id in ("XQL-CL", "XQL-CL-K8S"):  # no filter, or a filter naming no provider door
        req = _req(catalog, _det("XQL", "TTP-CLOUD-BARE", det_id))
        assert req.underived == "NO_PROVIDER", det_id
        assert req.all_of == ()


def test_single_provider_door_is_not_narrowed(catalog):
    """kubernetes_audit_logs names one provider door; there is no 'whichever provider' to pick."""
    req = _req(catalog, _det("XQL", "TTP-CLOUD-BARE", "XQL-K8S"))
    assert req.all_of == (("syslog_kubernetes_kubernetes", "cloud_aws"),)


def test_malformed_tags_raise(catalog):
    with pytest.raises(DoorResolutionError, match="tags"):
        _req(catalog, _det("XQL", "TTP-BAD-TAGS", "XQL-CL"))


# ---------------------------------------------------------------------------
# Rule 5: underived, never raised for the whole scenario
# ---------------------------------------------------------------------------


def test_doors_unknown_row_underived(catalog):
    assert _req(catalog, _det("BIOC", "TTP-EDR", "BIOC-DNS")).underived == "DOORS_UNKNOWN"
    # One unknown leg of a join leaves the whole detection underived.
    joined = _req(catalog, _det("BIOC", "TTP-EDR", "BIOC-DNS-JOIN"))
    assert joined.underived == "DOORS_UNKNOWN" and joined.all_of == ()


def test_missing_card_underived_not_raised(catalog):
    assert _req(catalog, _det("BIOC", "TTP-NOPE", "BIOC-1")).underived == "CARD_NOT_FOUND"
    assert _req(catalog, _det("BIOC", None, "BIOC-1")).underived == "NO_DATASET"
    assert _req(catalog, _det("BIOC", "TTP-EDR", None)).underived == "NO_DATASET"
    assert _req(catalog, _det("BIOC", "TTP-EDR", "BIOC-UNKNOWN")).underived == "NO_DATASET"
    assert _req(catalog, _det("IOC", "TTP-EDR", "IOC-1")).underived == "NO_DATASET"
    assert _req(catalog, _det("BIOC", "TTP-EDR", "BIOC-NONE")).underived == "NO_DATASET"
    assert _req(catalog, _det("XQL", "TTP-EDR", "XQL-NONE")).underived == "NO_DATASET"


def test_malformed_card_still_raises(catalog):
    with pytest.raises(DatasetRequirementsError, match="logic must be a string"):
        _req(catalog, _det("BIOC", "TTP-BAD-LOGIC", "BIOC-BAD"))


def test_unregistered_dataset_raises(catalog):
    with pytest.raises(DoorResolutionError, match="made_up_raw"):
        _req(catalog, _det("XQL", "TTP-UNREG", "XQL-UNREG"))


def test_absent_registry_raises(catalog):
    with pytest.raises(DoorResolutionError, match="registry"):
        detection_requirement(ENDPOINT_STEP, _det("BIOC", "TTP-EDR", "BIOC-XDR"),
                              catalog=catalog, registry=None)


@pytest.mark.parametrize("det", [
    {"ttp_ref": "TTP-EDR", "detection_id": "BIOC-XDR"},
    {"type": "Sigma", "ttp_ref": "TTP-EDR", "detection_id": "BIOC-XDR"},
    {"type": "BIOC", "ttp_ref": ["TTP-EDR"], "detection_id": "BIOC-XDR"},
    "BIOC-XDR",
])
def test_unrecognised_detection_shape_raises(catalog, det):
    with pytest.raises(DoorResolutionError):
        _req(catalog, det)


# ---------------------------------------------------------------------------
# DoorRequirement round trip
# ---------------------------------------------------------------------------


def test_requirement_json_round_trip():
    for req in (
        DoorRequirement(all_of=(("windows_events", "xdr_agent_xth"), ("ngfw_traffic",)), needs_analytics=True),
        DoorRequirement(all_of=(("http_collector",),), route="collector", collector_target="t",
                        route_blocked="DOOR_ROUTE_CANNOT_FEED"),
        DoorRequirement(underived="NO_PROVIDER"),
    ):
        data = req.to_json()
        assert json.loads(json.dumps(data)) == data
        assert DoorRequirement.from_json(data) == req


@pytest.mark.parametrize("bad", [
    {"all_of": [], "route": None, "collector_target": None, "route_blocked": None,
     "needs_analytics": False, "underived": None, "extra": 1},
    {"all_of": [], "route": None},
    {"all_of": [["no_such_door"]], "route": None, "collector_target": None, "route_blocked": None,
     "needs_analytics": False, "underived": None},
    {"all_of": [], "route": None, "collector_target": None, "route_blocked": None,
     "needs_analytics": False, "underived": "GUESSED"},
    {"all_of": [["xdr_agent"]], "route": None, "collector_target": None, "route_blocked": None,
     "needs_analytics": "no", "underived": None},
])
def test_from_json_rejects_unrecognised_shapes(bad):
    with pytest.raises(DoorResolutionError):
        DoorRequirement.from_json(bad)


# ---------------------------------------------------------------------------
# Scenario readiness
# ---------------------------------------------------------------------------


def _scenario(*dets_by_step) -> dict:
    steps = []
    for i, (step, dets) in enumerate(dets_by_step):
        steps.append({**step, "id": f"step-{i:02d}", "expected_detections": list(dets)})
    return {"scenario_id": "SIM-TEST-001", "steps": steps}


def _ready(catalog, scenario, open_keys, agent_id="a1"):
    return scenario_readiness(scenario, set(open_keys), agent_id=agent_id, catalog=catalog, registry=REGISTRY)


def test_readiness_ready_partial_needs(catalog):
    sc = _scenario(
        (ENDPOINT_STEP, [_det("BIOC", "TTP-EDR", "BIOC-XDR"), _det("BIOC", "TTP-EDR", "BIOC-DNS")]),
        (ENDPOINT_STEP, [_det("XQL", "TTP-EDR", "XQL-NGFW")]),
    )
    ready = _ready(catalog, sc, {"xdr_agent:a1", "ngfw_traffic"})
    assert ready == {
        "scenario_id": "SIM-TEST-001", "state": "ready", "scorable": 2, "total": 3, "underived": 1,
        "route_blocked": 0, "unevaluated": 0, "closed": [], "agent_scoped_evaluated": True,
    }

    partial = _ready(catalog, sc, {"xdr_agent:a1"})
    assert partial["state"] == "partial" and partial["scorable"] == 1
    assert partial["closed"] == [{"door_id": "ngfw_traffic", "detections": 1}]

    # A door open for another agent does not open it for this one.
    needs = _ready(catalog, sc, {"xdr_agent:a2"})
    assert needs["state"] == "needs_doors" and needs["scorable"] == 0 and needs["underived"] == 1
    assert needs["closed"] == [{"door_id": "ngfw_traffic", "detections": 1},
                               {"door_id": "xdr_agent", "detections": 1}]


def test_readiness_counts_a_detection_once_per_closed_door(catalog):
    sc = _scenario((ENDPOINT_STEP, [_det("Correlation", "TTP-EDR", "CR-JOIN"),
                                    _det("BIOC", "TTP-EDR", "BIOC-XDR")]))
    out = _ready(catalog, sc, set())
    assert out["closed"] == [{"door_id": "xdr_agent", "detections": 2},
                             {"door_id": "ngfw_traffic", "detections": 1}]


def test_agent_doors_not_evaluated_without_agent(catalog):
    sc = _scenario((ENDPOINT_STEP, [_det("BIOC", "TTP-EDR", "BIOC-XDR"), _det("XQL", "TTP-EDR", "XQL-NGFW")]))
    out = _ready(catalog, sc, {"ngfw_traffic", "xdr_agent:a1"}, agent_id=None)
    # The tenant door is still judged; the agent door is not, so nothing reads ready.
    assert out["agent_scoped_evaluated"] is False
    assert out["scorable"] == 1 and out["unevaluated"] == 1
    assert out["state"] == "partial" and out["closed"] == []

    closed_tenant = _ready(catalog, sc, set(), agent_id=None)
    assert closed_tenant["state"] == "needs_doors"
    assert closed_tenant["closed"] == [{"door_id": "ngfw_traffic", "detections": 1}]

    agent_only = _scenario((ENDPOINT_STEP, [_det("BIOC", "TTP-EDR", "BIOC-XDR")]))
    assert _ready(catalog, agent_only, set(), agent_id=None)["state"] == "not_evaluated"


def test_mixed_scope_or_set_without_agent(catalog):
    sc = _scenario((ENDPOINT_STEP, [_det("BIOC", "TTP-EDR", "BIOC-WIN")]))
    assert _ready(catalog, sc, {"windows_events"}, agent_id=None)["state"] == "ready"
    # windows_events closed, xdr_agent_xth not evaluated: the OR-set could still be met.
    assert _ready(catalog, sc, set(), agent_id=None)["state"] == "not_evaluated"
    assert _ready(catalog, sc, {"xdr_agent_xth:a1"}, agent_id="a1")["state"] == "ready"


def test_readiness_counts_collector_route_blocked(catalog):
    sc = _scenario((COLLECTOR_STEP, [_det("XQL", "TTP-IDP", "XQL-OKTA"), _det("BIOC", "TTP-IDP", "BIOC-OKTA")]))
    out = _ready(catalog, sc, {"http_collector:okta-col", "saas_okta"})
    assert out["route_blocked"] == 2 and out["scorable"] == 0 and out["closed"] == []
    assert out["state"] == "needs_doors"


def test_readiness_with_nothing_derivable_is_not_evaluated(catalog):
    """A zero is degraded: no derived detection never reads ready."""
    sc = _scenario((ENDPOINT_STEP, [_det("IOC", "TTP-EDR", "IOC-1")]))
    out = _ready(catalog, sc, set())
    assert out["state"] == "not_evaluated" and out["underived"] == 1 and out["total"] == 1
    assert _ready(catalog, _scenario(), set())["state"] == "not_evaluated"


@pytest.mark.parametrize("scenario", [
    {"scenario_id": "S", "steps": {"id": "x"}},
    {"scenario_id": "S", "steps": ["step"]},
    {"scenario_id": "S", "steps": [{"id": "x", "expected_detections": {"type": "BIOC"}}]},
])
def test_readiness_rejects_unrecognised_shapes(catalog, scenario):
    with pytest.raises(DoorResolutionError):
        _ready(catalog, scenario, set())
