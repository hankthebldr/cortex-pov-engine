"""A run that never executed must not render as a clean detection miss.

The beacon refuses correctly when a tool cannot be staged — exit 78, a per-step
`ARTIFACT NOT STAGED` frame, and an explicit "THIS STEP DID NOT RUN" line. The
POV report then threw all of that away: its sections were MITRE Mapping, Tools
Used, Detection Coverage, MTTD and Detailed Results, none of which read
`run.output`. A run whose tooling never arrived rendered as
`0/14 detections confirmed (0.0%)`, every row ❌, above a "Tools Used" table
introduced as the run's licence audit trail listing three tools that never
reached the target.

That is the manufactured false negative relocated out of the API and into the
one artifact a DC actually puts in front of a customer, where it reads as a gap
in THEIR coverage.
"""
from __future__ import annotations

import pytest

from api.runs import _execution_integrity


class _Run:
    def __init__(self, output):
        self.output = output


def test_an_ordinary_run_is_unmarked():
    assert _execution_integrity(_Run("--- STEP 1/3 ---\nok\n"))["ok"] is True
    assert _execution_integrity(_Run(None))["ok"] is True
    assert _execution_integrity(_Run(""))["problems"] == []


@pytest.mark.parametrize("marker", [
    "ARTIFACT STAGING FAILED",
    "PAYLOAD_NOT_STAGED_ON_TARGET",
    "ARTIFACT_SPEC_INVALID",
    "RUN FAILED ON RESTART",
])
def test_every_refusal_marker_voids_the_report(marker):
    """Both refusal points emit these — the beacon and the agents.py capability
    gate — so one vocabulary has to cover both."""
    verdict = _execution_integrity(_Run(f"=== ARTIFACT STAGING ===\n{marker}: linpeas.sh\n"))
    assert verdict["ok"] is False
    assert len(verdict["problems"]) == 1
    assert marker in verdict["problems"][0]


def test_multiple_markers_are_all_reported():
    out = "ARTIFACT STAGING FAILED: pspy64\nPAYLOAD_NOT_STAGED_ON_TARGET: linpeas.sh\n"
    assert len(_execution_integrity(_Run(out))["problems"]) == 2


@pytest.mark.asyncio
async def test_markdown_report_leads_with_the_warning(session_factory, make_client):
    """End to end through the real endpoint: the warning outranks the coverage
    numbers on the page, because a DC skims from the top."""
    from api.runs import router
    from models import Run, Scenario

    async with session_factory() as db:
        db.add(Scenario(
            scenario_id="SIM-EDR-022", name="shelf staged privesc", version="1.0",
            status="active", plane="EDR", detection_types=["BIOC"],
            uc_ref="UC-01", tc_ref="TC-01", uc_name="uc", tc_name="tc",
            tc_refs=["TC-01"], mitre_tactic="TA0007", mitre_tactic_name="Discovery",
            mitre_technique="T1082", mitre_technique_name="System Information Discovery",
            additional_techniques=[], execution_identity={},
            push_supported=False, pull_supported=True, external_tools=[], steps=[],
        ))
        db.add(Run(
            run_id="run-void", scenario_id="SIM-EDR-022", mode="pull",
            status="failed",
            output="=== ARTIFACT STAGING · 3 artifact(s) ===\n"
                   "ARTIFACT STAGING FAILED: linpeas.sh digest mismatch\n",
        ))
        await db.commit()

    client = make_client(router)
    body = client.get("/api/runs/run-void/report?format=markdown").text

    assert "Execution Integrity" in body
    assert "ARTIFACT STAGING FAILED" in body
    assert "treat the coverage numbers below as void" in body
    # Above the coverage section, not buried under it.
    assert body.index("Execution Integrity") < body.index("Detection Coverage Summary")

    # And the JSON surface carries the same verdict, so a console can render it.
    js = client.get("/api/runs/run-void/report?format=json").json()
    assert js["execution_integrity"]["ok"] is False
