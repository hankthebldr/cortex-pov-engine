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
    row900 = [l for l in text.splitlines() if l.startswith("| 900 |")]
    row901 = [l for l in text.splitlines() if l.startswith("| 901 |")]
    assert len(row900) == 1 and "| socfw\\_auto\\_triage |" in row900[0]
    assert len(row901) == 1 and "| open |" in row901[0]
    assert "https://acme.xdr.us.paloaltonetworks.com/incidents/900" in text
    # A tenant-supplied URL that is not https on paloaltonetworks.com is not a link.
    assert "](http://evil.example.com/x)" not in text


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


INJECTIONS = [
    "https://a.xdr.paloaltonetworks.com/x) [click](http://evil.example/",
    "https://a.xdr.paloaltonetworks.com/x\n\n# Injected",
    "https://evil.example\\@a.xdr.paloaltonetworks.com/x",
    "https://u:p@a.xdr.paloaltonetworks.com/x",
    "https://a.xdr.paloaltonetworks.com:8443/x",
    "https://a.xdr.paloaltonetworks.com/x`y",
    "https://evilpaloaltonetworks.com/x",
]


@pytest.mark.parametrize("url", INJECTIONS)
def test_hostile_xdr_url_is_withheld(client, session_factory, url):
    led = dict(LEDGER, cases=[dict(LEDGER["cases"][0], xdr_url=url)])
    _seed(session_factory, led)
    text = _md(client)
    assert "[click]" not in text and "\n# Injected" not in text
    assert "](http" not in text and "[open](" not in text
    rows = [l for l in text.splitlines() if l.startswith("| 900 |")]
    assert len(rows) == 1 and rows[0].endswith("(invalid link withheld) |")
    assert rows[0].count("|") == 7


def test_valid_url_is_reserialised_link(client, session_factory):
    _seed(session_factory, LEDGER)
    assert "[open](https://acme.xdr.us.paloaltonetworks.com/incidents/900)" in _md(client)


def test_hostile_case_id_and_status_cannot_break_row(client, session_factory):
    c = dict(LEDGER["cases"][0], case_id="9|0`0\n# H", status="a|b\n- x", closed_by="x`y")
    _seed(session_factory, dict(LEDGER, cases=[c]))
    text = _md(client)
    assert "\n# H" not in text and "\n- x" not in text
    row = [l for l in text.splitlines() if l.startswith("| 9")][0]
    assert row.replace("\\|", "").count("|") == 7


@pytest.mark.parametrize("missing", ["merge_class", "cases", "code"])
def test_ledger_missing_required_key_is_unreadable(client, session_factory, missing):
    led = {k: v for k, v in LEDGER.items() if k != missing}
    _seed(session_factory, led)
    text = _md(client)
    assert "Ledger unreadable" in text and CASE_DISCLAIMER in text
    assert "Case merge" not in text


def test_ledger_wrong_type_is_unreadable(client, session_factory):
    _seed(session_factory, dict(LEDGER, cases="oops"))
    assert "Ledger unreadable" in _md(client)


INLINE = [
    ("[click](http://evil.example)", r"\[click\]\(http://evil.example\)"),
    ("<img src=x onerror=alert(1)>", r"\<img src=x onerror=alert\(1\)\>"),
    ("**bold**", r"\*\*bold\*\*"),
]


@pytest.mark.parametrize("case_id,escaped", INLINE)
def test_inline_markdown_and_html_in_case_id_is_neutralised(
        client, session_factory, case_id, escaped):
    c = dict(LEDGER["cases"][0], case_id=case_id)
    _seed(session_factory, dict(LEDGER, cases=[c], merge_reason=f"case {case_id} sealed"))
    text = _md(client)
    row = [l for l in text.splitlines() if escaped in l and l.startswith("| ")]
    assert len(row) == 1
    assert row[0].replace("\\|", "").count("|") == 7
    # every metacharacter is backslash-escaped, so no unescaped link/tag/emphasis remains
    assert "<img" not in text.replace("\\<img", "")
    assert "](" not in text.replace("\\]\\(", "").replace("[open](", "")
    assert "**bold**" not in text.replace("\\*\\*bold\\*\\*", "")
    # the merge_reason (inline text, not a table) is escaped too
    assert any(escaped in l and l.startswith("**Case merge:**") for l in text.splitlines())


def test_code_span_values_carry_no_visible_backslashes(client, session_factory):
    _seed(session_factory, dict(LEDGER, integration="a|b", code="CASE_READ_FAILED"))
    text = _md(client)
    assert "`a|b`" in text and "\\|" not in text.split("| Case |")[0]
