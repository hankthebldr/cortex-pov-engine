"""
CortexSim Scenario Loader.

Reads all *.yml files from the scenarios/ directory (recursively), skipping _schema.yml.
Validates each file against the Pydantic ScenarioSchema and upserts into the Scenario table.
Invalid files are rejected with a clear logged error — never silently skipped.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cortexsim.loader")


# Canonical Cortex detection-engine vocabulary. XQL + Correlation are
# first-class alongside the original BIOC | Analytics | IOC set (GAP-2);
# ABIOC joins as the 6th type (Plan 01):
#   BIOC        — Behavioral Indicator of Compromise rule
#   XQL         — XSIAM Query Language scheduled/saved query detection
#   Analytics   — XSIAM analytics / ML behavioral detector
#   Correlation — XSIAM correlation rule stitching signals across sources
#   IOC         — atomic indicator (hash / domain / IP) match
#   ABIOC       — Analytics Behavioral IOC: PANW-authored, auto-tuned
#                 behavioral-ML detection with an identified causality chain
#                 (distinct from hand-authored BIOC)
# NOTE: XDM modeling rules (Plan 02) are a normalization *substrate*, NOT a
# detection_type — they are carried on cards as detections.modeling_rules[],
# never as an expected_detections[].type value. This frozenset stays at six.
DETECTION_TYPES: frozenset[str] = frozenset(
    {"BIOC", "XQL", "Analytics", "Correlation", "IOC", "ABIOC"}
)


# ---------------------------------------------------------------------------
# Pydantic validation schema (mirrors the YAML _schema.yml structure)
# ---------------------------------------------------------------------------


class ExecutionIdentitySchema(BaseModel):
    default: str
    options: list[str]


class KpiThreshold(BaseModel):
    """v2.0 KPI threshold — structured form of the master sheet's `Threshold` column.

    Optional and back-compatible. Existing scenarios without thresholds continue to load.
    Used by F1/F2/F6 family harnesses to compute pass/fail on Run completion.
    """
    kpi: str                       # e.g. "MTTD", "Cross-Source Correlation Rate"
    op: str                        # one of: ≤ ≥ < > = (unicode permitted for readability)
    value: float
    unit: Optional[str] = None     # "seconds", "%", "count", etc.


# ── Causality contract vocabulary (additive, back-compat) ───────────────────
# The seven typed causality-graph edge kinds a step may declare as its pivot to
# its parent step, and the OS/env platforms a step may run on. Kept in sync with
# core/engine/causality_graph.py EDGE_* constants and the lint hook.
_PIVOTS = {
    "process_lineage",
    "network_session",
    "endpoint_network_stitch",
    "shared_entity",
    "exposure_exploit",
    "exploit_impact",
    "temporal",
}
_PLATFORMS = {"linux", "windows", "macos", "container", "k8s"}


class CgoAnchorSchema(BaseModel):
    """Scenario-level Causality Group Owner anchor. Drives the CGO node's
    label + username in the causality graph, replacing the synthetic
    'cortexsim-agent'/'root' default. Optional/back-compat."""
    image_name: str
    primary_username: Optional[str] = None


class StepCausalitySchema(BaseModel):
    """Step-level causality contract: this step's process descends from
    ``parent_step`` via the declared ``pivot`` edge kind. The root step omits
    this block (it links from the CGO)."""
    parent_step: str
    pivot: str = "process_lineage"

    @field_validator("pivot")
    @classmethod
    def _v(cls, v: str) -> str:
        if v not in _PIVOTS:
            raise ValueError(f"causality.pivot must be one of {sorted(_PIVOTS)}")
        return v


class StepExpectedDetection(BaseModel):
    plane: str
    type: str
    description: str

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in DETECTION_TYPES:
            raise ValueError(
                f"expected_detection type must be one of "
                f"{sorted(DETECTION_TYPES)}, got '{v}'"
            )
        return v
    # Phase 1 — optional bridge to detection_scanner/ttps/*.json.
    # When both fields resolve in the TTP catalog at startup, the orchestrator
    # copies the card's BIOC / XQL / correlation logic onto the Result row so
    # the POV report can render the deployable query inline.
    ttp_ref: Optional[str] = None       # e.g. "TTP-2026-0002"
    detection_id: Optional[str] = None  # e.g. "bioc-lsass-handle-open-with-sensitive-access-rights"
    # v2.0 — optional XQL fragment that proves this detection fired. Run on a
    # timer by the validation harness; auto-marks observed when count > 0.
    verification_xql: Optional[str] = None
    # v2.0 — per-detection KPI contribution (e.g. this detection contributes to
    # the scenario-level MTTD measurement with its own threshold).
    kpi_contribution: Optional[KpiThreshold] = None


class StepSchema(BaseModel):
    id: str
    name: str
    command: str
    identity: str
    mitre_technique: str
    expected_detections: list[StepExpectedDetection] = []
    # ── Causality contract (all optional, back-compat) ──────────────────────
    # causality: this step's lineage/pivot to an EARLIER step. Omitted on the
    # root step (which links from the CGO). platforms: OS/env coverage for this
    # step. platform_variants: per-OS command equivalents for cross-platform TTPs.
    causality: Optional[StepCausalitySchema] = None
    platforms: list[str] = Field(default_factory=list)
    platform_variants: dict[str, str] = Field(default_factory=dict)

    @field_validator("platforms")
    @classmethod
    def _vp(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in _PLATFORMS]
        if bad:
            raise ValueError(f"platforms must be subset of {sorted(_PLATFORMS)}, got {bad}")
        return v


class ExternalToolSchema(BaseModel):
    name: str
    source: Optional[str] = None
    type: str
    install_inline: bool = False
    # Phase A — optional bridge to a Tool Adapter pack in tools/packs/.
    # When set, the loader resolves it against the adapter catalog at startup
    # and the orchestrator can substitute the adapter's run_template into
    # scenario step commands via the {adapter:<id>} placeholder. Existing
    # scenarios without adapter_ref continue to work via the legacy path.
    adapter_ref: Optional[str] = None


class CleanupSchema(BaseModel):
    commands: list[str] = []
    k8s_teardown: Optional[str] = None


class AdditionalTechnique(BaseModel):
    """A secondary MITRE ATT&CK technique exercised by a scenario beyond the
    primary ``mitre_technique`` (GAP-5).

    Author-facing YAML accepts two shapes, both normalized to this model:
      * mapping  — ``{technique: "T1082", name: "System Information Discovery"}``
      * string   — ``"T1021.004"`` → ``{technique: "T1021.004", name: ""}``

    Persisted to the Scenario ORM so the coverage heatmap can fuse these in.
    """

    technique: str
    name: str = ""


class ScenarioSchema(BaseModel):
    scenario_id: str
    name: str
    version: str
    status: str
    plane: str
    detection_types: list[str]
    uc_ref: str
    tc_ref: str
    uc_name: str
    tc_name: str
    mitre_tactic: str
    mitre_tactic_name: str
    mitre_technique: str
    mitre_technique_name: str
    # GAP-5 — secondary techniques exercised by the scenario. Accepts bare
    # technique-id strings or {technique, name} mappings; normalized to
    # AdditionalTechnique and persisted so the coverage heatmap fuses them in.
    additional_techniques: list[AdditionalTechnique] = Field(default_factory=list)
    threat_report: Optional[str] = None
    threat_report_url: Optional[str] = None
    execution_identity: ExecutionIdentitySchema
    push_supported: bool
    pull_supported: bool
    external_tools: list[ExternalToolSchema] = []
    steps: list[StepSchema]
    cleanup: Optional[CleanupSchema] = None
    tags: list[str] = []
    author: Optional[str] = None
    # ── Causality contract (scenario-level, optional/back-compat) ───────────
    # The Causality Group Owner anchor drives the CGO node label/username in the
    # causality graph. Absent → the graph falls back to 'cortexsim-agent'/'root'.
    cgo_anchor: Optional[CgoAnchorSchema] = None
    # GAP-3 / S-08 / S-10 — schema-vs-loader reconcile. The doc-schema once
    # marked `created`/`last_updated` "required", but the loader never modelled
    # them, so Pydantic silently dropped them. Model them here as optional ISO
    # date strings so the loader's real contract matches the documented one.
    # They are provenance only (not persisted to the ORM by _schema_to_orm_kwargs).
    created: Optional[str] = None
    last_updated: Optional[str] = None

    # Infra generator hints (optional, backward compatible)
    required_content: list[dict] = Field(default_factory=list,
        description="Open-source tool repos this scenario needs installed")
    infra_modules_needed: list[str] = Field(default_factory=list,
        description="IaC generator module names for auto-suggest (e.g. ['base', 'edr'])")

    # ── v2.0 KPI / methodology metadata (all optional, back-compatible) ─────
    # Mirror the columns in HR-Cortex-Use-Case-Index-Master-v2.0.xlsx.
    # Surfaced in POV reports and (later) used by the validation harness to
    # drive family-specific verification. Not yet persisted to the ORM —
    # validation-only in this pass. See docs/uc_tc_mapping/v2.0-methodology-master.md.
    validation_methodology: Optional[str] = None         # e.g. "Causality Graph Stitching"
    methodology_family: Optional[str] = None             # one of F1..F10
    primary_kpi: Optional[str] = None                    # e.g. "Cross-Source Correlation Rate"
    threshold: Optional[KpiThreshold] = None             # structured pass/fail threshold
    success_criteria: Optional[str] = None               # multi-line natural language
    moat_tier: Optional[str] = None                      # MOAT | LEAD | PARITY

    # F2-family additional fields (required-by-convention for ANALYTICS-plane
    # stitching scenarios; optional at schema level so non-F2 scenarios can omit).
    correlation_window_seconds: Optional[int] = None     # default 60 in F2 docs
    required_planes_in_incident: list[str] = Field(default_factory=list)
    stitching_key: Optional[str] = None                  # src_host | session_id | user_principal | container_id

    @field_validator("methodology_family")
    @classmethod
    def validate_methodology_family(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {f"F{i}" for i in range(1, 11)}
        if v not in allowed:
            raise ValueError(f"methodology_family must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("moat_tier")
    @classmethod
    def validate_moat_tier(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"MOAT", "LEAD", "PARITY"}
        if v not in allowed:
            raise ValueError(f"moat_tier must be one of {allowed}, got '{v}'")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"active", "draft", "deprecated"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}, got '{v}'")
        return v

    @field_validator("plane")
    @classmethod
    def validate_plane(cls, v: str) -> str:
        allowed = {
            "EDR", "CDR", "NDR", "ITDR", "CLOUD_APP", "ANALYTICS",
            # AI / Browser / Agentic detection-set expansion
            "AI_ACCESS",   # Cortex AI Access Security — egress to AI providers
            "AIRS",        # Cortex AI Runtime Security — vulnerable LLM app
            "AI_SPM",      # Cortex AI Security Posture Management — static AI asset inventory + config
            "BROWSER",     # Prisma Browser — DLP / extension / phishing
            "KOI",         # Agentic endpoint / supply-chain (MCPs, skills, exts)
            # Exposure-management / posture / intel planes (IaC-backed surfaces)
            "ASM",         # Cortex ASM / Xpanse — internet-exposed attack-surface discovery
            "CSPM",        # Cortex Cloud Posture Management — misconfig findings
            "TIM",         # Cortex Threat Intel Management — IOC feed + matching traffic
            # Email line of defense — Proofpoint TAP / M365 ingestion + phishing/BEC correlation
            "EMAIL",
        }
        if v not in allowed:
            raise ValueError(f"plane must be one of {allowed}, got '{v}'")
        return v

    @field_validator("detection_types")
    @classmethod
    def validate_detection_types(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("detection_types must contain at least one value")
        invalid = [t for t in v if t not in DETECTION_TYPES]
        if invalid:
            raise ValueError(
                f"detection_types values must each be one of "
                f"{sorted(DETECTION_TYPES)}, got invalid {invalid}"
            )
        return v

    @field_validator("additional_techniques", mode="before")
    @classmethod
    def normalize_additional_techniques(cls, v: Any) -> Any:
        """Normalize the heterogeneous YAML shapes to {technique, name} dicts.

        Accepts a list whose entries are either bare technique-id strings or
        {technique, name} mappings. Runs before the AdditionalTechnique model
        coercion so both shapes land on the same persisted structure.
        """
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("additional_techniques must be a list")
        normalized: list[dict[str, str]] = []
        for entry in v:
            if isinstance(entry, str):
                normalized.append({"technique": entry, "name": ""})
            elif isinstance(entry, dict):
                # leave dict as-is; AdditionalTechnique validates the keys
                normalized.append(entry)
            else:
                raise ValueError(
                    "additional_techniques entries must be a technique-id "
                    f"string or a {{technique, name}} mapping, got {type(entry).__name__}"
                )
        return normalized

    @model_validator(mode="after")
    def _validate_causality_spine(self) -> "ScenarioSchema":
        """Cross-check the declared step-level causality spine.

        (a) every ``causality.parent_step`` must be the id of an EARLIER step
            (index < the declaring step) — forward/self/unknown refs are errors.
        (b) at most ONE step may omit ``causality`` (the root) once any step
            declares it, keeping a single connected spine.

        A scenario where NO step declares causality is legacy/star and passes
        untouched.
        """
        step_ids = [s.id for s in self.steps]
        index_of = {sid: i for i, sid in enumerate(step_ids)}

        declared = [s for s in self.steps if s.causality is not None]
        if not declared:
            return self  # legacy star — no contract

        for i, step in enumerate(self.steps):
            caus = step.causality
            if caus is None:
                continue
            parent = caus.parent_step
            if parent == step.id:
                raise ValueError(
                    f"step '{step.id}' causality.parent_step is a self-reference"
                )
            if parent not in index_of:
                raise ValueError(
                    f"step '{step.id}' causality.parent_step references unknown "
                    f"step '{parent}'"
                )
            if index_of[parent] >= i:
                raise ValueError(
                    f"step '{step.id}' causality.parent_step '{parent}' must be an "
                    f"EARLIER step (forward/self references are not allowed)"
                )

        roots = [s for s in self.steps if s.causality is None]
        if len(roots) > 1:
            raise ValueError(
                "a declared causality spine allows at most one root step "
                f"(steps without causality), got {len(roots)}: "
                f"{[s.id for s in roots]}"
            )
        return self


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _find_yaml_files(scenarios_dir: str) -> list[str]:
    """Recursively find all *.yml files, skipping _schema.yml and any
    sub-trees that hold non-scenario YAML (cortex-prompt-attacker probe
    packs under scenarios/airs/probes/, cortex-browser-attacker browser
    campaigns under scenarios/browser/campaigns/, packaged supporting
    YAMLs under scenarios/multi_plane/packages/)."""
    skip_dirnames = {"probes", "packages", "campaigns"}
    found: list[str] = []
    for root, dirs, files in os.walk(scenarios_dir):
        # Prune sub-tree walks for non-scenario directories.
        dirs[:] = [d for d in dirs if d not in skip_dirnames]
        for fname in files:
            if fname.endswith(".yml") and fname != "_schema.yml":
                found.append(os.path.join(root, fname))
    return sorted(found)


def _parse_and_validate(filepath: str) -> tuple[Optional[ScenarioSchema], Optional[str]]:
    """
    Parse a YAML file and validate it against ScenarioSchema.
    Returns (schema_obj, None) on success, (None, error_message) on failure.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            raw: Any = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, f"YAML parse error: {exc}"

    if not isinstance(raw, dict):
        return None, "YAML root is not a mapping"

    try:
        schema = ScenarioSchema(**raw)
        return schema, None
    except ValidationError as exc:
        return None, f"Schema validation failed:\n{exc}"


async def load_scenarios(scenarios_dir: str, db: AsyncSession) -> list[str]:
    """
    Load all scenario YAML files from scenarios_dir into the database.
    Performs an upsert (insert or update) keyed on scenario_id.
    Returns a list of successfully loaded scenario_ids.
    Invalid files are logged and counted but do not halt the load.
    """
    # Import here to avoid circular imports at module load time
    from models import Scenario  # noqa: PLC0415

    yaml_files = _find_yaml_files(scenarios_dir)
    if not yaml_files:
        logger.warning("No scenario YAML files found in %s", scenarios_dir)
        return []

    loaded_ids: list[str] = []
    errors: list[str] = []

    for filepath in yaml_files:
        schema, error = _parse_and_validate(filepath)
        if error:
            msg = f"REJECTED {filepath}: {error}"
            logger.error(msg)
            errors.append(msg)
            continue

        assert schema is not None  # guaranteed by parse_and_validate

        # Phase 1 — warn (don't fail) on dangling ttp_ref / detection_id
        # references. The catalog is loaded best-effort; missing cards are
        # advisory until Phase 2 backfills the corpus.
        _warn_dangling_ttp_refs(schema, filepath)
        # Phase A (tool adapter framework) — same warn-not-fail pattern for
        # external_tools.adapter_ref pointing at tools/packs/<id>.yml.
        _warn_dangling_adapter_refs(schema, filepath)
        # Wave-2 schema hygiene (warn, never reject): detection_types drift
        # (S-09), too-thin step lists (S-01), and detection-less steps (S-02).
        _warn_scenario_hygiene(schema, filepath)

        # UC/TC foreign-key validation against the master index snapshot
        # (S-10..S-14). Warns by default; rejects only under
        # CORTEXSIM_STRICT_REFS, so an incomplete crosswalk cannot brick boot.
        ref_error = _check_uctc_refs(schema, filepath)
        if ref_error:
            msg = f"REJECTED {filepath}: {ref_error}"
            logger.error(msg)
            errors.append(msg)
            continue

        # Upsert: check if the scenario_id already exists
        result = await db.execute(
            select(Scenario).where(Scenario.scenario_id == schema.scenario_id)
        )
        existing: Optional[Scenario] = result.scalar_one_or_none()

        scenario_dict = _schema_to_orm_kwargs(schema)

        if existing is None:
            scenario = Scenario(**scenario_dict)
            db.add(scenario)
            logger.info("LOADED new scenario %s from %s", schema.scenario_id, filepath)
        else:
            for key, val in scenario_dict.items():
                if key != "created_at":  # preserve original created_at
                    setattr(existing, key, val)
            logger.info("UPDATED scenario %s from %s", schema.scenario_id, filepath)

        loaded_ids.append(schema.scenario_id)

    await db.commit()

    if errors:
        logger.warning(
            "Scenario load complete: %d loaded, %d REJECTED. Review errors above.",
            len(loaded_ids),
            len(errors),
        )
    else:
        logger.info("Scenario load complete: %d scenarios loaded.", len(loaded_ids))

    return loaded_ids


def _check_uctc_refs(schema: "ScenarioSchema", filepath: str) -> Optional[str]:
    """Validate ``uc_ref`` / ``tc_ref`` as a foreign key into the master UC/TC
    index, and flag positioning drift against the same row.

    Emits five codes:

    ``S-10`` ``tc_ref`` not in the index                        — ERROR
    ``S-11`` ``uc_ref`` is neither a known UCS group nor UC      — ERROR
    ``S-12`` ``tc_ref`` resolves but its parent ≠ ``uc_ref``     — ERROR
    ``S-13`` ``moat_tier`` disagrees with the index tier         — WARNING
    ``S-14`` the TC's ``validation_class`` is POS/PLT/AUT, i.e.
             an assertion the index does not expect a detection
             scenario to satisfy                                 — WARNING

    Returns a rejection reason when ``CORTEXSIM_STRICT_REFS`` is on and an
    S-10/S-11/S-12 fired; otherwise ``None`` (the scenario still loads and the
    condition is logged). The registry degrades to ``unverified`` when the
    snapshot is absent, so a stripped deployment never rejects on this path.
    """
    from config import settings  # noqa: PLC0415
    from engine.uctc_registry import registry  # noqa: PLC0415

    verdict = registry.validate_ref(schema.uc_ref, schema.tc_ref)
    strict = bool(getattr(settings, "CORTEXSIM_STRICT_REFS", False))

    if verdict.is_error:
        code = {
            "dangling_tc": "S-10",
            "dangling_uc": "S-11",
            "fk_mismatch": "S-12",
        }[verdict.status]
        detail = (
            f"{code} scenario={schema.scenario_id} uc_ref={schema.uc_ref} "
            f"tc_ref={schema.tc_ref}: {verdict.detail} (from {filepath})"
        )
        if strict:
            return detail
        logger.warning("%s [advisory — CORTEXSIM_STRICT_REFS is off]", detail)
        return None

    tc = verdict.test_case
    if tc is None:  # unverified — no snapshot loaded
        return None

    # S-13: the scenario's positioning claim vs the index's. The index wins;
    # a scenario claiming MOAT against a PARITY test case oversells the demo.
    if schema.moat_tier and tc.differentiation_tier and \
            schema.moat_tier != tc.differentiation_tier:
        logger.warning(
            "S-13 scenario=%s declares moat_tier=%s but index tc=%s carries "
            "differentiation_tier=%s (from %s)",
            schema.scenario_id, schema.moat_tier, tc.tc_id,
            tc.differentiation_tier, filepath,
        )

    # S-14: the index classifies this TC as a posture/platform/automation
    # assertion, not a detection. A detection scenario bound to it will never
    # produce the signal the TC is scored on.
    if tc.validation_class in ("POS", "PLT", "AUT"):
        logger.warning(
            "S-14 scenario=%s binds tc=%s whose validation_class=%s — the "
            "index expects a %s assertion here, not a detection scenario "
            "(from %s)",
            schema.scenario_id, tc.tc_id, tc.validation_class,
            tc.validation_class, filepath,
        )

    return None


def _warn_dangling_ttp_refs(schema: "ScenarioSchema", filepath: str) -> None:
    """Log a warning for each ``ttp_ref`` / ``detection_id`` that does not
    resolve in the TTP catalog. Never raises — the bridge is opt-in.
    """
    # Imported lazily so unit tests that exercise the schema alone (without
    # running startup) don't pay the catalog import cost.
    from engine.ttp_catalog import catalog  # noqa: PLC0415

    for step in schema.steps:
        for det in step.expected_detections:
            if not det.ttp_ref and not det.detection_id:
                continue
            card = catalog.find(det.ttp_ref, det.detection_id)
            if card is None:
                logger.warning(
                    "scenario=%s step=%s expected_detection references "
                    "unresolved TTP card ttp_ref=%s detection_id=%s (from %s)",
                    schema.scenario_id, step.id,
                    det.ttp_ref, det.detection_id, filepath,
                )


def _warn_dangling_adapter_refs(schema: "ScenarioSchema", filepath: str) -> None:
    """Log a warning for each ``external_tools[].adapter_ref`` that does not
    resolve in the adapter catalog. Never raises — adapter wiring is opt-in
    and back-compatible with the legacy free-form ``external_tools`` shape.
    """
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    for tool in schema.external_tools:
        if not tool.adapter_ref:
            continue
        if catalog.find(tool.adapter_ref) is None:
            logger.warning(
                "scenario=%s external_tool name=%s references unresolved "
                "tool adapter adapter_ref=%s (from %s)",
                schema.scenario_id, tool.name, tool.adapter_ref, filepath,
            )


def _warn_scenario_hygiene(schema: "ScenarioSchema", filepath: str) -> None:
    """Emit soft warnings for schema-hygiene drift. Never raises — these are
    advisory and must not block boot.

    S-09 — declared ``detection_types`` differs from the union of the steps'
           ``expected_detections[].type`` values.
    S-01 — fewer than 2 steps (genuinely too thin to model a TTP chain).
    S-02 — a step with an empty ``expected_detections`` list reduces report
           coverage silently.
    """
    # S-01: too-thin step lists.
    if len(schema.steps) < 2:
        logger.warning(
            "scenario=%s declares only %d step(s) — fewer than 2 is unusually "
            "thin (from %s)",
            schema.scenario_id, len(schema.steps), filepath,
        )

    # S-02: detection-less steps.
    emitted_types: set[str] = set()
    for step in schema.steps:
        if not step.expected_detections:
            logger.warning(
                "scenario=%s step=%s has empty expected_detections — this step "
                "contributes no detection coverage (from %s)",
                schema.scenario_id, step.id, filepath,
            )
        for det in step.expected_detections:
            emitted_types.add(det.type)

    # S-09: declared detection_types vs. union of step-emitted kinds.
    declared = set(schema.detection_types)
    if emitted_types and declared != emitted_types:
        missing_from_declared = sorted(emitted_types - declared)
        declared_but_unemitted = sorted(declared - emitted_types)
        logger.warning(
            "scenario=%s detection_types drift: declared=%s but steps emit=%s "
            "(emitted-not-declared=%s declared-not-emitted=%s) (from %s)",
            schema.scenario_id, sorted(declared), sorted(emitted_types),
            missing_from_declared, declared_but_unemitted, filepath,
        )


def _schema_to_orm_kwargs(schema: ScenarioSchema) -> dict[str, Any]:
    """Convert a validated ScenarioSchema into keyword args for the Scenario ORM model."""
    return {
        "scenario_id": schema.scenario_id,
        "name": schema.name,
        "version": schema.version,
        "status": schema.status,
        "plane": schema.plane,
        "detection_types": schema.detection_types,
        "uc_ref": schema.uc_ref,
        "tc_ref": schema.tc_ref,
        "uc_name": schema.uc_name,
        "tc_name": schema.tc_name,
        "mitre_tactic": schema.mitre_tactic,
        "mitre_tactic_name": schema.mitre_tactic_name,
        "mitre_technique": schema.mitre_technique,
        "mitre_technique_name": schema.mitre_technique_name,
        "additional_techniques": [t.model_dump() for t in schema.additional_techniques],
        "threat_report": schema.threat_report,
        "threat_report_url": schema.threat_report_url,
        "execution_identity": schema.execution_identity.model_dump(),
        "push_supported": schema.push_supported,
        "pull_supported": schema.pull_supported,
        "external_tools": [t.model_dump() for t in schema.external_tools],
        "steps": [s.model_dump() for s in schema.steps],
        "cleanup": schema.cleanup.model_dump() if schema.cleanup else None,
        "tags": schema.tags,
        "author": schema.author,
        "cgo_anchor": schema.cgo_anchor.model_dump() if schema.cgo_anchor else None,
        "created_at": datetime.utcnow(),
    }
