"""
CortexSim API — /api/scenarios router.

Endpoints:
  GET  /api/scenarios                            — list all (optional ?plane= and ?uc_ref= filters)
  GET  /api/scenarios/{scenario_id}              — single scenario detail
  GET  /api/scenarios/{scenario_id}/infra-hints  — adapter_refs + iac_modules a scenario implies
  GET  /api/scenarios/{scenario_id}/download     — download an UNGATED bundle
  POST /api/scenarios/{scenario_id}/bundle       — generate a bundle WITH a consent body

The bundle-generation gate lives here because **generation is the boundary that
bounds blast radius**. A Kubernetes manifest is applied by ``kubectl`` on a
laptop days later, on a host SimCore never sees, by a person who may not be the
person who downloaded it. Once the privileged bytes exist, nothing SimCore does
can recall them — so if consent is absent they must never be produced.

The authorization decision itself is NOT re-implemented here. There is exactly
one authority — :func:`engine.k8s_manifest.check_cluster_consent` — and both
surfaces that can put a privileged workload into a customer's cluster call it:

    generation  ← this module (the artifact boundary)
    launch      ← engine.orchestrator._check_launch_consent (the Run record)
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from engine import k8s_manifest as km
from engine.push_generator import (
    BundleTargetUnsatisfiable,
    emittable_targets,
    generate_bash,
    generate_k8s,
    generate_powershell,
    resolve_target,
)
from models import Scenario
from tools.adapter_catalog import catalog as adapter_catalog

logger = logging.getLogger("cortexsim.api.scenarios")

#: Cited by every cluster refusal so the operator lands on the contract, not on
#: a search box.
GATE_DOCS = "docs/reference/k8s-delivery.md"

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


# ---------------------------------------------------------------------------
# List projection
# ---------------------------------------------------------------------------
#
# The full corpus serialises to 1248.6 KB across 162 scenarios, and 817.1 KB of
# that (65%) is ``steps[]`` — 243 KB of shell ``command`` text and 149 KB of
# per-detection ``description`` prose that no list view renders. The console
# ships in this repo and is the only consumer, so the fields below were chosen
# by grepping ``ui/src`` for what is actually read off a LIST-derived scenario:
#
#   step.mitre_technique                → FilterPalette / useScenarioFilter /
#                                         ScenarioGrid technique facets
#   step.expected_detections[].type     → detection-type facet + filter
#   step.expected_detections[].plane    → cross-plane badge (ScenarioGrid,
#                                         StackCoverageView, useScenarioFilter)
#   steps.length                        → AppConsole command-palette meta
#
# Everything else in a step, plus the four heaviest unread top-level fields,
# is dropped here and served in full by ``GET /api/scenarios/{id}`` — which
# every consumer that needs detail already calls (OperationsView hydrates on
# card select; LaunchView / ScenarioInspector / UCTCMapper read the detail).
_LIST_OMIT_FIELDS = (
    "steps",             # replaced by the slim projection below
    "cleanup",           # 24.9 KB — detail + push bundle only
    "external_tools",    # 27.8 KB — LaunchView reads it off the DETAIL fetch
    "success_criteria",  # 78.8 KB — index detail view, not the scenario list
)
_LIST_STEP_KEYS = ("id", "name", "mitre_technique")
_LIST_DETECTION_KEYS = ("type", "plane")


def _slim_step(step: dict) -> dict:
    """Project one step down to the keys the list view renders."""
    out = {k: step.get(k) for k in _LIST_STEP_KEYS if k in step}
    dets = step.get("expected_detections")
    if isinstance(dets, list):
        out["expected_detections"] = [
            {k: d.get(k) for k in _LIST_DETECTION_KEYS if k in d}
            for d in dets if isinstance(d, dict)
        ]
    return out


def _slim_scenario(scenario: Scenario) -> dict:
    """Summary projection of a scenario for the list endpoint."""
    full = scenario.to_dict()
    slim = {k: v for k, v in full.items() if k not in _LIST_OMIT_FIELDS}
    steps = full.get("steps")
    slim["steps"] = [_slim_step(s) for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []
    return slim


@router.get("")
async def list_scenarios(
    plane: Optional[str] = Query(None, description="Filter by detection plane (e.g. CDR)"),
    uc_ref: Optional[str] = Query(None, description="Filter by UC reference (e.g. UCS-CDR-03)"),
    ttp_ref: Optional[str] = Query(None, description="Filter to scenarios whose steps[].expected_detections[].ttp_ref cites this TTP id"),
    entitlement: Optional[str] = Query(None, description="Filter to scenarios a tenant profile can license: ng-siem-bare | enterprise | premium"),
    db: AsyncSession = Depends(get_db),
):
    """List scenarios (summary projection), with optional plane / uc_ref /
    ttp_ref / entitlement filters.

    Rows are the SUMMARY shape — see ``_LIST_OMIT_FIELDS``. Fetch
    ``GET /api/scenarios/{id}`` for the full document (commands, cleanup,
    external_tools, success_criteria, per-detection logic).
    """
    stmt = select(Scenario)
    if plane:
        stmt = stmt.where(Scenario.plane == plane.upper())
    if uc_ref:
        stmt = stmt.where(Scenario.uc_ref == uc_ref)

    result = await db.execute(stmt)
    scenarios = result.scalars().all()

    # ttp_ref filter — applied in Python because expected_detections is
    # nested in a JSON column and SQLite has no portable accessor for it.
    # The scenario catalog is small (~50 today) so a full scan is fine.
    if ttp_ref:
        scenarios = [s for s in scenarios if _scenario_cites_ttp(s, ttp_ref)]

    # entitlement filter — a POV must not propose a scenario the prospect
    # cannot license. Same runnability rule as POST /api/pov/scope.
    if entitlement:
        scenarios = [s for s in scenarios if _scenario_is_licensable(s, entitlement)]

    logger.info(
        "list_scenarios plane=%s uc_ref=%s ttp_ref=%s entitlement=%s count=%d",
        plane, uc_ref, ttp_ref, entitlement, len(scenarios),
    )
    # `projection` names the shape explicitly so a consumer can tell a summary
    # row from a detail document without guessing at which keys are missing.
    return {
        "scenarios": [_slim_scenario(s) for s in scenarios],
        "total": len(scenarios),
        "projection": "summary",
    }


def _scenario_cites_ttp(scenario: Scenario, ttp_ref: str) -> bool:
    """Return True if any step's expected_detections cites ``ttp_ref``."""
    for step in (scenario.steps or []):
        if not isinstance(step, dict):
            continue
        for det in (step.get("expected_detections") or []):
            if isinstance(det, dict) and det.get("ttp_ref") == ttp_ref:
                return True
    return False


@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return full detail for a single scenario."""
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND", "detail": f"scenario_id='{scenario_id}'"},
        )

    logger.info("get_scenario scenario_id=%s", scenario_id)
    return scenario.to_dict()


@router.get("/{scenario_id}/infra-hints")
async def get_infra_hints(
    scenario_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve a scenario's ``external_tools[]`` into IaC generator hints.

    Walks each entry's ``adapter_ref``, looks it up in the catalog, and
    returns:

      * ``adapter_refs``       — full list of refs the scenario declared
      * ``resolved_adapters``  — adapters that resolved (with name, tier,
                                 safety_class, iac_module if any)
      * ``unresolved_refs``    — refs the catalog rejected (stale ids,
                                 typos) — surfaced so the operator can
                                 see the gap in the UI
      * ``suggested_modules``  — unioned set of ``install.iac_module``
                                 values from resolved adapters, deduped
                                 + sorted. Plug straight into
                                 ``/api/infra/generate?modules=...``

    UI workflow: DC opens the Lab view, picks a scenario_id, hits this
    endpoint, and the LabView auto-fills the modules + tool-adapters
    pickers. The actual generation goes through ``/api/infra/generate``
    with the same ``adapter_refs[]`` so PR #48's auto-pull provenance
    trail (ADAPTERS.md) lights up.
    """
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND", "detail": f"scenario_id='{scenario_id}'"},
        )

    adapter_refs: list[str] = []
    resolved: list[dict] = []
    unresolved: list[str] = []
    modules: list[str] = []

    for entry in (scenario.external_tools or []):
        if not isinstance(entry, dict):
            continue
        ref = entry.get("adapter_ref")
        if not ref:
            continue
        adapter_refs.append(ref)
        adapter = adapter_catalog.find(ref)
        if adapter is None:
            unresolved.append(ref)
            continue
        iac_module = adapter.install.iac_module
        resolved.append({
            "adapter_ref":  ref,
            "name":         adapter.name,
            "tier":         adapter.tier,
            "safety_class": adapter.safety_class,
            "iac_module":   iac_module,
        })
        if iac_module and iac_module not in modules:
            modules.append(iac_module)

    logger.info(
        "infra_hints scenario_id=%s refs=%d resolved=%d unresolved=%d modules=%s",
        scenario_id, len(adapter_refs), len(resolved), len(unresolved), modules,
    )
    return {
        "scenario_id":       scenario_id,
        "plane":             scenario.plane,
        "adapter_refs":      adapter_refs,
        "resolved_adapters": resolved,
        "unresolved_refs":   unresolved,
        "suggested_modules": sorted(modules),
    }


#: format → the execution target it needs the scenario to satisfy.
_FORMAT_TARGET = {"bash": "posix", "k8s": "posix", "powershell": "windows"}


def _unsatisfiable(scenario_id: str, target: str, unresolved) -> HTTPException:
    """409, not 400: the REQUEST is well-formed — the scenario's content cannot
    satisfy it. A 400 would tell the caller to fix its query string; the fix is
    to declare platform_variants on the named steps."""
    exc = BundleTargetUnsatisfiable(scenario_id, target, tuple(unresolved))
    return HTTPException(status_code=409, detail=exc.to_error())


# ===========================================================================
# The cluster-consent gate
# ===========================================================================
#
# Three properties, in the order they matter:
#
#   1. It fires on the SCANNED manifest, never only on the declaration. A
#      scenario author who adds host access without telling anyone must be
#      refused, not silently cleared — this repo has already shipped three
#      false-greens where a declaration and an emission drifted apart.
#   2. It does NOT fire on ordinary manifests. Every scenario in the corpus
#      today is baseline, so every existing caller keeps working byte-for-byte.
#      A prompt that fires on every download teaches the operator to click
#      through, which is strictly worse than no prompt.
#   3. When it refuses it names the capability, the object it lands on, and the
#      literal consent key that unblocks it.


def _allow_privileged_k8s() -> bool:
    """Deployment-level kill switch for TIER_NODE (node breakout), default OFF.

    Two principals on two timescales: whoever owns this SimCore deployment sets
    the flag once on their own jumpbox; the operator supplies consent per
    generation. A shared or demo SimCore cannot emit node-breakout bytes at all,
    no matter what anyone POSTs — which is the only control here that holds
    against a caller who simply asserts consent (SimCore has no user model).

    Delegated to the engine layer so the launch gate and this gate cannot answer
    the question differently.
    """
    from engine.orchestrator import allow_privileged_k8s  # noqa: PLC0415

    return allow_privileged_k8s()


# --- posture resolution ----------------------------------------------------
#
# The `cluster_posture` ORM column is owned by another track; until it lands,
# `Scenario.to_dict()` has no such key and the loader drops the block on the
# way into the DB. Reading the gate's input off the DB alone would therefore
# make the gate VACUOUS: a scenario that declares a privileged posture would
# generate a BASELINE manifest, and a DC who asked for the escape scenario
# would apply a manifest that cannot escape and report "the attack ran and
# Cortex missed it". That is the exact false-negative this engine exists to
# refuse, so the posture is hydrated from the YAML — which CLAUDE.md already
# names as the source of truth ("Scenarios are YAML source-of-truth. DB stores
# run history only.").
#
# The moment the ORM column exists, `"cluster_posture" in doc` is True and the
# disk path is never consulted again. No change required here.

_DISK_POSTURE_INDEX: Optional[dict[str, dict]] = None


def _build_disk_posture_index() -> dict[str, dict]:
    """Map scenario_id -> declared ``cluster_posture`` block, scanned from YAML."""
    from config import settings  # noqa: PLC0415

    index: dict[str, dict] = {}
    root = Path(settings.CORTEXSIM_BASE_DIR) / settings.CORTEXSIM_SCENARIOS_DIR
    if not root.is_dir():
        return index
    for path in sorted(root.rglob("*.yml")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - unreadable file is not a gate failure
            continue
        # Cheap pre-filter: on today's corpus this rejects every file, so the
        # YAML parser is never invoked and the scan costs one stat + read.
        if "cluster_posture" not in text:
            continue
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:  # pragma: no cover - loader already rejected it
            continue
        if not isinstance(doc, dict):
            continue
        sid = doc.get("scenario_id")
        posture = doc.get("cluster_posture")
        if isinstance(sid, str) and isinstance(posture, dict):
            index[sid] = posture
    return index


def _disk_posture(scenario_id: str) -> Optional[dict]:
    """Declared posture for ``scenario_id`` from the on-disk corpus, or None."""
    global _DISK_POSTURE_INDEX
    if _DISK_POSTURE_INDEX is None:
        _DISK_POSTURE_INDEX = _build_disk_posture_index()
    return _DISK_POSTURE_INDEX.get(scenario_id)


def _scenario_document(scenario: Scenario) -> dict[str, Any]:
    """The scenario dict the generator sees, with the posture reconciled."""
    doc = scenario.to_dict()
    if "cluster_posture" in doc:
        return doc  # the ORM carries it — nothing to reconcile
    posture = _disk_posture(doc.get("scenario_id") or "")
    if posture is not None:
        logger.warning(
            "scenario=%s declares cluster_posture but the ORM column is absent; "
            "hydrating from YAML so the manifest is not silently downgraded to "
            "baseline. Add the `cluster_posture` column to remove this path.",
            doc.get("scenario_id"),
        )
        doc["cluster_posture"] = posture
    return doc


def _posture_of(scenario_doc: dict[str, Any]) -> Optional[km.ClusterPosture]:
    """Parse the posture, mapping a load-time refusal onto HTTP 422.

    ``ClusterPosture``'s validators enforce the refusals no consent key unlocks
    — system namespaces, a namespace we would not own, writable hostPath on a
    node root, control-plane hostPorts, an unbounded TTL. They are re-raised
    here rather than 500'd because they are a property of the *content*, and
    the operator needs to be told which one.
    """
    try:
        return km.parse_posture(scenario_doc)
    except Exception as exc:  # pydantic ValidationError and friends
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Scenario declares a cluster posture this engine refuses outright",
                "code": "CLUSTER_CAPABILITY_FORBIDDEN",
                "detail": str(exc),
                "scenario_id": scenario_doc.get("scenario_id"),
                "remedy": (
                    "No consent key unlocks this. Fix the scenario's "
                    "cluster_posture block; the refusal list is in "
                    f"{GATE_DOCS} §8."
                ),
                "docs": GATE_DOCS,
            },
        ) from exc


def _consent_error(exc: km.ClusterConsentRequired, *, authorized_by: str) -> HTTPException:
    """Map the shipped refusal onto HTTP, adding the two operator-facing keys.

    403 vs 409 is not cosmetic: the fix is a different action by a different
    person. 409 says "send the consent key". 403 says "the person who owns this
    SimCore has to change a deployment flag" — telling a DC to add a consent key
    when the fix is an env var is how someone ends up editing the wrong file.
    """
    body = exc.to_error()
    if exc.disabled_by_deployment:
        body["remedy"] = (
            "Set CORTEXSIM_ALLOW_PRIVILEGED_K8S=true on the SimCore deployment "
            "and restart it. This is owned by whoever runs this SimCore, not by "
            "the caller — no consent key will change it."
        )
        body["docs"] = GATE_DOCS
        return HTTPException(status_code=403, detail=body)
    keys = " and ".join(f"consent.{k}=true" for k in exc.missing)
    body["remedy"] = (
        f"Re-request POST /api/scenarios/{exc.scenario_id}/bundle with {keys} "
        f"and authorized_by set to the operator who obtained the customer's "
        f"approval."
    )
    if not authorized_by.strip():
        body.setdefault("missing_consent", [])
        body["missing_consent"] = [*exc.missing, "authorized_by"]
        body["remedy"] += (
            " authorized_by is mandatory for a gated generation: an audit row "
            "with no named operator proves nothing."
        )
    body["docs"] = GATE_DOCS
    return HTTPException(status_code=409, detail=body)


def _gated_format_requires_post(scenario_id: str, posture: km.ClusterPosture) -> HTTPException:
    """The GET bypass, closed.

    Deliberately NOT a silent downgrade to a baseline manifest. A DC who asked
    for the escape scenario and received a manifest that cannot escape will
    report "the attack ran and Cortex missed it" — a manufactured false negative
    is a worse outcome than a refusal the operator can act on.
    """
    gated = [f for f in km.findings_for(posture) if f.tier]
    return HTTPException(
        status_code=409,
        detail={
            "error": "This scenario's k8s manifest requests gated cluster capabilities",
            "code": "GATED_FORMAT_REQUIRES_POST",
            "detail": (
                f"{scenario_id} plants [{', '.join(f.slug for f in gated)}]. "
                f"GET carries no consent body, so the privileged bytes are never "
                f"produced on this path."
            ),
            "scenario_id": scenario_id,
            "risk_tier": posture.risk_tier(),
            "requested_capabilities": [km._cap_row(f) for f in gated],
            "missing_consent": list(km.required_consent_keys(posture)),
            "remedy": (
                f"POST /api/scenarios/{scenario_id}/bundle with "
                f"{{\"format\": \"k8s\", \"consent\": {{...}}, \"authorized_by\": \"...\"}}."
            ),
            "docs": GATE_DOCS,
        },
    )


# --- verification of the bytes actually leaving SimCore ---------------------


def _manifest_objects(content: str) -> list[dict[str, Any]]:
    """Re-parse the rendered manifest. The gate reads what is being RETURNED."""
    try:
        return [d for d in yaml.safe_load_all(content) if isinstance(d, dict)]
    except yaml.YAMLError as exc:  # pragma: no cover - the generator emits via SafeDumper
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Generated manifest is not parseable YAML",
                "code": "MANIFEST_UNPARSEABLE",
                "detail": str(exc),
            },
        ) from exc


def _namespaces_in(objects: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for obj in objects:
        meta = obj.get("metadata") or {}
        names = []
        if obj.get("kind") == "Namespace":
            names.append(meta.get("name"))
        names.append(meta.get("namespace"))
        for n in names:
            if n is not None and n not in seen:
                seen.append(n)
    return seen


def _verify_emitted(
    scenario_id: str,
    content: str,
    consent: dict[str, Any],
    *,
    allow_privileged_k8s: bool,
    authorized_by: str,
) -> tuple[list[str], str]:
    """Scan the manifest ABOUT TO BE RETURNED and refuse if it exceeds consent.

    This is the load-bearing half of the gate. ``required_consent_keys`` reads
    the declaration; this reads the emission. They agree today — the manifest
    track proves it — and this is what notices on the day they stop agreeing,
    which is the failure mode that has actually bitten this repo.

    Returns ``(emitted_finding_slugs, risk_tier)`` on success.
    """
    objects = _manifest_objects(content)

    # ── blast radius: we only ever create objects in a namespace we own ─────
    for ns in _namespaces_in(objects):
        if not ns or not str(ns).strip():
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Manifest contains an object with an unnamed namespace",
                    "code": "CLUSTER_NAMESPACE_FORBIDDEN",
                    "detail": (
                        "An unnamed namespace applies into whatever `kubectl` "
                        "context is current — usually `default`. Refused."
                    ),
                    "scenario_id": scenario_id,
                    "docs": GATE_DOCS,
                },
            )
        bad = (
            ns in km.NAMESPACE_DENYLIST
            or ns.startswith(km.NAMESPACE_DENY_PREFIXES)
            or not ns.startswith("cortexsim-")
        )
        if bad:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Manifest targets a namespace CortexSim may not create or reap",
                    "code": "CLUSTER_NAMESPACE_FORBIDDEN",
                    "detail": (
                        f"namespace={ns!r}. CortexSim only ever creates objects it "
                        f"owns and labels, in a namespace it created, named "
                        f"`cortexsim-*`. We never adopt or relabel a customer "
                        f"namespace, and a system namespace can never be safely "
                        f"reaped."
                    ),
                    "scenario_id": scenario_id,
                    "docs": GATE_DOCS,
                },
            )

    # ── blast radius: every object must be findable by the teardown sweep ───
    unmanaged = km.unmanaged_objects(objects)
    if unmanaged:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Manifest contains objects the teardown label sweep cannot find",
                "code": "MANIFEST_NOT_SWEEPABLE",
                "detail": (
                    f"{', '.join(unmanaged)} carry no "
                    f"cortexsim.io/managed-by=push-generator label. A DC could not "
                    f"prove to the customer that this went away."
                ),
                "scenario_id": scenario_id,
                "docs": GATE_DOCS,
            },
        )

    # ── capability: gated findings present in the EMITTED graph ─────────────
    slugs = km.emitted_finding_slugs(objects)
    tiers: list[str] = []
    missing: list[str] = []
    rows: list[dict[str, Any]] = []
    for slug in slugs:
        f = km.finding(slug)
        if f is None or not f.tier:
            continue
        if f.tier not in tiers:
            tiers.append(f.tier)
        rows.append(km._cap_row(f))
        key = km.TIER_CONSENT_KEY[f.tier]
        if not bool(consent.get(key)) and key not in missing:
            missing.append(key)

    risk_tier = (
        km.TIER_NODE if km.TIER_NODE in tiers
        else km.TIER_CLUSTER if km.TIER_CLUSTER in tiers
        else "baseline"
    )

    if km.TIER_NODE in tiers and not allow_privileged_k8s:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Privileged Kubernetes workloads are disabled on this SimCore",
                "code": "CLUSTER_PRIVILEGE_DISABLED",
                "detail": (
                    f"The emitted manifest for {scenario_id} carries node-access "
                    f"capabilities but CORTEXSIM_ALLOW_PRIVILEGED_K8S is false."
                ),
                "scenario_id": scenario_id,
                "risk_tier": risk_tier,
                "requested_capabilities": rows,
                "remedy": (
                    "Set CORTEXSIM_ALLOW_PRIVILEGED_K8S=true on the SimCore "
                    "deployment and restart it."
                ),
                "docs": GATE_DOCS,
            },
        )

    if missing or (rows and not authorized_by.strip()):
        if rows and not authorized_by.strip() and "authorized_by" not in missing:
            missing.append("authorized_by")
        # CRITICAL, not WARNING: reaching here past the pre-check means the
        # declared posture and the emitted graph disagree. The bytes are
        # discarded, but the drift is a bug someone has to fix.
        logger.critical(
            "gate integrity: scenario=%s emitted %s but consent covered %s — "
            "manifest DISCARDED",
            scenario_id, ",".join(slugs) or "-", sorted(consent) or "-",
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Emitted manifest exceeds the consent presented",
                "code": "CLUSTER_CONSENT_REQUIRED",
                "detail": (
                    f"The manifest generated for {scenario_id} carries "
                    f"[{', '.join(slugs)}]. It was scanned after rendering and "
                    f"discarded before any byte was returned."
                ),
                "scenario_id": scenario_id,
                "risk_tier": risk_tier,
                "requested_capabilities": rows,
                "missing_consent": missing,
                "docs": GATE_DOCS,
            },
        )

    return list(slugs), risk_tier


# --- audit -----------------------------------------------------------------


def _self_contained(content: str) -> str:
    """"true"/"false" for the delivery-contract header, read off the manifest.

    ``embedded`` (the default) keeps the same no-SimCore-at-runtime invariant
    the bash and PowerShell bundles hold. ``served`` is the one documented
    exception. Reading the label rather than re-deriving the rule keeps this
    from drifting away from what the generator actually emitted.
    """
    return "false" if "cortexsim.io/delivery: served" in content else "true"


def _header_safe(value: str) -> str:
    """HTTP headers are latin-1; an operator name is free text."""
    return value.encode("latin-1", "replace").decode("latin-1")


async def _audit_bundle(entry: dict[str, Any]) -> None:
    """Record what was generated, for whom, under which consent.

    A durable ``GeneratedBundle`` row belongs in ``core/models.py``, which is
    owned by another track — so this is a structured log line plus a live SSE
    frame today. Both carry every field a DC needs to answer, six weeks later in
    a change-review meeting: what did you deploy, who authorised it, is it gone.
    """
    logger.warning(
        "bundle_generated scenario=%s format=%s risk_tier=%s capabilities=%s "
        "consent=%s authorized_by=%s sha256=%s selfcontained=%s reason=%s",
        entry["scenario_id"], entry["format"], entry["risk_tier"],
        ",".join(entry["capabilities"]) or "-",
        ",".join(entry["consent_keys"]) or "-",
        entry["authorized_by"] or "-", entry["manifest_sha256"],
        entry["self_contained"], entry["reason"] or "-",
    )
    try:
        from events import event_bus  # noqa: PLC0415

        await event_bus.publish(None, {"type": "bundle.generated", "run_id": None, "data": entry})
    except Exception:  # pragma: no cover - a bus hiccup must not fail generation
        logger.exception("event_bus publish failed for bundle audit")


class BundleRequest(BaseModel):
    """Consent-carrying bundle generation.

    Deliberately does NOT expose namespace / ttl / image overrides. Those live
    in the scenario's ``cluster_posture`` block, where the loader's refusals
    (system namespaces, writable node roots, control-plane hostPorts, unbounded
    TTL) validate them once. A request-time override would be a second,
    unvalidated path to the same fields.
    """

    format: str = "k8s"
    #: cluster_privilege_authorized · node_access_authorized. Orthogonal, not
    #: nested — a manifest requesting only one needs only that one.
    consent: dict[str, bool] = Field(default_factory=dict)
    #: Mandatory for a gated generation. An audit row with no named operator
    #: proves nothing, which is the same standard SafetyPolicy already applies
    #: to a live EAL campaign.
    authorized_by: Optional[str] = None
    reason: Optional[str] = None
    #: embedded (default, self-contained) | served (cluster→SimCore required)
    delivery: Optional[str] = None
    #: Cluster-reachable SimCore base URL, used only by served delivery.
    server: Optional[str] = None


async def _load_scenario(scenario_id: str, db: AsyncSession) -> Scenario:
    result = await db.execute(
        select(Scenario).where(Scenario.scenario_id == scenario_id)
    )
    scenario: Optional[Scenario] = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Scenario not found", "code": "SCENARIO_NOT_FOUND", "detail": f"scenario_id='{scenario_id}'"},
        )
    return scenario


def _resolve_format(scenario_id: str, scenario_dict: dict, format: str) -> tuple[str, str, list[str]]:
    """Validate + resolve ``format`` → (format, target, alternates)."""
    if format not in ("auto", *_FORMAT_TARGET):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid format",
                "code": "INVALID_FORMAT",
                "detail": "format must be one of 'auto', 'bash', 'powershell', 'k8s'",
            },
        )

    targets = emittable_targets(scenario_dict)

    if format == "auto":
        if not targets:
            # Every target failed — report the POSIX reasons, which name the
            # steps a DC is most likely to be able to fix.
            raise _unsatisfiable(
                scenario_id, "posix", resolve_target(scenario_dict, "posix").unresolved
            )
        # POSIX first: the console has always downloaded .sh, and a scenario
        # that can do both should not change format under an existing caller.
        # NOTE this never resolves to k8s, so `auto` can never become a path by
        # which a privileged manifest is obtained without asking for one.
        format = "bash" if "posix" in targets else "powershell"

    target = _FORMAT_TARGET[format]
    if target not in targets:
        raise _unsatisfiable(
            scenario_id, target, resolve_target(scenario_dict, target).unresolved
        )
    return format, target, [t for t in targets if t != target]


def _response(
    content: str,
    *,
    filename: str,
    media_type: str,
    headers: dict[str, str],
) -> PlainTextResponse:
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        **headers,
    }
    return PlainTextResponse(content=content, media_type=media_type, headers=headers)


@router.get("/{scenario_id}/download")
async def download_bundle(
    scenario_id: str,
    format: str = Query(
        "auto",
        description="Output format: auto | bash | powershell | k8s",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate and download an execution bundle. **Ungated path.**

    format=auto        → the scenario's own emittable target (POSIX preferred)
    format=bash        → shell script          (requires a POSIX-emittable scenario)
    format=powershell  → PowerShell script     (requires a Windows-emittable scenario)
    format=k8s         → Kubernetes manifest, **baseline postures only**

    A GET carries no body, so it can carry no consent and no ``authorized_by``.
    A scenario whose k8s manifest would plant gated cluster capabilities is
    therefore refused here with **409 GATED_FORMAT_REQUIRES_POST** naming the
    capabilities and the POST endpoint — it is *not* silently downgraded to a
    baseline manifest, because a DC who asked for the escape scenario and got a
    manifest that cannot escape reports "the attack ran and Cortex missed it".

    Every scenario in the corpus today is baseline, so every existing caller is
    unaffected.

    A scenario that cannot satisfy the requested target returns 409 naming the
    offending steps, rather than emitting a bundle that parses and then dies in
    front of a customer — which reads as "the stack detected nothing".
    """
    scenario = await _load_scenario(scenario_id, db)
    scenario_dict = _scenario_document(scenario)
    format, target, alternates = _resolve_format(scenario_id, scenario_dict, format)

    headers = {"X-CortexSim-Bundle-Target": target}
    if alternates:
        headers["X-CortexSim-Bundle-Alternates"] = ",".join(alternates)

    if format == "bash":
        content = generate_bash(scenario_dict)
        filename, media_type = f"cortexsim-{scenario_id}.sh", "text/x-shellscript"
        headers["X-CortexSim-Bundle-Selfcontained"] = "true"
    elif format == "powershell":
        content = generate_powershell(scenario_dict)
        filename = f"cortexsim-{scenario_id}.ps1"
        # NOT application/x-powershell — unregistered, and some proxies mangle it.
        media_type = "text/plain; charset=utf-8"
        headers["X-CortexSim-Bundle-Selfcontained"] = "true"
    else:  # k8s
        posture = _posture_of(scenario_dict)
        if km.required_consent_keys(posture):
            raise _gated_format_requires_post(scenario_id, posture)
        content = generate_k8s(scenario_dict)
        # Scan the bytes anyway. `required_consent_keys` read the declaration;
        # this reads the emission, and the whole point of the gate is that it
        # notices when those two disagree.
        slugs, risk_tier = _verify_emitted(
            scenario_id, content, {},
            allow_privileged_k8s=_allow_privileged_k8s(), authorized_by="",
        )
        filename, media_type = f"cortexsim-{scenario_id}-k8s.yaml", "application/x-yaml"
        headers["X-CortexSim-Bundle-Risk-Tier"] = risk_tier
        headers["X-CortexSim-Bundle-Capabilities"] = ",".join(slugs) or "-"
        # NOT hardcoded true. A baseline posture that stages tool payloads is
        # ungated and still reaches this path, and it emits a SERVED manifest
        # that needs cluster→SimCore reachability. Claiming self-containment
        # there would smuggle the one relaxed delivery contract past the caller
        # — the doc's rule is that it is stated, never discovered.
        headers["X-CortexSim-Bundle-Selfcontained"] = _self_contained(content)

    logger.info(
        "download_bundle scenario_id=%s format=%s target=%s alternates=%s",
        scenario_id, format, target, alternates,
    )
    return _response(content, filename=filename, media_type=media_type, headers=headers)


@router.post("/{scenario_id}/bundle")
async def generate_bundle(
    scenario_id: str,
    body: BundleRequest = Body(default_factory=BundleRequest),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a bundle **with a consent body**. The gated path.

    This is the only surface that can emit a Kubernetes manifest planting gated
    cluster capabilities, and it is a POST precisely because consent needs a
    body: two orthogonal booleans plus the operator who obtained the customer's
    approval.

    ```
    POST /api/scenarios/SIM-CDR-011/bundle
    {"format": "k8s",
     "consent": {"node_access_authorized": true},
     "authorized_by": "h.reed",
     "reason": "ACME POV day 2 — container escape"}
    ```

    Refusals: **409 CLUSTER_CONSENT_REQUIRED** (send the key),
    **403 CLUSTER_PRIVILEGE_DISABLED** (the deployment owner must set
    ``CORTEXSIM_ALLOW_PRIVILEGED_K8S``), **422 CLUSTER_CAPABILITY_FORBIDDEN** /
    **CLUSTER_NAMESPACE_FORBIDDEN** (nothing unlocks these).

    ``bash`` and ``powershell`` are accepted for surface symmetry and are
    ungated — they are self-contained scripts a DC runs on a host they already
    own, not objects created inside a customer's cluster.
    """
    scenario = await _load_scenario(scenario_id, db)
    scenario_dict = _scenario_document(scenario)
    fmt, target, alternates = _resolve_format(scenario_id, scenario_dict, body.format)

    authorized_by = (body.authorized_by or "").strip()
    consent = {k: bool(v) for k, v in (body.consent or {}).items()}
    allow_priv = _allow_privileged_k8s()

    headers = {"X-CortexSim-Bundle-Target": target}
    if alternates:
        headers["X-CortexSim-Bundle-Alternates"] = ",".join(alternates)

    if fmt == "bash":
        content = generate_bash(scenario_dict)
        filename, media_type = f"cortexsim-{scenario_id}.sh", "text/x-shellscript"
        slugs, risk_tier, self_contained = [], "baseline", True
    elif fmt == "powershell":
        content = generate_powershell(scenario_dict)
        filename = f"cortexsim-{scenario_id}.ps1"
        media_type = "text/plain; charset=utf-8"
        slugs, risk_tier, self_contained = [], "baseline", True
    else:  # k8s
        posture = _posture_of(scenario_dict)
        required = km.required_consent_keys(posture)

        # Consent first — `_consent_error` folds a blank `authorized_by` into
        # the same refusal, so a caller who sent neither gets one round trip and
        # one list of everything it must send.
        try:
            km.check_cluster_consent(
                scenario_id, posture, consent, allow_privileged_k8s=allow_priv,
            )
        except km.ClusterConsentRequired as exc:
            raise _consent_error(exc, authorized_by=authorized_by) from exc

        # Consent is complete but nobody's name is on it. An audit row with no
        # named operator proves nothing to the customer six weeks later, which
        # is the same standard SafetyPolicy already applies to a live campaign.
        if required and not authorized_by:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Gated bundle generation requires a named operator",
                    "code": "CLUSTER_CONSENT_REQUIRED",
                    "detail": (
                        f"{scenario_id} is a {posture.risk_tier()} manifest and "
                        f"consent [{', '.join(required)}] was presented, but "
                        f"authorized_by is empty."
                    ),
                    "scenario_id": scenario_id,
                    "risk_tier": posture.risk_tier(),
                    "missing_consent": ["authorized_by"],
                    "remedy": (
                        "Re-POST with authorized_by set to the operator who "
                        "obtained the customer's approval."
                    ),
                    "docs": GATE_DOCS,
                },
            )

        try:
            content = generate_k8s(
                scenario_dict,
                delivery=body.delivery,
                simcore_url=body.server or "",
                consent=consent,
                allow_privileged_k8s=allow_priv,
            )
        except km.ClusterConsentRequired as exc:  # pragma: no cover - defence in depth
            raise _consent_error(exc, authorized_by=authorized_by) from exc
        except km.PayloadTooLargeToEmbed as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Payload too large to embed in a ConfigMap",
                    "code": "PAYLOAD_TOO_LARGE_TO_EMBED",
                    "detail": str(exc),
                    "scenario_id": scenario_id,
                    "docs": GATE_DOCS,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Invalid bundle request",
                    "code": "INVALID_BUNDLE_REQUEST",
                    "detail": str(exc),
                    "scenario_id": scenario_id,
                },
            ) from exc

        slugs, risk_tier = _verify_emitted(
            scenario_id, content, consent,
            allow_privileged_k8s=allow_priv, authorized_by=authorized_by,
        )
        filename, media_type = f"cortexsim-{scenario_id}-k8s.yaml", "application/x-yaml"
        self_contained = _self_contained(content) == "true"

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    entry = {
        "scenario_id": scenario_id,
        "format": fmt,
        "target": target,
        "risk_tier": risk_tier,
        "capabilities": slugs,
        "consent_keys": sorted(k for k, v in consent.items() if v),
        "consent_presented": consent,
        "authorized_by": authorized_by,
        "reason": body.reason,
        "manifest_sha256": digest,
        "self_contained": self_contained,
    }
    await _audit_bundle(entry)

    headers.update({
        "X-CortexSim-Bundle-Format": fmt,
        "X-CortexSim-Manifest-SHA256": digest,
        "X-CortexSim-Bundle-Risk-Tier": risk_tier,
        "X-CortexSim-Bundle-Capabilities": ",".join(slugs) or "-",
        "X-CortexSim-Bundle-Consent-Keys": ",".join(entry["consent_keys"]) or "-",
        "X-CortexSim-Bundle-Authorized-By": _header_safe(authorized_by) or "-",
        # The machine-readable statement of the delivery contract. A script must
        # be able to see that a bundle needs cluster→SimCore reachability
        # without parsing the manifest's header comments.
        "X-CortexSim-Bundle-Selfcontained": "true" if self_contained else "false",
    })
    if not self_contained:
        headers["X-CortexSim-Bundle-Delivery"] = "served"
    return _response(content, filename=filename, media_type=media_type, headers=headers)


def _scenario_is_licensable(scenario: Scenario, profile: str) -> bool:
    """True when a canned tenant profile covers this scenario's requirements.

    Base platform is satisfied by owning ANY listed platform (the index names
    the platforms a use case can land on, not a set to own in full); add-ons
    must all be held. An unknown profile filters nothing out — better to show
    the full catalog than to silently hide it behind a typo.
    """
    from api.pov import CANNED_PROFILES  # noqa: PLC0415

    prof = CANNED_PROFILES.get(profile)
    if prof is None:
        return True
    need_base = scenario.required_base_platform or []
    base_ok = (not need_base) or bool(set(need_base) & set(prof["base_platform"]))
    addons_ok = all(a in prof["addons"] for a in (scenario.required_addons or []))
    return base_ok and addons_ok
