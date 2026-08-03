"""Shared measurement service — used by the API handlers AND the auto loop.

Two halves, both shared so an on-demand endpoint and a background sweep can
never drift apart:

* **Reconcile** — ``POST /api/runs/{id}/reconcile`` and the auto-reconcile
  sweep. Resolves the integration credential, pulls observed alerts, matches
  them to seeded results, sets ``observed_at`` (and therefore MTTD).
* **Score / verify** — turns those observations into a run-level
  ``Run.tc_verdict``. This is the half that was authored and then never
  invoked: ``verifier.verify_run`` had no production caller, so ``tc_verdict``
  was only ever written by tests while ``/api/uctc`` and the POV report both
  read the column. See :func:`score_run_for_run` for the tiering.

Functions return structured results and raise plain exceptions (the API layer
maps them to HTTP; the loop logs and moves on).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from connectors import ConnectorConfig, get_connector, reconcile
from connectors.base import HttpFetcher
from events import event_bus
from models import Result, Run, Scenario
from security.credentials import CredentialStore

logger = logging.getLogger("cortexsim.connectors.service")


class ReconcileError(Exception):
    """Raised when a reconcile cannot proceed (unknown connector / no
    integration / pull failure). Carries a machine ``code`` for the API."""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


@dataclass
class ReconcileOutcome:
    summary: dict[str, Any]
    newly_matched: int


async def reconcile_run(
    db: AsyncSession,
    run: Run,
    results: list[Result],
    *,
    connector_kind: str,
    integration_name: Optional[str] = None,
    window_seconds: int = 3600,
    fetcher: Optional[HttpFetcher] = None,
) -> ReconcileOutcome:
    """Resolve the integration credential, pull observations for the run's
    window, correlate to ``results``, persist matches, emit SSE.

    Raises :class:`ReconcileError` (never HTTP) on any failure.
    """
    conn = get_connector(connector_kind, fetcher=fetcher)
    if conn is None:
        raise ReconcileError("CONNECTOR_NOT_FOUND",
                             f"unknown connector '{connector_kind}'")

    store = CredentialStore(db)
    integrations = await store.list_integrations(kind=connector_kind)
    if not integrations:
        raise ReconcileError("NO_INTEGRATION",
                             f"configure an integration of kind '{connector_kind}' first")
    integ = (next((i for i in integrations if i.name == integration_name), integrations[0])
             if integration_name else integrations[0])
    try:
        secret = await store.get_integration_secret(integ.name)
    except Exception as e:  # noqa: BLE001
        raise ReconcileError("CREDENTIAL_ERROR", str(e))

    cfg = ConnectorConfig(integration_name=integ.name, config=integ.config or {}, secret=secret)

    exec_times = [r.executed_at for r in results if r.executed_at]
    since = (min(exec_times) if exec_times
             else (run.started_at or datetime.utcnow() - timedelta(hours=1)))
    until = datetime.utcnow() + timedelta(minutes=5)

    pull = conn.pull(cfg, since=since, until=until)
    if not pull.ok:
        await store.mark_integration_verified(integ.name, ok=False, error=pull.error)
        await db.commit()
        raise ReconcileError("CONNECTOR_PULL_FAILED", pull.error or "pull failed",
                             detail=pull.detail)

    await store.mark_integration_verified(integ.name, ok=True, error=None)
    verdicts = reconcile(results, pull.observations, window_seconds=window_seconds)
    summary, newly = await apply_verdicts(db, run.run_id, results, verdicts,
                                          source=f"{connector_kind}:{integ.name}")
    summary["pulled"] = len(pull.observations)
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]
    return ReconcileOutcome(summary=summary, newly_matched=newly)


async def apply_verdicts(
    db: AsyncSession, run_id: str, results: list[Result], verdicts: list, source: str
) -> tuple[dict[str, Any], int]:
    """Persist matched verdicts (observed/observed_at), emit SSE, summarise.

    Returns ``(summary, newly_matched)``.
    """
    by_id = {r.id: r for r in results}
    matched = 0
    for v in verdicts:
        if not v.matched:
            continue
        result = by_id.get(v.result_id)
        if result is None:
            continue
        result.observed = True
        result.observed_at = v.observed_at
        note = (f"auto-validated via {source} "
                f"(matched on {', '.join(v.matched_on)}; "
                f"alert={v.alert_external_id or v.alert_name or 'n/a'})")
        result.notes = ((result.notes + " | ") if result.notes else "") + note
        matched += 1

    await db.commit()

    for v in verdicts:
        if not v.matched:
            continue
        result = by_id.get(v.result_id)
        if result is None:
            continue
        try:
            await event_bus.publish(run_id, {
                "type": "result.observed", "run_id": run_id,
                "data": {"result_id": result.id, "observed": True, "plane": result.plane,
                         "mttd_seconds": result.mttd_seconds, "source": source,
                         "matched_on": v.matched_on},
            })
        except Exception:  # pragma: no cover - defensive
            logger.exception("event_bus publish failed result_id=%d", result.id)

    # THE primary scoring hook. This is where observed_at — and therefore
    # mttd_seconds, the only KPI the engine measures natively — becomes real,
    # and it is the single funnel for all three ingest paths (manual
    # /observations, credential-backed /reconcile, and the auto sweep). One
    # hook here scores every one of them, with no new scheduling and no
    # outbound call: Tier 1 reads only rows CortexSim already owns.
    summary = efficacy_summary(results, matched, source)
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is not None:
        summary["tc_verdict"] = await score_run_safely(
            db, run, source=f"reconcile:{source}", results=results)
    return summary, matched


def efficacy_summary(results: list[Result], newly_matched: int, source: str) -> dict[str, Any]:
    total = len(results)
    observed = sum(1 for r in results if r.observed)
    mttds = [r.mttd_seconds for r in results if r.mttd_seconds is not None]
    return {
        "source": source,
        "total_expected": total,
        "observed": observed,
        "newly_matched": newly_matched,
        "coverage_pct": round(observed / total * 100, 1) if total else 0.0,
        "mttd": {
            "count": len(mttds),
            "avg_seconds": round(sum(mttds) / len(mttds), 1) if mttds else None,
            "min_seconds": round(min(mttds), 1) if mttds else None,
            "max_seconds": round(max(mttds), 1) if mttds else None,
        },
    }


# ---------------------------------------------------------------------------
# Tier 1 — offline scoring (zero outbound calls)
# ---------------------------------------------------------------------------
#
# Deliberately NOT flag-gated. It issues no HTTP, touches no tenant, and reads
# only Run / Scenario / Result rows CortexSim already owns — gating it would
# gate honesty, not risk. Only 13 scenarios in the corpus carry any
# `verification_xql`; for every other scenario Tier 2 can never contribute
# anything and THIS is the whole measurement, so making them depend on an
# outbound code path would be an engineering mistake.


# Verdicts that are final. Once a run reaches one of these the sweep leaves it
# alone; only an explicit ``?force=true`` re-verifies.
_TERMINAL_VERDICTS = frozenset({"pass", "fail", "not_applicable"})


async def _publish_verdict(run_id: str, verdict: Optional[str], detail: dict[str, Any],
                           source: str) -> None:
    """Emit a ``run.verdict`` frame. Never lets a bus error reach the caller —
    a publish failure must not undo the scoring that triggered it."""
    try:
        await event_bus.publish(run_id, {
            "type": "run.verdict", "run_id": run_id,
            "data": {"tc_verdict": verdict, "detail": detail.get("detail"),
                     "primary": detail.get("primary"), "source": source},
        })
    except Exception:  # pragma: no cover - defensive
        logger.exception("event_bus publish failed run_id=%s (run.verdict)", run_id)


async def score_run_for_run(
    db: AsyncSession, run: Run, *, source: str,
    results: Optional[list[Result]] = None,
    extra_detail: Optional[dict[str, Any]] = None,
    commit: bool = True,
) -> Any:
    """Score one run against its scenario's threshold and persist the verdict.

    Pure-local: no credential, no network. Returns the :class:`verifier.RunScore`.

    A run whose Scenario row is gone (a stale run_id, a DB reset, a test
    fixture) scores against no threshold rather than exploding — the caller of
    this is a mutation path that must never fail because scoring failed.
    """
    from engine import verifier  # noqa: PLC0415

    if results is None:
        results = list((await db.execute(
            select(Result).where(Result.run_id == run.run_id)
        )).scalars().all())

    scenario = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == run.scenario_id)
    )).scalar_one_or_none()

    mttds = [r.mttd_seconds for r in results if r.mttd_seconds is not None]
    score = verifier.score_run(
        results,
        threshold=getattr(scenario, "threshold", None),
        primary_kpi=getattr(scenario, "primary_kpi", None),
        mttd_seconds=(sum(mttds) / len(mttds)) if mttds else None,
        tc_scoreable=verifier.tc_scoreable_for(getattr(scenario, "tc_ref", None)),
    )

    detail = score.to_dict()
    detail["source"] = source
    detail["scored_at"] = datetime.utcnow().isoformat()
    # Carry the verification bookkeeping across a re-score. Tier 1 runs far
    # more often than Tier 2 (every reconcile, every completion) and rebuilds
    # `tc_verdict_detail` wholesale; dropping the block here would reset
    # `attempts` to 0 on each reconcile and let a run evade the sweep's
    # attempt cap forever.
    carried = _bookkeeping(run)
    if carried:
        detail["verification"] = carried
    if extra_detail:
        detail.update(extra_detail)

    run.tc_verdict = score.verdict
    run.tc_verdict_detail = detail
    if commit:
        await db.commit()

    await _publish_verdict(run.run_id, score.verdict, detail, source)
    logger.info("run=%s scored verdict=%s source=%s (%s)",
                run.run_id, score.verdict, source, score.detail)
    return score


async def score_run_safely(
    db: AsyncSession, run: Run, *, source: str,
    results: Optional[list[Result]] = None,
) -> Optional[str]:
    """:func:`score_run_for_run` that can never break its caller.

    Used from ``/complete`` and from :func:`apply_verdicts` — both mutation
    paths whose real work is already committed by the time scoring runs. A
    scoring bug must degrade to "no verdict", never to a failed run or a 500
    on an agent callback.
    """
    try:
        score = await score_run_for_run(db, run, source=source, results=results)
        return score.verdict
    except Exception:  # noqa: BLE001 — scoring is advisory to its caller
        logger.exception("tc_verdict scoring failed run_id=%s source=%s",
                         getattr(run, "run_id", "?"), source)
        try:
            await db.rollback()
        except Exception:  # pragma: no cover - defensive
            logger.exception("rollback after failed scoring also failed")
        return None


# ---------------------------------------------------------------------------
# Tier 2 — verification against a tenant (outbound XQL, opt-in)
# ---------------------------------------------------------------------------
#
# Only 35 seeded results across 13 scenarios carry a ``verification_xql``, and
# each one costs a start_xql_query + up to 20 polls. That is a different animal
# from a reconcile (one POST per run) against a metered resource the customer's
# own analysts share, so it gets its own flag, its own credential kind and its
# own budget. Note the credential kinds are NOT interchangeable: reconcile
# resolves kind ``xsiam`` ({fqdn, api_key_id}); verification needs kind
# ``xsiam_tenant`` ({base_url, region, api_key_id}). An operator who configured
# reconcile has not thereby authorised XQL.


class VerifyError(Exception):
    """Raised when a verification cannot proceed. Carries a machine ``code``
    the API layer maps to a status."""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class _BudgetExhausted(RuntimeError):
    """Internal: the per-sweep query budget is spent. Raised INTO
    ``verifier.verify_results``, which catches any runner exception and marks
    that one result ``pending`` — never ``fail``. So a spent budget degrades
    honestly instead of manufacturing false red."""


@dataclass
class VerifyOutcome:
    run_id: str
    tc_verdict: Optional[str]
    detail: dict[str, Any] = field(default_factory=dict)
    tenant: Optional[str] = None
    queries_issued: int = 0
    reverified: bool = True
    reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tc_verdict": self.tc_verdict,
            "detail": self.detail,
            "tenant": self.tenant,
            "queries_issued": self.queries_issued,
            "reverified": self.reverified,
            "reason": self.reason,
        }


async def resolve_tenant_runner(
    db: AsyncSession, *, integration: Optional[str] = None,
    timeframe_seconds: int = 3600,
) -> tuple[Optional[Any], Optional[str]]:
    """Build a read-only XQL row-count runner from a registered tenant credential.

    Returns ``(None, None)`` when no ``xsiam_tenant`` integration exists at all
    and ``(None, name)`` when one exists but its client cannot be built — in
    both cases the caller resolves ``pending``. "No tenant wired" is an honest
    verdict, not a server error, so neither raises. Only an explicitly *named*
    integration that does not exist raises, because that is an operator typo.
    """
    from engine.verifier import xsiam_query_runner  # noqa: PLC0415
    from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client  # noqa: PLC0415

    store = CredentialStore(db)
    integrations = await store.list_integrations(kind=XSIAM_KIND)
    if not integrations:
        return None, None
    integ = (next((i for i in integrations if i.name == integration), None)
             if integration else integrations[0])
    if integ is None:
        raise VerifyError("INTEGRATION_NOT_FOUND",
                          f"no {XSIAM_KIND} integration named '{integration}'")
    try:
        client = await load_xsiam_client(db, integ.name)
    except Exception as exc:  # noqa: BLE001 — resolves to pending, not a 500
        logger.warning("could not build XSIAM client for '%s': %s", integ.name, exc)
        return None, integ.name
    return xsiam_query_runner(client, {"relativeTime": timeframe_seconds * 1000}), integ.name


def _new_budget(max_queries: int) -> dict[str, Any]:
    return {"remaining": max(0, int(max_queries)), "issued": 0, "tripped": False}


def _budgeted(runner: Any, budget: dict[str, Any]) -> Any:
    """Wrap a query runner in a hard query cap + a quota circuit breaker.

    The cap lives here rather than inside ``verifier.verify_results`` so the
    verifier stays a pure function of (results, runner) — and so a tripped
    breaker stops further HTTP for the WHOLE sweep, not just the current run.
    """
    from integrations.xsiam.exceptions import XsiamQuotaError  # noqa: PLC0415

    async def _run(query: str) -> int:
        if budget["tripped"] or budget["remaining"] <= 0:
            raise _BudgetExhausted("verification query budget exhausted")
        budget["remaining"] -= 1
        budget["issued"] += 1
        try:
            return await runner(query)
        except XsiamQuotaError:
            # The customer's analysts share this quota. Stop issuing queries
            # for the rest of the cycle and make sure the DC can see why.
            budget["tripped"] = True
            logger.warning("XQL quota exhausted on tenant '%s' — verification "
                           "circuit-broken for this cycle", budget.get("tenant"))
            raise

    return _run


def _bookkeeping(run: Run) -> dict[str, Any]:
    """Read this run's verification bookkeeping out of ``tc_verdict_detail``.

    It lives in the JSON blob rather than in dedicated ``runs`` columns because
    adding columns is a migration this change does not own. The consequence is
    that attempt-cap and backoff are Python-side filters over a SQL-filtered
    candidate set (``tc_verdict`` NULL-or-pending), not a single SQL predicate.
    """
    detail = run.tc_verdict_detail
    book = detail.get("verification") if isinstance(detail, dict) else None
    return dict(book) if isinstance(book, dict) else {}


# Only a run that has stopped executing is worth spending tenant quota on.
#   complete / failed — execution finished; whatever telemetry exists, exists.
#   staged           — push-mode terminal. The DC ran the bundle offline, so
#                      querying the tenant is the ONLY way to learn the outcome.
# pending / running are deliberately excluded: the steps are still emitting, so
# an empty answer means "too early", not "the detection failed" — and recording
# that as a verdict is the false red this whole harness is careful to avoid.
# aborted is excluded because the operator killed the run on purpose.
_VERIFIABLE_STATUSES = frozenset({"complete", "failed", "staged"})


def _verify_eligible(run: Run, *, max_attempts: int, backoff_seconds: int,
                     now: datetime) -> Optional[str]:
    """``None`` when the run should be verified, else why it was skipped."""
    if run.status not in _VERIFIABLE_STATUSES:
        return "run_not_finished"
    if run.tc_verdict in _TERMINAL_VERDICTS:
        return "terminal_verdict"
    book = _bookkeeping(run)
    attempts = int(book.get("attempts") or 0)
    if attempts >= max_attempts:
        # A detection that has not landed in the tenant after N sweeps is not
        # going to; re-querying forever is the defect this cap exists to stop.
        return "attempt_cap"
    last = book.get("last_attempt_at")
    if attempts and last:
        try:
            when = datetime.fromisoformat(last)
        except (TypeError, ValueError):
            return None
        delay = min(backoff_seconds * (2 ** (attempts - 1)), 4 * 3600)
        if now < when + timedelta(seconds=delay):
            return "backoff"
    return None


async def verify_run_now(
    db: AsyncSession, run: Run, *,
    integration: Optional[str] = None,
    timeframe_seconds: Optional[int] = None,
    max_queries: Optional[int] = None,
    force: bool = False,
    runner: Optional[Any] = None,
    tenant: Optional[str] = None,
    budget: Optional[dict[str, Any]] = None,
    source: str = "verify",
) -> VerifyOutcome:
    """Verify one run's ``verification_xql`` results, then score it.

    ``runner`` is injectable so tests never reach the network — the same
    pattern ``reconcile_run(fetcher=...)`` uses.

    Idempotent by default: a run already carrying a terminal verdict returns it
    with ``reverified=False`` and issues zero queries. Every path — no
    credential, no verifiable results, a spent budget — still falls through to
    Tier-1 scoring, so this never returns without a verdict and never returns
    ``pass`` on the strength of a query it did not run.
    """
    results = list((await db.execute(
        select(Result).where(Result.run_id == run.run_id)
    )).scalars().all())

    if run.tc_verdict in _TERMINAL_VERDICTS and not force:
        return VerifyOutcome(run.run_id, run.tc_verdict,
                             detail=run.tc_verdict_detail or {},
                             tenant=None, queries_issued=0, reverified=False,
                             reason="already_verified")

    verifiable = [r for r in results if getattr(r, "verification_xql", None)]
    if not verifiable:
        score = await score_run_for_run(db, run, source=source, results=results)
        return VerifyOutcome(run.run_id, score.verdict, detail=run.tc_verdict_detail or {},
                             reason="no_verification_xql")

    if runner is None:
        runner, tenant = await resolve_tenant_runner(
            db, integration=integration,
            timeframe_seconds=timeframe_seconds or settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS,
        )
    if runner is None:
        # No credential -> pending, never pass. The 35 seeded results keep the
        # `pending` kpi_verdict the orchestrator gave them, score_run's
        # precedence yields `pending`, and there is no path to `pass` without
        # a measurement.
        score = await score_run_for_run(db, run, source=source, results=results)
        return VerifyOutcome(run.run_id, score.verdict, detail=run.tc_verdict_detail or {},
                             tenant=tenant, reason="no_tenant_integration")

    from engine import verifier  # noqa: PLC0415

    if budget is None:
        budget = _new_budget(
            max_queries if max_queries is not None
            else settings.CORTEXSIM_AUTO_VERIFY_MAX_QUERIES_PER_SWEEP)
    budget.setdefault("tenant", tenant)

    before = budget["issued"]
    counts = await verifier.verify_results(results, _budgeted(runner, budget))
    issued = budget["issued"] - before

    book = _bookkeeping(run)
    book.update({
        "attempts": int(book.get("attempts") or 0) + (1 if issued else 0),
        "last_attempt_at": datetime.utcnow().isoformat() if issued else book.get("last_attempt_at"),
        "tenant": tenant,
        "queries_issued": issued,
        "counts": counts,
        "quota_tripped": bool(budget.get("tripped")),
    })
    score = await score_run_for_run(db, run, source=source, results=results,
                                    extra_detail={"verification": book})
    return VerifyOutcome(run.run_id, score.verdict, detail=run.tc_verdict_detail or {},
                         tenant=tenant, queries_issued=issued,
                         reason="quota_exhausted" if budget.get("tripped") else None)


async def auto_verify_once(
    db: AsyncSession, *, lookback_seconds: int,
    max_attempts: Optional[int] = None,
    backoff_seconds: Optional[int] = None,
    max_queries: Optional[int] = None,
    timeframe_seconds: Optional[int] = None,
    runner: Optional[Any] = None,
    tenant: Optional[str] = None,
) -> dict[str, Any]:
    """One verify phase: re-query recent runs whose verdict is still open.

    Runs as a PHASE of the reconcile sweep rather than on its own timer, because
    the verdict it computes consumes MTTD and MTTD is produced by reconcile. A
    second independent timer would race and verify runs seconds before they got
    the number they are scored on.
    """
    max_attempts = (max_attempts if max_attempts is not None
                    else settings.CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS)
    backoff_seconds = (backoff_seconds if backoff_seconds is not None
                       else settings.CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS)
    max_queries = (max_queries if max_queries is not None
                   else settings.CORTEXSIM_AUTO_VERIFY_MAX_QUERIES_PER_SWEEP)
    timeframe_seconds = (timeframe_seconds if timeframe_seconds is not None
                         else settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS)

    stats: dict[str, Any] = {"runs_considered": 0, "runs_verified": 0,
                             "queries_issued": 0, "quota_tripped": False,
                             "tenant": tenant}
    if runner is None:
        try:
            runner, tenant = await resolve_tenant_runner(
                db, timeframe_seconds=timeframe_seconds)
        except VerifyError as e:  # pragma: no cover — the sweep names no integration
            logger.debug("auto-verify skipped: %s", e.code)
            return stats
        stats["tenant"] = tenant
    if runner is None:
        # Same silence as auto_reconcile_once with no configured kinds: an
        # operator who never wired a tenant should not get a log line a sweep.
        logger.debug("auto-verify: no xsiam_tenant integration configured")
        return stats

    cutoff = datetime.utcnow() - timedelta(seconds=lookback_seconds)
    now = datetime.utcnow()
    candidates = list((await db.execute(
        select(Run).where(Run.started_at >= cutoff)
                   .where(Run.status.in_(_VERIFIABLE_STATUSES))
                   .where(or_(Run.tc_verdict.is_(None), Run.tc_verdict == "pending"))
    )).scalars().all())

    budget = _new_budget(max_queries)
    budget["tenant"] = tenant
    for run in candidates:
        if budget["tripped"] or budget["remaining"] <= 0:
            break
        skip = _verify_eligible(run, max_attempts=max_attempts,
                                backoff_seconds=backoff_seconds, now=now)
        if skip:
            logger.debug("auto-verify skip run=%s: %s", run.run_id, skip)
            continue
        stats["runs_considered"] += 1
        try:
            outcome = await verify_run_now(db, run, runner=runner, tenant=tenant,
                                           budget=budget, source="sweep")
        except Exception:  # noqa: BLE001 — one bad run must not kill the sweep
            logger.exception("auto-verify failed run=%s", run.run_id)
            continue
        if outcome.queries_issued:
            stats["runs_verified"] += 1

    stats["queries_issued"] = budget["issued"]
    stats["quota_tripped"] = bool(budget["tripped"])
    return stats


# ---------------------------------------------------------------------------
# Auto-reconcile sweep (opt-in background loop)
# ---------------------------------------------------------------------------

async def auto_reconcile_once(
    db: AsyncSession, *, lookback_seconds: int, window_seconds: int,
    fetcher: Optional[HttpFetcher] = None,
) -> dict[str, Any]:
    """One sweep: for every configured connector kind, reconcile each recent run
    that still has unobserved detections. Returns counts for logging.

    A run is a candidate when it finished recently (``completed_at`` within
    ``lookback_seconds``, or no completion but started within it) and at least
    one of its results is still unobserved. Connectors with no integration are
    skipped silently.
    """
    from connectors.base import available_connectors  # noqa: PLC0415

    store = CredentialStore(db)
    configured_kinds = {i.kind for i in await store.list_integrations()}
    active_kinds = [c["kind"] for c in available_connectors() if c["kind"] in configured_kinds]
    if not active_kinds:
        # No reconcile connector does NOT imply no verification: the two use
        # different credential kinds, and a tenant wired only for XQL is a
        # legitimate configuration.
        return await _with_verify_phase(
            db, {"runs_considered": 0, "runs_reconciled": 0, "newly_matched": 0,
                 "kinds": []}, lookback_seconds=lookback_seconds)

    cutoff = datetime.utcnow() - timedelta(seconds=lookback_seconds)
    runs = list((await db.execute(
        select(Run).where(Run.started_at >= cutoff)
    )).scalars().all())

    runs_considered = 0
    runs_reconciled = 0
    newly_total = 0
    for run in runs:
        results = list((await db.execute(
            select(Result).where(Result.run_id == run.run_id)
        )).scalars().all())
        if not any((not r.observed) and r.executed_at for r in results):
            continue
        runs_considered += 1
        for kind in active_kinds:
            try:
                outcome = await reconcile_run(
                    db, run, results, connector_kind=kind,
                    window_seconds=window_seconds, fetcher=fetcher,
                )
            except ReconcileError as e:
                logger.debug("auto-reconcile skip run=%s kind=%s: %s",
                             run.run_id, kind, e.code)
                continue
            if outcome.newly_matched:
                runs_reconciled += 1
                newly_total += outcome.newly_matched

    stats: dict[str, Any] = {
        "runs_considered": runs_considered,
        "runs_reconciled": runs_reconciled,
        "newly_matched": newly_total,
        "kinds": active_kinds,
    }

    return await _with_verify_phase(db, stats, lookback_seconds=lookback_seconds)


async def _with_verify_phase(db: AsyncSession, stats: dict[str, Any], *,
                             lookback_seconds: int) -> dict[str, Any]:
    """Append the opt-in verify phase to a completed reconcile sweep.

    Strictly after the reconcile loop, so every run it scores already has
    whatever MTTD this sweep produced. Its own flag: reconcile and verify use
    different credential kinds and have wildly different query costs, so one
    opt-in must not silently enable the other. A verify failure never
    invalidates the reconcile results that preceded it.
    """
    if not settings.CORTEXSIM_AUTO_VERIFY:
        return stats
    try:
        stats["verify"] = await auto_verify_once(db, lookback_seconds=lookback_seconds)
    except Exception:  # pragma: no cover - defensive
        logger.exception("auto-verify phase failed — reconcile results stand")
    return stats


async def auto_reconcile_loop(interval_seconds: int, lookback_seconds: int,
                              window_seconds: int) -> None:
    """Run :func:`auto_reconcile_once` forever on a fixed cadence. Opt-in;
    started from the lifespan only when CORTEXSIM_AUTO_RECONCILE is set.
    Transient errors are logged and never kill the loop."""
    import asyncio  # noqa: PLC0415
    from database import AsyncSessionLocal  # noqa: PLC0415

    logger.info("auto-reconcile loop started interval=%ds lookback=%ds",
                interval_seconds, lookback_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                async with AsyncSessionLocal() as db:
                    stats = await auto_reconcile_once(
                        db, lookback_seconds=lookback_seconds,
                        window_seconds=window_seconds)
                if stats["newly_matched"]:
                    logger.info("auto-reconcile: %d detection(s) validated across %d run(s) via %s",
                                stats["newly_matched"], stats["runs_reconciled"], stats["kinds"])
            except Exception:  # pragma: no cover - defensive
                logger.exception("auto-reconcile sweep failed — continuing")
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("auto-reconcile loop cancelled")
        raise
