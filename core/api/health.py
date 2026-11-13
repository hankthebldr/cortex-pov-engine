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
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request

from config import settings
from engine.dataset_requirements import load as dataset_requirements_load

logger = logging.getLogger("cortexsim.api.health")

router = APIRouter(tags=["health"])

APP_VERSION = "1.1.1"

# When THIS process started. The console header states the instance's uptime,
# and only SimCore's own clock can say it: the wall time is for display, the
# monotonic one for the arithmetic, so a clock step on the host never makes the
# uptime run backwards.
_PROCESS_STARTED_AT = datetime.now(timezone.utc)
_PROCESS_STARTED_MONO = time.monotonic()


def uptime_seconds() -> int:
    """Whole seconds since this SimCore process started."""
    return max(0, int(time.monotonic() - _PROCESS_STARTED_MONO))

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
        "what": "socfw_response_layer",
        "why": "presence and Auto Triage settings need tenant reads, which health never makes",
        "checked_by": "POST /api/connectors/xsiam_tenant/preflight (stage response_layer)",
    },
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


def probe_identity_spec() -> dict[str, Any]:
    """spec/identity_harness.json — every push bundle's run_as arm and S-17.

    An absent spec is served from a built-in copy pinned identical to it, so the
    bundles stay right and nothing used to say anything. That is how a deployed
    image ran without spec/ (tests/tools/test_image_ships_runtime_content.py):
    right output, broken deployment, no signal. A malformed spec raises
    everywhere it is read; this names it without making the probe the failure.
    """
    from engine import identity_spec  # noqa: PLC0415

    try:
        source = identity_spec.spec_source()
    except identity_spec.IdentitySpecError as exc:
        return _degraded(
            "IDENTITY_SPEC_MALFORMED",
            f"{exc} Until it parses, push-bundle generation and the S-17 identity "
            f"check raise, and SimCore will not boot with scenarios present. "
            f"Fix: restore spec/identity_harness.json from this build's source "
            f"tree and restart SimCore.",
            path=identity_spec._spec_path(),
        )
    if source["source"] == "fallback":
        return _degraded(
            "IDENTITY_SPEC_ABSENT",
            f"no identity harness spec at {source['path']} — SimCore is serving its "
            f"built-in copy, which a test pins identical to the spec this build "
            f"shipped, so push bundles and S-17 are unaffected. The deployment is "
            f"still wrong: spec/ was not shipped, or CORTEXSIM_BASE_DIR points at "
            f"the wrong tree and other base-dir content is likely missing with it. "
            f"Fix: confirm core/Dockerfile COPYs spec/, or set CORTEXSIM_BASE_DIR "
            f"to the checkout root, then restart SimCore (the spec is read once).",
            **source,
        )
    return _ok(**source)


def probe_dataset_requirements() -> dict[str, Any]:
    """spec/dataset_requirements.yml — what each dataset needs on the Cortex and source side.

    ``ok`` only when the registry loaded AND every dataset a card queries has an entry.
    An absent registry is degraded, never "no requirements": every card dataset is then
    unregistered. Reads local files only; no outbound call.
    """
    from engine import dataset_requirements as dr  # noqa: PLC0415
    from engine.ttp_catalog import catalog  # noqa: PLC0415

    path = dr.registry_path()
    try:
        registry = dataset_requirements_load()
    except dr.DatasetRequirementsError as exc:
        return _degraded(
            "DATASET_REGISTRY_MALFORMED",
            f"{exc} Until it parses, /api/prerequisites/* return a structured 500. "
            f"Fix: restore spec/dataset_requirements.yml from this build's source "
            f"tree. A malformed registry is not cached: it is re-read on every call, "
            f"so the fix takes effect without a restart.",
            path=path,
        )
    if registry is None:
        return _degraded(
            "DATASET_REGISTRY_ABSENT",
            f"no dataset requirement registry at {path} — scenario prerequisites "
            f"cannot be derived, and every card dataset reads as unregistered. "
            f"Fix: confirm core/Dockerfile COPYs spec/, or set CORTEXSIM_BASE_DIR to "
            f"the checkout root, then restart SimCore.",
            path=path,
        )
    try:
        unregistered = sorted(set(dr.card_datasets(catalog.all_raw())) - set(registry))
    except dr.DatasetRequirementsError as exc:
        return _degraded(
            "CARD_SHAPE_UNRECOGNISED",
            f"{exc} Until the card is fixed, its datasets cannot be checked against the "
            f"registry and /api/prerequisites/* return a structured 500. "
            f"Fix: correct the detections block on that card in detection_scanner/ttps/ "
            f"and restart SimCore.",
            path=path,
        )
    if unregistered:
        return _degraded(
            "DATASETS_UNREGISTERED",
            f"{len(unregistered)} dataset(s) read by detection cards have no registry "
            f"entry: {', '.join(unregistered)}. Fix: add them to {path}.",
            path=path, count=len(registry), unregistered=unregistered,
        )
    return _ok(path=path, count=len(registry), unregistered=[])


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


_SHIPPED_BEACON_TARGETS = ("linux/amd64", "linux/arm64", "darwin/amd64",
                           "darwin/arm64", "windows/amd64")


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
    # The shipped matrix (scripts/build-agent-dist.sh and the core/Dockerfile
    # agent-builder stage; tests/installer/test_agent_dist_matrix_parity.py
    # holds those two together). This used to want <goos>/amd64 only, so a
    # shelf without the arm64 beacons read ok while an Apple-silicon Mac or an
    # arm64 Linux host got a 404 and a source-build fallback.
    want = set(_SHIPPED_BEACON_TARGETS)
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
        consequence = (
            "GET /api/agents/binary 404s for those targets and the POSIX installer "
            "falls back to a source build, which needs Go on the host")
        if "windows/amd64" in missing:
            consequence = (
                "GET /api/agents/binary?os=windows will 404 and the PowerShell "
                "installer will refuse WINDOWS_AGENT_UNAVAILABLE"
                + ("; the other targets fall back to a source build"
                   if len(missing) > 1 else ""))
        return _degraded(
            "AGENT_DIST_INCOMPLETE",
            f"prebuilt beacons are missing for {', '.join(missing)}. {consequence}. "
            f"Fix: run `make agent-dist` (it cross-compiles the five shipped targets) "
            f"and rebuild.",
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


def probe_api_auth() -> dict[str, Any]:
    """SEC-01 / SEC-10 — what this deployment exposes, stated as a component.

    There is no API authentication in SimCore yet, so this is permanently
    ``degraded`` (code ``API_UNAUTHENTICATED``). That is deliberate and it is
    the repo's own rule: a zero is degraded, not ok. A deployment anyone on the
    network can drive must not read green, and the overall status of every
    current deployment is therefore ``degraded`` until auth lands.

    It flips only when an authentication mechanism exists to inspect — never
    because a bind address or firewall rule *looks* narrow, which this process
    cannot verify (a 127.0.0.1 publish behind a port-forward is still exposed).
    """
    from security import host_guard  # noqa: PLC0415

    raw_bind = (getattr(settings, "CORTEXSIM_BIND", "") or "").strip()
    bind = raw_bind or "0.0.0.0"
    port = settings.CORTEXSIM_PORT
    raw_allowed = getattr(settings, "CORTEXSIM_ALLOWED_HOSTS", "")
    allowed = host_guard.parse_allowed_hosts(raw_allowed)
    ignored = host_guard.unusable_allowed_hosts(raw_allowed)
    cors = [o.strip() for o in (getattr(settings, "CORTEXSIM_CORS_ORIGINS", "") or "").split(",")
            if o.strip()]
    # CORTEXSIM_BIND is a SETTING — the address docker-compose publishes the
    # port on. This process does not bind a socket from it and cannot see the
    # address it is actually reachable on, so it is reported as configuration,
    # never as "SimCore is published on X" (RG-GUARD-06): under a host-run
    # uvicorn that statement was simply false.
    published = (
        f"CORTEXSIM_BIND={raw_bind} is the address docker-compose is configured "
        f"to publish port {port} on" if raw_bind else
        f"CORTEXSIM_BIND is unset, so docker-compose publishes port {port} on "
        f"every interface of the host ({bind})"
    )
    refused = host_guard.refusals.snapshot()
    # What the guard admits as this machine's own name (RG-UI-05). No DNS here:
    # the FQDN is whatever the running guard already resolved, or None.
    local = host_guard.local_host_names()
    guard_notes = ""
    if ignored:
        guard_notes += (
            f" CORTEXSIM_ALLOWED_HOSTS has {len(ignored)} entr"
            f"{'y' if len(ignored) == 1 else 'ies'} that can never match a Host "
            f"header and {'is' if len(ignored) == 1 else 'are'} ignored: "
            f"{', '.join(repr(e) for e in ignored)} — write the name only "
            f"('jumpbox.lab' or '*.lab')."
        )
    if refused["total"]:
        names = ", ".join(repr(e["host"]) for e in refused["hosts"][:5])
        guard_notes += (
            f" The Host guard has refused {refused['total']} request(s) since "
            f"boot (HOST_NOT_ALLOWED), most recently for {names}. If one of those "
            f"names is yours — the console URL, or a beacon's or installer's "
            f"--server — add it to CORTEXSIM_ALLOWED_HOSTS and restart SimCore; "
            f"until then that endpoint cannot poll, download or report."
        )
    return _degraded(
        "API_UNAUTHENTICATED",
        f"The operator and agent APIs are unauthenticated: anyone who can reach "
        f"this SimCore's port can launch runs, start tools, read the credential "
        f"inventory, enroll or impersonate agents and post their output. Where it "
        f"listens is not verified here: {published}, and a host-run SimCore "
        f"(scripts/dev-up-native.sh, a bare uvicorn) binds whatever --host uvicorn "
        f"was started with, which this process cannot see. The Host guard "
        f"blocks DNS-rebinding pages and CORS is "
        f"{'limited to ' + ', '.join(cors) if cors else 'off (same-origin only)'}, "
        f"but neither stops a direct network client, and a body-less cross-site "
        f"POST to an IP address is still delivered.{guard_notes} "
        f"Fix: publish SimCore only where the DC needs it — 127.0.0.1 for a "
        f"console-only host, or the lab NIC's address; set it as CORTEXSIM_BIND "
        f"under compose, or pass it as uvicorn's --host when host-run — and "
        f"restrict the "
        f"port to the DC's and the enrolled endpoints' addresses with a security "
        f"group or host firewall. API authentication itself is not implemented "
        f"yet (SEC-01); this component stays degraded until it is.",
        authenticated=False,
        bind=bind,
        bind_source="CORTEXSIM_BIND" if raw_bind else "default",
        bind_verified=False,
        port=port,
        host_guard="disabled" if "*" in allowed else "enforced",
        allowed_hosts=allowed,
        ignored_allowed_hosts=ignored,
        local_host_names=local["hostname"],
        local_fqdn_names=local["fqdn"],
        host_guard_refusals=refused["total"],
        last_refused_hosts=refused["hosts"],
        cors_origins=cors,
    )


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
        rows = (await session.execute(
            select(Scenario.status, func.count()).group_by(Scenario.status)
        )).all()
    by_status = {str(status): int(n or 0) for status, n in rows}
    # `count` is the corpus the Library shows — /api/scenarios defaults to
    # status=active. A composer draft is a persisted Scenario row too, but a
    # draft is not corpus coverage (Gate A5), so it is reported apart and never
    # added in: folding it in put 194 on the About page against 193 in the
    # Library, and the two were the same catalog.
    count = by_status.get("active", 0)
    drafts = by_status.get("draft", 0)
    if count == 0:
        return _degraded(
            "SCENARIO_CATALOG_EMPTY",
            "0 scenarios loaded — scenarios/ is missing from this deployment or "
            "every file was rejected by the schema validator. Nothing can be "
            "launched. Fix: check the boot log for 'Scenarios loaded: 0' and the "
            "per-file rejection lines above it; confirm the image COPYs scenarios/.",
            count=0, drafts=drafts, by_status=by_status,
        )
    return _ok(count=count, drafts=drafts, by_status=by_status)


async def probe_agents(session_factory) -> dict[str, Any]:
    """Agent liveness, derived from ``last_seen`` age — never from a probe.

    SimCore is pull-model: it does not dial the beacon. Reporting 'agents are
    healthy' would be a claim this process cannot make.
    """
    from sqlalchemy import select  # noqa: PLC0415

    import api.agents as agents_api  # noqa: PLC0415
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

    # RG-INSTALL-05: is an agent's recorded address really its own? Two signs
    # it is a proxy's: an X-Forwarded-For SimCore ignored because the peer is
    # not in CORTEXSIM_TRUSTED_PROXIES, and distinct hosts sharing one address.
    # last_ip is what scopes a reconcile to a host, so this is not cosmetic.
    forwarders = list(agents_api.ignored_forwarders())
    by_ip: dict[str, set] = {}
    for agent in rows:
        ip, host = getattr(agent, "last_ip", None), getattr(agent, "hostname", None)
        if ip and host:
            by_ip.setdefault(ip, set()).add(host)
    shared = {ip: sorted(hosts) for ip, hosts in sorted(by_ip.items()) if len(hosts) > 1}
    if forwarders:
        common["ignored_forwarders"] = forwarders
    if shared:
        common["shared_addresses"] = shared
    address_fault = None
    if forwarders:
        address_fault = (
            "XFF_FROM_UNTRUSTED_PEER",
            f"X-Forwarded-For arrived from {', '.join(forwarders)}, which "
            f"CORTEXSIM_TRUSTED_PROXIES does not list, so the header was ignored and "
            f"agents behind that peer are recorded with the peer's address, not their "
            f"own. A reconcile scoped by that address matches the wrong host. Fix: if "
            f"that peer is your proxy, add it to CORTEXSIM_TRUSTED_PROXIES (CIDRs, "
            f"passed to the container as an environment variable) and restart SimCore.")
    elif shared:
        ip, hosts = next(iter(shared.items()))
        address_fault = (
            "AGENT_ADDRESS_SHARED",
            f"{len(shared)} address(es) are recorded for more than one host (e.g. {ip}: "
            f"{', '.join(hosts)}), so SimCore sees those agents through one proxy or NAT "
            f"and an address-scoped reconcile cannot tell them apart. Fix: if a proxy "
            f"sends X-Forwarded-For, list it in CORTEXSIM_TRUSTED_PROXIES; if the hosts "
            f"sit behind one NAT, pass ?host= on /reconcile to name the endpoint.")

    if not rows and address_fault is None:
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
    if rows and buckets["online"] == 0:
        return _degraded(
            "NO_AGENT_ONLINE",
            f"{len(rows)} agent(s) are enrolled but none has checked in within 30s "
            f"({buckets['stale']} stale, {buckets['offline']} offline). A pull-mode "
            f"launch will queue a task nothing collects. Fix: on the target check "
            f"`systemctl status cortexsim-agent` (or launchctl), and confirm it can "
            f"reach this SimCore's /api/agents/{{id}}/tasks.",
            **common,
        )
    if address_fault is not None:
        return _degraded(*address_fault, **common)
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
            f"online: {listing}. The run's own status stays 'running' with no "
            f"output until the beacon polls — it does NOT read 'pending' (grepping "
            f"for 'pending' finds nothing) and it is NOT lost (the queue is durable "
            f"and survives a SimCore restart). Fix: bring the beacon back, or abort "
            f"the run with POST /api/runs/{{id}}/abort and relaunch against a live agent.",
            stranded=dict(sorted(stranded.items())), **common,
        )
    return _ok(stranded={}, **common)


async def probe_master_key(session_factory) -> dict[str, Any]:
    """OPS-12 — does the current ``CORTEXSIM_SECRET`` open this vault at all?

    A regenerated ``.env`` over a surviving data volume leaves every stored
    credential undecryptable. Before this probe, ``integrations`` still read
    ``ok`` with N configured and the first sign was a ``CRYPTO_ERROR`` 500 on a
    tenant test in front of the customer.

    It decrypts the key-check SimCore writes at first boot (or, for a database
    that predates it, test-decrypts the stored secrets). A local decrypt only:
    no tenant call, and ``stamp=False`` — a health probe never writes.

    Its own component, not a field of ``integrations``: vault integrity is not
    tenant configuration, and a mismatch must name itself in
    ``degraded_components`` rather than hide behind an integrations code.
    """
    from security.credentials import check_master_key  # noqa: PLC0415

    async with session_factory() as session:
        result = await check_master_key(session, stamp=False)

    common = {"key_check": result.status, "secrets_checked": result.secrets_checked}
    if result.status == "mismatch":
        return _degraded(result.code, result.detail, **common)
    return _ok(detail=result.detail, **common)


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


def probe_mcp(app: Any = None) -> dict[str, Any]:
    """The opt-in streamable-HTTP MCP mount at ``/mcp``.

    Checked here, without leaving the process: whether the flag is on, whether
    the MCP SDK and ``mcp_server.server`` import, whether ``app`` actually
    carries a mounted server (``app.state.mcp``, set only by
    ``mcp_server.server.mount``), and how many tools that server registered —
    read back from it, never inferred from the flag. Checked here, NOT here:
    whether a client can actually reach ``/mcp`` — that needs an inbound
    request, which this endpoint never makes. A flag that is OFF is ``ok`` with
    ``MCP_DISABLED``: an opt-in feature that was never asked for is not a
    fault. Zero outbound calls.
    """
    import importlib.metadata  # noqa: PLC0415

    try:
        sdk = f"mcp=={importlib.metadata.version('mcp')}"
    except Exception:  # noqa: BLE001 — the SDK version is reported, never fatal
        sdk = "unavailable"
    common = {"path": "/mcp", "transport": "streamable-http", "sdk": sdk}

    if not settings.CORTEXSIM_MCP_HTTP:
        return _ok(
            code="MCP_DISABLED",
            mounted=False,
            tools=0,
            detail="opt-in; set CORTEXSIM_MCP_HTTP=1 to mount",
            **common,
        )

    if not settings.CORTEXSIM_MCP_TOKEN.get_secret_value().strip():
        return _degraded(
            "MCP_TOKEN_MISSING",
            "CORTEXSIM_MCP_HTTP is set but CORTEXSIM_MCP_TOKEN is empty, so /mcp is NOT mounted. "
            "Fix: generate a token with python -c 'import secrets; print(secrets.token_urlsafe(32))', "
            "set CORTEXSIM_MCP_TOKEN to it, and restart SimCore.",
            mounted=False,
            tools=0,
            **common,
        )

    try:
        from mcp_server.server import registered_ids  # noqa: PLC0415
    except ImportError as exc:
        return _degraded(
            "MCP_SDK_IMPORT_FAILED",
            f"CORTEXSIM_MCP_HTTP is set but the MCP server did not import "
            f"({type(exc).__name__}: {exc}), so /mcp is not mounted. Fix: pip install -r "
            f"core/requirements.txt (mcp==1.30.0) and confirm core/mcp_server/ is in the image.",
            mounted=False,
            tools=0,
            **common,
        )

    mcp = getattr(getattr(app, "state", None), "mcp", None)
    if mcp is None:
        return _degraded(
            "MCP_NOT_MOUNTED",
            "CORTEXSIM_MCP_HTTP is set but this SimCore has no server mounted at /mcp, so a "
            "client configured for it gets the console instead. Fix: read the boot log for the "
            "'/mcp is NOT mounted' line and restart SimCore once its cause is resolved.",
            mounted=False,
            tools=0,
            **common,
        )

    tools = len(registered_ids(mcp))
    if tools == 0:
        return _degraded(
            "MCP_NO_TOOLS",
            "/mcp is mounted but its server registered zero tools, so a connected client "
            "sees an empty surface. Fix: mcp_server.server.build must register "
            "cortexsim.specs() + xsiam.specs() through registry.register.",
            mounted=True,
            tools=0,
            **common,
        )

    return _ok(
        mounted=True,
        tools=tools,
        detail=f"mounted at /mcp with {tools} tools",
        **common,
    )


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
    "identity_spec",
    "dataset_requirements",
    "eal",
    "xsiam_operation_catalog",
    "payload_shelf",
    "agent_binaries",
    "agents",
    "task_queue",
    "master_key",
    "integrations",
    "eal_collectors",
    "mcp",
    # Exposure, not readiness: last so the list still reads top-down from "can
    # this process store anything" to "is anything configured to leave it".
    "api_auth",
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


async def component_health(app: Any = None) -> dict[str, dict[str, Any]]:
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
    components["identity_spec"] = _guard("identity_spec", probe_identity_spec)
    components["dataset_requirements"] = _guard("dataset_requirements", probe_dataset_requirements)
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
    components["master_key"] = await _aguard("master_key", probe_master_key)
    components["integrations"] = await _aguard("integrations", probe_integrations)
    components["eal_collectors"] = await _aguard("eal_collectors", probe_eal_collectors)
    components["mcp"] = _guard("mcp", lambda: probe_mcp(app))
    components["api_auth"] = _guard("api_auth", probe_api_auth)
    return components


async def health_payload(app: Any = None) -> dict[str, Any]:
    components = await component_health(app)
    degraded = sorted(
        name for name, comp in components.items() if comp.get("status") != OK
    )
    return {
        "status": OK if not degraded else DEGRADED,
        "version": APP_VERSION,
        "commit": commit_sha(),
        "env": settings.CORTEXSIM_ENV,
        "started_at": _PROCESS_STARTED_AT.isoformat().replace("+00:00", "Z"),
        "uptime_seconds": uptime_seconds(),
        "components": components,
        "degraded_components": degraded,
        "checked": list(COMPONENT_KEYS),
        "not_checked": [dict(item) for item in NOT_CHECKED],
    }


@router.get("/health")
async def health_check(request: Request):
    """Liveness + readiness (GAP-API-007).

    Always HTTP 200 — a probe has to be able to READ the detail, and a 503 that
    a load balancer swallows tells a DC nothing. ``status`` is ``ok`` only when
    every component is ``ok``; ``degraded_components`` names the ones that are
    not, and ``not_checked`` names what this endpoint deliberately did not
    verify so a green result is never over-read.
    """
    return await health_payload(app=request.app)
