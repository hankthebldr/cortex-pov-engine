"""Direct router tests for the TTP browser API.

The TTP catalog singleton is populated against the real corpus once per
module so the list / detail / reverse-cross-ref endpoints are exercised
end-to-end against the same data the engine sees at runtime.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TTPS_DIR  = REPO_ROOT / "detection_scanner" / "ttps"
PACKS_DIR = REPO_ROOT / "tools" / "packs"


@pytest.fixture(scope="module", autouse=True)
def _load_catalogs():
    """Both catalogs are needed: the TTP catalog feeds the browser
    endpoints, and the adapter catalog drives the reverse cross-link."""
    from engine.ttp_catalog import catalog as ttp_catalog  # noqa: PLC0415
    from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415

    ttp_catalog.load(str(TTPS_DIR))
    adapter_catalog.load(str(PACKS_DIR))
    assert len(ttp_catalog.all_entries()) > 0
    assert adapter_catalog.count() > 0


@pytest.fixture
def client(make_client):
    from api.ttps import router  # noqa: PLC0415
    return make_client(router)


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_ttps_returns_corpus(client):
    resp = client.get("/api/ttps")
    assert resp.status_code == 200
    body = resp.json()
    assert "ttps" in body
    assert body["total"] == len(body["ttps"])
    # Sanity floor — corpus is small but non-empty at PR time
    assert body["total"] >= 10
    # Sorted by id for stable rendering
    ids = [t["id"] for t in body["ttps"]]
    assert ids == sorted(ids)


def test_list_ttps_summary_shape(client):
    body = client.get("/api/ttps").json()
    one = body["ttps"][0]
    # Required keys for the UI card render
    for key in (
        "id", "name", "status", "summary", "tags", "platforms",
        "simulation_class", "destructive", "technique_ids",
        "tactic_ids", "kill_chain_phase", "actor_names",
        "detection_counts", "panw_products",
    ):
        assert key in one, f"missing key {key!r} in summary payload"
    # detection_counts is a sub-dict with the canonical detection kinds
    for k in ("iocs", "biocs", "xql_queries", "correlation_rules", "analytics_modules"):
        assert k in one["detection_counts"]


def test_list_ttps_filter_by_status(client):
    body = client.get("/api/ttps?status=active").json()
    for t in body["ttps"]:
        assert t["status"] == "active"


def test_list_ttps_filter_by_tactic(client):
    body = client.get("/api/ttps?tactic=TA0006").json()
    # Every returned card has Credential Access in its tactic_ids
    for t in body["ttps"]:
        assert "TA0006" in t["tactic_ids"]
    # TTP-2026-0002 (LSASS) and TTP-2026-0004 (DCSync) both cite TA0006
    ids = {t["id"] for t in body["ttps"]}
    assert "TTP-2026-0002" in ids
    assert "TTP-2026-0004" in ids


def test_list_ttps_filter_by_platform(client):
    body = client.get("/api/ttps?platform=windows").json()
    for t in body["ttps"]:
        assert "windows" in t["platforms"]


def test_list_ttps_unknown_filter_value_returns_empty(client):
    # Defensive: a stale UI sending `status=NOPE` shouldn't 400 — it
    # should quietly return an empty list so the picker shows "no matches".
    body = client.get("/api/ttps?status=NOPE").json()
    assert body == {"ttps": [], "total": 0}


def test_list_ttps_filters_compose_with_logical_and(client):
    body = client.get("/api/ttps?status=active&tactic=TA0006&platform=windows").json()
    for t in body["ttps"]:
        assert t["status"] == "active"
        assert "TA0006" in t["tactic_ids"]
        assert "windows" in t["platforms"]


# ---------------------------------------------------------------------------
# Detail endpoint
# ---------------------------------------------------------------------------


def test_get_ttp_detail_returns_full_card(client):
    """Detail endpoint surfaces every JSON top-level the corpus carries."""
    resp = client.get("/api/ttps/TTP-2026-0004")
    assert resp.status_code == 200
    body = resp.json()
    # Top-level keys from the corpus schema
    for key in (
        "id", "status", "metadata", "identity", "threat_context",
        "mitre_attack", "execution", "detections", "panw_mapping",
        "references",
    ):
        assert key in body, f"missing top-level key {key!r}"
    assert body["id"] == "TTP-2026-0004"
    # Identity carries the human-readable name + summary
    assert "DCSync" in body["identity"]["name"]


def test_get_ttp_detail_reverse_cross_links_adapters(client):
    """TTP-2026-0004 (DCSync) is referenced by mimikatz + rubeus + bloodhound
    in their ``ttp_refs[]`` — the detail endpoint surfaces all three so the
    UI can render the reverse-lookup section."""
    body = client.get("/api/ttps/TTP-2026-0004").json()
    assert "referenced_by_adapters" in body
    ids = {a["adapter_id"] for a in body["referenced_by_adapters"]}
    # The exact set depends on which adapters cite this TTP — assert the
    # known-live ones rather than equality so adding a new citation is
    # additive, not breaking.
    assert "TOOL-MIMIKATZ" in ids
    assert "TOOL-RUBEUS" in ids
    assert "TOOL-BLOODHOUND" in ids
    # Each row carries the metadata the UI card needs
    sample = next(a for a in body["referenced_by_adapters"] if a["adapter_id"] == "TOOL-MIMIKATZ")
    assert sample["tier"] == 3
    assert sample["safety_class"] == "dual-use-lab-only"


def test_get_ttp_detail_referenced_by_empty_when_no_adapters_cite_it(client):
    """A TTP with no adapter citations gets ``referenced_by_adapters: []``."""
    # TTP-2026-0001 (helpdesk MFA reset) — only evilginx2 cites this.
    # Pick a TTP that's NOT cited to test the empty path.
    body = client.get("/api/ttps/TTP-2026-0007").json()
    # TTP-2026-0007 is the AI Access scenario; nothing in tools/packs/
    # references it currently. If that changes, this test guards the
    # empty-list code path with a clearer message.
    refs = body.get("referenced_by_adapters", [])
    assert isinstance(refs, list)


def test_get_ttp_detail_unknown_id_404(client):
    resp = client.get("/api/ttps/TTP-DOES-NOT-EXIST")
    assert resp.status_code == 404
    err = resp.json()["detail"]
    assert err["code"] == "TTP_NOT_FOUND"
    assert "TTP-DOES-NOT-EXIST" in err["detail"]


# ---------------------------------------------------------------------------
# Runs-by-TTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_ttp_runs(session_factory):
    """Seed two runs whose Result rows cite TTP-2026-0004 (DCSync), plus
    one run citing a different TTP and one orphan Result with no ttp_ref
    so the filter logic gets exercised."""
    from models import Run, Result, Scenario

    async def _seed():
        async with session_factory() as db:
            db.add(Scenario(
                scenario_id="SIM-ITDR-002",
                name="DCSync chain",
                plane="ITDR",
                version="1.0",
                status="active",
                uc_ref="UCS-ITDR-02",
                uc_name="Credential Replication",
                tc_ref="TC-ITDR-02",
                tc_name="DCSync Replication Abuse",
                mitre_tactic="TA0006",
                mitre_tactic_name="Credential Access",
                mitre_technique="T1003.006",
                mitre_technique_name="DCSync",
                steps=[],
            ))

            now = datetime.utcnow()

            # Run 1 — newest, 2 expected, 2 observed, fast MTTD
            db.add(Run(
                run_id="r-dcsync-1", scenario_id="SIM-ITDR-002",
                mode="push", status="complete",
                started_at=now - timedelta(minutes=10),
                completed_at=now - timedelta(minutes=5),
            ))
            db.add(Result(
                run_id="r-dcsync-1", plane="ITDR",
                signal_type="BIOC", expected_detection="DRSUAPI from non-DC",
                observed=True, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-001",
                executed_at=now - timedelta(minutes=10),
                observed_at=now - timedelta(minutes=9, seconds=30),
            ))
            db.add(Result(
                run_id="r-dcsync-1", plane="ITDR",
                signal_type="Analytics", expected_detection="Event 4662",
                observed=True, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-002",
                executed_at=now - timedelta(minutes=10),
                observed_at=now - timedelta(minutes=8),
            ))

            # Run 2 — older, 2 expected, 1 observed (partial)
            db.add(Run(
                run_id="r-dcsync-2", scenario_id="SIM-ITDR-002",
                mode="push", status="complete",
                started_at=now - timedelta(hours=2),
                completed_at=now - timedelta(hours=1, minutes=55),
            ))
            db.add(Result(
                run_id="r-dcsync-2", plane="ITDR",
                signal_type="BIOC", expected_detection="DRSUAPI",
                observed=True, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-001",
                executed_at=now - timedelta(hours=2),
                observed_at=now - timedelta(hours=1, minutes=50),
            ))
            db.add(Result(
                run_id="r-dcsync-2", plane="ITDR",
                signal_type="BIOC", expected_detection="Mimikatz dcsync pattern",
                observed=False, ttp_ref="TTP-2026-0004",
                detection_id="BIOC-CRED-DCSYNC-003",
            ))

            # Run 3 — cites a DIFFERENT TTP, must NOT appear in the
            # TTP-2026-0004 history
            db.add(Run(
                run_id="r-other", scenario_id="SIM-ITDR-002",
                mode="push", status="complete",
                started_at=now - timedelta(minutes=20),
            ))
            db.add(Result(
                run_id="r-other", plane="ITDR",
                signal_type="BIOC", expected_detection="LSASS dump",
                observed=True, ttp_ref="TTP-2026-0002",
            ))

            # Orphan Result with no ttp_ref — defensive: don't count it
            db.add(Result(
                run_id="r-dcsync-1", plane="ITDR",
                signal_type="BIOC", expected_detection="untyped step",
                observed=False, ttp_ref=None,
            ))

            await db.commit()

    asyncio.get_event_loop().run_until_complete(_seed())


def test_runs_by_ttp_returns_only_matching_runs(client, seeded_ttp_runs):
    resp = client.get("/api/ttps/TTP-2026-0004/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ttp_id"] == "TTP-2026-0004"
    run_ids = [r["run_id"] for r in body["runs"]]
    assert run_ids == ["r-dcsync-1", "r-dcsync-2"]  # newest-first, no r-other
    assert body["total"] == 2


def test_runs_by_ttp_rolls_up_per_run_counts(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0004/runs").json()
    by_id = {r["run_id"]: r for r in body["runs"]}

    # Run 1 — 2 expected, 2 observed, fastest MTTD = 30 s
    assert by_id["r-dcsync-1"]["expected"] == 2
    assert by_id["r-dcsync-1"]["observed"] == 2
    assert by_id["r-dcsync-1"]["min_mttd_seconds"] == pytest.approx(30, abs=1)
    assert set(by_id["r-dcsync-1"]["detection_ids"]) == {
        "BIOC-CRED-DCSYNC-001", "BIOC-CRED-DCSYNC-002",
    }

    # Run 2 — 2 expected, 1 observed, MTTD = 600 s (one Result lacks
    # observed_at so it doesn't contribute to min_mttd)
    assert by_id["r-dcsync-2"]["expected"] == 2
    assert by_id["r-dcsync-2"]["observed"] == 1
    assert by_id["r-dcsync-2"]["min_mttd_seconds"] == pytest.approx(600, abs=1)


def test_runs_by_ttp_scenario_id_propagated(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0004/runs").json()
    for r in body["runs"]:
        assert r["scenario_id"] == "SIM-ITDR-002"
        assert r["run_status"] == "complete"
        assert isinstance(r["started_at"], str)


def test_runs_by_ttp_empty_when_no_results(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0003/runs").json()
    assert body == {"ttp_id": "TTP-2026-0003", "runs": [], "total": 0}


def test_runs_by_ttp_unknown_ttp_404(client):
    resp = client.get("/api/ttps/TTP-NOPE/runs")
    assert resp.status_code == 404
    err = resp.json()["detail"]
    assert err["code"] == "TTP_NOT_FOUND"


def test_runs_by_ttp_respects_limit(client, seeded_ttp_runs):
    body = client.get("/api/ttps/TTP-2026-0004/runs?limit=1").json()
    assert body["total"] == 1
    # Newest-first — r-dcsync-1 wins
    assert body["runs"][0]["run_id"] == "r-dcsync-1"


# ---------------------------------------------------------------------------
# Authoring endpoints (issue #59)
# ---------------------------------------------------------------------------
#
# These tests redirect the corpus dir to a tmp_path so they don't touch the
# in-repo TTP files. The CORTEXSIM_AUTHORING_ENABLED env gate is flipped
# per-test so we exercise both the enabled and disabled code paths.


import os
import shutil
import json


@pytest.fixture
def authoring_corpus(tmp_path, monkeypatch):
    """Spin up an isolated corpus directory + drafts subdir, point the
    api.ttps module's helpers at it, and enable authoring for the test.
    Yields (corpus_dir, drafts_dir)."""
    corpus = tmp_path / "ttps"
    drafts = corpus / "_drafts"
    drafts.mkdir(parents=True)

    # Seed one already-active TTP we can target with PUT. Cloning a real
    # entry keeps the fixture in lockstep with the schema.
    seed_src = REPO_ROOT / "detection_scanner" / "ttps" / "TTP-2026-0004-dcsync-credential-replication.json"
    seed = json.loads(seed_src.read_text(encoding="utf-8"))
    seed["id"] = "TTP-2026-9001"
    seed["status"] = "active"
    seed["identity"]["name"]    = "Seed Test TTP"
    seed["identity"]["summary"] = (
        "Pre-existing active TTP used by the authoring tests to exercise "
        "the update and promote paths."
    )
    (corpus / "TTP-2026-9001-seed.json").write_text(
        json.dumps(seed, indent=2) + "\n", encoding="utf-8",
    )

    # Schema lives at detection_scanner/schema/ — copy into tmp so the
    # module's _SCHEMA_PATH resolves under our tmp BASE_DIR.
    real_schema = REPO_ROOT / "detection_scanner" / "schema" / "ttp-entry.schema.json"
    (tmp_path / "detection_scanner" / "schema").mkdir(parents=True)
    shutil.copy(real_schema, tmp_path / "detection_scanner" / "schema" / "ttp-entry.schema.json")

    # Move the corpus to the conventional path under our tmp BASE_DIR.
    (tmp_path / "detection_scanner").mkdir(exist_ok=True)
    final_corpus = tmp_path / "detection_scanner" / "ttps"
    if corpus.resolve() != final_corpus.resolve():
        if final_corpus.exists():
            shutil.rmtree(final_corpus)
        shutil.move(str(corpus), str(final_corpus))

    from api import ttps as ttps_module
    monkeypatch.setattr(ttps_module, "_BASE_DIR",   tmp_path)
    monkeypatch.setattr(ttps_module, "_CORPUS_DIR", final_corpus)
    monkeypatch.setattr(ttps_module, "_DRAFTS_DIR", final_corpus / "_drafts")
    monkeypatch.setattr(ttps_module, "_SCHEMA_PATH",
                        tmp_path / "detection_scanner" / "schema" / "ttp-entry.schema.json")
    monkeypatch.setattr(ttps_module, "_ALLOWED_PARENTS", {
        final_corpus.resolve(), (final_corpus / "_drafts").resolve(),
    })
    monkeypatch.setenv("CORTEXSIM_AUTHORING_ENABLED", "true")

    # Load the catalog from the tmp dir so list/detail calls in the
    # same test see the seed and any writes the test makes.
    from engine.ttp_catalog import catalog as ttp_catalog
    ttp_catalog.load(str(final_corpus))

    yield final_corpus, final_corpus / "_drafts"

    # Restore the in-repo catalog so unrelated tests keep their floor.
    ttp_catalog.load(str(REPO_ROOT / "detection_scanner" / "ttps"))


def _make_valid_payload(ttp_id: str) -> dict:
    """Clone a known-good live TTP and overwrite the id/name/summary.

    Chasing every required field by hand is brittle — the schema is
    deep and changes over time. Cloning a real entry guarantees the
    fixture stays in sync with the schema floor `test_every_active_ttp_validates`
    enforces.
    """
    src = REPO_ROOT / "detection_scanner" / "ttps" / "TTP-2026-0004-dcsync-credential-replication.json"
    doc = json.loads(src.read_text(encoding="utf-8"))
    doc["id"] = ttp_id
    doc["status"] = "draft"
    doc["identity"]["name"]    = f"Authoring Test {ttp_id}"
    doc["identity"]["summary"] = (
        "Customer-grade test summary, deliberately longer than twenty "
        "characters so the schema minLength constraint is satisfied "
        "and shorter than the 400-char cap."
    )
    return doc


def test_authoring_get_schema_returns_jsonschema(client):
    resp = client.get("/api/ttps/_schema")
    assert resp.status_code == 200
    body = resp.json()
    # Sanity: it's the TTP schema, not the runs endpoint or detail.
    assert body.get("$schema") or body.get("type") == "object"
    # Identity.summary is a known property of the schema.
    summary_prop = body["properties"]["identity"]["properties"]["summary"]
    assert summary_prop["maxLength"] == 400


def test_authoring_post_requires_env_gate(client):
    resp = client.post("/api/ttps", json=_make_valid_payload("TTP-2026-7777"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "AUTHORING_DISABLED"


def test_authoring_create_draft_writes_to_drafts_dir(client, authoring_corpus):
    _, drafts = authoring_corpus
    payload = _make_valid_payload("TTP-2026-7777")
    payload["status"] = "active"  # we set status=active in payload; backend forces draft
    resp = client.post("/api/ttps", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ttp_id"] == "TTP-2026-7777"
    assert body["status"] == "draft", "create endpoint must force status=draft"
    assert "_drafts" in body["path"]
    # File lands on disk
    files = list(drafts.glob("TTP-2026-7777-*.json"))
    assert len(files) == 1
    # And status got rewritten to draft regardless of payload value
    written = json.loads(files[0].read_text())
    assert written["status"] == "draft"


def test_authoring_create_rejects_conflict(client, authoring_corpus):
    payload = _make_valid_payload("TTP-2026-9001")  # already on disk
    resp = client.post("/api/ttps", json=payload)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "TTP_ID_CONFLICT"


def test_authoring_create_rejects_bad_id_format(client, authoring_corpus):
    payload = _make_valid_payload("not-a-ttp-id")
    payload["id"] = "../../etc/passwd"
    resp = client.post("/api/ttps", json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "TTP_ID_INVALID"


def test_authoring_create_rejects_schema_invalid_payload(client, authoring_corpus):
    payload = _make_valid_payload("TTP-2026-7778")
    # Summary too short to pass the schema's minLength=20.
    payload["identity"]["summary"] = "x"
    resp = client.post("/api/ttps", json=payload)
    assert resp.status_code == 422
    body = resp.json()["detail"]
    assert body["code"] == "TTP_SCHEMA_INVALID"
    assert body["path"] == ["identity", "summary"]


def test_authoring_put_updates_existing_in_place(client, authoring_corpus):
    corpus, _ = authoring_corpus
    payload = _make_valid_payload("TTP-2026-9001")
    payload["status"] = "active"
    payload["identity"]["name"] = "Updated Name"
    resp = client.put("/api/ttps/TTP-2026-9001", json=payload)
    assert resp.status_code == 200, resp.text
    files = list(corpus.glob("TTP-2026-9001-*.json"))
    assert len(files) == 1
    written = json.loads(files[0].read_text())
    assert written["identity"]["name"] == "Updated Name"


def test_authoring_put_rejects_id_mismatch(client, authoring_corpus):
    payload = _make_valid_payload("TTP-2026-9001")
    payload["id"] = "TTP-2026-9999"
    resp = client.put("/api/ttps/TTP-2026-9001", json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "ID_MISMATCH"


def test_authoring_put_404_unknown(client, authoring_corpus):
    payload = _make_valid_payload("TTP-2026-8888")
    resp = client.put("/api/ttps/TTP-2026-8888", json=payload)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "TTP_NOT_FOUND"


def test_authoring_promote_moves_draft_to_active(client, authoring_corpus):
    corpus, drafts = authoring_corpus
    # Create a draft first
    client.post("/api/ttps", json=_make_valid_payload("TTP-2026-7779"))
    assert (list(drafts.glob("TTP-2026-7779-*.json")))

    resp = client.post("/api/ttps/TTP-2026-7779/promote")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["moved"] is True

    # File moved from drafts to active corpus root
    assert not list(drafts.glob("TTP-2026-7779-*.json"))
    actives = list(corpus.glob("TTP-2026-7779-*.json"))
    assert len(actives) == 1
    written = json.loads(actives[0].read_text())
    assert written["status"] == "active"


def test_authoring_promote_is_idempotent_on_active(client, authoring_corpus):
    resp = client.post("/api/ttps/TTP-2026-9001/promote")
    assert resp.status_code == 200
    assert resp.json()["moved"] is False


def test_authoring_reload_endpoint_reindexes(client, authoring_corpus):
    resp = client.post("/api/ttps/_reload")
    assert resp.status_code == 200
    body = resp.json()
    assert body["loaded"] >= 1


def test_authoring_full_lifecycle_e2e(client, authoring_corpus):
    """Create → list (draft visible? no, drafts are skipped) →
    update body → promote → list (now visible as active)."""
    corpus, _ = authoring_corpus
    new_id = "TTP-2026-7780"

    # 1. Create draft
    resp = client.post("/api/ttps", json=_make_valid_payload(new_id))
    assert resp.status_code == 201

    # 2. Draft must NOT appear in the active list (catalog skips _drafts/)
    listing = client.get("/api/ttps").json()
    assert new_id not in [t["id"] for t in listing["ttps"]]

    # 3. Update — bump the summary
    payload = _make_valid_payload(new_id)
    payload["identity"]["summary"] = (
        "Updated summary that still exceeds twenty characters and is "
        "customer-grade per the schema cap."
    )
    resp = client.put(f"/api/ttps/{new_id}", json=payload)
    assert resp.status_code == 200

    # 4. Promote
    resp = client.post(f"/api/ttps/{new_id}/promote")
    assert resp.status_code == 200
    assert resp.json()["moved"] is True

    # 5. Now visible in the list as active
    listing = client.get("/api/ttps").json()
    assert new_id in [t["id"] for t in listing["ttps"]]
    target = next(t for t in listing["ttps"] if t["id"] == new_id)
    assert target["status"] == "active"
    assert "Updated summary" in target["summary"]
