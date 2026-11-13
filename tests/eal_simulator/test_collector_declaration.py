"""Route verdict for an HTTP log collector: will the dataset the consultant names
be the one the detections read?"""
from __future__ import annotations

from collections import Counter

from eal_simulator.collector_declaration import prefill, route_verdict

V = lambda key, v, p, card, tenant=(), kinds=(): route_verdict(  # noqa: E731
    key, v, p, card_dataset=card, tenant_datasets=tenant, reading_kinds=kinds).verdict


def test_raw_card_dataset_matches_when_the_names_agree():
    assert V("duo", "duo", "auth", "duo_auth_raw") == "matches"
    assert V("duo", "Duo", "Auth", "duo_auth_raw") == "matches"


def test_raw_card_dataset_read_by_biocs_is_xql_only():
    assert V("x", "a", "b", "a_b_raw", kinds={"biocs", "xql_queries"}) == "xql_only"


def test_raw_card_dataset_with_other_names_differs():
    assert V("duo", "duo", "logs", "duo_auth_raw") == "name_differs"


def test_documented_tenant_name_makes_a_non_raw_card_disputed():
    assert V("okta", "okta", "sso", "okta_sso", tenant={"okta_sso_raw"}) == "name_disputed"


def test_non_raw_card_without_a_different_tenant_name_cannot_feed():
    assert V("aws_audit_log", "aws", "audit_log", "cloud_audit_logs",
             tenant={"amazon_aws_raw", "cloud_audit_logs"}) == "cannot_feed"
    assert V("google_workspace_audit_logs", "google", "workspace",
             "google_workspace_audit") == "cannot_feed"


def test_native_dataset_by_card_or_by_typed_name():
    assert V("pan_firewall_traffic_logs", "panw", "ngfw_traffic",
             "panw_ngfw_traffic_raw") == "native_dataset"
    assert V("okta", "PANW", "ngfw_threat", "okta_sso",
             tenant={"okta_sso_raw"}) == "native_dataset"


def test_native_check_compares_a_canonical_form_too():
    # lowercase, runs of non-alphanumerics collapsed to "_", edge "_" trimmed
    assert V("okta", "panw", "ngfw-traffic", "okta_sso", tenant={"okta_sso_raw"}) == "native_dataset"
    assert V("okta", "PANW.", "ngfw traffic", "okta_sso", tenant={"okta_sso_raw"}) == "native_dataset"
    assert V("okta", "panw", "ngfw__traffic", "okta_sso", tenant={"okta_sso_raw"}) == "native_dataset"
    assert V("okta", "okta", "sso", "okta_sso", tenant={"okta_sso_raw"}) == "name_disputed"


def test_test_channel():
    assert V("test_channel", "cortexsim", "door_http", None) == "test_channel"


def test_prefill_splits_card_dataset_then_source_key():
    assert prefill("duo", "duo_auth_raw") == ("duo", "auth")
    assert prefill("aws_audit_log", "cloud_audit_logs") == ("aws", "audit_log")
    assert prefill("okta", "okta_sso") == ("okta", "logs")


def test_verdict_split_over_the_real_catalogue():
    from config import settings
    from eal_simulator import get_default_registry
    from eal_simulator.analytics_catalogue import coverage_report
    from eal_simulator.ingestion_routes import tenant_datasets
    from eal_simulator.targets import collector_plugin_names
    from engine.dataset_requirements import card_dataset_index
    from engine.ttp_catalog import catalog, default_corpus_dir

    catalog.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR))
    index = card_dataset_index(catalog.all_raw())
    reg = get_default_registry()
    collectors = collector_plugin_names(reg)
    split: Counter[str] = Counter()
    for row in coverage_report(reg)["sources"]:
        if not collectors & set(row["emitters"] + row["partial_emitters"]):
            continue
        key, ds = row["key"], row["dataset"]
        kinds = {k for ks in index.get(ds, {}).values() for k in ks}
        split[route_verdict(key, *prefill(key, ds), card_dataset=ds,
                            tenant_datasets=tenant_datasets(key),
                            reading_kinds=kinds).verdict] += 1
    # 20 sources with a collector emitter, measured 2026-11-13
    assert dict(split) == {"native_dataset": 4, "matches": 4, "name_disputed": 9, "cannot_feed": 3}
