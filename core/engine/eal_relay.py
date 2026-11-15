"""The EAL relay: a beacon step that calls the SimCore-only EAL CLI (#325).

71 scenario files run ``python3 -m scripts.eal_simulator.cli ...`` as an
ordinary agent step. That CLI exists only inside SimCore, so on a target the
step died with ``ModuleNotFoundError``. The step now stays on the beacon, in
order and with its shell intact; the beacon swaps that one call for its own
``eal`` subcommand, which asks SimCore to run the campaign:

* a **collector** step (a plugin that POSTs records to a collector) is executed
  here, by the same ``CampaignExecutor``, against the same instance collector
  targets and behind the same live gate as an in-process ``channel: eal`` step
  (``orchestrator._dispatch_eal_in_process``). Where the POST comes from does
  not matter to the tenant;
* a **network-shape** step is not executed here. It is rendered to primitive
  network operations (``eal_simulator/wire_plan.py``) and the beacon performs
  them from the target, so a firewall attributes the traffic to the endpoint;
* any other plugin is not relayable yet: the call exits non-zero with one line,
  ``EAL_WIRE_NOT_RELAYABLE: <plugin>``.

Trust. The request comes from a target, which may be hostile, and the campaign
text is whatever the target sent. So:

* a request is served only for a ``(run, step, agent)`` this SimCore marked at
  launch (:func:`mark_relay_steps` writes the row) while that run is in flight;
* live sending needs the launch's ``consent.simulation_authorized`` and
  ``authorized_by`` (and ``consent.c2_authorized`` for a C2-shaped plugin). The
  campaign's own consent fields are never read. Without consent the campaign is
  a dry run;
* SimCore itself only ever POSTs to a stored collector target. The stored key
  goes with the stored URL (``targets.apply_targets``); a ``collector_url`` the
  campaign names is replaced by the target, or, when no target serves the
  plugin, the step stays dry;
* only collector plugins run here. A network-shape plugin is rendered without
  touching the network, and every operation is bounded.

A step that sent nothing has its seeded Results stamped ``not_executed`` with
the reason, as the in-process path does, so they leave the efficacy denominator
instead of reading as a miss.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

import yaml
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cortexsim.engine.eal_relay")

#: The capability a beacon advertises when it can run a marked step
#: (``agent/capabilities.go``).
RELAY_CAPABILITY = "eal-relay"

#: The one call a marked step makes. The beacon rewrites exactly this text
#: (``agent/ealrelay/rewrite.go`` holds the same expression).
EAL_CLI_RE = re.compile(r"\bpython3?(?:\.exe)?\s+-m\s+scripts\.eal_simulator\.cli\b")

MAX_CAMPAIGN_BYTES = 256 * 1024
MAX_CAMPAIGN_STEPS = 16
#: Requests one marked step may make over its lifetime (preflights included).
MAX_RELAYS_PER_STEP = 16
#: Collector POSTs and bytes one relayed campaign may make from SimCore.
MAX_COLLECTOR_REQUESTS = 5000
MAX_COLLECTOR_BYTES = 256 * 1024 * 1024

# Exit codes the subcommand ends with. 0-2 are the CLI's own.
EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_USAGE = 2
EXIT_NOT_RELAYABLE = 3

NOT_RELAYABLE = "EAL_WIRE_NOT_RELAYABLE"
NO_CONSENT_DETAIL = (
    "live delivery needs consent.simulation_authorized and a named "
    "authorized_by on the launch"
)


class RelayRefused(Exception):
    """The request may not be served. Rendered as the API's structured error."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


@dataclasses.dataclass(frozen=True)
class _Grant:
    id: int
    authorized_by: Optional[str]
    c2_authorized: bool
    sent_before: int  # records delivered + operations issued by earlier relays


# ---------------------------------------------------------------------------
# Launch: mark the steps
# ---------------------------------------------------------------------------


def _texts(step: Mapping[str, Any]) -> list[str]:
    """The step's command and every platform variant of it."""
    texts = [step.get("command") or ""]
    texts += [v for v in (step.get("platform_variants") or {}).values() if isinstance(v, str)]
    return texts


def calls_eal_cli(step: Mapping[str, Any]) -> bool:
    """Does this step's command, or any platform variant of it, call the EAL CLI?"""
    return any(EAL_CLI_RE.search(t) for t in _texts(step))


_PYTHON_RE = re.compile(r"\bpython3?\b")


def _marked(step: Mapping[str, Any]) -> dict[str, Any]:
    """The step as a relay-capable beacon receives it.

    When the EAL CLI call was the step's only use of Python, the step no longer
    needs Python on the target, so that name leaves ``requires_interpreters``:
    the beacon would otherwise refuse to run a step that no longer needs it.
    """
    marked = {**step, "eal_relay": True}
    required = step.get("requires_interpreters")
    if required and not any(_PYTHON_RE.search(EAL_CLI_RE.sub("", t)) for t in _texts(step)):
        kept = [name for name in required if name not in ("python", "python3")]
        if kept:
            marked["requires_interpreters"] = kept
        else:
            del marked["requires_interpreters"]
    return marked


async def mark_relay_steps(
    db: AsyncSession,
    run_id: str,
    steps: list[dict[str, Any]],
    launch_target: Optional[str],
    *,
    authorized_by: Optional[str],
    c2_authorized: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mark the agent steps that call the EAL CLI and record who may relay them.

    Returns ``(steps, warnings)``. A marked step is a copy carrying
    ``"eal_relay": True``; every other step is the same object, so a task with
    no such step is byte-identical to before. A step is marked only when its
    endpoint advertises ``eal-relay``; otherwise the launch carries one
    ``EAL_RELAY_UNAVAILABLE`` warning per endpoint and the step runs as it
    always did. The grant rows ride the caller's next commit.
    """
    from eal_relay_models import EalRelayStep  # noqa: PLC0415
    from engine.scenario_loader import effective_channel  # noqa: PLC0415
    from models import Agent  # noqa: PLC0415

    by_agent: dict[Optional[str], list[int]] = {}
    for i, step in enumerate(steps):
        if effective_channel(step) == "agent" and calls_eal_cli(step):
            by_agent.setdefault(step.get("target") or launch_target, []).append(i)
    if not by_agent:
        return steps, []

    out = list(steps)
    warnings: list[dict[str, Any]] = []
    for agent_id, indexes in by_agent.items():
        row = (await db.execute(
            select(Agent).where(Agent.agent_id == agent_id)
        )).scalar_one_or_none()
        if RELAY_CAPABILITY not in list(getattr(row, "capabilities", None) or []):
            warnings.append({
                "code": "EAL_RELAY_UNAVAILABLE",
                "agent_id": agent_id,
                "steps": [steps[i].get("id") for i in indexes],
                "detail": "this beacon cannot relay EAL steps, so they will fail; update it",
            })
            continue
        for i in indexes:
            out[i] = _marked(steps[i])
            db.add(EalRelayStep(
                run_id=run_id, step_id=str(steps[i].get("id")), agent_id=str(agent_id),
                authorized_by=authorized_by, c2_authorized=bool(c2_authorized),
            ))
    return out, warnings


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


async def relay(
    db: AsyncSession,
    *,
    agent_id: str,
    run_id: str,
    step_id: str,
    verb: str,
    campaign_yaml: Optional[str] = None,
    plugin: Optional[str] = None,
    live: bool = False,
    is_terminal: Callable[[Any], bool],
) -> dict[str, Any]:
    """Serve one relay request. Returns ``{exit_code, stdout, stderr, ops,
    allowlist}``; raises :class:`RelayRefused` when the asker has no standing.

    ``is_terminal`` is the runs API's own "is this run finished" rule.
    """
    from eal_simulator import get_default_registry  # noqa: PLC0415

    grant = await _authorise(db, agent_id, run_id, step_id, is_terminal)
    registry = get_default_registry()

    if verb == "list-plugins":
        return _reply(stdout=_print_json({"plugins": registry.manifest(), "total": len(registry)}))
    if verb == "describe":
        if not plugin or not registry.has(plugin):
            return _reply(EXIT_USAGE, stderr=(
                f"error: unknown plugin '{plugin}'\navailable: {registry.names()}\n"))
        return _reply(stdout=_print_json(registry.get(plugin).metadata()))
    if verb == "run":
        return await _run(db, grant, run_id, step_id, campaign_yaml, live, registry)
    raise RelayRefused(422, "EAL_RELAY_VERB_UNKNOWN", f"verb {verb!r} is not run, describe or list-plugins")


async def _authorise(
    db: AsyncSession, agent_id: str, run_id: str, step_id: str,
    is_terminal: Callable[[Any], bool],
) -> _Grant:
    from eal_relay_models import EalRelayStep  # noqa: PLC0415
    from models import Run  # noqa: PLC0415

    row = (await db.execute(
        select(EalRelayStep).where(
            EalRelayStep.run_id == run_id, EalRelayStep.step_id == step_id)
    )).scalar_one_or_none()
    if row is None or row.agent_id != agent_id:
        raise RelayRefused(
            403, "EAL_RELAY_STEP_NOT_MARKED",
            "no run marked this step for this agent to relay")
    run = (await db.execute(select(Run).where(Run.run_id == run_id))).scalar_one_or_none()
    if run is None or run.mode != "pull" or is_terminal(run):
        raise RelayRefused(
            409, "EAL_RELAY_RUN_NOT_IN_FLIGHT",
            "the run that marked this step is not in flight")

    grant = _Grant(
        id=row.id, authorized_by=row.authorized_by, c2_authorized=bool(row.c2_authorized),
        sent_before=int(row.delivered or 0) + int(row.ops_issued or 0),
    )
    # Count the request atomically, and close the transaction before any
    # campaign runs: a relay can take as long as the campaign sleeps, and the
    # beacon keeps writing to this database meanwhile.
    counted = await db.execute(
        update(EalRelayStep)
        .where(EalRelayStep.id == row.id, EalRelayStep.relays < MAX_RELAYS_PER_STEP)
        .values(relays=EalRelayStep.relays + 1, last_relay_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if counted.rowcount == 0:
        raise RelayRefused(
            429, "EAL_RELAY_LIMIT",
            f"this step has already made {MAX_RELAYS_PER_STEP} relay requests")
    return grant


def _reply(exit_code: int = EXIT_OK, *, stdout: str = "", stderr: str = "",
           ops: Optional[list] = None, allowlist: Optional[list] = None) -> dict[str, Any]:
    return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr,
            "ops": ops or [], "allowlist": allowlist or []}


def _print_json(obj: Any) -> str:
    """What the CLI's ``_print_json`` writes."""
    return json.dumps(obj, indent=2, default=str) + "\n"


def _usage(message: str) -> dict[str, Any]:
    return _reply(EXIT_USAGE, stderr=f"error: {message}\n")


# ---------------------------------------------------------------------------
# verb: run
# ---------------------------------------------------------------------------


async def _run(
    db: AsyncSession, grant: _Grant, run_id: str, step_id: str,
    campaign_yaml: Optional[str], live: bool, registry: Any,
) -> dict[str, Any]:
    from eal_simulator import wire_plan  # noqa: PLC0415
    from eal_simulator.base import BaseSimulation  # noqa: PLC0415
    from eal_simulator.campaign import Campaign, CampaignStep  # noqa: PLC0415
    from eal_simulator.delivery import (  # noqa: PLC0415
        PLUGIN_MISCONFIGURED,
        TARGET_NOT_AUTHORISED,
        summarise_step_results,
    )
    from eal_simulator.executor import CampaignExecutor  # noqa: PLC0415
    from eal_simulator.safety import (  # noqa: PLC0415
        SafetyError,
        SafetyPolicy,
        campaign_c2_plugins,
    )
    from eal_simulator.targets import apply_targets, collector_plugin_names  # noqa: PLC0415
    from engine.collector_targets import load_specs, load_tokens  # noqa: PLC0415
    from engine.orchestrator import (  # noqa: PLC0415
        _eal_live_blockers,
        _eal_params_invalid,
    )

    # ── the campaign file, as the CLI reads it ──────────────────────────────
    if not campaign_yaml or len(campaign_yaml.encode("utf-8")) > MAX_CAMPAIGN_BYTES:
        return _usage(f"campaign file is empty or over {MAX_CAMPAIGN_BYTES} bytes")
    try:
        # An alias lets a few bytes of text stand for an enormous structure
        # once something walks it. A campaign file has no use for one.
        if any(isinstance(event, yaml.AliasEvent)
               for event in yaml.parse(campaign_yaml, Loader=yaml.SafeLoader)):
            return _usage("campaign file uses a YAML alias, which the relay does not accept")
        raw = yaml.safe_load(campaign_yaml)
    except yaml.YAMLError:
        return _usage("campaign file is not valid YAML")
    if not isinstance(raw, dict):
        return _usage("campaign file must define a YAML mapping at the root")
    if isinstance(raw.get("steps"), list) and len(raw["steps"]) > MAX_CAMPAIGN_STEPS:
        return _usage(f"campaign has over {MAX_CAMPAIGN_STEPS} steps")
    # The CLI's rule: --live, or a file that itself says dry_run: false.
    want_live = bool(live) or raw.get("dry_run") is False
    try:
        # Shape only. The file's own consent block is not what makes this live.
        campaign = Campaign.model_validate({**raw, "dry_run": True})
    except Exception as exc:  # noqa: BLE001 - the CLI reports any refusal the same way
        return _usage(f"invalid campaign spec: {exc}")

    # ── what each step is ───────────────────────────────────────────────────
    collector_plugins = collector_plugin_names(registry)
    kind: dict[str, str] = {}
    for cs in campaign.steps:
        if not registry.has(cs.plugin):
            kind[cs.step_id] = "unknown"
        elif cs.plugin in collector_plugins:
            kind[cs.step_id] = "collector"
        elif wire_plan.has_renderer(cs.plugin):
            kind[cs.step_id] = "wire"
        else:
            kind[cs.step_id] = "not_relayable"

    stranded = sorted({cs.plugin for cs in campaign.steps if kind[cs.step_id] == "not_relayable"})
    if stranded:
        await _account(db, grant, run_id, step_id, status="not_relayable", code=NOT_RELAYABLE)
        return _reply(EXIT_NOT_RELAYABLE,
                      stdout="".join(f"{NOT_RELAYABLE}: {p}\n" for p in stranded))

    # ── collector steps: the instance's targets, as every in-process consumer ─
    errors: dict[str, tuple[str, str]] = {}  # step_id -> (code, detail), rejected before any run
    for cs in campaign.steps:
        if kind[cs.step_id] == "unknown":
            errors[cs.step_id] = (PLUGIN_MISCONFIGURED, f"plugin '{cs.plugin}' is not registered")

    collector_steps = [cs for cs in campaign.steps if kind[cs.step_id] == "collector"]
    spec = {"steps": [cs.model_dump() for cs in collector_steps], "target_allowlist": []}
    specs = await load_specs(db)
    resolved, res = apply_targets(spec, registry, specs)
    resolutions = {r.step_id: r for r in res}
    for sid, why in _eal_params_invalid(resolved["steps"], registry).items():
        errors[sid] = ("EAL_STEP_INVALID", why)

    wire_params: dict[str, Any] = {}
    for cs in campaign.steps:
        if kind[cs.step_id] != "wire":
            continue
        try:
            wire_params[cs.step_id] = registry.get(cs.plugin).validate_params(cs.params)
        except Exception as exc:  # noqa: BLE001
            errors[cs.step_id] = ("EAL_STEP_INVALID", _invalid_fields(cs.plugin, exc))

    collector_steps = [cs for cs in collector_steps if cs.step_id not in errors]
    wire_steps = [cs for cs in campaign.steps
                  if kind[cs.step_id] == "wire" and cs.step_id not in errors]

    # ── may it go live? ─────────────────────────────────────────────────────
    # The one policy object for this campaign: the launch's consent, and the
    # campaign's own allowlist for the destinations its network steps name.
    policy = SafetyPolicy(
        simulation_authorized=grant.authorized_by is not None,
        authorized_by=grant.authorized_by, target_allowlist=campaign.target_allowlist,
        dry_run=False, c2_authorized=grant.c2_authorized)
    blockers: dict[str, tuple[str, str]] = {}
    dry: Optional[tuple[str, str]] = None
    if not want_live:
        dry = ("EAL_DRY_RUN_REQUESTED", "the step asked for a dry run")
    elif grant.authorized_by is None:
        dry = ("EAL_DRY_RUN_NO_CONSENT", NO_CONSENT_DETAIL)
    else:
        # The C2 gate every live campaign passes (api/eal.py launch): the same
        # classification, by name and by each plugin's MITRE techniques, and
        # the same policy check. Asked before anything is rendered or sent.
        c2 = campaign_c2_plugins(campaign.steps, registry)
        try:
            policy.assert_c2_authorized(c2)
        except SafetyError:
            dry = ("EAL_DRY_RUN_NO_C2_CONSENT",
                   f"plugin(s) {sorted(set(c2))} are C2-shaped and need "
                   "consent.c2_authorized on the launch")
    if dry is None:
        blockers = _eal_live_blockers(
            collector_steps, resolutions, collector_plugins,
            authorized_by=grant.authorized_by, rejected=set(errors))
        firm = [blockers[cs.step_id] for cs in collector_steps
                if cs.step_id in blockers and blockers[cs.step_id][0] != "EAL_DRY_RUN_HELD"]
        if firm:
            dry = firm[0]
        elif blockers or errors:
            held = ", ".join(sorted(errors) or sorted(blockers))
            dry = ("EAL_DRY_RUN_HELD",
                   f"held at dry-run because step(s) {held} cannot run; the campaign "
                   "goes live whole or not at all")
    is_live = dry is None

    # ── render the network-shape steps for the beacon ───────────────────────
    # Before anything is sent. One Plan for the whole campaign, bounded as it
    # grows; a plan that is refused stops the campaign, collector steps included.
    plan = wire_plan.Plan()
    rendered: dict[str, dict[str, Any]] = {}
    for cs in wire_steps if is_live else []:
        wc = wire_plan.WireContext(
            campaign_id=campaign.campaign_id,
            simulation_run_id=BaseSimulation.new_simulation_run_id(),
            authorise=policy.authorise,
            verify_tls=bool(raw.get("verify_tls", False)))
        before = len(plan.ops)
        try:
            step_ops = wire_plan.render(cs.plugin, wire_params[cs.step_id], wc, plan)
        except SafetyError as exc:
            del plan.ops[before:]
            rendered[cs.step_id] = _step_result(
                cs, "error", error=f"safety_violation: {exc}",
                detail={"failure_code": TARGET_NOT_AUTHORISED})
        except wire_plan.WirePlanError as exc:
            await _account(db, grant, run_id, step_id, status="refused", code=exc.code)
            return _reply(EXIT_INCOMPLETE,
                          stdout=f"{exc.code}: {cs.step_id} ({cs.plugin}): {exc.detail}\n")
        else:
            detail: dict[str, Any] = {"operations": len(step_ops), "performed_by": "beacon"}
            if cs.plugin in wire_plan.NOT_RENDERED:
                detail["not_rendered"] = wire_plan.NOT_RENDERED[cs.plugin]
            rendered[cs.step_id] = _step_result(cs, "relayed", detail=detail)
    ops = plan.ops

    # ── run the collector steps here ────────────────────────────────────────
    tokens: dict[str, str] = {}
    if is_live and collector_steps:
        # The gate passed: only now are the keys read, and only into this
        # in-memory spec, each beside its own target's URL.
        tokens = await load_tokens(db, {r.target for r in res if r.target})
        resolved, res = apply_targets(spec, registry, specs, tokens=tokens)
        resolutions = {r.step_id: r for r in res}
    await db.commit()  # nothing is held open while the campaign runs

    started = _now()
    state = None
    live_ids = {cs.step_id for cs in collector_steps}
    run_steps = [CampaignStep(**rs) for rs in resolved["steps"] if rs["step_id"] in live_ids]
    if run_steps:
        kw: dict[str, Any] = {"dry_run": True}
        if is_live:
            kw = {"dry_run": False, "simulation_authorized": True,
                  "authorized_by": grant.authorized_by,
                  "target_allowlist": resolved["target_allowlist"]}
        executor = CampaignExecutor(
            registry=registry, max_total_bytes=MAX_COLLECTOR_BYTES,
            max_total_requests=MAX_COLLECTOR_REQUESTS)
        state = await executor.execute(
            Campaign(campaign_id=campaign.campaign_id, name=campaign.name,
                     steps=run_steps, **kw),
            run_id=run_id)

    # ── the printed result, in campaign order ───────────────────────────────
    ran = {r["step_id"]: r for r in (state.to_dict()["step_results"] if state else [])}
    results: list[dict[str, Any]] = []
    for cs in campaign.steps:
        sid = cs.step_id
        if sid in errors:
            code, why = errors[sid]
            results.append(_step_result(cs, "error", error=why, detail={"failure_code": code}))
        elif sid in ran:
            results.append(ran[sid])
        elif sid in rendered:
            results.append(rendered[sid])
        else:  # a network-shape step of a dry run: planned, not rendered, not sent
            results.append(_step_result(cs, "success", detail={"dry_run": True}))
    for r in results:
        code = (blockers.get(r["step_id"]) or (None, None))[0]
        resolution = resolutions.get(r["step_id"])
        if code or resolution is not None:
            r["relay"] = {k: v for k, v in {
                "code": code,
                "collector_target": getattr(resolution, "target", None),
                "collector_source": getattr(resolution, "source", None),
            }.items() if v is not None}

    status = state.status if state else "complete"
    printed = {
        "run_id": run_id,
        "campaign_id": campaign.campaign_id,
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": _now().isoformat(),
        "step_results": results,
        "error": state.error if state else None,
        "dry_run": not is_live,
        "delivery": summarise_step_results(results),
        "relay": {
            "mode": "live" if is_live else "dry_run",
            "code": dry[0] if dry else None,
            "detail": dry[1] if dry else None,
            "operations": len(ops),
        },
    }
    stdout = _print_json(printed)
    for token in tokens.values():
        if token:  # nothing the collector was handed leaves in the reply
            stdout = stdout.replace(token, "***")

    delivered = _delivered(state) if is_live else 0
    # Why nothing was sent, should that be so: the dry-run code, else the first
    # failed step's own code, else the in-process path's word for a live
    # campaign whose collector accepted nothing.
    failed = next((r for r in results if r["status"] not in ("success", "relayed")), {})
    unsent_code = dry[0] if dry else str(
        (failed.get("detail") or {}).get("failure_code") or "not_delivered")
    await _account(db, grant, run_id, step_id,
                   status="live" if is_live else "dry_run", code=unsent_code,
                   delivered=delivered, ops=len(ops))
    logger.info(
        "eal relay run_id=%s step=%s mode=%s code=%s delivered=%d operations=%d",
        run_id, step_id, printed["relay"]["mode"], printed["relay"]["code"],
        delivered, len(ops))
    return _reply(EXIT_OK if status == "complete" else EXIT_INCOMPLETE,
                  stdout=stdout, ops=ops, allowlist=wire_plan.allowlist_for(ops))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _delivered(state: Any) -> int:
    """Records a collector accepted in this campaign (0 for a dry run)."""
    if state is None or state.dry_run:
        return 0
    return sum(int(r.events_emitted or 0) for r in state.step_results)


def _step_result(cs: Any, status: str, *, error: Optional[str] = None,
                 detail: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """A step result in ``SimulationResult.to_dict()``'s shape, for a step the
    executor did not run."""
    now = _now().isoformat()
    return {
        "plugin": cs.plugin, "step_id": cs.step_id, "status": status,
        "started_at": now, "completed_at": now, "duration_seconds": 0.0,
        "events_emitted": 0, "bytes_sent": 0, "detail": detail or {}, "error": error,
    }


def _invalid_fields(plugin: str, exc: Exception) -> str:
    """Failing fields and error types only, never a value (the campaign is the
    target's text and may carry anything)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        fields = ", ".join(
            f"{'.'.join(str(p) for p in (e.get('loc') or ())) or '(params)'} ({e.get('type')})"
            for e in errors())
        return f"params invalid for plugin '{plugin}': {fields}"
    return f"params invalid for plugin '{plugin}': {type(exc).__name__}"


async def _account(
    db: AsyncSession, grant: _Grant, run_id: str, step_id: str, *,
    status: str, code: str, delivered: int = 0, ops: int = 0,
) -> None:
    """Add this relay to the step's ledger and settle its Results.

    A step that has sent nothing so far (no record accepted, no operation
    issued, across every relay it made) has its undecided Results stamped
    ``not_executed`` with ``code``. The moment it sends something, a stamp an
    earlier relay of the same step left is lifted again.
    """
    from eal_relay_models import EalRelayStep  # noqa: PLC0415
    from engine.outcome import OUTCOME_NOT_EXECUTED  # noqa: PLC0415
    from models import Result  # noqa: PLC0415

    row = (await db.execute(
        select(EalRelayStep).where(EalRelayStep.id == grant.id))).scalar_one()
    stamped_with = row.last_code if row.last_status == "stamped" else None
    row.delivered = int(row.delivered or 0) + delivered
    row.ops_issued = int(row.ops_issued or 0) + ops
    sent = grant.sent_before + delivered + ops

    results = (await db.execute(
        select(Result).where(Result.run_id == run_id, Result.step_id == step_id)
    )).scalars().all()
    if sent == 0:
        for r in results:
            if r.outcome is None or (r.outcome == OUTCOME_NOT_EXECUTED
                                     and r.outcome_reason == stamped_with):
                r.outcome, r.outcome_reason = OUTCOME_NOT_EXECUTED, code
        row.last_status, row.last_code = "stamped", code
    else:
        for r in results:
            if (stamped_with is not None and r.outcome == OUTCOME_NOT_EXECUTED
                    and r.outcome_reason == stamped_with):
                r.outcome, r.outcome_reason = None, None
        row.last_status, row.last_code = status, None
    await db.commit()
