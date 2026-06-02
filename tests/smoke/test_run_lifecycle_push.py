"""Layer 1 smoke: launch a scenario in PUSH mode and exercise the full
report pipeline without needing a live beacon agent.

Push mode is the right vehicle for smoke because:
  * It's deterministic — no agent polling race
  * It seeds the same Result rows as pull mode (orchestrator behavior)
  * The push bundle download proves the generator works end-to-end
  * MTTD + report export are fully exercised once results are validated
"""

from __future__ import annotations

import time

import httpx
import pytest


def _wait_for_run_terminal(client: httpx.Client, run_id: str, timeout: float = 30.0) -> dict:
    """Poll a run until it leaves a non-terminal state or timeout."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}")
        r.raise_for_status()
        last = r.json()
        if last.get("status") in {"complete", "failed", "pending_download"}:
            return last
        time.sleep(0.5)
    return last


def test_push_run_full_lifecycle(client: httpx.Client, known_scenario_id: str) -> None:
    """End-to-end: launch → results seeded → validate → report → bundle."""

    # 1. Launch ------------------------------------------------------------
    launch = client.post(
        "/api/run",
        json={"scenario_id": known_scenario_id, "mode": "push",
              "consent": {"simulation_authorized": True, "c2_authorized": True}},
    )
    assert launch.status_code == 200, launch.text
    body = launch.json()
    run_id = body["run_id"]
    assert body["mode"] == "push"
    # Push runs return a download URL for the bundle
    assert "download_url" in body or body.get("message"), (
        f"push launch response missing download_url/message: {body}"
    )

    # 2. Run record visible immediately -----------------------------------
    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["scenario_id"] == known_scenario_id

    # 3. Result rows auto-seeded from expected_detections -----------------
    results_resp = client.get(f"/api/results/{run_id}")
    assert results_resp.status_code == 200, results_resp.text
    results_body = results_resp.json()
    results = results_body["results"]
    assert results, (
        f"orchestrator did not seed Result rows for {known_scenario_id} "
        "— expected_detections empty or auto-seed broke"
    )
    # All results start unobserved
    assert all(r["observed"] is False for r in results), (
        "newly-seeded results should default to observed=False"
    )

    # 4. DC validates each detection — proves MTTD pipeline ---------------
    for r in results:
        v = client.put(
            f"/api/results/{r['id']}/validate",
            json={"observed": True, "notes": "smoke-test auto-observation"},
        )
        assert v.status_code == 200, v.text
        updated = v.json()
        assert updated["observed"] is True
        assert updated["observed_at"] is not None
        # MTTD is observed_at - executed_at; both must be set
        assert updated.get("mttd_seconds") is not None, (
            f"MTTD not computed for result id={r['id']} — executed_at likely null"
        )

    # 5. Coverage recomputed ---------------------------------------------
    after = client.get(f"/api/results/{run_id}").json()
    assert after["coverage"]["pct"] == 100.0, after["coverage"]
    assert after["mttd"] is not None

    # 6. Push bundle is downloadable (proves push_generator) --------------
    if "download_url" in body:
        bundle = client.get(body["download_url"])
        assert bundle.status_code == 200, bundle.text
        assert len(bundle.content) > 100, "push bundle suspiciously small"

    # 7. POV report renders both formats ---------------------------------
    report_md = client.get(f"/api/runs/{run_id}/report", params={"format": "markdown"})
    assert report_md.status_code == 200
    assert "POV Detection Validation Report" in report_md.text
    assert "MITRE ATT&CK Mapping" in report_md.text

    report_json = client.get(f"/api/runs/{run_id}/report", params={"format": "json"})
    assert report_json.status_code == 200
    rep = report_json.json()
    assert rep["coverage"]["pct"] == 100.0

    # 8. Detection matrix CSV --------------------------------------------
    matrix = client.get(f"/api/runs/{run_id}/report/matrix")
    assert matrix.status_code == 200
    assert matrix.headers["content-type"].startswith("text/csv")
    assert matrix.text.count("\n") > 1, "matrix CSV has no data rows"

    # 9. ATT&CK Navigator layer ------------------------------------------
    nav = client.get(f"/api/runs/{run_id}/report/navigator")
    assert nav.status_code == 200
    layer = nav.json()
    assert layer.get("techniques"), "navigator layer has no techniques"
    # v4.x Navigator schema is fairly stable on these keys
    assert "name" in layer and "domain" in layer

    # 10. Combined bundle -------------------------------------------------
    pov_bundle = client.get(f"/api/runs/{run_id}/report/bundle")
    assert pov_bundle.status_code == 200
    assert pov_bundle.content[:2] == b"\x1f\x8b", "bundle is not gzip"


def test_launch_invalid_mode_rejected(client: httpx.Client, known_scenario_id: str) -> None:
    r = client.post(
        "/api/run",
        json={"scenario_id": known_scenario_id, "mode": "bogus"},
    )
    assert r.status_code == 400
    body = r.json()
    # Structured error per CLAUDE.md design rule
    assert "detail" in body
    detail = body["detail"]
    assert detail.get("code") == "INVALID_MODE"


def test_launch_unknown_scenario_rejected(client: httpx.Client) -> None:
    r = client.post(
        "/api/run",
        json={"scenario_id": "SIM-EDR-DOES-NOT-EXIST", "mode": "push"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "LAUNCH_FAILED"
