"""Tests for the verdict-scoring service — the wiring that makes
``Run.tc_verdict`` real instead of a column only tests ever wrote.

Two tiers under test:

* **Tier 1** (:func:`score_run_for_run`) — offline, zero outbound calls. Reads
  Run / Scenario / Result rows and writes a verdict.
* **Tier 2** (:func:`verify_run_now`) — runs ``verification_xql`` against a
  tenant. The runner is injected, so nothing here touches the network.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest


INDEX_DIR = Path(__file__).resolve().parents[2] / "docs" / "uc_tc_mapping" / "_v2.2-source"

MTTD_THRESHOLD = {"kpi": "MTTD", "op": "<=", "value": 300, "unit": "s"}


def _seed(SessionLocal, *, run_id="run-1", scenario_id="SIM-EDR-001",
          threshold=None, primary_kpi=None, tc_ref="TC-EDR-01",
          observed_after=None, verification_xql=None, results=1,
          tc_verdict=None, tc_verdict_detail=None, started_at=None):
    """Insert one Scenario + one Run + N Results. Returns nothing; read back
    through the service under test."""
    from models import Result, Run, Scenario

    executed = datetime.utcnow() - timedelta(minutes=10)

    async def _do():
        async with SessionLocal() as db:
            if scenario_id is not None:
                db.add(Scenario(
                    scenario_id=scenario_id, name="Credential Dumping", plane="EDR",
                    version="1.0", status="active",
                    uc_ref="UCS-EDR-01", uc_name="X", tc_ref=tc_ref, tc_name="Y",
                    mitre_tactic="TA0006", mitre_tactic_name="Credential Access",
                    mitre_technique="T1003.001", mitre_technique_name="LSASS Memory",
                    steps=[], threshold=threshold, primary_kpi=primary_kpi,
                ))
            db.add(Run(run_id=run_id, scenario_id=scenario_id or "SIM-GONE",
                       mode="pull", status="complete",
                       started_at=started_at or executed,
                       completed_at=datetime.utcnow(),
                       tc_verdict=tc_verdict, tc_verdict_detail=tc_verdict_detail))
            for i in range(results):
                db.add(Result(
                    run_id=run_id, step_id=f"step-0{i + 1}", plane="EDR",
                    signal_type="BIOC", expected_detection=f"detection {i}",
                    observed=observed_after is not None,
                    executed_at=executed,
                    observed_at=(executed + timedelta(seconds=observed_after)
                                 if observed_after is not None else None),
                    detection_id=f"bioc-{i}",
                    verification_xql=verification_xql,
                    kpi_verdict="pending" if verification_xql else None,
                ))
            await db.commit()

    asyncio.run(_do())


async def _score(SessionLocal, run_id="run-1", source="test"):
    from connectors.service import score_run_for_run
    from models import Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
        score = await score_run_for_run(db, run, source=source)
        return score, run


# ---------------------------------------------------------------------------
# Tier 1 — offline scoring
# ---------------------------------------------------------------------------


def test_scores_pending_when_threshold_declared_but_unmeasured(session_factory):
    """A completed-but-unobserved run must read `pending`, not `pass`.

    This is the state every run is in the instant the agent posts /complete.
    """
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD")
    score, run = asyncio.run(_score(session_factory, source="complete"))

    assert score.verdict == "pending"
    assert "not measured" in score.detail
    assert run.tc_verdict == "pending"
    assert run.tc_verdict_detail["source"] == "complete"
    assert run.tc_verdict_detail["primary"]["verdict"] == "pending"


def test_scores_pass_when_mttd_clears_threshold(session_factory):
    """Observed 60s after execution against a `MTTD <= 300s` bar → pass."""
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60)
    score, run = asyncio.run(_score(session_factory))

    assert score.verdict == "pass"
    assert run.tc_verdict == "pass"
    assert run.tc_verdict_detail["primary"]["actual"] == 60.0
    assert run.tc_verdict_detail["primary"]["expected"] == 300.0


def test_scores_fail_when_mttd_breaches_threshold(session_factory):
    """Observed 20 minutes later against the same bar → fail, and FAIL is
    never clamped away."""
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=1200)
    score, run = asyncio.run(_score(session_factory))

    assert score.verdict == "fail"
    assert run.tc_verdict == "fail"
    assert run.tc_verdict_detail["primary"]["actual"] == 1200.0


def test_scoring_persists_and_is_readable_after_commit(session_factory):
    """The verdict must survive the session that wrote it — /api/uctc and the
    report read it from a later request."""
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60)
    asyncio.run(_score(session_factory))

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Run).where(Run.run_id == "run-1"))).scalar_one()

    assert asyncio.run(_read()).tc_verdict == "pass"


def test_scoring_tolerates_a_missing_scenario_row(session_factory):
    """A run whose Scenario is gone must score, not explode. `/complete` calls
    this on the agent's callback path."""
    _seed(session_factory, scenario_id=None, observed_after=60)
    score, run = asyncio.run(_score(session_factory))

    assert score.verdict == "not_applicable"
    assert run.tc_verdict == "not_applicable"


def test_score_run_safely_swallows_a_scoring_failure(session_factory, monkeypatch):
    """A bug in scoring must never break the mutation path that triggered it."""
    from connectors import service
    from models import Run
    from sqlalchemy import select

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD")

    def _boom(*a, **kw):
        raise RuntimeError("scoring is broken")

    monkeypatch.setattr("engine.verifier.score_run", _boom)

    async def _run():
        async with session_factory() as db:
            run = (await db.execute(select(Run).where(Run.run_id == "run-1"))).scalar_one()
            return await service.score_run_safely(db, run, source="complete")

    assert asyncio.run(_run()) is None


def test_unscoreable_test_case_clamps_pass_to_not_applicable(session_factory, monkeypatch):
    """A run that clears its threshold but binds an index row carrying no
    measurable threshold reports `not_applicable`, never `pass`.

    123 of the 162 scenarios are in this position. A silent green here is the
    exact readout inflation the verifier exists to prevent.
    """
    from engine.uctc_registry import UcTcRegistry

    reg = UcTcRegistry()
    reg.load(str(INDEX_DIR))
    unscoreable = [tc for tc in reg.all_test_cases() if not tc.is_scoreable]
    assert unscoreable, "index snapshot has no qualitative test cases — fixture is stale"
    monkeypatch.setattr("engine.uctc_registry.registry", reg)

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, tc_ref=unscoreable[0].tc_id)
    score, run = asyncio.run(_score(session_factory))

    assert score.verdict == "not_applicable"
    assert "no measurable threshold" in score.detail
    assert run.tc_verdict == "not_applicable"


def test_scoreable_test_case_still_passes(session_factory, monkeypatch):
    """Control for the clamp test — a scoreable index row is left alone."""
    from engine.uctc_registry import UcTcRegistry

    reg = UcTcRegistry()
    reg.load(str(INDEX_DIR))
    scoreable = [tc for tc in reg.all_test_cases() if tc.is_scoreable]
    assert scoreable
    monkeypatch.setattr("engine.uctc_registry.registry", reg)

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, tc_ref=scoreable[0].tc_id)
    score, _run = asyncio.run(_score(session_factory))

    assert score.verdict == "pass"


def test_tc_scoreable_for_degrades_open_when_no_snapshot(monkeypatch):
    """With no index loaded there is no claim to clamp against, so clamping
    would make every run unpassable in a stripped deployment."""
    from engine import verifier
    from engine.uctc_registry import UcTcRegistry

    monkeypatch.setattr("engine.uctc_registry.registry", UcTcRegistry())
    assert verifier.tc_scoreable_for("TC-ANYTHING-99") is True


def test_scoring_emits_a_run_verdict_sse_frame(session_factory):
    """The console's live stream needs the verdict, not just the status."""
    from events import event_bus

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60)

    async def _run():
        q = event_bus.subscribe("run-1")
        try:
            await _score(session_factory)
            frames = []
            while not q.empty():
                frames.append(q.get_nowait())
            return frames
        finally:
            event_bus.unsubscribe("run-1", q)

    frames = asyncio.run(_run())
    verdicts = [f for f in frames if f.get("type") == "run.verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["data"]["tc_verdict"] == "pass"
    assert verdicts[0]["run_id"] == "run-1"


# ---------------------------------------------------------------------------
# Tier 2 — verification against a tenant
# ---------------------------------------------------------------------------


async def _verify(SessionLocal, run_id="run-1", **kw):
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
        outcome = await verify_run_now(db, run, **kw)
        return outcome, run


def _runner(rows: int, calls: list | None = None):
    async def _run(query: str) -> int:
        if calls is not None:
            calls.append(query)
        return rows
    return _run


def test_verify_without_a_tenant_integration_resolves_pending(session_factory):
    """No credential must resolve `pending` — never `pass`, never a raise."""
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, verification_xql="dataset = xdr_data | limit 1")
    outcome, run = asyncio.run(_verify(session_factory))

    assert outcome.reason == "no_tenant_integration"
    assert outcome.tc_verdict == "pending"
    assert outcome.queries_issued == 0
    assert run.tc_verdict == "pending"


def test_verify_marks_results_pass_when_the_query_returns_rows(session_factory):
    from models import Result
    from sqlalchemy import select

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, verification_xql="dataset = xdr_data | limit 1")
    calls: list[str] = []
    outcome, _ = asyncio.run(_verify(session_factory, runner=_runner(3, calls),
                                     tenant="acme-prod"))

    assert outcome.queries_issued == 1
    assert calls == ["dataset = xdr_data | limit 1"]
    assert outcome.tc_verdict == "pass"
    assert outcome.tenant == "acme-prod"

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Result))).scalars().all()

    assert [r.kpi_verdict for r in asyncio.run(_read())] == ["pass"]


def test_verify_marks_results_fail_when_the_query_returns_nothing(session_factory):
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, verification_xql="dataset = xdr_data | limit 1")
    outcome, _ = asyncio.run(_verify(session_factory, runner=_runner(0)))

    assert outcome.tc_verdict == "fail"


def test_verify_is_idempotent_once_terminal(session_factory):
    """A run already carrying a terminal verdict issues zero queries."""
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, verification_xql="dataset = xdr_data",
          tc_verdict="pass", tc_verdict_detail={"verdict": "pass", "detail": "d"})
    calls: list[str] = []
    outcome, _ = asyncio.run(_verify(session_factory, runner=_runner(1, calls)))

    assert outcome.reverified is False
    assert outcome.reason == "already_verified"
    assert calls == []


def test_verify_force_requeries_a_terminal_run(session_factory):
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60, verification_xql="dataset = xdr_data",
          tc_verdict="fail", tc_verdict_detail={"verdict": "fail"})
    calls: list[str] = []
    outcome, _ = asyncio.run(_verify(session_factory, runner=_runner(5, calls),
                                     force=True))

    assert calls == ["dataset = xdr_data"]
    assert outcome.tc_verdict == "pass"


def test_verify_skips_querying_when_no_result_carries_verification_xql(session_factory):
    """149 of 162 scenarios are in this state — they must cost zero queries."""
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          observed_after=60)
    calls: list[str] = []
    outcome, _ = asyncio.run(_verify(session_factory, runner=_runner(1, calls)))

    assert calls == []
    assert outcome.reason == "no_verification_xql"
    assert outcome.tc_verdict == "pass"


def test_verify_records_attempt_bookkeeping(session_factory):
    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          verification_xql="dataset = xdr_data")
    _outcome, run = asyncio.run(_verify(session_factory, runner=_runner(0),
                                        tenant="acme-prod"))

    book = run.tc_verdict_detail["verification"]
    assert book["attempts"] == 1
    assert book["tenant"] == "acme-prod"
    assert book["queries_issued"] == 1
    assert book["last_attempt_at"]


def test_rescoring_preserves_verification_bookkeeping(session_factory):
    """Tier 1 rebuilds tc_verdict_detail wholesale and runs far more often than
    Tier 2. If it dropped the bookkeeping, `attempts` would reset to 0 on every
    reconcile and a run could evade the sweep's attempt cap forever."""
    _seed(session_factory, verification_xql="dataset = xdr_data",
          tc_verdict="pending",
          tc_verdict_detail={"verdict": "pending",
                             "verification": {"attempts": 2, "tenant": "acme-prod"}})
    _, run = asyncio.run(_score(session_factory, source="reconcile:manual"))

    assert run.tc_verdict_detail["verification"]["attempts"] == 2
    assert run.tc_verdict_detail["source"] == "reconcile:manual"


def test_verify_budget_stops_issuing_queries(session_factory):
    """The budget is a hard stop; the results it could not reach stay pending,
    which is honest — a spent budget is not a detection failure."""
    from connectors.service import _new_budget
    from models import Result
    from sqlalchemy import select

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          verification_xql="dataset = xdr_data", results=4)
    calls: list[str] = []
    budget = _new_budget(2)
    asyncio.run(_verify(session_factory, runner=_runner(1, calls), budget=budget))

    assert len(calls) == 2
    assert budget["remaining"] == 0

    async def _read():
        async with session_factory() as db:
            return (await db.execute(select(Result).order_by(Result.id))).scalars().all()

    verdicts = [r.kpi_verdict for r in asyncio.run(_read())]
    assert verdicts == ["pass", "pass", "pending", "pending"]


def test_quota_error_trips_the_breaker_and_stops_further_queries(session_factory):
    """XQL quota belongs to the customer's own analysts. One 429 stops the
    whole cycle rather than hammering the tenant result by result."""
    from connectors.service import _new_budget
    from integrations.xsiam.exceptions import XsiamQuotaError

    _seed(session_factory, threshold=MTTD_THRESHOLD, primary_kpi="MTTD",
          verification_xql="dataset = xdr_data", results=5)
    calls: list[str] = []

    async def _quota_runner(query: str) -> int:
        calls.append(query)
        raise XsiamQuotaError("XQL_0003 quota exceeded")

    budget = _new_budget(50)
    outcome, run = asyncio.run(_verify(session_factory, runner=_quota_runner,
                                       budget=budget, tenant="acme-prod"))

    assert len(calls) == 1, "breaker must stop after the first quota error"
    assert budget["tripped"] is True
    assert outcome.reason == "quota_exhausted"
    # A tenant outage is not a detection failure.
    assert run.tc_verdict == "pending"


def test_verify_does_not_reuse_the_reconcile_credential_kind(session_factory):
    """A tenant configured for reconcile (kind 'xsiam') must NOT make the run
    verifiable — verification needs kind 'xsiam_tenant'."""
    from connectors.service import resolve_tenant_runner
    from security.credentials import CredentialStore

    async def _do():
        async with session_factory() as db:
            store = CredentialStore(db)
            await store.put_integration(
                name="xsiam-prod", kind="xsiam", plaintext_secret="K",
                config={"fqdn": "api-t.xdr.us.paloaltonetworks.com", "api_key_id": "1"})
            await db.commit()
            return await resolve_tenant_runner(db)

    runner, tenant = asyncio.run(_do())
    assert runner is None and tenant is None


def test_resolve_tenant_runner_rejects_an_unknown_named_integration(session_factory):
    """A typo'd integration name is an operator error, not a pending verdict."""
    from connectors.service import VerifyError, resolve_tenant_runner
    from security.credentials import CredentialStore

    async def _do():
        async with session_factory() as db:
            store = CredentialStore(db)
            await store.put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="K",
                config={"base_url": "https://api-acme.xdr.us.paloaltonetworks.com",
                        "region": "us", "api_key_id": "1"})
            await db.commit()
            return await resolve_tenant_runner(db, integration="nope")

    with pytest.raises(VerifyError) as exc:
        asyncio.run(_do())
    assert exc.value.code == "INTEGRATION_NOT_FOUND"


# ---------------------------------------------------------------------------
# Tier 2 — the run's own absolute window (P-5)
# ---------------------------------------------------------------------------


class _WindowClient:
    def __init__(self):
        self.timeframes = []

    async def run_xql(self, query, timeframe, **kw):
        from tests.connectors.test_xql_envelope import _reply
        self.timeframes.append(timeframe)
        return _reply(1)


def _wire_tenant(SessionLocal, monkeypatch):
    from integrations.xsiam import loader
    from security.credentials import CredentialStore

    client = _WindowClient()

    async def _fake_load(db, name):
        return client
    monkeypatch.setattr(loader, "load_xsiam_client", _fake_load)

    async def _do():
        async with SessionLocal() as db:
            await CredentialStore(db).put_integration(
                name="acme", kind="xsiam_tenant", plaintext_secret="K" * 40,
                config={"base_url": "https://api-t.xdr.us.paloaltonetworks.com",
                        "region": "us", "api_key_id": "1", "capabilities": ["xql"]})
            await db.commit()
    asyncio.run(_do())
    return client


def _set_run(SessionLocal, run_id, *, started, completed, window=None, mode=None,
             status=None, steps=None):
    from models import Run, Scenario
    from sqlalchemy import select

    async def _do():
        async with SessionLocal() as db:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            run.started_at, run.completed_at = started, completed
            if mode is not None:
                run.mode = mode
            if status is not None:
                run.status = status
            if window is not None or steps is not None:
                sc = (await db.execute(select(Scenario).where(
                    Scenario.scenario_id == run.scenario_id))).scalar_one()
                if window is not None:
                    sc.correlation_window_seconds = window
                if steps is not None:
                    sc.steps = steps
            await db.commit()
    asyncio.run(_do())


def _verification_book(SessionLocal, run_id="run-1"):
    from models import Run
    from sqlalchemy import select

    async def _do():
        async with SessionLocal() as db:
            r = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            return r.tc_verdict_detail["verification"]
    return asyncio.run(_do())


async def _verify_win(SessionLocal, run_id="run-1", **kw):
    from connectors.service import verify_run_now
    from models import Run
    from sqlalchemy import select

    async with SessionLocal() as db:
        run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
        out = await verify_run_now(db, run, **kw)
        await db.commit()
        return out


def test_late_verify_queries_the_runs_window_not_the_last_hour(session_factory, monkeypatch):
    from connectors.verify_window import run_timeframe

    started = datetime.utcnow() - timedelta(hours=2)
    completed = started + timedelta(minutes=2)
    _seed(session_factory, verification_xql="dataset = xdr_data | limit 1")
    _set_run(session_factory, "run-1", started=started, completed=completed, window=3600)
    client = _wire_tenant(session_factory, monkeypatch)

    asyncio.run(_verify_win(session_factory))

    assert client.timeframes
    for tf in client.timeframes:
        assert "relativeTime" not in tf
        want = run_timeframe(started, completed, 3600, now=datetime.utcnow())
        assert tf["from"] == want["from"]
        assert abs(tf["to"] - want["to"]) < 5000  # `to` is clamped to now (here: not clamped)


def test_explicit_timeframe_seconds_still_wins(session_factory, monkeypatch):
    _seed(session_factory, verification_xql="dataset = xdr_data | limit 1")
    client = _wire_tenant(session_factory, monkeypatch)
    asyncio.run(_verify_win(session_factory, timeframe_seconds=600))
    assert client.timeframes == [{"relativeTime": 600000}]


def test_sweep_queries_each_runs_window(session_factory, monkeypatch):
    from connectors.service import auto_verify_once

    now = datetime.utcnow()
    s1, s2 = now - timedelta(hours=3), now - timedelta(hours=1)
    _seed(session_factory, run_id="run-1", verification_xql="dataset = a | limit 1",
          started_at=s1)
    _seed(session_factory, run_id="run-2", scenario_id=None,
          verification_xql="dataset = a | limit 1", started_at=s2)
    _set_run(session_factory, "run-1", started=s1, completed=s1 + timedelta(minutes=1), window=3600)
    _set_run(session_factory, "run-2", started=s2, completed=s2 + timedelta(minutes=1))
    client = _wire_tenant(session_factory, monkeypatch)

    async def _do():
        async with session_factory() as db:
            return await auto_verify_once(db, lookback_seconds=86400)
    asyncio.run(_do())

    froms = {tf["from"] for tf in client.timeframes}
    assert len(froms) == 2
    from models import Run
    from sqlalchemy import select

    async def _win():
        async with session_factory() as db:
            r = (await db.execute(select(Run).where(Run.run_id == "run-1"))).scalar_one()
            return r.tc_verdict_detail["verification"]["window"]
    assert asyncio.run(_win()) in client.timeframes
    assert all("relativeTime" not in tf for tf in client.timeframes)


def test_missing_scenario_falls_back_to_default_window(session_factory, monkeypatch):
    from config import settings

    started = datetime.utcnow() - timedelta(minutes=30)
    _seed(session_factory, scenario_id=None, verification_xql="dataset = a | limit 1")
    _set_run(session_factory, "run-1", started=started, completed=None)
    client = _wire_tenant(session_factory, monkeypatch)
    asyncio.run(_verify_win(session_factory))

    tf = client.timeframes[0]
    # completed None -> to = min(started + default window, now) = now (30 min in)
    assert tf["to"] - tf["from"] <= (settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS + 300) * 1000
    assert tf["to"] - tf["from"] >= (30 * 60 + 300) * 1000 - 5000


# ---------------------------------------------------------------------------
# Push runs: SimCore never learns when the bundle ran (C1)
# ---------------------------------------------------------------------------


def test_push_run_verified_late_keeps_the_relative_lookback(session_factory, monkeypatch):
    """A push run reaches `staged` at bundle generation; the bundle runs offline
    later. Querying [staging - 5 min, staging + window] finds nothing once the
    bundle ran after it, so a push run keeps the relativeTime lookback and says why."""
    from config import settings

    staged = datetime.utcnow() - timedelta(hours=3)
    _seed(session_factory, verification_xql="dataset = xdr_data | limit 1")
    _set_run(session_factory, "run-1", started=staged, completed=staged,
             window=3600, mode="push", status="staged")
    client = _wire_tenant(session_factory, monkeypatch)

    asyncio.run(_verify_win(session_factory))

    want = {"relativeTime": settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS * 1000}
    assert client.timeframes and all(tf == want for tf in client.timeframes)
    book = _verification_book(session_factory)
    assert book["window"] is None
    assert book["window_reason"] == "push_execution_time_unknown"


def test_pull_run_records_its_window_and_no_reason(session_factory, monkeypatch):
    started = datetime.utcnow() - timedelta(hours=3)
    _seed(session_factory, verification_xql="dataset = xdr_data | limit 1")
    _set_run(session_factory, "run-1", started=started,
             completed=started + timedelta(minutes=2), window=3600)
    client = _wire_tenant(session_factory, monkeypatch)

    asyncio.run(_verify_win(session_factory))

    assert all("relativeTime" not in tf for tf in client.timeframes)
    book = _verification_book(session_factory)
    assert book["window"] in client.timeframes
    assert "window_reason" not in book


def test_sweep_keeps_the_relative_lookback_for_a_push_run(session_factory, monkeypatch):
    from config import settings
    from connectors.service import auto_verify_once

    now = datetime.utcnow()
    staged, started = now - timedelta(hours=3), now - timedelta(hours=2)
    _seed(session_factory, run_id="run-push", verification_xql="dataset = a | limit 1",
          started_at=staged)
    _seed(session_factory, run_id="run-pull", scenario_id=None,
          verification_xql="dataset = a | limit 1", started_at=started)
    _set_run(session_factory, "run-push", started=staged, completed=staged,
             window=3600, mode="push", status="staged")
    _set_run(session_factory, "run-pull", started=started,
             completed=started + timedelta(minutes=1))
    client = _wire_tenant(session_factory, monkeypatch)

    async def _do():
        async with session_factory() as db:
            return await auto_verify_once(db, lookback_seconds=86400)
    asyncio.run(_do())

    relative = {"relativeTime": settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS * 1000}
    assert relative in client.timeframes
    push_book = _verification_book(session_factory, "run-push")
    assert push_book["window"] is None
    assert push_book["window_reason"] == "push_execution_time_unknown"
    pull_book = _verification_book(session_factory, "run-pull")
    assert pull_book["window"] in client.timeframes
    assert "relativeTime" not in pull_book["window"]


# ---------------------------------------------------------------------------
# Back-dated record bodies widen the lead (I4)
# ---------------------------------------------------------------------------


def _eal_step(step_id, offset):
    return {"step_id": step_id, "channel": "eal",
            "eal": {"plugin": "idp_signin_emulator",
                    "params": {"event_time_offset_seconds": offset}}}


def test_back_dated_eal_bodies_open_the_window_earlier(session_factory):
    """An eal step with event_time_offset_seconds back-dates its record body
    times; a collector that takes `_time` from the body puts those rows before
    `started_at`. The most negative offset widens the lead."""
    from connectors.service import _run_window
    from models import Run
    from sqlalchemy import select

    started = datetime(2026, 11, 11, 10, 0, 0)
    now = datetime(2026, 11, 11, 14, 0, 0)
    _seed(session_factory, run_id="run-plain", scenario_id="SIM-PLAIN")
    _seed(session_factory, run_id="run-dated", scenario_id="SIM-DATED")
    _set_run(session_factory, "run-plain", started=started, completed=started,
             window=3600, steps=[{"step_id": "step-01", "command": "id"}])
    _set_run(session_factory, "run-dated", started=started, completed=started,
             window=3600, steps=[_eal_step("step-01", -1500), _eal_step("step-02", -300),
                                 _eal_step("step-03", "-9000"),
                                 {"step_id": "step-04", "command": "id"}])

    async def _windows():
        async with session_factory() as db:
            out = {}
            for rid in ("run-plain", "run-dated"):
                run = (await db.execute(select(Run).where(Run.run_id == rid))).scalar_one()
                out[rid] = await _run_window(db, run, now=now)
            return out
    w = asyncio.run(_windows())

    assert w["run-plain"]["from"] - w["run-dated"]["from"] == 1500 * 1000
    assert w["run-plain"]["to"] == w["run-dated"]["to"]


def test_dry_run_eal_run_persists_pending_and_the_report_says_so(session_factory):
    """Every Result `not_executed` (an eal run that stayed dry-run): the stored
    verdict is `pending` with `reason: not_executed`, and the run report reads
    PENDING — never "NOT SCOREABLE — the test case carries no measurable
    threshold", which is false for a run where nothing ran."""
    from api.runs import _verdict_section
    from models import Result
    from sqlalchemy import select

    _seed(session_factory, verification_xql="dataset = xdr_data", results=2)

    async def _mark_dry_run():
        async with session_factory() as db:
            for r in (await db.execute(select(Result))).scalars().all():
                r.outcome, r.outcome_reason = "not_executed", "EAL_DRY_RUN_NO_CONSENT"
            await db.commit()
    asyncio.run(_mark_dry_run())

    score, run = asyncio.run(_score(session_factory, source="complete"))

    assert run.tc_verdict == "pending"
    assert run.tc_verdict_detail["reason"] == "not_executed"
    section = "\n".join(_verdict_section(run))
    assert "PENDING" in section
    assert "EAL_DRY_RUN_NO_CONSENT" in section
    assert "NOT SCOREABLE" not in section
