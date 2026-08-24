"""API tests for /api/assertions.

The properties under test are the ones a DC's readout depends on: a dry run
renders the exact question without touching a tenant, an unconfigured tenant
yields ``pending`` rather than green, and every artifact the loader refused is
visible in the listing (a guard nobody can see is not a guard).
"""
from __future__ import annotations

import pytest
import yaml


BASE = {
    "assertion_id": "POS-CSPM-001",
    "name": "Planted CSPM misconfiguration discovery",
    "validation_class": "POS",
    "kind": "state",
    "plane": "CSPM",
    "uc_ref": "UCS-CSPM-02",
    "tc_ref": "TC-CSPM-02",
    "scope_limitations": (
        "Proves the tenant surfaced the finding classes the IaC module planted. "
        "Does NOT prove terraform apply ran — the operator attests that."
    ),
    "stimulus": {"kind": "iac_fixture", "iac_module": "aws/cspm",
                 "detail": "terraform apply infra/modules/aws/cspm"},
    "checks": [
        {
            "check_id": "chk-01",
            "title": "S3 data-exposure findings surfaced",
            "probe": "xql_rows",
            "primary": True,
            "params": {
                "query": ('dataset = cortex_cloud_posture | filter tags contains '
                          '"CortexSimCSPMFinding" and finding_class in '
                          '("public-read-bucket", "versioning-disabled", '
                          '"no-customer-managed-kms")'),
            },
            "threshold": {
                "kpi": "planted finding classes discovered", "op": ">=", "value": 3,
                "source": "authored_sharpening",
                "rationale": "the module plants exactly 3 S3 classes; the count is objective",
            },
            "negative_control": {
                "description": "the public-read bucket is never surfaced as a finding",
                "measured_value": 2,
            },
        }
    ],
}


@pytest.fixture
def loaded_catalog(tmp_path):
    """Load a throwaway corpus into the process catalog, then restore it."""
    from engine import assertions as ae

    good = dict(BASE)
    bad = {**BASE, "assertion_id": "POS-CSPM-002"}
    bad["checks"] = [{**BASE["checks"][0],
                      "threshold": {**BASE["checks"][0]["threshold"],
                                    "op": ">=", "value": 0},
                      "negative_control": {"description": "nothing", "measured_value": 0}}]

    (tmp_path / "good.yml").write_text(yaml.safe_dump(good), encoding="utf-8")
    (tmp_path / "bad.yml").write_text(yaml.safe_dump(bad), encoding="utf-8")

    saved = (ae.catalog._by_id.copy(), ae.catalog._rejected.copy(),
             ae.catalog._warnings.copy(), ae.catalog._loaded_from)
    ae.catalog.load(str(tmp_path), strict=False)
    yield ae.catalog
    (ae.catalog._by_id, ae.catalog._rejected,
     ae.catalog._warnings, ae.catalog._loaded_from) = saved


@pytest.fixture
def client(make_client, loaded_catalog):
    from api.assertions import router

    return make_client(router)


def test_list_reports_the_corpus_and_everything_it_refused(client):
    r = client.get("/api/assertions")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["assertions"][0]["assertion_id"] == "POS-CSPM-001"
    # The refusal is not swallowed — an author has to be able to see it.
    assert body["rejected"]
    assert any(d["code"] == "A-17"
               for rj in body["rejected"] for d in rj["diagnostics"])


def test_list_filters_by_validation_class(client):
    assert client.get("/api/assertions?validation_class=POS").json()["count"] == 1
    assert client.get("/api/assertions?validation_class=AUT").json()["count"] == 0


def test_get_exposes_scope_limitations_and_the_probe_failure_conditions(client):
    body = client.get("/api/assertions/POS-CSPM-001").json()
    assert "Does NOT prove terraform apply ran" in body["scope_limitations"]
    assert body["checks"][0]["failure_conditions"]
    assert body["checks"][0]["threshold"]["source"] == "authored_sharpening"


def test_get_unknown_assertion_is_a_structured_404(client):
    r = client.get("/api/assertions/POS-NOPE-999")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "ASSERTION_NOT_FOUND"
    assert set(detail) == {"error", "code", "detail"}


def test_probes_endpoint_publishes_the_authoring_contract(client):
    probes = {p["name"]: p for p in client.get("/api/assertions/probes").json()["probes"]}
    assert set(probes) >= {"xql_rows", "xql_distinct", "xql_scalar",
                           "xql_ratio", "xql_latency"}
    assert all(p["failure_conditions"] for p in probes.values())
    assert all(p["read_only"] for p in probes.values())


def test_dry_run_renders_the_query_and_touches_no_tenant(client):
    r = client.post("/api/assertions/POS-CSPM-001/run", json={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["tc_verdict"] == "pending"
    assert body["reason"] == "dry_run"
    assert "cortex_cloud_posture" in body["checks"][0]["queries"]["query"]
    # Nothing was persisted — a preview is not a run.
    assert "run_id" not in body
    assert client.get("/api/assertions/runs").json()["count"] == 0


def test_a_real_run_without_a_tenant_is_pending_never_green(client):
    r = client.post("/api/assertions/POS-CSPM-001/run", json={"dry_run": False})
    assert r.status_code == 200
    body = r.json()
    assert body["tc_verdict"] == "pending"
    assert body["reason"] == "no_tenant_integration"
    assert body["checks"][0]["remediation"].startswith("Register a read-only XSIAM")


def test_a_real_run_persists_a_run_and_its_checks(client):
    body = client.post("/api/assertions/POS-CSPM-001/run",
                       json={"dry_run": False}).json()
    run_id = body["run_id"]

    listing = client.get("/api/assertions/runs").json()
    assert listing["count"] == 1
    assert listing["runs"][0]["run_id"] == run_id

    detail = client.get(f"/api/assertions/runs/{run_id}").json()
    assert detail["tc_verdict"] == "pending"
    assert detail["reason"] == "no_tenant_integration"
    assert len(detail["checks"]) == 1
    check = detail["checks"][0]
    # The rendered query is on the row even with no tenant — that is the
    # offline day-one value: the exact question, next to the planted item.
    assert "cortex_cloud_posture" in check["verification_xql"]
    assert check["kpi_verdict"] == "pending"
    assert check["negative_control"].startswith("the public-read bucket")
    assert check["verified_at"] is None


def test_unknown_run_is_a_structured_404(client):
    r = client.get("/api/assertions/runs/AR-nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "ASSERTION_RUN_NOT_FOUND"


def test_context_values_are_substituted_into_the_rendered_query(
    make_client, tmp_path, monkeypatch
):
    from engine import assertions as ae

    spec = {**BASE, "assertion_id": "POS-CSPM-003", "context_vars": ["canary"]}
    spec["checks"] = [{**BASE["checks"][0],
                       "params": {"query": 'dataset = a | filter u = "{{canary}}"'}}]
    (tmp_path / "a.yml").write_text(yaml.safe_dump(spec), encoding="utf-8")

    saved = (ae.catalog._by_id.copy(), ae.catalog._loaded_from)
    ae.catalog.load(str(tmp_path), strict=False)
    try:
        from api.assertions import router

        c = make_client(router)
        body = c.post("/api/assertions/POS-CSPM-003/run",
                      json={"dry_run": True, "context": {"canary": "CSIM-abc123"}}).json()
        assert 'u = "CSIM-abc123"' in body["checks"][0]["queries"]["query"]
    finally:
        ae.catalog._by_id, ae.catalog._loaded_from = saved


def test_a_context_value_that_could_reshape_the_query_is_refused(
    make_client, tmp_path
):
    from engine import assertions as ae

    spec = {**BASE, "assertion_id": "POS-CSPM-004", "context_vars": ["canary"]}
    spec["checks"] = [{**BASE["checks"][0],
                       "params": {"query": 'dataset = a | filter u = "{{canary}}"'}}]
    (tmp_path / "a.yml").write_text(yaml.safe_dump(spec), encoding="utf-8")

    saved = (ae.catalog._by_id.copy(), ae.catalog._loaded_from)
    ae.catalog.load(str(tmp_path), strict=False)
    try:
        from api.assertions import router

        c = make_client(router)
        body = c.post("/api/assertions/POS-CSPM-004/run",
                      json={"dry_run": True,
                            "context": {"canary": 'x" | limit 99999 //'}}).json()
        assert body["checks"][0]["taxonomy_code"] == "PROBE_QUERY_FAILED"
        assert body["tc_verdict"] == "pending"
    finally:
        ae.catalog._by_id, ae.catalog._loaded_from = saved
