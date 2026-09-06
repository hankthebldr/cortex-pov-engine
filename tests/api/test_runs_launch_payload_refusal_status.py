"""A payload refusal must surface the SAME HTTP status on the launch route as
on the shelf route.

``core/api/payloads.py`` already maps the shelf's own error family honestly:

    HTTPException(status_code=getattr(exc, "http_status", 409), detail=exc.to_error())

The launch route did not. ``orchestrator._handle_pull`` caught
``PayloadResolutionError`` and rebuilt the refusal by hand, dropping ``code``,
``detail`` and ``http_status`` on the floor. ``LaunchResult.error_code`` fell
back to its ``"LAUNCH_FAILED"`` default, missed
``api.runs._LAUNCH_PRECONDITION_CODES``, and every payload refusal came out of
``POST /api/runs`` as a flat **422** with ``code: "LAUNCH_FAILED"``.

Three places said otherwise: ``PayloadResolutionError.http_status = 409``, the
comment directly above the status selection in ``api/runs.py`` (which names
PAYLOAD_NOT_STAGED as a 409 precondition), and CLAUDE.md's payload-shelf
section. Nothing asserted it, so the drift was invisible.

Why it is not cosmetic. 422 means *your request was malformed* — it points the
operator at the launch body. The body was fine; the SHELF is missing bytes, and
the fix is ``scripts/build-payloads.sh``. A DC chasing a 422 is debugging the
wrong system, and ``code: "LAUNCH_FAILED"`` gives a console nothing to branch
on, so it cannot offer the one action that resolves it.

The parametrisation below is the drift guard: it asserts the launch route
returns the exception's OWN ``http_status``, so the family can grow (or a
member can stop being a 409, as ``PayloadDestRefused`` already has at 400)
without anyone having to remember to update a hand-kept list of codes.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def client(make_client):
    from api.runs import router, compat_router
    return make_client(router, compat_router)


def _seed(session_factory, scenario_id="SIM-EDR-996"):
    from models import Scenario

    async def _do():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=scenario_id, name="payload refusal", version="1.0",
                status="active", plane="EDR",
                uc_ref="UCS-EDR-01", uc_name="x", tc_ref="TC-EDR-01", tc_name="y",
                tc_refs=["TC-EDR-01"],
                mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                mitre_technique="T1003", mitre_technique_name="OS Credential Dumping",
                execution_identity={"default": "direct", "options": ["direct"]},
                push_supported=True, pull_supported=True,
                steps=[{
                    "id": "step-01", "name": "s", "identity": "direct",
                    "command": "bash /tmp/linpeas.sh",
                    "mitre_technique": "T1003",
                    "expected_detections": [
                        {"plane": "EDR", "type": "BIOC", "description": "d"},
                    ],
                }],
            ))
            await db.commit()

    asyncio.run(_do())
    return scenario_id


def _raiser(exc_cls, code, detail):
    def _raise(_scenario):
        raise exc_cls(code, "Payload could not be resolved", detail)
    return _raise


# ---------------------------------------------------------------------------
# The status is the exception's own — not a hand-kept list of codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls_name,code", [
    ("PayloadNotStaged", "PAYLOAD_NOT_STAGED"),
    ("PayloadPinMismatch", "PAYLOAD_PIN_MISMATCH"),
    # Already NOT a 409 on the shelf route. Any fix that enumerates "payload
    # codes are 409" gets this one wrong, and the two routes disagree about the
    # same exception.
    ("PayloadDestRefused", "PAYLOAD_DEST_REFUSED"),
])
def test_launch_surfaces_the_payload_errors_own_http_status(
    client, session_factory, monkeypatch, cls_name, code,
):
    from engine import orchestrator as orch
    from engine import payload_shelf as shelf

    exc_cls = getattr(shelf, cls_name)
    monkeypatch.setattr(
        orch, "_compose_artifacts", _raiser(exc_cls, code, "linpeas.sh"),
    )

    sid = _seed(session_factory)
    r = client.post(
        "/api/runs",
        json={"scenario_id": sid, "mode": "pull", "target_agent_id": "a1"},
    )

    assert r.status_code == exc_cls.http_status, r.text
    body = r.json()["detail"]
    assert body["code"] == code
    # The structured payload is the shelf's own `to_error()` envelope — the
    # identical shape GET /api/shelf/compose returns — so a console parses a
    # payload refusal the same way whichever route surfaced it.
    assert body["detail"]["code"] == code
    assert "linpeas.sh" in body["detail"]["detail"]


def test_payload_refusal_is_a_precondition_not_a_malformed_request(
    client, session_factory, monkeypatch,
):
    """The headline case, stated plainly: a missing shelf artifact is 409.

    422 sends the operator to inspect a launch body that was never wrong.
    """
    from engine import orchestrator as orch
    from engine import payload_shelf as shelf

    monkeypatch.setattr(
        orch, "_compose_artifacts",
        _raiser(shelf.PayloadNotStaged, "PAYLOAD_NOT_STAGED", "linpeas.sh"),
    )

    sid = _seed(session_factory)
    r = client.post(
        "/api/runs",
        json={"scenario_id": sid, "mode": "pull", "target_agent_id": "a1"},
    )

    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PAYLOAD_NOT_STAGED"


# ---------------------------------------------------------------------------
# The refusal still terminalises the run (the lifecycle fix must survive)
# ---------------------------------------------------------------------------

def test_payload_refusal_still_leaves_the_run_terminal_failed(
    client, session_factory, monkeypatch,
):
    from sqlalchemy import select

    from engine import orchestrator as orch
    from engine import payload_shelf as shelf
    from models import Run

    monkeypatch.setattr(
        orch, "_compose_artifacts",
        _raiser(shelf.PayloadNotStaged, "PAYLOAD_NOT_STAGED", "linpeas.sh"),
    )

    sid = _seed(session_factory)
    client.post(
        "/api/runs",
        json={"scenario_id": sid, "mode": "pull", "target_agent_id": "a1"},
    )

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Run))).scalars().all()

    runs = asyncio.run(_read())
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert "PAYLOAD_NOT_STAGED" in (runs[0].output or "")


# ---------------------------------------------------------------------------
# The statuses that must NOT move
# ---------------------------------------------------------------------------

def test_unknown_mode_is_rejected_by_the_route_before_the_orchestrator(
    client, session_factory,
):
    """Pins where the boundary actually is.

    The route validates ``mode`` itself and answers 400 INVALID_MODE, so the
    orchestrator's own unknown-mode branch is unreachable over HTTP (it is
    still reachable by a direct ``launch()`` call, which is where
    tests/engine/test_orchestrator_launch_refusal_terminal.py covers it).
    Carrying the exception's http_status must not disturb this.
    """
    sid = _seed(session_factory)
    r = client.post("/api/runs", json={"scenario_id": sid, "mode": "sideways"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "INVALID_MODE"
