# tests/integration/xsiam/test_operation_catalog_harvest.py
"""Ops harvested from the field-built cortex-bot MCP server (2026-11-02).

cortex-bot (a DC-authored layer on the official Cortex MCP server) calls 32
tenant operations the catalog did not carry. Each one was exercised against a
live tenant by its author, which is more than any CortexSim path can say
(tenant-verified is 0), so the catalog records them with provenance rather
than leaving a DC to reach for an ungoverned tool.

Classification is ours, not cortex-bot's: cortex-bot gates on an LLM-settable
boolean, CortexSim gates on ``access_class``. Where the two disagree we take
the stricter reading (``terminate_causality`` is irreversible on the host, so
it is ``destructive`` even though the pack convention files ``isolate`` as
``write``).

Review: docs/brainstorm/2026-11-02-cortex-bot-review-and-pov-integration.md
"""
from __future__ import annotations

import os
import re

import pytest

from integrations.xsiam.operations import catalog as catmod
from integrations.xsiam.operations.catalog import OperationCatalog

PACKS = os.path.join(os.path.dirname(catmod.__file__), "packs")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DOC_MIRROR = os.path.join(
    REPO, "docs", "reference", "xsiam-platform-apis", "14-operation-catalog.md")

PROVENANCE = "cortex-bot-observed"

# op_id → (method, path, access_class). Paths are kept exactly as cortex-bot
# sends them, trailing slash included: the slash form is the one with
# tenant evidence behind it.
HARVEST = {
    # alerts
    "OP-ALERTS-GET-ALERTS-MULTI-EVENTS-V2": (
        "POST", "/public_api/v2/alerts/get_alerts_multi_events", "read"),
    "OP-ALERTS-GET-CONTRIBUTING-EVENT": (
        "POST", "/public_api/v1/alerts/get_contributing_event/", "read"),
    # system
    "OP-SYSTEM-GET-TENANT-INFO": (
        "POST", "/public_api/v1/system/get_tenant_info", "read"),
    # cases & issues
    "OP-CASE-SEARCH": ("POST", "/public_api/v1/case/search/", "read"),
    "OP-ISSUE-SEARCH": ("POST", "/public_api/v1/issue/search/", "read"),
    "OP-ISSUE-CREATE": ("POST", "/public_api/v1/issue", "write"),
    # identity threat
    "OP-GET-RISKY-HOSTS": ("POST", "/public_api/v1/get_risky_hosts", "read"),
    "OP-GET-RISKY-USERS": ("POST", "/public_api/v1/get_risky_users", "read"),
    # triage
    "OP-GET-TRIAGE-PRESETS": ("POST", "/public_api/v1/get_triage_presets", "read"),
    # asset inventory
    "OP-ASSETS-GET-ASSETS": ("POST", "/public_api/v1/assets/get_assets/", "read"),
    "OP-COMPLIANCE-GET-ASSESSMENT-RESULTS": (
        "POST", "/public_api/v1/compliance/get_assessment_results/", "read"),
    # response action
    "OP-ACTIONS-GET-ACTION-STATUS": (
        "POST", "/public_api/v1/actions/get_action_status/", "read"),
    "OP-ENDPOINTS-TERMINATE-CAUSALITY": (
        "POST", "/public_api/v1/endpoints/terminate_causality", "destructive"),
    # war room
    "OP-ENTRIES-GET": ("POST", "/public_api/v1/entries/get", "read"),
    "OP-ENTRIES-INSERT": ("POST", "/public_api/v1/entries/insert", "write"),
    # managed threat hunting (MDR) reports
    "OP-MTH-GET-ALL-REPORTS": (
        "POST", "/public_api/v1/mth/child/get_all_reports", "read"),
    "OP-MTH-GET-REPORTS-BY-SOURCE-ID": (
        "POST", "/public_api/v1/mth/child/get_reports_by_source_id", "read"),
    "OP-MTH-GET-REPORTS-BY-INCIDENT-ID": (
        "POST", "/public_api/v1/mth/child/get_reports_by_incident_id", "read"),
    "OP-MTH-GET-REPORTS-BY-STATUSES": (
        "POST", "/public_api/v1/mth/child/get_reports_by_statuses", "read"),
    "OP-MTH-GET-COMMENTS": ("POST", "/public_api/v1/mth/child/get_comments", "read"),
    "OP-MTH-ADD-COMMENT": ("POST", "/public_api/v1/mth/child/add_comment", "write"),
    "OP-MTH-REPORT-UPDATE-STATUS": (
        "POST", "/public_api/v1/mth/child/report/update/status", "write"),
    "OP-MTH-REPORT-UPDATE-ASSIGN": (
        "POST", "/public_api/v1/mth/child/report/update/assign", "write"),
    # dashboards (widgets)
    "OP-WIDGETS-GET": ("POST", "/public_api/v1/widgets/get", "read"),
    "OP-WIDGETS-INSERT": ("POST", "/public_api/v1/widgets/insert", "write"),
    "OP-WIDGETS-DELETE": ("POST", "/public_api/v1/widgets/delete", "destructive"),
    # vulnerability management
    "OP-UVEM-GET-VULNERABILITIES": (
        "POST", "/public_api/uvem/v1/get_vulnerabilities", "read"),
    "OP-VULN-MGMT-FINDING-SEARCH": (
        "POST",
        "/public_api/v1/vulnerability-management/v1/vulnerability-finding/search",
        "read"),
    "OP-VULN-MGMT-FINDING-PLATFORM-ID": (
        "POST",
        "/public_api/v1/vulnerability-management/v1/vulnerability-finding/{platform_id}",
        "read"),
    "OP-VULN-MGMT-SCAN": (
        "POST", "/public_api/vulnerability-management/v1/scan", "write"),
    "OP-VULN-MGMT-EXTERNAL-SCANS-ASSETS": (
        "POST", "/public_api/vulnerability-management/v1/external-scans/assets", "write"),
    "OP-VULN-MGMT-EXTERNAL-SCANS-JOB": (
        "GET",
        "/public_api/vulnerability-management/v1/external-scans/assets/jobs/{job_id}",
        "read"),
}


@pytest.fixture(scope="module")
def cat() -> OperationCatalog:
    c = OperationCatalog()
    c.load(PACKS)
    return c


def test_harvest_is_thirty_two_ops_split_22_8_2():
    """The table itself is the spec; pin its shape so an edit is deliberate."""
    classes = [ac for (_m, _p, ac) in HARVEST.values()]
    assert len(HARVEST) == 32
    assert classes.count("read") == 22
    assert classes.count("write") == 8
    assert classes.count("destructive") == 2


@pytest.mark.parametrize("op_id", sorted(HARVEST))
def test_harvested_op_resolves_exactly(cat, op_id):
    method, path, access = HARVEST[op_id]
    op = cat.find(op_id)
    assert op is not None, f"{op_id} missing from the catalog"
    assert (op.method, op.path, op.access_class) == (method, path, access)
    assert PROVENANCE in op.tags


def test_provenance_tag_marks_exactly_the_harvest(cat):
    """`cortex-bot-observed` is a claim about tenant evidence; it must not spread."""
    tagged = {o.op_id for o in cat.all() if PROVENANCE in o.tags}
    assert tagged == set(HARVEST)


def test_no_harvested_path_duplicates_an_existing_op(cat):
    """One path, one op id. A second id for a path would split its gate."""
    by_path: dict[tuple[str, str], list[str]] = {}
    for o in cat.all():
        by_path.setdefault((o.method, o.path.rstrip("/")), []).append(o.op_id)
    dupes = {k: v for k, v in by_path.items() if len(v) > 1}
    assert dupes == {}


def test_byos_ingest_declares_its_bare_envelope(cat):
    """BYOS ingest takes no request_data wrapper (cortex-bot RELEASE_NOTES 1.2.0)."""
    op = cat.find("OP-VULN-MGMT-EXTERNAL-SCANS-ASSETS")
    assert op is not None
    assert "no-request-data-wrapper" in op.tags


def test_doc_mirror_headline_matches_loaded_catalog(cat):
    """The doc mirror quotes counts; this keeps the quote honest."""
    with open(DOC_MIRROR, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(
        r"\*\*(\d+) operations\*\* across \*\*(\d+) categories\*\* — "
        r"(\d+) read · (\d+) write · (\d+) destructive", text)
    assert m, "headline line not found in the doc mirror"
    quoted = tuple(int(g) for g in m.groups())
    ops = cat.all()
    loaded = (
        len(ops),
        len(cat.categories()),
        sum(o.access_class == "read" for o in ops),
        sum(o.access_class == "write" for o in ops),
        sum(o.access_class == "destructive" for o in ops),
    )
    assert quoted == loaded


def test_doc_mirror_lists_every_loaded_op(cat):
    with open(DOC_MIRROR, encoding="utf-8") as fh:
        text = fh.read()
    missing = sorted(o.op_id for o in cat.all() if f"`{o.op_id}`" not in text)
    assert missing == []
