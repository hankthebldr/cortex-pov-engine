"""The markdown POV report states the caveats the run record already knows (UI-09 / CONT-11).

Three facts sat in ``run.output`` / the scenario and never reached the one
artifact a DC puts in front of a customer:

* **Identity not honoured.** A Windows beacon cannot impersonate a service
  account unattended, so it runs the step as itself and writes
  ``!! IDENTITY NOT HONOURED:`` into the step's output. Lineage-keyed
  detections (``causality_actor_*`` / the acting user) then cannot match by
  construction — a miss on those steps is not a coverage measurement. 117
  Windows steps in this corpus declare ``svc-backup``.
* **Cleanup not run.** Pull mode never executes ``cleanup.commands`` (only the
  push generator emits them); the report said nothing, so a DC left the
  scenario's persistence artefacts on the customer's endpoint.
* **Platform variant.** A step that ran its ``platform_variants`` text (the
  pull platform gate) carries ``!! PLATFORM VARIANT``: its expectations were
  authored against the primary command.

None of these voids the run (that is the execution-integrity section); each is
a scoped caveat, listing the steps it applies to.
"""
from __future__ import annotations

import asyncio

import pytest

IDENTITY_OUTPUT = (
    "=== STEP 1/3 · step-01 · T1059.001 · identity=svc-backup ===\n"
    "!! IDENTITY NOT HONOURED: windows has no credential-free impersonation; ran as SYSTEM\n"
    "ok\n--- exit_code=0 duration=1s ---"
    "=== STEP 2/3 · step-02 · T1003.001 · identity=direct ===\n"
    "ok\n--- exit_code=0 duration=1s ---"
    "=== STEP 3/3 · step-03 · T1547.001 · identity=svc-backup ===\n"
    "!! IDENTITY NOT HONOURED: windows has no credential-free impersonation; ran as SYSTEM\n"
    "--- exit_code=0 duration=1s ---"
)


def _seed(session_factory, *, run_id, mode="pull", output="", cleanup=None):
    from models import Run, Scenario

    async def _go():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id=f"SIM-RPT-{run_id}", name="report caveats", version="1.0",
                status="active", plane="EDR", detection_types=["BIOC"],
                uc_ref="UC-01", tc_ref="TC-01", uc_name="uc", tc_name="tc",
                mitre_tactic="TA0002", mitre_tactic_name="Execution",
                mitre_technique="T1059", mitre_technique_name="Interpreter",
                execution_identity={}, push_supported=True, pull_supported=True,
                external_tools=[], steps=[], cleanup=cleanup,
            ))
            db.add(Run(run_id=run_id, scenario_id=f"SIM-RPT-{run_id}", mode=mode,
                       status="complete", output=output))
            await db.commit()

    asyncio.run(_go())


@pytest.fixture
def client(make_client):
    from api.runs import router

    return make_client(router)


def _report(client, run_id) -> str:
    r = client.get(f"/api/runs/{run_id}/report?format=markdown")
    assert r.status_code == 200, r.text
    return r.text


def test_identity_not_honoured_steps_are_listed_as_a_caveat(client, session_factory):
    _seed(session_factory, run_id="id-1", output=IDENTITY_OUTPUT)
    body = _report(client, "id-1")
    assert "Identity Not Honoured" in body
    assert "lineage-keyed misses on these steps are not a coverage measurement" in body
    section = body.split("Identity Not Honoured", 1)[1].split("\n## ", 1)[0]
    assert "step-01" in section and "step-03" in section
    assert "step-02" not in section
    # A caveat, not a void: the integrity banner is for runs that did not run.
    assert "READ THIS FIRST" not in body


def test_pull_mode_names_the_cleanup_commands_it_did_not_run(client, session_factory):
    cmds = ["rm -f /tmp/cortexsim_cron_test", "crontab -r || true"]
    _seed(session_factory, run_id="cl-1", cleanup={"commands": cmds})
    body = _report(client, "cl-1")
    assert "pull mode does not execute cleanup.commands: 2 commands not run" in body
    for c in cmds:
        assert c in body


def test_push_mode_does_not_claim_cleanup_was_skipped(client, session_factory):
    """The push bundle carries and runs its own cleanup."""
    _seed(session_factory, run_id="cl-2", mode="push",
          cleanup={"commands": ["rm -f /tmp/x"]})
    assert "does not execute cleanup.commands" not in _report(client, "cl-2")


def test_platform_variant_steps_are_listed(client, session_factory):
    out = ("!! PLATFORM VARIANT: step=step-02 ran its windows variant; its "
           "expected_detections were authored against the primary command and "
           "are unverified for this OS\n")
    _seed(session_factory, run_id="pv-1", output=out)
    body = _report(client, "pv-1")
    assert "Platform Variants" in body
    section = body.split("Platform Variants", 1)[1].split("\n## ", 1)[0]
    assert "step-02" in section and "windows" in section


def test_an_unremarkable_run_grows_no_caveat_section(client, session_factory):
    _seed(session_factory, run_id="plain-1", output="=== STEP 1/1 · step-01 · T1 · identity=root ===\nok\n")
    body = _report(client, "plain-1")
    assert "Caveats" not in body
    assert "Identity Not Honoured" not in body and "cleanup.commands" not in body
