"""
CortexSim — ``GET /api/health``: the one diagnostic surface.

This is the endpoint a Domain Consultant hits when something is wrong in front
of a customer, so it obeys four rules that the previous inline implementation
did not:

1. **It never reports green for something it did not check.** A catalog whose
   entire purpose is to be non-empty and is empty is ``degraded``, not ``ok``.
   Before this module, booting without ``tools/`` (every ``adapter_ref`` in the
   corpus unresolvable, the payload shelf able to stage nothing) reported
   ``adapter_catalog: {status: ok, count: 0}`` and an overall ``ok``.
2. **It never makes an outbound call.** Not to a customer tenant, not to a log
   collector, not to an agent. A health check that goes green because it pinged
   something is spending the customer's quota to manufacture confidence; a
   health check that goes green because it pinged *itself* is worse than none.
   Everything reachability-shaped is listed in ``not_checked`` with the endpoint
   that *does* check it.
3. **Every non-ok component carries a machine ``code`` and a remediation
   ``detail``** — the same contract as the installer's exit codes, which is what
   made "ran the one-liner, nothing appeared" answerable.
4. **It degrades, it does not fail.** Every probe is independently guarded and
   the response is always HTTP 200 so a probe can read the detail. Overall
   ``status`` is ``degraded`` when any component is not ``ok``.

``degraded_components`` is a flat top-level list so the console's context bar can
render a banner without walking the component map.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter

from config import settings

logger = logging.getLogger("cortexsim.api.health")

router = APIRouter(tags=["health"])

APP_VERSION = "1.0.0"

OK = "ok"
DEGRADED = "degraded"
ERROR = "error"


# ---------------------------------------------------------------------------
# What this endpoint deliberately does NOT verify.
#
# Named explicitly and returned in the body. A DC reading a green health check
# must be able to see the boundary of the claim: "these components loaded" is
# not "the POV will work", and the difference is exactly the set below.
# ---------------------------------------------------------------------------

NOT_CHECKED: tuple[dict[str, str], ...] = (
    {
        "what": "xsiam_tenant_reachability",
        "why": "a health probe must not spend the customer's API quota, and must "
               "not claim reachability it did not measure",
        "checked_by": "POST /api/xsiam/tenants/{name}/test",
    },
    {
        "what": "eal_collector_reachability",
        "why": "reaching a Broker VM / HTTP log collector is an outbound POST from "
               "this host and belongs to an operator action, not a liveness probe",
        "checked_by": "GET /api/eal/campaigns/{id}/preflight",
    },
    {
        "what": "agent_target_reachability",
        "why": "the beacon is pull-model — SimCore never dials the target. Liveness "
               "below is derived from last_seen age, not from a probe",
        "checked_by": "GET /api/agents (last_seen age) and the run's own step output",
    },
    {
        "what": "payload_upstream_origins",
        "why": "the shelf exists so the target never fetches from the public "
               "internet; SimCore fetching upstream at health time would defeat it",
        "checked_by": "scripts/build-payloads.sh (at build time, with the pin)",
    },
    {
        "what": "detection_efficacy",
        "why": "whether a detection fired is a measurement against a tenant, not a "
               "property of this process",
        "checked_by": "POST /api/runs/{id}/reconcile and POST /api/runs/{id}/verify",
    },
)


# ---------------------------------------------------------------------------
# Probe plumbing
# ---------------------------------------------------------------------------


def _guard(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one synchronous probe, converting any exception into a reported
    component rather than a 500.

    A probe that throws is itself a finding — it must be visible, not swallowed
    into an ``ok`` that hides it.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a failed probe is a reported state
        logger.warning("health probe %s failed: %s", name, exc)
        return {
            "status": ERROR,
            "code": "PROBE_FAILED",
            "detail": (
                f"the {name} probe raised {type(exc).__name__}: {exc}. This is a "
                f"defect in SimCore, not in the deployment — capture the server log "
                f"and report it. "
                f"Fix: capture the server log around this timestamp and open an "
                f"issue; a probe that throws is a SimCore bug, so there is nothing "
                f"to reconfigure on this host."
            ),
        }


def _ok(**extra: Any) -> dict[str, Any]:
    return {"status": OK, "code": "OK", **extra}


def _degraded(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": DEGRADED, "code": code, "detail": detail, **extra}


# ---------------------------------------------------------------------------
# Build identity
# ---------------------------------------------------------------------------


def commit_sha() -> str:
    """Best-effort build/commit identifier.

    Tries (in order): ``CORTEXSIM_COMMIT_SHA`` / ``GIT_COMMIT`` / ``SOURCE_COMMIT``
    env vars, a stamped file at ``{BASE_DIR}/COMMIT_SHA``, then ``git rev-parse``
    if a checkout is present. Returns ``"unknown"`` when none resolve — never
    raises (a health probe must not fail because the build wasn't stamped)."""
    for var in ("CORTEXSIM_COMMIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        val = os.environ.get(var)
        if val:
            return val.strip()[:40]
    stamp = os.path.join(settings.CORTEXSIM_BASE_DIR, "COMMIT_SHA")
    try:
        if os.path.isfile(stamp):
            with open(stamp, encoding="utf-8") as fh:
                line = fh.readline().strip()
                if line:
                    return line[:40]
    except OSError:  # pragma: no cover - defensive
        pass
    try:
        import subprocess  # noqa: PLC0415

        out = subprocess.run(
            ["git", "-C", settings.CORTEXSIM_BASE_DIR, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # pragma: no cover - defensive
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Individual probes — each returns one component dict.
#
# Sync probes take no arguments and read in-process singletons or the
# filesystem. Async probes take a session factory. Splitting them keeps the
# guard simple and the whole set independently unit-testable.
# ---------------------------------------------------------------------------


def probe_ttp_catalog() -> dict[str, Any]:
    from engine.ttp_catalog import catalog as ttp_catalog  # noqa: PLC0415

    count = len(ttp_catalog.all_entries())
    if count == 0:
        return _degraded(
            "TTP_CATALOG_EMPTY",
            "0 TTP detection cards loaded — detection_scanner/ttps/ is missing from "
            "this deployment or every card was rejected. Every scenario's ttp_ref "
            "will dangle and the POV report will carry no deployable detection "
            "content. Fix: confirm core/Dockerfile COPYs detection_scanner/ and "
            "check the boot log for card rejections.",
            count=0,
        )
    return _ok(count=count)


def probe_assertion_catalog() -> dict[str, Any]:
    """The POS/PLT/AUT corpus — the half of the index scenarios cannot close.

    This probe exists because its absence is what let the gap ship: every other
    base-dir-relative content directory had a probe saying "confirm the image
    COPYs it", assertions/ did not, and so 18 authored artifacts loaded as 0 in
    every deployed image with `rejected: []` and no boot error. An empty
    catalog and an unshipped one are the same response, which is the reading
    this probe exists to break.
    """
    from engine.assertions import catalog as assertion_catalog  # noqa: PLC0415

    cat = assertion_catalog.ensure_loaded()
    count = len(cat._by_id)
    rejected = len(cat._rejected)
    if count == 0:
        return _degraded(
            "ASSERTION_CATALOG_EMPTY",
            "0 assertions loaded — assertions/ is missing from this deployment "
            "or every artifact was rejected. GET /api/assertions will serve an "
            "empty list that reads as 'none authored' rather than 'none "
            "shipped', and every test case evidenced only by an assertion "
            "silently loses its evidence. Fix: confirm core/Dockerfile COPYs "
            "assertions/ and check the boot log for A-* rejections.",
            count=0,
            rejected=rejected,
        )
    return _ok(count=count, rejected=rejected)


def probe_adapter_catalog() -> dict[str, Any]:
    from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415

    count = adapter_catalog.count()
    if count == 0:
        return _degraded(
            "ADAPTER_CATALOG_EMPTY",
            "0 adapter packs loaded — tools/ is missing from this deployment "
            "(core/Dockerfile must COPY tools/). Every scenario's adapter_ref will "
            "be unresolvable, the payload shelf can stage nothing, and push bundles "
            "will emit no tool installs. Fix: rebuild the image, or set "
            "CORTEXSIM_BASE_DIR at a checkout that contains tools/packs/.",
            count=0,
        )
    return _ok(count=count)


def probe_uctc_registry() -> dict[str, Any]:
    from engine.uctc_registry import registry as uctc_registry  # noqa: PLC0415

    count = len(uctc_registry.all_test_cases())
    if not uctc_registry.loaded or count == 0:
        return _degraded(
            "UCTC_SNAPSHOT_ABSENT",
            "the FY27 UC/TC index snapshot did not load — docs/uc_tc_mapping/"
            "_v2.2-source/ is missing from this deployment. Scenario uc_ref/tc_ref "
            "validation degrades to advisory (a dangling ref will NOT be rejected "
            "even with CORTEXSIM_STRICT_REFS=true) and /api/uctc returns "
            "index_loaded:false. Render that as degraded, never as '0 test cases'. "
            "Fix: confirm core/Dockerfile COPYs docs/uc_tc_mapping/.",
            count=count,
            version=uctc_registry.version or None,
            strict_refs=bool(settings.CORTEXSIM_STRICT_REFS),
        )
    return _ok(
        count=count,
        version=uctc_registry.version or None,
        strict_refs=bool(settings.CORTEXSIM_STRICT_REFS),
    )


def probe_eal() -> dict[str, Any]:
    from eal_simulator import get_default_registry  # noqa: PLC0415

    plugins = len(get_default_registry().manifest())
    if plugins == 0:
        return _degraded(
            "EAL_REGISTRY_EMPTY",
            "0 EAL simulator plugins registered — the signal shapes the identity "
            "harness cannot produce (IdP sign-ins, collector-POSTed audit logs, "
            "LLM egress, browser drive) are all unavailable. Fix: check the boot "
            "log for a plugin import error.",
            plugins=0,
        )
    return _ok(plugins=plugins)


def probe_xsiam_operation_catalog() -> dict[str, Any]:
    """The ~116 declarative Cortex Platform API operation packs.

    This probe existed but was UNREACHABLE — merge 0ef802b orphaned it after
    ``return components``, so it never appeared in a live response. Re-attached
    here, and ``test_health.py`` now asserts every component this module defines
    is present in the payload so a careless merge cannot silently drop one
    again.
    """
    from integrations.xsiam.operations.catalog import catalog as op_catalog  # noqa: PLC0415

    count = op_catalog.count()
    if count == 0:
        return _degraded(
            "XSIAM_OPERATION_CATALOG_EMPTY",
            "0 XSIAM API operation packs loaded — core/integrations/xsiam/operations/"
            "packs/ is missing from this deployment. Tenant health/metrics reads and "
            "the operation harness will 404 on every operation id. Fix: confirm the "
            "image COPYs core/integrations/.",
            count=0,
        )
    return _ok(count=count)


def probe_agent_binaries() -> dict[str, Any]:
    """Which prebuilt beacons this SimCore can hand to an endpoint.

    A short matrix is not a crash — the installer degrades to a source build and
    says so — but it IS the difference between a one-line enrollment and a DC
    discovering mid-POV that the Windows installer refuses.
    """
    from api.agents import _agent_dist_dir, _available_binaries  # noqa: PLC0415

    dist_dir = str(_agent_dist_dir())
    binaries = _available_binaries()
    have = {f"{b['os']}/{b['arch']}" for b in binaries}
    want = {f"{goos}/amd64" for goos in ("linux", "darwin", "windows")}
    missing = sorted(want - have)

    if not binaries:
        return _degraded(
            "AGENT_DIST_EMPTY",
            f"no prebuilt beacons in {dist_dir} — GET /api/agents/binary returns 404 "
            f"for every target and the one-line installer falls back to a source "
            f"build, which needs a Go toolchain on the customer host. Fix: run "
            f"`make agent-dist` on the SimCore host, or rebuild the image (the "
            f"agent-builder stage bakes the matrix in).",
            dist_dir=dist_dir, available=[], missing=sorted(want),
        )
    if missing:
        return _degraded(
            "AGENT_DIST_INCOMPLETE",
            f"prebuilt beacons are missing for {', '.join(missing)}. "
            f"GET /api/agents/binary?os=windows will 404 and the PowerShell "
            f"installer will refuse WINDOWS_AGENT_UNAVAILABLE. Fix: run "
            f"`make agent-dist` (it cross-compiles linux/darwin/windows) and rebuild.",
            dist_dir=dist_dir, available=sorted(have), missing=missing,
        )
    return _ok(dist_dir=dist_dir, available=sorted(have), missing=[])


def probe_payload_shelf(scenarios: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Declared-vs-staged accounting for the tool artifact shelf.

    ``declared_artifacts`` is called WITH ``scenarios=`` — the same way the CLI
    does — so ``used_by`` is populated and the remediation can name the
    scenarios that will refuse at compose time rather than just a count.
    """
    from api.payloads import inventory, payload_dist_dir  # noqa: PLC0415
    from engine import payload_shelf  # noqa: PLC0415

    dist_dir = str(payload_dist_dir())
    declared = payload_shelf.declared_artifacts(scenarios=scenarios or [])
    on_shelf = {p["name"] for p in inventory()}

    missing = [a for a in declared if a.name not in on_shelf]
    unpinned = [a for a in declared if not a.pinned]

    blocked: list[str] = []
    for art in missing:
        used = (art.used_by or "").strip()
        if used and not used.startswith("("):
            blocked.extend(s.strip() for s in used.split(",") if s.strip())
    blocked = sorted(set(blocked))

    common = {
        "declared": len(declared),
        "staged": len(declared) - len(missing),
        "unpinned": len(unpinned),
        "dist_dir": dist_dir,
        "missing": sorted(a.name for a in missing),
        "blocks_scenarios": blocked,
    }

    if unpinned:
        return _degraded(
            "SHELF_ARTIFACT_UNPINNED",
            f"{len(unpinned)} declared artifact(s) carry no sha256 pin "
            f"({', '.join(sorted(a.name for a in unpinned))}). An unpinned artifact "
            f"cannot be verified on the target, so a tampered or truncated file "
            f"would stage and run. Fix: add install.artifact.sha256 to the pack and "
            f"re-run scripts/build-payloads.sh.",
            **common,
        )
    if missing:
        # Built outside the f-string: a nested quote inside an f-string
        # expression is a syntax error before Python 3.12 and this image is 3.11.
        blocked_clause = (
            "Scenarios binding them will refuse at compose with PAYLOAD_NOT_STAGED: "
            + ", ".join(blocked) + ". "
        ) if blocked else ""
        return _degraded(
            "SHELF_ARTIFACTS_MISSING",
            f"{len(declared)} adapter pack(s) declare a staged artifact; "
            f"{common['staged']} are on the shelf at {dist_dir}. {blocked_clause}"
            f"Fix: ./scripts/build-payloads.sh then rebuild the image, or scp the "
            f"artifacts in and set CORTEXSIM_PAYLOAD_DIST.",
            **common,
        )
    if not declared:
        # No pack declares an artifact at all. That is only reachable when the
        # adapter catalog is empty — which its own probe already reports — so
        # this stays 'ok' rather than double-counting the same deployment fault.
        return _ok(**common)
    return _ok(**common)


# -- async probes -----------------------------------------------------------


async def probe_db(session_factory) -> dict[str, Any]:
    from sqlalchemy import text  # noqa: PLC0415

    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return {
            "status": ERROR,
            "code": "DB_UNREACHABLE",
            "detail": (
                f"SELECT 1 failed against the run-history database: "
                f"{type(exc).__name__}: {exc}. Run history, the durable task queue "
                f"and credentials are all unavailable; scenarios/cards/adapters "
                f"still load from disk. Fix: see the DB_* boot diagnostics in the "
                f"server log — usually a read-only data/ mount or a corrupt file."
            ),
        }
    return _ok()


async def probe_scenario_catalog(session_factory) -> dict[str, Any]:
    from sqlalchemy import func, select  # noqa: PLC0415
    from models import Scenario  # noqa: PLC0415

    async with session_factory() as session:
        count = int(await session.scalar(select(func.count()).select_from(Scenario)) or 0)
    if count == 0:
        return _degraded(
            "SCENARIO_CATALOG_EMPTY",
            "0 scenarios loaded — scenarios/ is missing from this deployment or "
            "every file was rejected by the schema validator. Nothing can be "
            "launched. Fix: check the boot log for 'Scenarios loaded: 0' and the "
            "per-file rejection lines above it; confirm the image COPYs scenarios/.",
            count=0,
        )
    return _ok(count=count)


async def probe_agents(session_factory) -> dict[str, Any]:
    """Agent liveness, derived from ``last_seen`` age — never from a probe.

    SimCore is pull-model: it does not dial the beacon. Reporting 'agents are
    healthy' would be a claim this process cannot make.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from api.agents import _derive_status  # noqa: PLC0415
    from models import Agent  # noqa: PLC0415

    now = datetime.utcnow()
    async with session_factory() as session:
        rows = (await session.execute(select(Agent))).scalars().all()

    buckets: dict[str, int] = {"online": 0, "stale": 0, "offline": 0}
    for agent in rows:
        status, _age = _derive_status(agent.last_seen, now)
        buckets[status] = buckets.get(status, 0) + 1

    common = {"registered": len(rows), **buckets,
              "derived_from": "last_seen age; no outbound probe is made"}

    if not rows:
        # Deliberately OK, not degraded. A fresh SimCore, and any push-only
        # deployment, has no agents by design — flagging that amber forever
        # would make the banner background noise, and a banner nobody reads is
        # how the real degradation gets missed. Nothing is broken until an
        # intent has been declared (an agent enrolled) that is not being met,
        # which is the branch below.
        return _ok(
            code_hint="NO_AGENTS_ENROLLED",
            detail="no agents are enrolled. Pull-mode launches have nowhere to "
                   "go until one is; push mode needs none. Enrol with "
                   "POST /api/agents/enroll/tokens then the one-liner from "
                   "GET /api/agents/install?os=linux.",
            **common,
        )
    if buckets["online"] == 0:
        return _degraded(
            "NO_AGENT_ONLINE",
            f"{len(rows)} agent(s) are enrolled but none has checked in within 30s "
            f"({buckets['stale']} stale, {buckets['offline']} offline). A pull-mode "
            f"launch will queue a task nothing collects. Fix: on the target check "
            f"`systemctl status cortexsim-agent` (or launchctl), and confirm it can "
            f"reach this SimCore's /api/agents/{{id}}/tasks.",
            **common,
        )
    return _ok(**common)


async def probe_task_queue(session_factory) -> dict[str, Any]:
    """Durable queue depth, and whether anything is queued for a dead agent.

    Depth alone is not a fault — a task is queued for a couple of seconds on
    every launch. Depth for an agent that is offline IS a fault, and it is the
    shape of "I launched it and nothing happened".
    """
    from sqlalchemy import select  # noqa: PLC0415

    from api.agents import _derive_status  # noqa: PLC0415
    from models import Agent, QueuedTask  # noqa: PLC0415

    now = datetime.utcnow()
    async with session_factory() as session:
        tasks = (await session.execute(select(QueuedTask))).scalars().all()
        agents = (await session.execute(select(Agent))).scalars().all()

    liveness = {a.agent_id: _derive_status(a.last_seen, now)[0] for a in agents}
    per_agent: dict[str, int] = {}
    for task in tasks:
        per_agent[task.agent_id] = per_agent.get(task.agent_id, 0) + 1

    stranded = {
        agent_id: n for agent_id, n in per_agent.items()
        if liveness.get(agent_id, "offline") != "online"
    }
    common = {"queued": len(tasks), "per_agent": dict(sorted(per_agent.items()))}

    if stranded:
        listing = ", ".join(f"{a} ({n})" for a, n in sorted(stranded.items()))
        return _degraded(
            "TASKS_QUEUED_FOR_UNAVAILABLE_AGENT",
            f"{sum(stranded.values())} task(s) are queued for agent(s) that are not "
            f"online: {listing}. The run will sit in 'pending' until the beacon "
            f"polls — it is NOT lost (the queue is durable and survives a SimCore "
            f"restart). Fix: bring the beacon back, or abort the run with "
            f"POST /api/runs/{{id}}/abort and relaunch against a live agent.",
            stranded=dict(sorted(stranded.items())), **common,
        )
    return _ok(stranded={}, **common)


async def probe_integrations(session_factory) -> dict[str, Any]:
    """Configured tenant credentials — read from the DB ONLY.

    This component makes no tenant call and must never imply reachability. It
    reports what is configured and when each credential last answered a
    deliberate operator-run test, and says so in its own detail so nobody reads
    ``ok`` as "the tenant is up".

    The two XSIAM credential kinds are reported separately on purpose:
    configuring alert read-back (``xsiam``) must not silently authorise XQL
    against the tenant (``xsiam_tenant``).
    """
    from sqlalchemy import select  # noqa: PLC0415

    from models import IntegrationCredential  # noqa: PLC0415

    async with session_factory() as session:
        rows = (await session.execute(select(IntegrationCredential))).scalars().all()

    by_kind: dict[str, int] = {}
    verified = 0
    failing: list[str] = []
    for row in rows:
        by_kind[row.kind] = by_kind.get(row.kind, 0) + 1
        if row.last_verified_ok:
            verified += 1
        elif row.last_verified_at is not None:
            failing.append(row.name)

    detail = (
        "NOT a liveness probe. No tenant call is made by /api/health. 'verified' "
        "counts credentials whose last operator-run test succeeded; 0 means no "
        "tenant has ever answered. Reconcile (kind 'xsiam') and XQL verification "
        "(kind 'xsiam_tenant') are separate credentials on purpose — configuring "
        "read-back does not authorise XQL."
    )
    common = {
        "configured": len(rows),
        "by_kind": dict(sorted(by_kind.items())),
        "verified": verified,
        "detail": detail,
    }
    if failing:
        return {
            "status": DEGRADED,
            "code": "INTEGRATION_LAST_TEST_FAILED",
            **common,
            "last_test_failed": sorted(failing),
            "detail": (
                f"{detail} The last test FAILED for: {', '.join(sorted(failing))}. "
                f"Fix: re-run POST /api/xsiam/tenants/{{name}}/test and read the "
                f"structured error."
            ),
        }
    return {"status": OK, "code": "OK", **common}


async def probe_eal_collectors(session_factory) -> dict[str, Any]:
    """Collector endpoints declared by persisted campaigns.

    Reachability is deliberately NOT probed (see ``NOT_CHECKED``) — that is an
    outbound POST and belongs to ``/api/eal/campaigns/{id}/preflight``. What IS
    checkable here without leaving the process is the configuration fault that
    guarantees a refusal: a collector host that is not in the campaign's own
    ``target_allowlist``, which the safety policy will refuse before any bytes
    go out.
    """
    from urllib.parse import urlparse  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from models import EalCampaign  # noqa: PLC0415

    async with session_factory() as session:
        campaigns = (await session.execute(select(EalCampaign))).scalars().all()

    hosts: set[str] = set()
    violations: list[dict[str, str]] = []
    for campaign in campaigns:
        allow = {str(h).lower() for h in (campaign.target_allowlist or [])}
        for step in (campaign.spec or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            url = ((step.get("params") or {}).get("collector_url")) or ""
            if not url:
                continue
            host = (urlparse(url).hostname or "").lower()
            if not host:
                continue
            hosts.add(host)
            if allow and host not in allow:
                violations.append({
                    "campaign_id": campaign.campaign_id,
                    "step_id": str(step.get("step_id") or step.get("id") or "?"),
                    "host": host,
                })

    common = {
        "campaigns": len(campaigns),
        "collector_hosts": sorted(hosts),
        "reachability_checked": False,
    }
    if violations:
        return _degraded(
            "COLLECTOR_NOT_IN_ALLOWLIST",
            f"{len(violations)} campaign step(s) point at a collector host that is "
            f"not in that campaign's target_allowlist, so the safety policy will "
            f"refuse them with target_not_authorised before any bytes are sent. "
            f"Fix: add the host to the campaign's target_allowlist, or correct "
            f"collector_url.",
            allowlist_violations=violations, **common,
        )
    return _ok(**common)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

#: Every component key this module produces. ``test_health.py`` asserts the live
#: response carries exactly these — the structural guard against a merge
#: orphaning a probe the way 0ef802b orphaned ``xsiam_operation_catalog``.
COMPONENT_KEYS: tuple[str, ...] = (
    "db",
    "scenario_catalog",
    "ttp_catalog",
    "assertion_catalog",
    "adapter_catalog",
    "uctc_registry",
    "eal",
    "xsiam_operation_catalog",
    "payload_shelf",
    "agent_binaries",
    "agents",
    "task_queue",
    "integrations",
    "eal_collectors",
)


async def _scenario_tool_rows(session_factory) -> list[dict[str, Any]]:
    """``[{scenario_id, external_tools}]`` for the shelf's ``used_by`` join.

    Read from the DB rather than re-walking scenarios/ so the probe costs one
    query, not 170 YAML parses.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from models import Scenario  # noqa: PLC0415

    async with session_factory() as session:
        rows = (await session.execute(
            select(Scenario.scenario_id, Scenario.external_tools)
        )).all()
    return [{"scenario_id": sid, "external_tools": tools or []} for sid, tools in rows]


async def component_health() -> dict[str, dict[str, Any]]:
    """Every component probe, each independently guarded.

    Ordered by ``COMPONENT_KEYS`` so the response reads top-down from "can this
    process store anything" to "is anything configured to leave it".
    """
    from database import AsyncSessionLocal  # noqa: PLC0415

    components: dict[str, dict[str, Any]] = {}

    async def _aguard(name: str, coro_fn) -> dict[str, Any]:
        try:
            return await coro_fn(AsyncSessionLocal)
        except Exception as exc:  # noqa: BLE001
            logger.warning("health probe %s failed: %s", name, exc)
            return {
                "status": ERROR,
                "code": "PROBE_FAILED",
                "detail": (
                    f"the {name} probe raised {type(exc).__name__}: {exc}. When the "
                    f"db component is also failing this is a consequence of that, "
                    f"not a separate fault. "
                    f"Fix: check the `db` component first — if it is failing, repair "
                    f"that and re-check; the database at CORTEXSIM_BASE_DIR/data/ "
                    f"must exist and be writable, and schema creation runs at "
                    f"startup, so a missing table means SimCore never completed its "
                    f"lifespan init."
                ),
            }

    components["db"] = await _aguard("db", probe_db)
    components["scenario_catalog"] = await _aguard("scenario_catalog", probe_scenario_catalog)
    components["ttp_catalog"] = _guard("ttp_catalog", probe_ttp_catalog)
    components["assertion_catalog"] = _guard("assertion_catalog", probe_assertion_catalog)
    components["adapter_catalog"] = _guard("adapter_catalog", probe_adapter_catalog)
    components["uctc_registry"] = _guard("uctc_registry", probe_uctc_registry)
    components["eal"] = _guard("eal", probe_eal)
    components["xsiam_operation_catalog"] = _guard(
        "xsiam_operation_catalog", probe_xsiam_operation_catalog
    )

    # The shelf probe needs the scenario→adapter_ref join for `used_by`; a DB
    # failure must not take the shelf probe down with it, so the join degrades
    # to an empty list (counts stay correct, `blocks_scenarios` goes empty).
    try:
        scenario_rows = await _scenario_tool_rows(AsyncSessionLocal)
    except Exception:  # noqa: BLE001
        scenario_rows = []
    components["payload_shelf"] = _guard(
        "payload_shelf", lambda: probe_payload_shelf(scenario_rows)
    )

    components["agent_binaries"] = _guard("agent_binaries", probe_agent_binaries)
    components["agents"] = await _aguard("agents", probe_agents)
    components["task_queue"] = await _aguard("task_queue", probe_task_queue)
    components["integrations"] = await _aguard("integrations", probe_integrations)
    components["eal_collectors"] = await _aguard("eal_collectors", probe_eal_collectors)
    return components


async def health_payload() -> dict[str, Any]:
    components = await component_health()
    degraded = sorted(
        name for name, comp in components.items() if comp.get("status") != OK
    )
    return {
        "status": OK if not degraded else DEGRADED,
        "version": APP_VERSION,
        "commit": commit_sha(),
        "env": settings.CORTEXSIM_ENV,
        "components": components,
        "degraded_components": degraded,
        "checked": list(COMPONENT_KEYS),
        "not_checked": [dict(item) for item in NOT_CHECKED],
    }


@router.get("/health")
async def health_check():
    """Liveness + readiness (GAP-API-007).

    Always HTTP 200 — a probe has to be able to READ the detail, and a 503 that
    a load balancer swallows tells a DC nothing. ``status`` is ``ok`` only when
    every component is ``ok``; ``degraded_components`` names the ones that are
    not, and ``not_checked`` names what this endpoint deliberately did not
    verify so a green result is never over-read.
    """
    return await health_payload()
