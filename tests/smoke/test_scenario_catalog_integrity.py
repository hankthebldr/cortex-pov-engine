"""Layer 3 smoke: every scenario in the live catalogue is launchable.

Cheap regression guard for the YAML library — if anyone adds a scenario
with a malformed step that the orchestrator can't queue, we catch it the
moment SimCore comes up rather than in front of a customer.

This is the "scenarios are YAML source-of-truth" rule from CLAUDE.md
enforced in a running system, not just at unit-test boot.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture(scope="module")
def all_scenarios(client: httpx.Client) -> list[dict]:
    r = client.get("/api/scenarios")
    r.raise_for_status()
    return r.json()["scenarios"]


def test_every_scenario_has_required_fields(all_scenarios: list[dict]) -> None:
    """Spot-check the fields the UI depends on are non-null for every scenario."""
    required = {"scenario_id", "name", "plane", "mitre_tactic", "mitre_technique"}
    bad: list[tuple[str, set[str]]] = []
    for s in all_scenarios:
        missing = {k for k in required if not s.get(k)}
        if missing:
            bad.append((s.get("scenario_id", "?"), missing))
    assert not bad, f"scenarios missing required fields: {bad}"


def test_every_scenario_launches_push(client: httpx.Client, all_scenarios: list[dict]) -> None:
    """Each scenario should be at least push-launchable.

    We intentionally don't download every bundle (would be slow + heavy) —
    successful 200 from /api/run + a queryable run_id is enough to prove
    that the orchestrator can ingest the YAML's step list end to end.
    """
    failures: list[tuple[str, int, str]] = []
    for s in all_scenarios:
        sid = s["scenario_id"]
        r = client.post("/api/run", json={"scenario_id": sid, "mode": "push"})
        if r.status_code != 200:
            failures.append((sid, r.status_code, r.text[:200]))
            continue
        run_id = r.json()["run_id"]
        results = client.get(f"/api/results/{run_id}").json()
        if not results.get("results"):
            failures.append((sid, 0, "no result rows seeded — empty expected_detections?"))

    assert not failures, (
        "scenarios that failed to launch in push mode:\n  "
        + "\n  ".join(f"{sid} → {code}: {msg}" for sid, code, msg in failures)
    )


def test_plane_distribution_sane(all_scenarios: list[dict]) -> None:
    """Catch the 'I deleted all NDR scenarios by accident' class of regression."""
    counts: dict[str, int] = {}
    for s in all_scenarios:
        counts[s["plane"]] = counts.get(s["plane"], 0) + 1
    # Per CLAUDE.md table — active planes should each have multiple scenarios
    for plane in ("EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "AI_ACCESS", "AIRS", "BROWSER", "KOI"):
        assert counts.get(plane, 0) >= 1, f"plane {plane} has no scenarios"
