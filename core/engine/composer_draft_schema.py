"""
CortexSim Composer — draft scenario schema + draft→ORM converter.

A DC composes a chain in the Composer console (`ui/.../ComposerView.jsx`) and
persists it as a ``Scenario`` row with ``status='draft'``. That row is launchable
through the EXISTING orchestrator path (``Orchestrator.launch`` selects the DB
row by id), so a draft needs no new orchestration substrate — only a schema that
accepts a *from-scratch* chain the strict corpus loader (``ScenarioSchema``)
would reject for lacking a UC/TC binding, a version, MITRE metadata, etc.

``DraftScenarioSchema`` is that schema. It RELAXES, relative to ``ScenarioSchema``:

  * ``uc_ref`` / ``tc_ref`` / ``uc_name`` / ``tc_name`` / ``mitre_*`` /
    ``version`` / ``push_supported`` / ``pull_supported`` become optional and
    are sentinel-filled by :func:`draft_to_orm_kwargs`.
  * it does NOT run the UC/TC index FK check at rest — an ``UNBOUND`` draft is a
    legal *saved* state. Binding is enforced at LAUNCH (the D5 gate), not at save.

What it DOES NOT relax — reused verbatim from the strict loader so the two paths
cannot drift into two notions of "well-formed":

  * the per-step shape (``StepSchema``) and per-detection shape
    (``StepExpectedDetection``, incl. the six-type ``detection_type`` enum),
  * the causality-spine rule (``validate_causality_spine`` — one root, no
    self/forward/unknown ``parent_step`` refs, valid ``pivot``),
  * ``cgo_anchor`` / ``execution_identity`` / ``cleanup`` / ``external_tools``
    structural schemas, and the plane enum (``VALID_PLANES``).

Draft-specific structural rules it ADDS (surfaced as ``DRAFT_SCHEMA_INVALID``):
empty ``steps`` rejected, duplicate step ids rejected, and the derived
``detection_types`` union must be non-empty (mirrors
``ScenarioSchema.validate_detection_types`` — a draft with no detection anywhere
proves nothing and is not a legal saved state).

This module NEVER touches the corpus boot path: ``scenario_loader``'s loader,
``ScenarioSchema`` and ``make check-refs`` stay byte-identical, and drafts live
only in ``data/cortexsim.db`` — never written to ``scenarios/``.
"""

from __future__ import annotations

import secrets
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Reuse the strict loader's validated building blocks VERBATIM. Importing (not
# copying) them is the whole point: a change to the step/detection/causality
# contract lands in one place and both the corpus and the composer inherit it.
from engine.scenario_loader import (
    DETECTION_TYPES,
    VALID_PLANES,
    AdditionalTechnique,
    CgoAnchorSchema,
    CleanupSchema,
    ExecutionIdentitySchema,
    ExternalToolSchema,
    KpiThreshold,
    StepSchema,
    validate_causality_spine,
)

__all__ = [
    "DraftScenarioSchema",
    "DraftValidationError",
    "draft_to_orm_kwargs",
    "orm_sentinels",
]


class DraftValidationError(ValueError):
    """Raised when a composed draft is structurally invalid. The API layer maps
    this to ``422 {code: 'DRAFT_SCHEMA_INVALID'}``."""


# ---------------------------------------------------------------------------
# Draft schema
# ---------------------------------------------------------------------------


class DraftStepSchema(StepSchema):
    """A draft step relaxes exactly the two fields a work-in-progress chain may
    not have filled yet, while keeping everything else — ``id`` / ``name`` /
    ``command`` required, the detection shape, and the causality-spine rules —
    as strict as the corpus loader:

    - ``identity``: how the step runs. A blank step the DC just dropped on the
      canvas has none; it defaults to ``direct`` (the agent's own user) at
      ORM-conversion so the run path is always well-defined.
    - ``mitre_technique``: bound later from a TTP card. Optional here so the
      step can be authored before its technique is chosen.

    Reusing ``StepSchema`` for the rest means the strict command/detection/spine
    validation is not forked — only these two fields are widened.
    """

    identity: Optional[str] = None
    mitre_technique: Optional[str] = None


class DraftScenarioSchema(BaseModel):
    """A composer-authored draft chain, validated for structural well-formedness
    but NOT for a UC/TC index binding (that is the launch gate's job).

    Only ``name``, ``plane`` and well-formed ``steps`` are required. Every other
    field is optional and, when omitted, sentinel-filled at ORM-conversion time.
    """

    # ── Required ────────────────────────────────────────────────────────────
    name: str
    plane: str
    steps: list[DraftStepSchema]

    # ── Optional identity / provenance ──────────────────────────────────────
    author: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    # ── Optional UC/TC index binding (sentinel-filled when omitted) ─────────
    uc_ref: Optional[str] = None
    tc_ref: Optional[str] = None
    tc_refs: list[str] = Field(default_factory=list)
    pov_scenario_id: Optional[str] = None

    # ── Optional MITRE metadata (sentinel-filled when omitted) ──────────────
    mitre_tactic: Optional[str] = None
    mitre_tactic_name: Optional[str] = None
    mitre_technique: Optional[str] = None
    mitre_technique_name: Optional[str] = None
    additional_techniques: list[AdditionalTechnique] = Field(default_factory=list)

    # ── Optional causality / identity / execution config ────────────────────
    cgo_anchor: Optional[CgoAnchorSchema] = None
    execution_identity: Optional[ExecutionIdentitySchema] = None
    cleanup: Optional[CleanupSchema] = None
    external_tools: list[ExternalToolSchema] = Field(default_factory=list)

    # ── Optional measurement contract ───────────────────────────────────────
    threshold: Optional[KpiThreshold] = None
    primary_kpi: Optional[str] = None
    validation_methodology: Optional[str] = None
    methodology_family: Optional[str] = None
    success_criteria: Optional[str] = None
    moat_tier: Optional[str] = None
    correlation_window_seconds: Optional[int] = None
    stitching_key: Optional[str] = None
    required_planes_in_incident: list[str] = Field(default_factory=list)

    # -- validators ----------------------------------------------------------

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must be a non-empty string")
        return v

    @field_validator("plane")
    @classmethod
    def _validate_plane(cls, v: str) -> str:
        # Reuse the single source of truth for the plane enum.
        if v not in VALID_PLANES:
            raise ValueError(f"plane must be one of {set(VALID_PLANES)}, got '{v}'")
        return v

    @field_validator("methodology_family")
    @classmethod
    def _validate_methodology_family(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {f"F{i}" for i in range(1, 11)}
        if v not in allowed:
            raise ValueError(
                f"methodology_family must be one of {sorted(allowed)}, got '{v}'"
            )
        return v

    @field_validator("moat_tier")
    @classmethod
    def _validate_moat_tier(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"MOAT", "LEAD", "PARITY"}
        if v not in allowed:
            raise ValueError(f"moat_tier must be one of {allowed}, got '{v}'")
        return v

    @field_validator("additional_techniques", mode="before")
    @classmethod
    def _normalize_additional_techniques(cls, v: Any) -> Any:
        """Accept bare technique-id strings or {technique, name} mappings —
        identical normalization to ScenarioSchema so the two agree."""
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("additional_techniques must be a list")
        normalized: list[dict[str, str]] = []
        for entry in v:
            if isinstance(entry, str):
                normalized.append({"technique": entry, "name": ""})
            elif isinstance(entry, dict):
                normalized.append(entry)
            else:
                raise ValueError(
                    "additional_techniques entries must be a technique-id "
                    f"string or a {{technique, name}} mapping, got "
                    f"{type(entry).__name__}"
                )
        return normalized

    @model_validator(mode="after")
    def _require_steps(self) -> "DraftScenarioSchema":
        if not self.steps:
            raise ValueError("a draft must declare at least one step")
        return self

    @model_validator(mode="after")
    def _reject_duplicate_step_ids(self) -> "DraftScenarioSchema":
        seen: set[str] = set()
        dupes: list[str] = []
        for step in self.steps:
            if step.id in seen and step.id not in dupes:
                dupes.append(step.id)
            seen.add(step.id)
        if dupes:
            raise ValueError(
                f"duplicate step id(s) not allowed in a draft: {dupes}"
            )
        return self

    @model_validator(mode="after")
    def _validate_spine(self) -> "DraftScenarioSchema":
        # Same one-root/no-forward-ref spine rule as the strict loader.
        validate_causality_spine(self.steps)
        return self

    @model_validator(mode="after")
    def _require_a_detection(self) -> "DraftScenarioSchema":
        # The derived detection_types union must be non-empty — mirrors
        # ScenarioSchema.validate_detection_types. A draft where NO step carries
        # any expected detection proves nothing and is not a legal saved state.
        # (Individual steps MAY have empty expected_detections — the canvas
        # renders those as an on-canvas "NO EXPECTED DETECTION" gap — but the
        # chain as a whole must declare at least one.)
        types = {d.type for s in self.steps for d in s.expected_detections}
        if not types:
            raise ValueError(
                "a draft must declare at least one expected detection "
                "(detection_types would be empty)"
            )
        return self


# ---------------------------------------------------------------------------
# Draft → ORM sentinels + converter
# ---------------------------------------------------------------------------

# The explicit sentinels a from-scratch draft uses to satisfy the Scenario ORM's
# ``nullable=False`` columns that a draft legitimately lacks. Kept as a module
# constant so the API layer and tests reference ONE definition (the frozen
# contract's ``orm_sentinels``).
orm_sentinels: dict[str, Any] = {
    "version": "0.1-draft",
    "status": "draft",
    "uc_ref": "UNBOUND",
    "tc_ref": "UNBOUND",
    "uc_name": "(draft — unbound)",
    "tc_name": "(draft — unbound)",
    "mitre_tactic": "TA0000",
    "mitre_tactic_name": "Uncategorized",
    "mitre_technique": "T0000",
    "mitre_technique_name": "",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    """Lowercase, non-alphanumeric → '-', collapse repeats, trim edges."""
    s = _SLUG_RE.sub("-", name.lower()).strip("-")
    return s or "draft"


def _derive_push_supported(steps: list[dict[str, Any]], cleanup: Optional[dict[str, Any]]) -> bool:
    """True when the push generator can emit a runnable bundle for at least one
    target, derived from the step COMMAND TEXT (never a label). Failing closed
    to False on any import/parse error keeps a draft saveable even if the push
    generator is unhappy — push-ability is informational for a draft."""
    try:
        from engine.push_generator import emittable_targets  # noqa: PLC0415

        return bool(emittable_targets({"steps": steps, "cleanup": cleanup}))
    except Exception:  # pragma: no cover - defensive; never block a save
        return False


def draft_to_orm_kwargs(
    draft: DraftScenarioSchema,
    *,
    author: str = "composer",
    scenario_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build ``Scenario`` ORM keyword args from a validated draft.

    Fills every ``nullable=False`` column a from-scratch draft lacks with the
    explicit sentinels in :data:`orm_sentinels`; derives ``detection_types``
    from the steps' expected detections and ``push_supported`` from the command
    text; and stamps ``status='draft'`` and the ``'composer-draft'`` tag.

    ``scenario_id`` defaults to ``SIM-DRAFT-<slug>-<rand>`` where ``<slug>`` is
    the slugified name and ``<rand>`` is a short random suffix, so a draft id is
    collision-resistant by construction (the API may still choose to derive a
    deterministic id and handle collisions itself; pass ``scenario_id`` to
    override).
    """
    steps = [s.model_dump() for s in draft.steps]
    # A blank draft step carries no identity; the run path needs a defined one,
    # so an unset identity persists as 'direct' (the agent's own user) rather
    # than null. mitre_technique may stay null — it is metadata, bound later.
    for s in steps:
        if not s.get("identity"):
            s["identity"] = "direct"

    # detection_types = union of every step's expected_detections[].type,
    # sorted and de-duped. The schema guarantees this is non-empty.
    detection_types = sorted(
        {d.type for s in draft.steps for d in s.expected_detections}
    )

    # Author precedence: an explicit author on the draft body wins over the
    # caller default; neither present → 'composer'.
    resolved_author = draft.author or author or "composer"

    # tags always include 'composer-draft', de-duped, order-preserving.
    tags = list(dict.fromkeys([*draft.tags, "composer-draft"]))

    # tc_refs: honour a client-provided list; else derive [tc_ref] when a real
    # (non-sentinel) tc_ref is bound; else empty.
    tc_ref = draft.tc_ref or orm_sentinels["tc_ref"]
    uc_ref = draft.uc_ref or orm_sentinels["uc_ref"]
    if draft.tc_refs:
        tc_refs = list(dict.fromkeys(draft.tc_refs))
    elif tc_ref and tc_ref != orm_sentinels["tc_ref"]:
        tc_refs = [tc_ref]
    else:
        tc_refs = []

    # mitre_technique sentinel: prefer the first step's declared technique.
    first_technique = draft.steps[0].mitre_technique if draft.steps else ""
    mitre_technique = (
        draft.mitre_technique or first_technique or orm_sentinels["mitre_technique"]
    )

    execution_identity = (
        draft.execution_identity.model_dump()
        if draft.execution_identity is not None
        else {"default": "direct", "options": ["direct"]}
    )

    cleanup = draft.cleanup.model_dump() if draft.cleanup else None

    resolved_id = scenario_id or f"SIM-DRAFT-{_slug(draft.name)}-{secrets.token_hex(3)}"

    kwargs: dict[str, Any] = {
        "scenario_id": resolved_id,
        "name": draft.name,
        "version": orm_sentinels["version"],
        "status": orm_sentinels["status"],
        "plane": draft.plane,
        "detection_types": detection_types,
        "uc_ref": uc_ref,
        "tc_ref": tc_ref,
        "tc_refs": tc_refs,
        "pov_scenario_id": draft.pov_scenario_id,
        "uc_name": orm_sentinels["uc_name"],
        "tc_name": orm_sentinels["tc_name"],
        "mitre_tactic": draft.mitre_tactic or orm_sentinels["mitre_tactic"],
        "mitre_tactic_name": (
            draft.mitre_tactic_name or orm_sentinels["mitre_tactic_name"]
        ),
        "mitre_technique": mitre_technique,
        "mitre_technique_name": (
            draft.mitre_technique_name
            if draft.mitre_technique_name is not None
            else orm_sentinels["mitre_technique_name"]
        ),
        "additional_techniques": [t.model_dump() for t in draft.additional_techniques],
        "execution_identity": execution_identity,
        "push_supported": _derive_push_supported(steps, cleanup),
        # The agent beacon runs any command; channel is implicitly 'agent' in
        # Phase 1, so pull is always supported for a draft.
        "pull_supported": True,
        "external_tools": [t.model_dump() for t in draft.external_tools],
        "steps": steps,
        "cleanup": cleanup,
        "tags": tags,
        "author": resolved_author,
        "cgo_anchor": draft.cgo_anchor.model_dump() if draft.cgo_anchor else None,
        # Measurement contract (all optional on a draft).
        "validation_methodology": draft.validation_methodology,
        "methodology_family": draft.methodology_family,
        "primary_kpi": draft.primary_kpi,
        "threshold": draft.threshold.model_dump() if draft.threshold else None,
        "success_criteria": draft.success_criteria,
        "moat_tier": draft.moat_tier,
        "correlation_window_seconds": draft.correlation_window_seconds,
        "stitching_key": draft.stitching_key,
        "required_planes_in_incident": draft.required_planes_in_incident,
        # Entitlements are DERIVED from the index for corpus scenarios; a draft
        # is UNBOUND, so it declares none.
        "required_base_platform": [],
        "required_addons": [],
        "created_at": datetime.utcnow(),
    }
    return kwargs
