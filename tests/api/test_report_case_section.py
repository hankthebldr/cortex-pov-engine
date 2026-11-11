"""The ``## Case`` section of the POV report (Task B6).

Tenant disposition is context, never detection evidence: the section renders
the stored ledger verbatim and carries ``CASE_DISCLAIMER``. Transports are not
involved; nothing here touches a tenant (tenant-verified is 0).
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from connectors.case_ledger import CASE_DISCLAIMER

LEDGER = {
    "read_at": "2026-11-11T10:00:00+00:00",
    "basis": "alert_incident_ids",
    "integration": "acme",
    "merge_class": "SPLIT_SEALED",
    "merge_reason": "case 901 was sealed before the chain finished",
    "code": "CASE_LEDGER_OK",
    "shared_with_other_runs": True,
    "cases": [
        {"case_id": "900", "status": "resolved_other", "closed_by": "socfw_auto_triage",
         "sealed": True, "starred": False, "assigned": False,
         "xdr_url": "https://acme.xdr.us.paloaltonetworks.com/incidents/900",
         "resolved_at": "2026-11-11T09:00:00+00:00", "resolve_comment": "auto",
         "issues_from_run": 2, "issues_other": 3, "issues_closed_first": 1},
        {"case_id": "901", "status": "new", "closed_by": "open",
         "sealed": False, "starred": False, "assigned": False,
         "xdr_url": "http://evil.example.com/x",
         "resolved_at": None, "resolve_comment": None,
         "issues_from_run": 1, "issues_other": 0, "issues_closed_first": 0},
    ],
    "alerts": [],
}


@pytest.fixture
def client(make_client):
    from api.runs import router
    return make_client(router)


def _seed(session_factory, ledger):
    from models import Run

    async def go():
        async with session_factory() as db:
            db.add(Run(run_id="r1", scenario_id="SIM-X", mode="pull", status="complete",
                       started_at=datetime.utcnow(), case_ledger=ledger))
            await db.commit()
    asyncio.run(go())


def _md(client):
    r = client.get("/api/runs/r1/report?format=markdown")
    assert r.status_code == 200, r.text
    return r.text


def test_markdown_case_section_has_disclaimer_verbatim(client, session_factory):
    _seed(session_factory, LEDGER)
    text = _md(client)
    assert "## Case" in text
    assert CASE_DISCLAIMER in text
    assert CASE_DISCLAIMER == "Tenant disposition is shown for context. It is not detection evidence."
    assert "SPLIT_SEALED" in text and "case 901 was sealed before the chain finished" in text
    assert "2026-11-11T10:00:00+00:00" in text


def test_markdown_lists_each_case_with_closed_by_and_link(client, session_factory):
    _seed(session_factory, LEDGER)
    text = _md(client)
    assert "socfw_auto_triage" in text and "open" in text
    assert "https://acme.xdr.us.paloaltonetworks.com/incidents/900" in text
    # A tenant-supplied URL that is not https on paloaltonetworks.com is not a link.
    assert "](http://evil.example.com/x)" not in text
    assert "901" in text


def test_markdown_shows_degraded_code_and_failed_issue_read(client, session_factory):
    led = dict(LEDGER, code="CASE_READ_FAILED",
               issue_read={"ok": False, "code": "ISSUE_READ_500", "pages": 0,
                           "remediation": "retry the reconcile"})
    _seed(session_factory, led)
    text = _md(client)
    assert "CASE_READ_FAILED" in text and "ISSUE_READ_500" in text and "retry the reconcile" in text


def test_markdown_without_ledger_says_not_read(client, session_factory):
    _seed(session_factory, None)
    text = _md(client)
    assert "## Case" in text
    assert "Not read yet — reconcile the run." in text


def test_json_report_includes_case_ledger(client, session_factory):
    _seed(session_factory, LEDGER)
    body = client.get("/api/runs/r1/report?format=json").json()
    assert body["case_ledger"]["merge_class"] == "SPLIT_SEALED"
