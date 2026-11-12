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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from connectors import ConnectorConfig, get_connector, reconcile
from connectors.base import HttpFetcher, ObservedAlert, coerce_utc
from connectors.case_ledger import (
    CaseShapeError,
    MEDIUM_PLUS,
    bridge_diagnosis,
    build_case_ledger,
    ledger_alert,
    ledger_case_from_incident,
    LEDGER_READ_ERRORS,
    ledger_from_dict,
)
from connectors.matcher import DEFAULT_WINDOW_SECONDS, scope_to_host
from connectors.measures import (
    CORRELATION,
    build_measurement,
    denominator_exclusion,
    empty_exclusions,
    kpi_family,
)
from engine.outcome import (
    EVIDENCE_INCOMPLETE,
    EXCLUDED_FROM_DENOMINATOR,
    OUTCOME_DETECTED,
    OUTCOME_MISSED,
    OUTCOME_PENDING,
    OUTCOME_PREVENTED,
    SOAK_REASONS,
    PASS_WITHHELD_UNSCOPED,
    PreventVerdict,
    evaluate_prevent,
    resolve_module_mode,
    silent_detect_verdict,
    is_hit,
    soak_pending_reason,
)
from events import event_bus
from connectors.xsiam import IssueReadError
from integrations.xsiam.codes import remediation_for
from integrations.xsiam.loader import XSIAM_KIND as XSIAM_TENANT_KIND
from models import Agent, Observation, Result, Run, Scenario, StepOutcome
from security.credentials import CredentialStore

logger = logging.getLogger("cortexsim.connectors.service")


def _row_module_mode(run: Any, result: Any) -> Optional[str]:
    """The Action Mode one prevent row is graded against (review F1)."""
    return resolve_module_mode(
        agent_profile_mode=run.agent_profile_mode,
        module_modes=getattr(run, "module_modes", None),
        prevention_module=getattr(result, "prevention_module", None))


class ReconcileError(Exception):
    """Raised when a reconcile cannot proceed (unknown connector / no
    integration / pull failure). Carries a machine ``code`` for the API."""

    def __init__(self, code: str, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def window_by_type() -> dict[str, int]:
    """Per-``Result.signal_type`` match windows, passed to every ``reconcile``.

    Analytics/ABIOC detectors fire on a timescale the default window does not
    cover (activation and test periods per the *Cortex XDR Analytics Alert
    Reference Guide*); every other type uses the caller's ``window_seconds``.
    """
    w = int(settings.CORTEXSIM_RECONCILE_WINDOW_ANALYTICS)
    return {"Analytics": w, "ABIOC": w}


@dataclass
class ReconcileOutcome:
    summary: dict[str, Any]
    newly_matched: int


# ── which credential kind may serve which connector ─────────────────────────
#
# ONE definition, consumed by GET /api/connectors, POST /runs/{id}/reconcile,
# POST /api/connectors/xsiam/preflight and the auto-reconcile sweep. They forked
# once: the listing reported the `xsiam` connector configured on an
# `xsiam_tenant` row while the other three filtered on kind == "xsiam" alone, so
# the tenant a DC had just been told was configured answered NO_INTEGRATION a
# click later and the sweep was a silent no-op every cycle.
#
# The table is ONE-WAY by construction. Reading alerts is the baseline every
# XSIAM credential carries (`_project` in api/connectors.py), so the Tier-2
# tenant credential may serve alert read-back. Nothing maps the other way:
# configuring reconcile must not silently authorise XQL, which spends a metered
# resource the customer's own analysts share.
_EQUIVALENT_CREDENTIAL_KINDS: dict[str, tuple[str, ...]] = {
    "xsiam": (XSIAM_TENANT_KIND,),
}


def credential_kinds_for(connector_kind: str) -> tuple[str, ...]:
    """Every credential kind that may serve ``connector_kind``, dedicated kind first."""
    return (connector_kind, *_EQUIVALENT_CREDENTIAL_KINDS.get(connector_kind, ()))


async def resolve_connector_integrations(store: CredentialStore, connector_kind: str) -> list:
    """Credential rows that may serve ``connector_kind``: the dedicated kind's rows
    first, then each equivalent kind's, each group in the store's name order."""
    rows: list = []
    for kind in credential_kinds_for(connector_kind):
        rows.extend(await store.list_integrations(kind=kind))
    return rows


def pick_integration(rows: list, integration_name: Optional[str]):
    """The row to use: the named one, else the first. A name that matches nothing
    RAISES rather than falling back — a reconcile asked for tenant A that quietly
    read tenant B would credit the wrong tenant's alerts to this run."""
    if integration_name is None:
        return rows[0]
    for row in rows:
        if row.name == integration_name:
            return row
    raise ReconcileError(
        "INTEGRATION_NOT_FOUND",
        f"no integration named '{integration_name}' may serve this connector",
        detail={"available": [r.name for r in rows]})


async def _resolve_connector(
    db: AsyncSession, connector_kind: str, integration_name: Optional[str],
    fetcher: Optional[HttpFetcher],
) -> tuple[Any, ConnectorConfig, CredentialStore, Any]:
    """Connector + decrypted credential config for ``connector_kind``.

    The one place a credentialed read is set up, shared by :func:`reconcile_run`
    and :func:`refresh_case_ledger` so neither can resolve a different tenant.
    """
    conn = get_connector(connector_kind, fetcher=fetcher)
    if conn is None:
        raise ReconcileError("CONNECTOR_NOT_FOUND",
                             f"unknown connector '{connector_kind}'")

    store = CredentialStore(db)
    integrations = await resolve_connector_integrations(store, connector_kind)
    if not integrations:
        kinds = " or ".join(f"'{k}'" for k in credential_kinds_for(connector_kind))
        raise ReconcileError("NO_INTEGRATION",
                             f"configure an integration of kind {kinds} first")
    integ = pick_integration(integrations, integration_name)
    try:
        secret = await store.get_integration_secret(integ.name)
    except Exception as e:  # noqa: BLE001
        raise ReconcileError("CREDENTIAL_ERROR", str(e))

    cfg = ConnectorConfig(integration_name=integ.name, config=integ.config or {}, secret=secret)
    return conn, cfg, store, integ


async def reconcile_run(
    db: AsyncSession,
    run: Run,
    results: list[Result],
    *,
    connector_kind: str,
    integration_name: Optional[str] = None,
    window_seconds: int = 3600,
    fetcher: Optional[HttpFetcher] = None,
    host: Optional[str] = None,
) -> ReconcileOutcome:
    """Resolve the integration credential, pull observations for the run's
    window, scope them to the run's target host, correlate to ``results``,
    persist matches, emit SSE.

    ``host`` overrides the scope host; when omitted it is resolved from
    ``Run.target`` → ``Agent.hostname``. A run with no resolvable host is
    reconciled UNSCOPED and the summary says so — on a shared tenant that means
    an alert from any endpoint could have been credited.

    Raises :class:`ReconcileError` (never HTTP) on any failure.
    """
    conn, cfg, store, integ = await _resolve_connector(
        db, connector_kind, integration_name, fetcher)

    exec_times = [r.executed_at for r in results if r.executed_at]
    since = (min(exec_times) if exec_times
             else (run.started_at or datetime.utcnow() - timedelta(hours=1)))
    until = datetime.utcnow() + timedelta(minutes=5)

    pull = conn.pull(cfg, since=since, until=until)
    if not pull.ok:
        # `pull.error` is persisted to IntegrationCredential.last_verified_error,
        # which GET /api/credentials/integrations returns. The connector already
        # keeps the tenant's response BODY out of that string (log only); this is
        # the second half of that boundary.
        await store.mark_integration_verified(integ.name, ok=False, error=pull.error)
        await db.commit()
        detail = dict(pull.detail or {})
        detail["code"] = pull.code
        detail["remediation"] = remediation_for(pull.code)
        raise ReconcileError("CONNECTOR_PULL_FAILED", pull.error or "pull failed",
                             detail=detail)

    await store.mark_integration_verified(integ.name, ok=True, error=None)

    scope_host = host if host is not None else await resolve_run_host(db, run)
    eligible, host_scope = scope_to_host(pull.observations, scope_host)
    verdicts = reconcile(results, eligible, window_seconds=window_seconds,
                         window_seconds_by_type=window_by_type(),
                         window_end_by_result=await match_window_ends(
                             db, run.run_id, results, window_seconds=window_seconds))

    # ── what the scorer cannot know on its own: the pull context ──────────
    truncation = (pull.detail or {}).get("truncation")
    by_ext = {a.external_id: a for a in eligible if a.external_id}
    matched_ext = [v.alert_external_id for v in verdicts if v.matched and v.alert_external_id]
    ctx: dict[str, Any] = {
        "basis": "reconcile",
        "unscoped": host_scope["host"] is None,
        "truncated": bool(truncation),
        "dropped": pull.dropped,
        "alert_incidents": {e: getattr(by_ext.get(e), "incident_id", None) for e in matched_ext},
    }
    scenario = (await db.execute(
        select(Scenario).where(Scenario.scenario_id == run.scenario_id))).scalar_one_or_none()
    family = kpi_family(getattr(scenario, "primary_kpi", None),
                        getattr(scenario, "threshold", None))
    incidents_queries = 0
    if (family == CORRELATION and host_scope["host"] is not None
            and not any(ctx["alert_incidents"].values())
            and hasattr(conn, "pull_incidents")):
        # The tenant put no incident id on the alerts: one more read, the
        # incident list for the same window, scoped to the run's host. Only
        # for correlation-shaped scenarios — accuracy never needs it.
        ipull = conn.pull_incidents(cfg, since=since, until=until)
        incidents_queries = _pages_issued(ipull.detail)
        if ipull.ok:
            want = host_scope["host"]
            ctx["incidents"] = [i for i in ipull.incidents
                                if any(h.lower() == want for h in i.hosts)]
            ctx["incidents_truncated"] = bool((ipull.detail or {}).get("truncated"))
        else:
            ctx["incidents_error"] = {"code": ipull.code, "error": ipull.error,
                                      "remediation": remediation_for(ipull.code)}

    # The case ledger is built BEFORE scoring so ctx["merge_class"] is there for
    # the one guard that reads it; it is disposition CONTEXT and writes nothing
    # onto a Result. EVERY reconcile rebuilds it, a zero-match one included,
    # from the stored ledger's alerts plus this reconcile's matches: a late
    # match is the case the ledger exists for (spec §4.2), and it is only a
    # split if the earlier alerts are still in the population.
    case_queries = 0
    ledger: Optional[dict] = None
    population, fresh = ledger_population(run.case_ledger, by_ext, matched_ext,
                                          integration=cfg.integration_name,
                                          source=connector_kind)
    if population and hasattr(conn, "pull_cases"):
        ledger, case_queries = await build_and_store_case_ledger(
            db, run, conn=conn, cfg=cfg, alerts=population, bridge_alerts=fresh,
            basis="alert_incident_ids", keep_stored_on_read_failure=True)
        if ledger.get("stored_ledger_kept"):
            # A failed read never withholds: the stored claim is not re-proven.
            ctx["merge_class"] = "PENDING"
        else:
            ctx["merge_class"] = ledger["merge_class"]
            # What the ledger classified: the withhold applies only when it
            # covers every cased alert the KPI counts (measures.ledger_covers).
            ctx["case_ledger_alerts"] = {a["external_id"]: a["case_id"]
                                         for a in ledger["alerts"] if a.get("case_id")}

    summary, newly = await apply_verdicts(db, run.run_id, results, verdicts,
                                          source=f"{connector_kind}:{integ.name}",
                                          pull_context=ctx, observations=eligible,
                                          window_seconds=window_seconds)
    summary["pulled"] = len(pull.observations)
    # Ledger units: every tenant page read is one query; the incidents read
    # counts its own pages when it was attempted (ok or not).
    summary["queries_issued"] = _pages_issued(pull.detail) + incidents_queries + case_queries
    if ledger is not None:
        summary["case_ledger"] = {"merge_class": ctx["merge_class"], "code": ledger["code"]}
        if ledger.get("stored_ledger_kept"):
            summary["case_ledger"]["stored_ledger_kept"] = True
    summary["host_scope"] = host_scope
    if "incidents" in ctx:
        summary["incidents_considered"] = len(ctx["incidents"])
    if "incidents_error" in ctx:
        summary["incidents_error"] = ctx["incidents_error"]
    summary["verdicts"] = [v.to_dict() for v in verdicts if v.matched]

    warnings: list[str] = []
    if host_scope["host"] is None:
        warnings.append(
            "unscoped: this run has no resolvable target host, so an alert from "
            "ANY endpoint in the tenant was eligible to be credited — on a shared "
            "tenant treat every match here as unconfirmed.")
    if truncation:
        summary["truncated"] = True
        summary["truncation"] = truncation
        warnings.append(
            f"the tenant holds {truncation.get('total_count')} alerts in this window "
            f"and {truncation.get('returned')} were fetched ({truncation.get('pages')} "
            f"page(s), cap {truncation.get('max_pages')}) — coverage below is a floor, "
            f"not a measurement.")
    if pull.dropped:
        # Never absorbed into the coverage number: these alerts existed in the
        # tenant and CortexSim could not date them, so they are NOT evidence
        # that a detection failed to fire.
        summary["dropped_unparseable_timestamps"] = pull.dropped
        warnings.append(
            f"{pull.dropped} of {pull.dropped + len(pull.observations)} alerts had "
            f"an unreadable timestamp and were not counted — coverage below is a "
            f"floor, not a measurement.")
    if warnings:
        summary["warnings"] = warnings
        summary["warning"] = " ".join(warnings)
    return ReconcileOutcome(summary=summary, newly_matched=newly)


def ledger_population(
    stored: Optional[dict], by_ext: dict[str, ObservedAlert], matched_ext: list[str], *,
    integration: str, source: str,
) -> tuple[list[ObservedAlert], list[ObservedAlert]]:
    """The alerts a reconcile builds the case ledger from: ``(population, fresh)``.

    ``population`` is the stored ledger's alerts followed by this reconcile's
    newly matched ones (de-duplicated by external id, stored order first). Each
    is taken from this pull (``by_ext``) when the pull returned it, so a case id
    the tenant stamped since the last reconcile is seen; otherwise it is rebuilt
    from the stored entry. ``fresh`` is the subset taken from this pull: only
    those carry a host, so only they can answer the bridge's mixed-key question.

    The stored alerts are carried only when the stored ledger was read through
    the same integration (or records none): case ids are tenant-local. A stored
    ledger that cannot be read back is not carried; the rebuilt ledger replaces
    it, and the read error is logged.
    """
    picked: dict[str, ObservedAlert] = {}
    fresh: dict[str, ObservedAlert] = {}
    if isinstance(stored, dict) and stored.get("integration") in (None, integration):
        try:
            entries = [(str(e["external_id"]), e) for e in stored["alerts"]]
            for eid, e in entries:
                if eid in by_ext:
                    picked[eid] = fresh[eid] = by_ext[eid]
                    continue
                picked[eid] = ObservedAlert(
                    source=source, observed_at=datetime.fromisoformat(e["observed_at"]),
                    external_id=eid, severity=e["severity"], incident_id=e["case_id"])
        except (*LEDGER_READ_ERRORS, ValueError) as e:
            logger.warning("stored case ledger of run not carried (unreadable): %s", e)
            picked, fresh = {}, {}
    for eid in matched_ext:
        if eid in by_ext and eid not in picked:
            picked[eid] = fresh[eid] = by_ext[eid]
    return list(picked.values()), list(fresh.values())


async def build_and_store_case_ledger(
    db: AsyncSession, run: Run, *, conn: Any, cfg: ConnectorConfig,
    alerts: list[ObservedAlert], basis: str, now: Optional[datetime] = None,
    commit: bool = False, bridge_alerts: Optional[list[ObservedAlert]] = None,
    keep_stored_on_read_failure: bool = False,
) -> tuple[dict, int]:
    """Build the run's case ledger for ``alerts`` and set it on ``run.case_ledger``.

    Returns ``(ledger, queries)``. ``queries`` counts the pages of every tenant
    read ATTEMPTED, success or failure: the by-id case read, the issue-status
    read and, on a mixed-key native split, the one endpoint read of the bridge
    diagnosis (:func:`_add_bridge`, which only reads ``bridge_alerts``, default
    ``alerts``).

    * No alert carries a case id: no tenant read at all. The ledger is built
      from the alerts alone (``NO_CASE_YET`` for Medium+ alerts) with
      ``issue_read = {"ok": None}``, and a later reconcile rebuilds it once the
      tenant has grouped them. A refresh cannot: it re-reads cases only.
    * Otherwise the cases are read by id, then the issue statuses.

    Never raises on a tenant failure (``asyncio.CancelledError`` and other
    ``BaseException`` still propagate). Degradation:

    * case read fails, or any unexpected exception from a read or from
      building (logged with traceback) -> ``code="CASE_READ_FAILED"``,
      ``merge_class="PENDING"``. With ``keep_stored_on_read_failure`` and a
      stored ledger on the run, the stored ledger is NOT replaced: the returned
      ledger carries ``stored_ledger_kept: True`` and is not stored, so a
      transient failure cannot erase a good ledger;
    * unrecognised case/issue shape (:class:`CaseShapeError`)
      -> ``code="CASE_STATUS_UNRECOGNIZED"``, ``PENDING``, stored (the old
      merge claim can no longer be trusted);
    * issue read fails (the cases were read fine) -> the merge claim stands and
      ``code`` stays ``CASE_LEDGER_OK``, but every issue reads ``unread`` (never
      ``open``) and ``ledger["issue_read"] = {"ok": False, "code": <tenant
      code>, "pages": n}``. ``issue_read`` is ``{"ok": True, "pages": n}`` on
      success and ``{"ok": None}`` when the issue read was skipped.

    The issue read is skipped once the case read has failed: the tenant is
    already failing, and issue context cannot repair a PENDING merge.

    Transaction: by default this does NOT commit. On the reconcile path the
    ledger rides ``apply_verdicts``'s commit, so it persists atomically with the
    verdicts (a scoring failure rolls both back). A standalone caller passes
    ``commit=True`` or commits itself.
    """
    read_at = now or datetime.now(timezone.utc)
    queries = 0
    bare = [ledger_alert(a, None) for a in alerts]
    skipped: dict[str, Any] = {"ok": None}

    def degraded(code: str, reason: str, issue_read: dict) -> dict:
        ledger = build_case_ledger(bare, {}, basis=basis, read_at=read_at, code=code)
        ledger["merge_class"] = "PENDING"
        ledger["merge_reason"] = reason
        ledger["issue_read"] = issue_read
        return ledger

    # ``compute`` builds the ledger and nothing else. Storing and committing sit
    # AFTER it: a ``db.commit()`` inside the try was re-caught as an
    # "unexpected" ledger error and retried on a failed session. A DB error now
    # reaches the caller untouched.
    def compute() -> dict:
        nonlocal queries
        try:
            case_ids = sorted({str(a.incident_id) for a in alerts
                               if a.incident_id not in (None, "")})
            if not case_ids:
                ledger = build_case_ledger(bare, {}, basis=basis, read_at=read_at)
                ledger["issue_read"] = skipped
                return ledger
            cpull = conn.pull_cases(cfg, case_ids)
            queries += _pages_issued(cpull.detail)
            if not cpull.ok:
                ledger = degraded("CASE_READ_FAILED",
                                  f"the case read failed ({cpull.code}); no merge claim is made",
                                  skipped)
                ledger["case_read"] = {"code": cpull.code,
                                       "remediation": remediation_for(cpull.code)}
                return ledger
            try:
                cases = {}
                for inc in cpull.incidents:
                    c = ledger_case_from_incident(inc)
                    cases[c.case_id] = c
            except CaseShapeError as e:
                return degraded("CASE_STATUS_UNRECOGNIZED", str(e), skipped)

            status_map: dict = {}
            issue_ids = sorted({a.external_id for a in alerts if a.external_id})
            try:
                smap = conn.pull_issue_status(cfg, issue_ids)
                queries += int(smap.pages)  # before the copy: a dict() copy drops .pages
                status_map = dict(smap)
                issue_read: dict[str, Any] = {"ok": True, "pages": int(smap.pages)}
            except IssueReadError as e:
                pages = _pages_issued(e.detail)
                queries += pages
                issue_read = {"ok": False, "code": e.code, "pages": pages,
                              "remediation": remediation_for(e.code)}
            try:
                ledger = build_case_ledger(
                    [ledger_alert(a, status_map.get(str(a.external_id))) for a in alerts],
                    cases, basis=basis, read_at=read_at)
            except CaseShapeError as e:
                return degraded("CASE_STATUS_UNRECOGNIZED", str(e), issue_read)
            ledger["issue_read"] = issue_read
            return ledger
        except Exception as e:  # noqa: BLE001 - context-only data must never fail reconcile
            logger.warning("case ledger degraded on unexpected %s", type(e).__name__, exc_info=True)
            ledger = degraded("CASE_READ_FAILED",
                              f"the case ledger could not be built ({type(e).__name__}); "
                              "no merge claim is made", skipped)
            return ledger

    ledger = compute()
    # Case ids are tenant-local: record WHICH integration read them so a refresh
    # asks the same tenant (a second tenant can return another case under the id).
    ledger["integration"] = cfg.integration_name
    if (keep_stored_on_read_failure and ledger["code"] == "CASE_READ_FAILED"
            and isinstance(run.case_ledger, dict)):
        ledger["stored_ledger_kept"] = True
        return ledger, queries
    queries += await _add_bridge(db, run, ledger,
                                 alerts if bridge_alerts is None else bridge_alerts,
                                 conn=conn, cfg=cfg)
    run.case_ledger = ledger
    if commit:
        await db.commit()
    return ledger, queries


async def _add_bridge(db: AsyncSession, run: Run, ledger: dict,
                      alerts: list[ObservedAlert], *, conn: Any, cfg: ConnectorConfig) -> int:
    """Diagnose the IP-to-host bridge on a native split; returns queries spent.

    Only when ``merge_class`` is ``SPLIT_NATIVE`` and the Medium+ alerts mix an
    IP-only alert (``host is None``, e.g. NGFW) with a host-keyed one. Context
    only: sets ``ledger["bridge"]`` and appends its code to ``merge_reason``;
    never touches ``merge_class``, ``code`` or any verdict. A failed or
    unrecognised endpoint read is ``BRIDGE_UNREAD`` and never raises (the one
    request it makes is counted either way).

    Not run by :func:`refresh_case_ledger`: the stored ledger keeps no per-alert
    host, so a refresh cannot re-evaluate the mixed-key condition; it carries
    the stored diagnosis forward instead.
    """
    if ledger.get("merge_class") != "SPLIT_NATIVE" or not hasattr(conn, "pull_endpoint_ips"):
        return 0
    mp = [a for a in alerts if (a.severity or "").lower() in MEDIUM_PLUS]
    if not (any(a.host is None for a in mp) and any(a.host is not None for a in mp)):
        return 0
    host = await resolve_run_host(db, run)
    agent = None
    if getattr(run, "target", None):
        agent = (await db.execute(
            select(Agent).where(Agent.agent_id == run.target))).scalar_one_or_none()
    agent_ip = getattr(agent, "last_ip", None)
    ips = None
    spent = 0
    if host and agent_ip:
        spent = 1
        try:
            ips = conn.pull_endpoint_ips(cfg, host)
        except Exception as e:  # noqa: BLE001 - context-only data must never fail reconcile
            logger.warning("bridge endpoint read failed: %s", type(e).__name__, exc_info=True)
    diag = bridge_diagnosis(host=host, agent_ip=agent_ip, endpoint_ips=ips)
    diag["read_at"] = ledger["read_at"]   # when this was computed; a refresh may carry it
    ledger["bridge"] = diag
    ledger["merge_reason"] = f"{ledger['merge_reason']}; ip-to-host bridge: {diag['code']}"
    return spent


def _pages_issued(detail: Optional[dict[str, Any]]) -> int:
    """Ledger units a harvest spent: ``pages`` on success, the ``page`` a failed read died on."""
    d = detail or {}
    # ``is not None``, not ``or``: a reported 0 is a real count, not a missing one.
    for key in ("pages", "page"):
        if d.get(key) is not None:
            return int(d[key])
    return 1


CASE_LEDGER_CONNECTOR = "xsiam"


async def refresh_case_ledger(
    db: AsyncSession, run: Run, *, fetcher: Optional[HttpFetcher] = None,
) -> dict:
    """Re-read the cases named in ``run.case_ledger`` and rebuild it.

    Disposition drifts after a run (Auto Triage seals cases ~6 h later), so the
    stored ledger goes stale. This re-reads CASES only: the stored alerts are
    kept, and each alert's stored ``issue_closed_by`` is carried forward
    unchanged (``issue_override``), never recomputed from the restored alerts,
    which carry no issue status and would all read ``unread``. ``merge_class``
    and per-case disposition are recomputed from the fresh cases.

    Read-only toward Cortex. Raises :class:`ReconcileError`:

    * ``NO_CASE_LEDGER`` - nothing to refresh (409 at the API);
    * ``CASE_LEDGER_UNREADABLE`` - the stored ledger is corrupt;
    * ``CONNECTOR_PULL_FAILED`` - the case read failed. The stored ledger is
      KEPT: a transient failure must not overwrite a good ledger with PENDING.
      ``detail["queries_issued"]`` carries the pages spent (the sweep counts
      them, and ``detail["status"] == 429`` trips its quota breaker).

    An unrecognised case shape does not raise to the caller: the stored ledger
    is replaced by a degraded one (``CASE_STATUS_UNRECOGNIZED``, ``PENDING``),
    because the old merge claim can no longer be trusted.

    Transaction: this function commits (outside any ``try`` that could swallow
    it); a commit failure rolls back and propagates, and the commit is never
    retried on a failed session.
    """
    stored = run.case_ledger
    if not stored:
        raise ReconcileError("NO_CASE_LEDGER",
                             f"run '{run.run_id}' has no case ledger to refresh")
    try:
        alerts, old_cases = ledger_from_dict(stored)
        carried = {a["external_id"]: a["issue_closed_by"] for a in stored["alerts"]}
        basis = stored["basis"]
    except LEDGER_READ_ERRORS as e:
        raise ReconcileError("CASE_LEDGER_UNREADABLE", f"stored case ledger is unreadable: {e}")

    case_ids = sorted({a.case_id for a in alerts if a.case_id} | set(old_cases))
    if not case_ids:
        return {"run_id": run.run_id, "refreshed": False, "queries_issued": 0,
                "merge_class": stored.get("merge_class"), "code": stored.get("code"),
                "read_at": stored.get("read_at"), "ledger": stored,
                "reason": ("the ledger names no case to re-read; the next reconcile "
                           "rebuilds it once the tenant has grouped the alerts into a case")}

    recorded = stored.get("integration")
    if not recorded:
        # Legacy ledger: the tenant was not recorded. Only unambiguous when
        # exactly one integration may serve the connector.
        rows = await resolve_connector_integrations(CredentialStore(db), CASE_LEDGER_CONNECTOR)
        if len(rows) > 1:
            raise ReconcileError(
                "CASE_LEDGER_TENANT_UNKNOWN",
                "this ledger does not record which integration read it and more than one "
                "could have; case ids are tenant-local, so it is not refreshed",
                detail={"available": [r.name for r in rows]})
    # A recorded name that no longer exists raises INTEGRATION_NOT_FOUND (stored ledger kept).
    conn, cfg, _store, integ = await _resolve_connector(
        db, CASE_LEDGER_CONNECTOR, recorded, fetcher)
    cpull = conn.pull_cases(cfg, case_ids)
    pages = _pages_issued(cpull.detail)
    if not cpull.ok:
        detail = dict(cpull.detail or {})
        detail.update({"code": cpull.code, "remediation": remediation_for(cpull.code),
                       "queries_issued": pages})
        raise ReconcileError("CONNECTOR_PULL_FAILED", cpull.error or "case read failed",
                             detail=detail)

    read_at = datetime.now(timezone.utc)
    try:
        cases = {}
        for inc in cpull.incidents:
            c = ledger_case_from_incident(inc)
            cases[c.case_id] = c
        ledger = build_case_ledger(alerts, cases, basis=basis, read_at=read_at,
                                   issue_override=carried)
    except CaseShapeError as e:
        ledger = build_case_ledger(alerts, {}, basis=basis, read_at=read_at,
                                   code="CASE_STATUS_UNRECOGNIZED", issue_override=carried)
        ledger["merge_class"] = "PENDING"
        ledger["merge_reason"] = str(e)
    ledger["issue_read"] = stored.get("issue_read", {"ok": None})
    ledger["integration"] = integ.name
    if stored.get("bridge") and ledger["merge_class"] == "SPLIT_NATIVE":
        # Carried, not re-read: the stored ledger keeps no per-alert host, so the
        # mixed-key condition cannot be re-evaluated here (see _add_bridge).
        # Marked ``carried`` with its ORIGINAL ``read_at``: a snapshot, not a fresh read.
        ledger["bridge"] = {**stored["bridge"], "carried": True}
        ledger["merge_reason"] += f"; ip-to-host bridge: {stored['bridge'].get('code')}"

    run.case_ledger = ledger
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"run_id": run.run_id, "refreshed": True, "queries_issued": pages,
            "merge_class": ledger["merge_class"], "code": ledger["code"],
            "read_at": ledger["read_at"], "ledger": ledger,
            "previous_merge_class": stored.get("merge_class")}


def _ledger_terminal(ledger: dict) -> bool:
    """Every case sealed (resolved and not ``auto_resolved``): nothing can change.
    An ``auto_resolved`` case can still reopen, so it is NOT terminal."""
    cases = ledger.get("cases")
    return bool(cases) and all(c.get("sealed") is True for c in cases)


def _read_at_of(ledger: dict) -> Optional[datetime]:
    try:
        return coerce_utc(ledger.get("read_at"))
    except Exception:  # noqa: BLE001 - unreadable read_at: treat as stale
        return None


def _read_age_ok(ledger: dict, now: datetime, min_interval: timedelta) -> bool:
    read_at = _read_at_of(ledger)
    return read_at is None or now - read_at >= min_interval


async def refresh_case_ledgers_once(
    db: AsyncSession, *, window_hours: int, max_runs: int,
    min_interval_minutes: Optional[int] = None,
    fetcher: Optional[HttpFetcher] = None,
) -> dict[str, int]:
    """One bounded sweep over stored case ledgers (opt-in: called only from
    ``auto_reconcile_loop``, i.e. only with ``CORTEXSIM_AUTO_RECONCILE`` on).

    Candidates are runs with a stored ``case_ledger`` whose ``Run.started_at``
    (the run's own timestamp, naive UTC) is within ``window_hours``. A run is
    skipped (no tenant call) when its ledger's ``read_at`` is younger than
    ``min_interval_minutes`` or when every case in it is sealed (terminal).
    Eligible runs are ordered least-recently-read first, so a window larger than
    ``max_runs`` rotates instead of starving older runs. At most ``max_runs``
    are attempted per sweep; every tenant page is counted in ``queries_issued``;
    a quota error (HTTP 429) stops the whole sweep. Returns
    ``{"refreshed", "skipped", "queries_issued", "quota_tripped"}``.
    """
    if min_interval_minutes is None:
        min_interval_minutes = settings.CORTEXSIM_CASE_LEDGER_MIN_INTERVAL_MINUTES
    min_interval = timedelta(minutes=min_interval_minutes)
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    now = datetime.utcnow()  # coerce_utc yields naive UTC
    rows = (await db.execute(select(Run.run_id, Run.case_ledger).where(Run.started_at >= cutoff)
                             )).all()
    # Plain-data snapshot: a rollback in one refresh expires every ORM instance
    # in this session, so the loop must not touch instances loaded before it.
    eligible = []
    for run_id, ledger in rows:
        if not ledger or not isinstance(ledger, dict):
            continue
        if _ledger_terminal(ledger) or not _read_age_ok(ledger, now, min_interval):
            continue
        eligible.append((_read_at_of(ledger) or datetime.min,
                         run_id))
    eligible.sort()
    refreshed = skipped = queries = 0
    tripped = False
    for _read_at, run_id in eligible[:max_runs]:
        try:
            run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one()
            out = await refresh_case_ledger(db, run, fetcher=fetcher)
        except ReconcileError as e:
            skipped += 1
            detail = e.detail if isinstance(e.detail, dict) else {}
            queries += int(detail.get("queries_issued") or 0)
            if _is_quota_error(e):
                tripped = True
                logger.warning("case-ledger refresh: tenant reported quota exhaustion (%s) "
                               "- sweep circuit-broken", e.code)
                break
            logger.debug("case-ledger refresh skip run=%s: %s", run_id, e.code)
            continue
        except Exception:  # noqa: BLE001 - e.g. a failed commit; the session was rolled back
            skipped += 1
            logger.warning("case-ledger refresh failed run=%s", run_id, exc_info=True)
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
            continue
        queries += int(out["queries_issued"])
        if out["refreshed"]:
            refreshed += 1
        else:
            skipped += 1
    return {"refreshed": refreshed, "skipped": skipped, "queries_issued": queries,
            "quota_tripped": int(tripped)}


async def resolve_run_host(db: AsyncSession, run: Run) -> Optional[str]:
    """The hostname the run executed on, or None when it cannot be known.

    ``Run.target`` is the launch agent id; the beacon registered its hostname.
    Push-mode runs and runs whose agent row is gone resolve None, which the
    caller reports as *unscoped* rather than silently matching tenant-wide.
    """
    target = getattr(run, "target", None)
    if not target:
        return None
    agent = (await db.execute(
        select(Agent).where(Agent.agent_id == target))).scalar_one_or_none()
    if agent is None or not agent.hostname:
        return None
    return agent.hostname


def _reported_at_map(host_rows: list) -> dict[str, Any]:
    """When each step's host record says it had finished: ``/output`` arrival,
    else the ``/complete`` record."""
    return {so.step_id: so.started_at or so.recorded_at for so in host_rows}


def result_window_end(result: Any, *, window_seconds: int,
                      by_type: dict[str, int],
                      reported_at: Optional[dict[str, Any]]) -> Optional[datetime]:
    """THE window end for one Result -- the one value both the matcher's upper
    bound and the silent-row judgement use (round 4 C-1 rule 3, round 5 C-1R).

    ``max(executed_at, the step's host record) + window``. The lower bound and
    the MTTD start stay ``executed_at``. None when the row has no executed_at.
    """
    executed_at = getattr(result, "executed_at", None)
    if executed_at is None:
        return None
    start = executed_at
    reported = (reported_at or {}).get(getattr(result, "step_id", None))
    if reported is not None and reported > start:
        start = reported
    window = by_type.get(getattr(result, "signal_type", None), window_seconds)
    return start + timedelta(seconds=window)


async def match_window_ends(db: AsyncSession, run_id: str, results: list, *,
                            window_seconds: int) -> dict[Any, datetime]:
    """``{result_id: window_end}`` for the matcher, from the run's host records."""
    host_rows = (await db.execute(
        select(StepOutcome).where(StepOutcome.run_id == run_id))).scalars().all()
    reported = _reported_at_map(host_rows)
    by_type = window_by_type()
    out: dict[Any, datetime] = {}
    for r in results:
        end = result_window_end(r, window_seconds=window_seconds, by_type=by_type,
                                reported_at=reported)
        if end is not None and getattr(r, "id", None) is not None:
            out[r.id] = end
    return out


async def apply_verdicts(
    db: AsyncSession, run_id: str, results: list[Result], verdicts: list, source: str,
    *, pull_context: Optional[dict[str, Any]] = None,
    observations: Optional[list] = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> tuple[dict[str, Any], int]:
    """Persist matched verdicts (observed/observed_at), emit SSE, summarise.

    ``pull_context`` is what the reconcile path knows and the scorer cannot
    (host scope, truncation, the matched alerts' incident ids); it is what
    turns matched/seeded into a Detection-Accuracy measurement and incident
    membership into a correlation rate. Omitted by the completion-time score,
    where nothing has been measured yet.

    ``observations`` is the alert list the verdicts were matched against; the
    matched alert's untouched record is persisted as an ``Observation`` row.
    ``window_seconds`` is the match window for every type ``window_by_type``
    does not override; it decides when a silent row's window has closed.

    Outcomes: a ``prevent`` expectation is judged by
    :func:`engine.outcome.evaluate_prevent` (two witnesses: the alert and the
    host); a ``detect`` expectation that matched is ``detected``. A row the host
    already marked ``not_reached`` / ``not_executed`` is never re-judged here.

    Returns ``(summary, newly_matched)``.
    """
    by_id = {r.id: r for r in results}
    # Loaded BEFORE the loop: the run's profile mode and block-mode status are
    # inputs to every prevent verdict.
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    host_rows = (await db.execute(
        select(StepOutcome).where(StepOutcome.run_id == run_id))).scalars().all()
    terminations = {so.step_id: so.termination for so in host_rows}
    # When each step's host record says it had finished (``/output`` arrival,
    # else the ``/complete`` record): the silent-row window starts there.
    reported_at = _reported_at_map(host_rows)
    alerts = list(observations or [])
    by_ext = {a.external_id: a for a in alerts if getattr(a, "external_id", None)}
    unscoped = bool(pull_context and pull_context.get("unscoped"))
    seen_obs = await _existing_observation_keys(db, run_id)

    matched = 0
    applied: list[tuple[Any, Result]] = []
    for v in verdicts:
        if not v.matched:
            continue
        result = by_id.get(v.result_id)
        if result is None:
            continue
        # The alert is evidence either way, so it is recorded once per
        # (result, alert) — a re-evaluation must not duplicate it.
        alert = _matched_alert(v, alerts, by_ext)
        key = _observation_key(result.id, v.alert_external_id, v.observed_at, v.alert_name)
        if key not in seen_obs:
            seen_obs.add(key)
            db.add(Observation(
                run_id=run_id, result_id=result.id, source=source,
                external_id=v.alert_external_id, observed_at=v.observed_at,
                name=v.alert_name, alert_source=v.alert_source,
                action=getattr(alert, "action", None),
                action_pretty=getattr(alert, "action_pretty", None),
                alert_outcome=v.alert_outcome,
                raw=getattr(alert, "raw", None),
            ))
        if result.outcome in EXCLUDED_FROM_DENOMINATOR:
            # The host says this step never ran: an alert inside its window came
            # from something else. Never observed, never MTTD, never re-judged.
            continue
        result.observed = True
        result.observed_at = v.observed_at
        note = (f"auto-validated via {source} "
                f"(matched on {', '.join(v.matched_on)}; "
                f"alert={v.alert_external_id or v.alert_name or 'n/a'})")
        result.notes = ((result.notes + " | ") if result.notes else "") + note
        result.alert_action = v.alert_action
        result.alert_outcome = v.alert_outcome
        if result.expected_outcome == "prevent":
            if run is not None:  # no run row ⇒ no profile mode ⇒ leave unjudged
                pv = evaluate_prevent(
                    agent_profile_mode=run.agent_profile_mode,
                    alert_outcome=v.alert_outcome,
                    termination=terminations.get(result.step_id),
                    window_closed=False,
                    block_mode_status=run.block_mode_status,
                    module_mode=_row_module_mode(run, result))
                if pv.outcome == OUTCOME_PREVENTED and unscoped:
                    # Any endpoint's alert could have been credited.
                    pv = PreventVerdict(OUTCOME_PENDING, "pending", PASS_WITHHELD_UNSCOPED)
                _write_prevent(result, pv)
        else:
            # Whatever the alert's own action: a `detect` expectation is met,
            # and any earlier soak reason is stale.
            result.outcome = OUTCOME_DETECTED
            result.outcome_reason = None
        matched += 1
        applied.append((v, result))

    if run is not None:
        await _judge_silent_results(db, run, results, terminations,
                                    window_seconds=window_seconds,
                                    evidence_complete=_evidence_complete(pull_context),
                                    reported_at=reported_at)

    await db.commit()

    for v, result in applied:
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
    if run is not None:
        summary["tc_verdict"] = await score_run_safely(
            db, run, source=f"reconcile:{source}", results=results,
            pull_context=pull_context)
        detail = run.tc_verdict_detail if isinstance(run.tc_verdict_detail, dict) else {}
        if detail.get("measurement"):
            summary["measurement"] = detail["measurement"]
    return summary, matched


def _observation_key(result_id: Any, external_id: Optional[str], observed_at: Any,
                     name: Optional[str]) -> tuple:
    if external_id is not None:
        return (result_id, "ext", external_id)
    return (result_id, "at", observed_at, name)


async def _existing_observation_keys(db: AsyncSession, run_id: str) -> set:
    rows = (await db.execute(
        select(Observation).where(Observation.run_id == run_id))).scalars().all()
    return {_observation_key(o.result_id, o.external_id, o.observed_at, o.name) for o in rows}


def _evidence_complete(pull_context: Optional[dict[str, Any]]) -> bool:
    """Is silence in this pull evidence of absence?

    Only a credential-backed pull that was host-scoped, not truncated and
    dropped nothing. A manual import is the alerts a DC chose to submit, so
    silence there says nothing about the rows they did not submit for.
    """
    if not pull_context or pull_context.get("basis") == "manual":
        return False
    return not (pull_context.get("unscoped") or pull_context.get("truncated")
                or pull_context.get("dropped"))


def _matched_alert(v: Any, alerts: list, by_ext: dict[str, Any]) -> Any:
    """The alert a verdict matched, from the list it was matched against."""
    if v.alert_external_id and v.alert_external_id in by_ext:
        return by_ext[v.alert_external_id]
    for a in alerts:
        if a.observed_at == v.observed_at and a.name == v.alert_name:
            return a
    return None


def _write_prevent(result: Any, pv: Any) -> None:
    """Write a prevent verdict. ``kpi_verdict`` is never erased by a None.

    A ``fail`` is sticky unless it came from Tier 1's own silent-row path
    (current outcome ``missed``): two real witnesses arriving later are strictly
    better evidence than silence. Any other fail (DETECTED_NOT_PREVENTED, a
    Tier-2 fail) stays.
    """
    replaceable = result.kpi_verdict != "fail" or result.outcome == OUTCOME_MISSED
    result.outcome = pv.outcome
    result.outcome_reason = pv.reason
    if pv.kpi_verdict is not None and replaceable:
        result.kpi_verdict = pv.kpi_verdict


async def _judge_silent_results(
    db: AsyncSession, run: Run, results: list[Result], terminations: dict[str, Any],
    *, window_seconds: int, evidence_complete: bool,
    reported_at: Optional[dict[str, Any]] = None,
) -> None:
    """Judge every still-unobserved Result.

    * Analytics/ABIOC with a soak reason (activation period, or the dedup
      window of a paired second run) → ``pending`` with that reason, from the
      first sweep and whatever the window — it leaves the denominator at once.
    * Otherwise only once the window has closed (``result_window_end``: the
      later of ``executed_at`` and the step's host record, plus the window): a
      ``prevent`` row goes through ``evaluate_prevent`` with no alert; a
      ``detect`` row is ``missed`` only with a host record of ``exited`` /
      ``timeout`` / ``signaled`` / ``anchor_terminated`` (no record or
      ``beacon_lost`` -> ``pending`` / ``HOST_EVIDENCE_ABSENT``, ``aborted`` ->
      ``pending`` / ``RUN_ABORTED``). An affirmative ``missed`` stands only on
      complete evidence — else ``pending`` / ``EVIDENCE_INCOMPLETE``.

    Rows already ``not_reached`` / ``not_executed`` are never touched, nor is
    any row already judged beyond ``pending``.
    """
    now = datetime.utcnow()  # naive UTC, exactly as executed_at is stored
    by_type = window_by_type()
    first_run_seen: Optional[set] = None
    for r in results:
        if r.observed or r.executed_at is None:
            continue
        if r.outcome not in (None, OUTCOME_PENDING):
            continue
        if r.signal_type in by_type:
            if first_run_seen is None:
                first_run_seen = await _first_run_observed_keys(db, run)
            reason = soak_pending_reason(
                signal_type=r.signal_type, soakless_attested=run.soakless_attested,
                first_run_observed=bool(_result_keys(r) & first_run_seen))
            if reason is not None:
                r.outcome = OUTCOME_PENDING
                r.outcome_reason = reason
                continue
            if r.outcome_reason in SOAK_REASONS:
                # The soak no longer applies (e.g. soakless since attested).
                r.outcome = None
                r.outcome_reason = None
        # Round 4 C-1 rule 3 / round 5: the window closes ``window`` after the
        # step REPORTED (its host record), not after the launch the row was
        # seeded with -- the same end the matcher accepts alerts until. The
        # seeded ``executed_at`` stays the lower bound and the MTTD start.
        end = result_window_end(r, window_seconds=window_seconds, by_type=by_type,
                                reported_at=reported_at)
        if now < end:
            continue
        if r.expected_outcome == "prevent":
            pv = evaluate_prevent(
                agent_profile_mode=run.agent_profile_mode, alert_outcome=None,
                termination=terminations.get(r.step_id), window_closed=True,
                block_mode_status=run.block_mode_status,
                module_mode=_row_module_mode(run, r))
            if pv.outcome == OUTCOME_MISSED and not evidence_complete:
                # Only a would-be fail softens to pending; a report-mode None
                # stays None (and so is not written).
                pv = PreventVerdict(
                    OUTCOME_PENDING,
                    "pending" if pv.kpi_verdict == "fail" else pv.kpi_verdict,
                    EVIDENCE_INCOMPLETE)
            _write_prevent(r, pv)
        else:
            # A `detect` expectation: silent after its window closed is a miss
            # -- WRITTEN, so no surface infers it from `observed=False` (R4/R7)
            # -- but only when the host says the step ran (round 4 C-1), and
            # only on complete evidence; otherwise the same softening as the
            # prevent branch. kpi_verdict is
            # the Tier-2 verifier's and is not touched. A later matched alert
            # replaces `missed` with `detected` (apply_verdicts).
            # Round 4 C-1: a miss needs the host's word that the step ran --
            # an in-flight, queued, aborted or push run has no record.
            judged = silent_detect_verdict(terminations.get(r.step_id), window_closed=True)
            if judged is None:
                continue
            outcome, reason = judged
            if outcome == OUTCOME_MISSED and not evidence_complete:
                outcome, reason = OUTCOME_PENDING, EVIDENCE_INCOMPLETE
            r.outcome = outcome
            r.outcome_reason = reason


def _result_keys(r: Any) -> set:
    """Both spellings of a Result's identity within its step (None skipped)."""
    return {(r.step_id, k) for k in (r.detection_id, r.expected_detection) if k is not None}


async def _first_run_observed_keys(db: AsyncSession, run: Run) -> set:
    """Union of ``(step_id, detection_id)`` and ``(step_id, expected_detection)``
    over the Results the paired first run observed."""
    if not run.paired_with_run_id:
        return set()
    rows = (await db.execute(select(Result).where(
        Result.run_id == run.paired_with_run_id, Result.observed.is_(True)))).scalars().all()
    keys: set = set()
    for r in rows:
        keys |= _result_keys(r)
    return keys


def efficacy_summary(results: list[Result], newly_matched: int, source: str) -> dict[str, Any]:
    """Coverage over the rows that could have produced evidence.

    Rows ``not_reached`` / ``not_executed`` and soak-pending Analytics rows
    leave numerator and denominator; ``excluded`` counts them by reason.
    """
    total = len(results)
    excluded = empty_exclusions()
    counted = []
    for r in results:
        why = denominator_exclusion(r)
        if why is None:
            counted.append(r)
        else:
            excluded[why] += 1
    denom = len(counted)
    observed = sum(1 for r in counted if is_hit(r.outcome, r.observed))
    mttds = [r.mttd_seconds for r in counted if r.mttd_seconds is not None]
    return {
        "source": source,
        "total_expected": total,
        "denominator": denom,
        "excluded": excluded,
        "observed": observed,
        "newly_matched": newly_matched,
        "coverage_pct": round(observed / denom * 100, 1) if denom else 0.0,
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
    pull_context: Optional[dict[str, Any]] = None,
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

    # A step the host says never ran cannot contribute a detection time.
    mttds = [r.mttd_seconds for r in results
             if r.mttd_seconds is not None and r.outcome not in EXCLUDED_FROM_DENOMINATOR]
    # Sprint 2: the two non-MTTD values the loop can derive. None when the
    # scenario's KPI is MTTD-shaped, when nothing was pulled (completion-time
    # score), or when the honesty guard withholds it — all of which score
    # exactly as before.
    measured_value, measurement = build_measurement(
        getattr(scenario, "primary_kpi", None), getattr(scenario, "threshold", None),
        results, pull_context)
    prior = (run.tc_verdict_detail.get("measurement")
             if isinstance(run.tc_verdict_detail, dict) else None)
    if pull_context is None and isinstance(prior, dict) and prior.get("scored") is not None:
        # A re-score with no new evidence (a Tier-2 verify pass, a completion
        # callback that arrived late) keeps the last measured value: it must
        # not flip a measured `pass` back to `pending` because THIS caller
        # happened not to be the one holding the pull.
        measured_value = prior["scored"]
        measurement = prior
    score = verifier.score_run(
        results,
        threshold=getattr(scenario, "threshold", None),
        primary_kpi=getattr(scenario, "primary_kpi", None),
        mttd_seconds=(sum(mttds) / len(mttds)) if mttds else None,
        measured_value=measured_value,
        tc_scoreable=verifier.tc_scoreable_for(getattr(scenario, "tc_ref", None)),
    )

    detail = score.to_dict()
    detail["source"] = source
    if measurement:
        detail["measurement"] = measurement
    elif isinstance(prior, dict):
        # A re-score must not erase the last real measurement record either.
        detail["measurement"] = prior
    detail["scored_at"] = datetime.utcnow().isoformat()
    # Carry the verification bookkeeping across a re-score. Tier 1 runs far
    # more often than Tier 2 (every reconcile, every completion) and rebuilds
    # `tc_verdict_detail` wholesale; dropping the block here would reset
    # `attempts` to 0 on each reconcile and let a run evade the sweep's
    # attempt cap forever.
    carried = _bookkeeping(run)
    if carried:
        detail["verification"] = carried
    # Same reason as `verification`: Tier 1 rebuilds tc_verdict_detail wholesale
    # and runs far more often, so dropping the reconcile block here would reset
    # `attempts` to 0 on every completion and let a run evade the sweep's cap
    # forever — and would erase `stopped_reason`, which is the whole point of
    # making a capped-out run visible instead of silent.
    carried_reconcile = _reconcile_bookkeeping(run)
    if carried_reconcile:
        detail["reconciliation"] = carried_reconcile
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
    pull_context: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """:func:`score_run_for_run` that can never break its caller.

    Used from ``/complete`` and from :func:`apply_verdicts` — both mutation
    paths whose real work is already committed by the time scoring runs. A
    scoring bug must degrade to "no verdict", never to a failed run or a 500
    on an agent callback.
    """
    try:
        score = await score_run_for_run(db, run, source=source, results=results,
                                        pull_context=pull_context)
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
    #: What the DC should DO about ``reason``. A code with no remediation sends
    #: them to guess; the audit's F-09 was exactly that failure.
    remediation: Optional[str] = None
    #: Set when the reason is a deliberate pause (backoff), so a caller can say
    #: "try again in N seconds" instead of implying the run is finished.
    retry_after_seconds: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tc_verdict": self.tc_verdict,
            "detail": self.detail,
            "tenant": self.tenant,
            "queries_issued": self.queries_issued,
            "reverified": self.reverified,
            "reason": self.reason,
            "remediation": self.remediation,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass
class TenantRunner:
    """Resolution of "can we query the tenant, and if not, WHY not?".

    ``reason`` used to be a single string, ``"no_tenant_integration"``, for all
    four outcomes below. Three of them are lies: the tenant IS registered, and
    telling a DC to configure a tenant they already configured sends them to fix
    the wrong thing. The verdict (``pending``) was always right; the explanation
    was not.
    """

    runner: Optional[Any] = None
    tenant: Optional[str] = None
    reason: Optional[str] = None
    remediation: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.runner is not None

    def __iter__(self):
        """Unpack as ``(runner, tenant)``.

        This function is a monkeypatch seam several test modules already use,
        and its historical return type was a 2-tuple. Staying unpackable means
        adding the diagnosis does not break a caller that only wanted the pair.
        """
        yield self.runner
        yield self.tenant


def _as_tenant_runner(value: Any) -> TenantRunner:
    """Accept a :class:`TenantRunner` or the legacy ``(runner, tenant)`` tuple.

    A stubbed ``resolve_tenant_runner`` (tests, and any caller written against
    the old signature) returns the tuple; normalising here means the reason /
    remediation fields degrade to None rather than raising AttributeError.
    """
    if isinstance(value, TenantRunner):
        return value
    runner, tenant = value
    return TenantRunner(runner=runner, tenant=tenant,
                        reason=None if runner is not None else "no_tenant_integration",
                        remediation=(None if runner is not None
                                     else TENANT_REMEDIATION["no_tenant_integration"]))


#: reason -> what the DC should actually do. Deliberately the same shape as
#: ``engine.assertions.REMEDIATION`` rather than a second vocabulary.
TENANT_REMEDIATION: dict[str, str] = {
    "no_tenant_integration": (
        "No Cortex tenant is registered. Add one under Settings → Tenants "
        "(kind 'xsiam_tenant'). Until then every verification resolves `pending`."
    ),
    "tenant_config_invalid": (
        "The registered tenant's config is invalid — base_url must be "
        "https://api-<sub>.(xdr|xsiam).<region>.paloaltonetworks.com and "
        "api_key_id must be set. Re-register the tenant."
    ),
    "credential_undecryptable": (
        "The stored API key cannot be decrypted: CORTEXSIM_SECRET was rotated or "
        "the ciphertext is corrupt. Re-enter the tenant API key."
    ),
    "tenant_client_unavailable": (
        "The tenant is registered but a client could not be built for it. See "
        "the SimCore log for the underlying error."
    ),
    "xql_not_authorised": (
        "This tenant is registered for alert read-back only. XQL verification is "
        "a separate, explicit authorisation because it spends the customer's "
        "query quota — add 'xql' to the tenant's capabilities to enable it."
    ),
}


async def resolve_tenant_runner(
    db: AsyncSession, *, integration: Optional[str] = None,
    timeframe_seconds: int = 3600,
) -> TenantRunner:
    """Build a read-only XQL row-count runner from a registered tenant credential.

    Never raises for a missing/unusable tenant — the caller resolves ``pending``
    and "no tenant wired" is an honest verdict, not a server error. Only an
    explicitly *named* integration that does not exist raises, because that is
    an operator typo rather than a state of the world.
    """
    from engine.verifier import xsiam_query_runner  # noqa: PLC0415
    from integrations.xsiam.exceptions import XsiamConfigError  # noqa: PLC0415
    from integrations.xsiam.guards import xql_capability_granted  # noqa: PLC0415
    from integrations.xsiam.loader import XSIAM_KIND, load_xsiam_client  # noqa: PLC0415
    from security.crypto import CryptoError  # noqa: PLC0415

    store = CredentialStore(db)
    integrations = await store.list_integrations(kind=XSIAM_KIND)
    if not integrations:
        return TenantRunner(reason="no_tenant_integration",
                            remediation=TENANT_REMEDIATION["no_tenant_integration"])
    integ = (next((i for i in integrations if i.name == integration), None)
             if integration else integrations[0])
    if integ is None:
        raise VerifyError("INTEGRATION_NOT_FOUND",
                          f"no {XSIAM_KIND} integration named '{integration}'")

    # Capabilities gate. Registering a tenant grants alert read-back; XQL is a
    # SEPARATE authorisation because it spends a metered resource the customer's
    # own analysts share. An absent list means the legacy default — read only —
    # so migrating an existing record can never silently grant XQL.
    if not xql_capability_granted(integ.config or {}):
        return TenantRunner(tenant=integ.name, reason="xql_not_authorised",
                            remediation=TENANT_REMEDIATION["xql_not_authorised"])

    def _fail(reason: str, detail: str) -> TenantRunner:
        logger.warning("tenant '%s' unusable for XQL (%s): %s", integ.name, reason, detail)
        return TenantRunner(tenant=integ.name, reason=reason,
                            remediation=TENANT_REMEDIATION[reason])

    try:
        client = await load_xsiam_client(db, integ.name)
    except XsiamConfigError as exc:
        return _fail("tenant_config_invalid", str(exc))
    except CryptoError as exc:
        return _fail("credential_undecryptable", str(exc))
    except Exception as exc:  # noqa: BLE001 — resolves to pending, not a 500
        return _fail("tenant_client_unavailable", str(exc))
    return TenantRunner(
        runner=xsiam_query_runner(client, {"relativeTime": timeframe_seconds * 1000}),
        tenant=integ.name,
    )


def _new_budget(max_queries: int) -> dict[str, Any]:
    return {"remaining": max(0, int(max_queries)), "issued": 0, "tripped": False}


def _budgeted(runner: Any, budget: dict[str, Any]) -> Any:
    """Wrap a query runner in a hard query cap + a quota circuit breaker.

    Two budgets, deliberately: the per-sweep ``budget`` dict caps ONE invocation,
    and :data:`integrations.xsiam.ledger.ledger` is the process-wide rolling-day
    ceiling that survives across calls. The per-sweep cap alone was not enough —
    ``POST /verify`` built a fresh one on every request, so it capped nothing.

    The cap lives here rather than inside ``verifier.verify_results`` so the
    verifier stays a pure function of (results, runner) — and so a tripped
    breaker stops further HTTP for the WHOLE sweep, not just the current run.

    Every raise out of this wrapper is caught by ``verify_results`` and recorded
    as ``pending``. A budget CortexSim spent is not evidence about the customer.
    """
    from integrations.xsiam.exceptions import XsiamQuotaError  # noqa: PLC0415
    from integrations.xsiam.ledger import QuotaBreakerOpen  # noqa: PLC0415

    async def _run(query: str) -> int:
        if budget["tripped"] or budget["remaining"] <= 0:
            raise _BudgetExhausted("verification query budget exhausted")
        budget["remaining"] -= 1
        budget["issued"] += 1
        try:
            return await runner(query)
        except (XsiamQuotaError, QuotaBreakerOpen):
            # The customer's analysts share this quota. Stop issuing queries for
            # the rest of THIS sweep. The cross-cycle half — the per-tenant
            # ledger and its breaker — is charged inside the runner itself
            # (`verifier._tenant_budget`) so that assertion runs, which build a
            # runner directly and never reach this wrapper, are bounded too.
            budget["tripped"] = True
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
                     now: datetime, ignore_terminal: bool = False) -> Optional[str]:
    """``None`` when the run should be verified, else why it was skipped.

    ``ignore_terminal`` is what ``?force=true`` buys: re-verifying a settled
    verdict. It deliberately does NOT skip the attempt cap or the backoff below
    — those protect the customer's query quota, not CortexSim's idempotence, and
    an operator holding down a button is exactly the case they exist for.
    """
    if run.status not in _VERIFIABLE_STATUSES:
        return "run_not_finished"
    if run.tc_verdict in _TERMINAL_VERDICTS and not ignore_terminal:
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


def _retry_after(run: Run, *, backoff_seconds: int,
                 now: Optional[datetime] = None) -> Optional[int]:
    """Seconds until this run's backoff expires, for a caller to report."""
    book = _bookkeeping(run)
    attempts = int(book.get("attempts") or 0)
    last = book.get("last_attempt_at")
    if not attempts or not last:
        return None
    try:
        when = datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return None
    delay = min(backoff_seconds * (2 ** (attempts - 1)), 4 * 3600)
    remaining = (when + timedelta(seconds=delay)) - (now or datetime.utcnow())
    return max(0, int(remaining.total_seconds()))


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

    # Attempt cap + backoff on the MANUAL path too. `_verify_eligible` owns both
    # and was called only from the sweep, so `POST /verify` re-queried a stuck
    # run on every request with a brand-new budget and no delay — a retry storm
    # one shell loop away. `force=true` bypasses the TERMINAL check above (that
    # is its documented job); it does not buy an exemption from the cap, because
    # the cap protects the customer's quota, not CortexSim's idempotence.
    if budget is None:
        skip = _verify_eligible(
            run,
            max_attempts=settings.CORTEXSIM_AUTO_VERIFY_MAX_ATTEMPTS,
            backoff_seconds=settings.CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS,
            now=datetime.utcnow(), ignore_terminal=force,
        )
        if skip in ("attempt_cap", "backoff"):
            # 200 with the verdict unchanged, never an error: the run is still
            # `pending` and still owed. Reporting a 429 here would read as a
            # failure of the run rather than a deliberate pause.
            return VerifyOutcome(
                run.run_id, run.tc_verdict, detail=run.tc_verdict_detail or {},
                queries_issued=0, reverified=False, reason=skip,
                retry_after_seconds=_retry_after(
                    run, backoff_seconds=settings.CORTEXSIM_AUTO_VERIFY_BACKOFF_SECONDS)
                if skip == "backoff" else None,
                remediation=(
                    "This run has been verified the maximum number of times without "
                    "the detection appearing in the tenant; it stays `pending`, not "
                    "`fail`. Check ingestion for the datasets its queries name."
                    if skip == "attempt_cap" else
                    "Verification backs off exponentially to protect the tenant's "
                    "query quota. The verdict is unchanged and still owed."
                ),
            )

    if runner is None:
        resolved = _as_tenant_runner(await resolve_tenant_runner(
            db, integration=integration,
            timeframe_seconds=timeframe_seconds or settings.CORTEXSIM_AUTO_VERIFY_TIMEFRAME_SECONDS,
        ))
        runner, tenant = resolved.runner, resolved.tenant
        if runner is None:
            # No usable credential -> pending, never pass. The seeded results
            # keep the `pending` kpi_verdict the orchestrator gave them,
            # score_run's precedence yields `pending`, and there is no path to
            # `pass` without a measurement. The REASON is now specific: a
            # rotated master key is not "no tenant configured".
            score = await score_run_for_run(db, run, source=source, results=results)
            return VerifyOutcome(run.run_id, score.verdict,
                                 detail=run.tc_verdict_detail or {},
                                 tenant=resolved.tenant, reason=resolved.reason,
                                 remediation=resolved.remediation)

    from engine import verifier  # noqa: PLC0415

    if budget is None:
        budget = _new_budget(
            max_queries if max_queries is not None
            else settings.CORTEXSIM_AUTO_VERIFY_MAX_QUERIES_PER_SWEEP)
    budget.setdefault("tenant", tenant)

    before = budget["issued"]
    counts = await verifier.verify_results(
        results, _budgeted(runner, budget),
        agent_profile_mode=getattr(run, "agent_profile_mode", None) or "unknown",
        module_modes=getattr(run, "module_modes", None))
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
            resolved = _as_tenant_runner(
                await resolve_tenant_runner(db, timeframe_seconds=timeframe_seconds))
        except VerifyError as e:  # pragma: no cover — the sweep names no integration
            logger.debug("auto-verify skipped: %s", e.code)
            return stats
        runner, tenant = resolved.runner, resolved.tenant
        stats["tenant"] = tenant
        stats["reason"] = resolved.reason
    if runner is None:
        # "No tenant wired" stays at DEBUG — an operator who never configured one
        # should not get a log line a sweep. But a tenant that IS wired and
        # unusable (rotated master key, invalid config) is a real misconfiguration
        # the DC needs to see, and it used to be indistinguishable from silence.
        reason = stats.get("reason")
        if reason and reason != "no_tenant_integration":
            logger.warning("auto-verify cannot query the tenant (%s): %s",
                           reason, TENANT_REMEDIATION.get(reason, ""))
        else:
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

def _reconcile_bookkeeping(run: Run) -> dict[str, Any]:
    """This run's reconcile bookkeeping out of ``tc_verdict_detail``.

    Sibling of :func:`_bookkeeping` (which owns the ``verification`` block), in
    the same JSON blob and for the same reason: adding ``runs`` columns is a
    migration this change does not own.
    """
    detail = run.tc_verdict_detail
    book = detail.get("reconciliation") if isinstance(detail, dict) else None
    return dict(book) if isinstance(book, dict) else {}


def _reconcile_eligible(run: Run, *, max_attempts: int, backoff_seconds: int,
                        now: datetime) -> Optional[str]:
    """``None`` when the run should be reconciled, else why it was skipped."""
    book = _reconcile_bookkeeping(run)
    attempts = int(book.get("attempts") or 0)
    if attempts >= max_attempts:
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


async def _record_reconcile_attempt(db: AsyncSession, run: Run, *, matched: int,
                                    pulled: int, stopped_reason: Optional[str] = None,
                                    increment: bool = True) -> None:
    """Persist one reconcile attempt into ``tc_verdict_detail["reconciliation"]``.

    A capped-out run MUST be visible. Silently stopping is how a DC watches
    coverage plateau at 40 %, concludes the customer's detections are weak, and
    never learns that CortexSim simply stopped asking. The verdict is untouched
    — polling stopping is not evidence, so it never downgrades to `fail`.
    """
    detail = dict(run.tc_verdict_detail) if isinstance(run.tc_verdict_detail, dict) else {}
    book = _reconcile_bookkeeping(run)
    if increment:
        book["attempts"] = int(book.get("attempts") or 0) + 1
        book["last_attempt_at"] = datetime.utcnow().isoformat()
    book["pulled"] = pulled
    book["matched"] = int(book.get("matched") or 0) + matched
    if stopped_reason:
        book["stopped_reason"] = stopped_reason
    else:
        book.pop("stopped_reason", None)
    detail["reconciliation"] = book
    run.tc_verdict_detail = detail
    await db.commit()


async def auto_reconcile_once(
    db: AsyncSession, *, lookback_seconds: int, window_seconds: int,
    fetcher: Optional[HttpFetcher] = None,
    max_attempts: Optional[int] = None,
    backoff_seconds: Optional[int] = None,
    max_pulls: Optional[int] = None,
) -> dict[str, Any]:
    """One sweep: for every configured connector kind, reconcile each recent run
    that still has unobserved detections. Returns counts for logging.

    A run is a candidate when it started within ``lookback_seconds`` and at
    least one of its results is still unobserved. Connectors with no integration
    are skipped silently.

    **Bounded.** This loop used to have no budget, no attempt cap, no backoff and
    no breaker: every unobserved run in a 24 h window was re-POSTed on every
    300 s sweep, forever. 100 runs in a POV day is ~28,800 alert queries against
    the customer's metered Public API, with the DC unaware. All four controls the
    verify sweep already had now apply here too — they had been built and then
    fitted only to the *cheaper* of the two loops.
    """
    from connectors.base import available_connectors  # noqa: PLC0415
    from integrations.xsiam import codes  # noqa: PLC0415

    from . import tuning  # noqa: PLC0415

    max_attempts = tuning.reconcile_max_attempts(max_attempts)
    backoff_seconds = tuning.reconcile_backoff_seconds(backoff_seconds)
    max_pulls = tuning.reconcile_max_pulls_per_sweep(max_pulls)

    store = CredentialStore(db)
    configured_kinds = {i.kind for i in await store.list_integrations()}
    active_kinds = [c["kind"] for c in available_connectors()
                    if configured_kinds.intersection(credential_kinds_for(c["kind"]))]
    if not active_kinds:
        # No reconcile connector does NOT imply no verification: the two use
        # different credential kinds, and a tenant wired only for XQL is a
        # legitimate configuration.
        return await _with_verify_phase(
            db, {"runs_considered": 0, "runs_reconciled": 0, "newly_matched": 0,
                 "kinds": [], "skipped": {}, "pulls_issued": 0},
            lookback_seconds=lookback_seconds)

    cutoff = datetime.utcnow() - timedelta(seconds=lookback_seconds)
    now = datetime.utcnow()
    runs = list((await db.execute(
        select(Run).where(Run.started_at >= cutoff)
    )).scalars().all())

    runs_considered = 0
    runs_reconciled = 0
    newly_total = 0
    pulls_issued = 0
    # Why a candidate produced nothing, counted by code. `auto_reconcile_once`
    # logged every skip at DEBUG, so a sweep that considered runs and matched
    # zero was indistinguishable from a sweep that did nothing at all — in
    # production that is a completely silent zero-coverage reconcile.
    skipped: dict[str, int] = {}
    breaker_tripped = False

    def _note(code: str) -> None:
        skipped[code] = skipped.get(code, 0) + 1

    for run in runs:
        if pulls_issued >= max_pulls or breaker_tripped:
            _note("sweep_budget_spent")
            continue
        results = list((await db.execute(
            select(Result).where(Result.run_id == run.run_id)
        )).scalars().all())
        if not any((not r.observed) and r.executed_at for r in results):
            continue
        skip = _reconcile_eligible(run, max_attempts=max_attempts,
                                   backoff_seconds=backoff_seconds, now=now)
        if skip:
            _note(skip)
            if skip == "attempt_cap":
                # increment=False: recording WHY we stopped is not another
                # attempt. Written every sweep so the reason cannot be lost to a
                # later Tier-1 re-score.
                await _record_reconcile_attempt(
                    db, run, matched=0, pulled=0, increment=False,
                    stopped_reason=(
                        f"stopped polling after {max_attempts} attempts; no matching "
                        f"alert appeared in the tenant. The verdict stays `pending` — "
                        f"CortexSim stopped asking, the detection did not fail."),
                )
            continue
        runs_considered += 1
        matched_here = 0
        pulled_here = 0
        for kind in active_kinds:
            if pulls_issued >= max_pulls or breaker_tripped:
                _note("sweep_budget_spent")
                break
            pulls_issued += 1
            try:
                outcome = await reconcile_run(
                    db, run, results, connector_kind=kind,
                    window_seconds=window_seconds, fetcher=fetcher,
                )
            except ReconcileError as e:
                _note(e.code)
                # A 429 is the customer's shared quota talking. Stop the whole
                # sweep rather than walking the remaining runs into the same wall.
                if _is_quota_error(e):
                    breaker_tripped = True
                    logger.warning(
                        "auto-reconcile: tenant reported quota exhaustion (%s) — "
                        "sweep circuit-broken. %s", e.code,
                        codes.remediation_for(codes.XSIAM_QUOTA_ERROR))
                else:
                    logger.debug("auto-reconcile skip run=%s kind=%s: %s",
                                 run.run_id, kind, e.code)
                continue
            pulled_here += int(outcome.summary.get("pulled") or 0)
            if outcome.newly_matched:
                matched_here += outcome.newly_matched
        if matched_here:
            runs_reconciled += 1
            newly_total += matched_here
        await _record_reconcile_attempt(db, run, matched=matched_here, pulled=pulled_here)

    stats: dict[str, Any] = {
        "runs_considered": runs_considered,
        "runs_reconciled": runs_reconciled,
        "newly_matched": newly_total,
        "kinds": active_kinds,
        "skipped": skipped,
        "pulls_issued": pulls_issued,
        "quota_tripped": breaker_tripped,
    }
    if runs_considered and not newly_total:
        # A sweep that looked at runs and matched nothing is a SIGNAL, not a
        # no-op: either the detections are not firing or CortexSim is querying
        # the wrong window/tenant. Either way the DC should hear about it.
        logger.warning(
            "auto-reconcile considered %d run(s) and matched 0 detections "
            "(kinds=%s, skipped=%s). Check the tenant credential and that the "
            "run's datasets are ingested.",
            runs_considered, active_kinds, skipped or "{}")

    return await _with_verify_phase(db, stats, lookback_seconds=lookback_seconds)


def _is_quota_error(err: ReconcileError) -> bool:
    """True when a reconcile failure was the tenant's quota, not our config."""
    from integrations.xsiam import codes  # noqa: PLC0415

    detail = err.detail if isinstance(err.detail, dict) else {}
    return (detail.get("code") == codes.XSIAM_QUOTA_ERROR
            or detail.get("status") == 429)


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


async def _refresh_ledgers_phase() -> None:
    """The case-ledger refresh sweep; a failure never stops the reconcile loop."""
    from database import AsyncSessionLocal  # noqa: PLC0415
    try:
        async with AsyncSessionLocal() as db:
            out = await refresh_case_ledgers_once(
                db, window_hours=settings.CORTEXSIM_CASE_LEDGER_WINDOW_HOURS,
                max_runs=settings.CORTEXSIM_CASE_LEDGER_MAX_RUNS_PER_SWEEP,
                min_interval_minutes=settings.CORTEXSIM_CASE_LEDGER_MIN_INTERVAL_MINUTES)
        if out["refreshed"] or out["skipped"]:
            logger.info("case-ledger refresh: %s", out)
    except Exception:  # pragma: no cover - defensive
        logger.exception("case-ledger refresh phase failed - continuing")


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
                if not stats.get("quota_tripped"):
                    # Same flag (this loop only runs with it on), same breaker:
                    # a quota error in the reconcile sweep skips the refresh.
                    await _refresh_ledgers_phase()
            except Exception:  # pragma: no cover - defensive
                logger.exception("auto-reconcile sweep failed — continuing")
    except asyncio.CancelledError:  # pragma: no cover - shutdown path
        logger.info("auto-reconcile loop cancelled")
        raise
