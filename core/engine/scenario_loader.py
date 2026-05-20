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
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("cortexsim.loader")


# ---------------------------------------------------------------------------
# Pydantic validation schema (mirrors the YAML _schema.yml structure)
# ---------------------------------------------------------------------------


class ExecutionIdentitySchema(BaseModel):
    default: str
    options: list[str]


class StepExpectedDetection(BaseModel):
    plane: str
    type: str
    description: str
    # Phase 1 — optional bridge to detection_scanner/ttps/*.json.
    # When both fields resolve in the TTP catalog at startup, the orchestrator
    # copies the card's BIOC / XQL / correlation logic onto the Result row so
    # the POV report can render the deployable query inline.
    ttp_ref: Optional[str] = None       # e.g. "TTP-2026-0002"
    detection_id: Optional[str] = None  # e.g. "bioc-lsass-handle-open-with-sensitive-access-rights"


class StepSchema(BaseModel):
    id: str
    name: str
    command: str
    identity: str
    mitre_technique: str
    expected_detections: list[StepExpectedDetection] = []


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

    # Infra generator hints (optional, backward compatible)
    required_content: list[dict] = Field(default_factory=list,
        description="Open-source tool repos this scenario needs installed")
    infra_modules_needed: list[str] = Field(default_factory=list,
        description="IaC generator module names for auto-suggest (e.g. ['base', 'edr'])")

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
            "BROWSER",     # Prisma Browser — DLP / extension / phishing
            "KOI",         # Agentic endpoint / supply-chain (MCPs, skills, exts)
        }
        if v not in allowed:
            raise ValueError(f"plane must be one of {allowed}, got '{v}'")
        return v


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
        "created_at": datetime.utcnow(),
    }
