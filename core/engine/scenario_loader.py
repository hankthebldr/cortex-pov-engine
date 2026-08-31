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

# The K8s delivery contract. Pure model + vocabulary, no heavy deps, so a
# module-level import is safe here (the loader already owns the schema).
from engine.k8s_manifest import AppIdentitySpec, ClusterPosture, findings_for

logger = logging.getLogger("cortexsim.loader")

# PyYAML's pure-Python SafeLoader parses the 161-file corpus in ~754 ms; the
# libyaml-backed CSafeLoader — already present in the prod image — does the
# identical parse in ~84 ms. Boot pays this on every container start, so prefer
# the C loader and fall back only where libyaml was not compiled in. Both
# implement the same YAML 1.1 safe subset, so the parse result is unchanged.
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


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
    # ── Runtime-dependency contract (optional, additive, back-compat) ───────
    # Logical interpreters (e.g. "python") this step's COMMAND depends on but
    # does not itself install — the gap SIM-EDR-001 step-05 exposed: it
    # downloads mimipenguin.sh, which shells out to `python` internally, and
    # the step's own `|| echo '... complete'` fallback silently absorbed the
    # failure when python was absent, reporting exit_code=0. Declaring this
    # does not change what the step runs; it lets the beacon check for the
    # dependency BEFORE running the command and refuse to run it (rather than
    # let a masked failure report success) when it is genuinely absent — see
    # agent/beacon/client.go::resolveRuntimeDeps and
    # docs/design/agent-runtime-dependencies.md.
    requires_interpreters: list[str] = Field(default_factory=list)

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
    #: Stage this adapter's artifact under a DIFFERENT on-disk name/path.
    #:
    #: The rename negative control. Two BIOCs in this corpus key on the tool's
    #: FILENAME (`command_line contains "linpeas"`), which proves a customer's
    #: stack can match a string, not that it understands privilege-escalation
    #: reconnaissance. Staging the same bytes as `/tmp/.cache/sysinfo.sh` splits
    #: those apart: the behavioural detections must still fire, and if nothing
    #: fires the finding is that the coverage is name-keyed — not that the TTP
    #: did not run.
    #:
    #: Authored here rather than only as an API override so the control is
    #: committed, reviewable and re-runnable. Validated by S-19 against the same
    #: staging allowlist the composer enforces, because a destination is a WRITE
    #: on a host the DC does not own and the beacon frequently runs as root.
    stage_as: Optional[str] = None

    @field_validator("stage_as")
    @classmethod
    def _validate_stage_as(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        from tools.adapter_loader import _validate_stage_path  # noqa: PLC0415

        return _validate_stage_path(v, code="S-19", field="external_tools[].stage_as")


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
    # ── UC/TC payload join (v2.2 index) ────────────────────────────────────
    # The master index binds one POV-SC *payload* to many test cases, so a
    # scenario evidences a SET of TCs rather than exactly one. ``tc_ref`` stays
    # the primary binding (required, back-compatible); ``tc_refs`` carries the
    # full evidence set and defaults to ``[tc_ref]`` when omitted.
    # ``pov_scenario_id`` names the index payload this scenario instantiates.
    tc_refs: list[str] = Field(default_factory=list)
    pov_scenario_id: Optional[str] = None
    # ── License gating (Phase 3) ────────────────────────────────────────────
    # What a tenant must own to run this scenario. Left empty in YAML and
    # DERIVED at load time from tc_refs -> use case -> product/add-on row; an
    # authored copy of index-owned data is the drift this work removes. A
    # scenario may still override by declaring them explicitly.
    required_base_platform: list[str] = Field(default_factory=list)
    required_addons: list[str] = Field(default_factory=list)
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
    # ── K8s delivery contract (optional, back-compat) ───────────────────────
    # Declares the cluster posture the generated manifest must PLANT. The
    # workload is simultaneously the KSPM finding a posture engine should
    # discover, the execution vehicle the runtime TTP needs to be non-vacuous,
    # and the CGO anchor of a genuinely connected process spine. Absent ->
    # generate_k8s emits the baseline (ungated, self-contained) manifest.
    # Model + finding vocabulary: core/engine/k8s_manifest.py.
    # Contract: docs/reference/k8s-delivery.md.
    cluster_posture: Optional[ClusterPosture] = None
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
    # Mirror the columns in the master use-case index. Persisted to the
    # Scenario ORM since Phase 2 and consumed by engine/verifier.py to score a
    # run against its threshold. See docs/uc_tc_mapping/v2.0-methodology-master.md.
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

    @model_validator(mode="after")
    def default_tc_refs_to_primary(self) -> "ScenarioSchema":
        """``tc_refs`` defaults to ``[tc_ref]`` and always contains it.

        Keeps the two fields from drifting: a caller that reads only
        ``tc_refs`` sees the primary binding, and a scenario that never
        declares the list behaves exactly as it did before the payload join.
        """
        refs = list(dict.fromkeys(self.tc_refs))  # de-dupe, preserve order
        if self.tc_ref and self.tc_ref not in refs:
            refs.insert(0, self.tc_ref)
        object.__setattr__(self, "tc_refs", refs)
        return self

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
            # Data Loss Prevention & Data Security
            "DLP",
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

    @model_validator(mode="after")
    def _validate_cluster_posture(self) -> "ScenarioSchema":
        """K8s delivery contract cross-checks.

        ``S-17`` ``cluster_posture.app_identity.name`` and
                 ``cgo_anchor.image_name`` are both set and DIFFER — ERROR.
        ``S-18`` ``cluster_posture`` is declared but no step lists ``k8s`` or
                 ``container`` in ``platforms`` — WARNING.

        S-17 is STRUCTURAL and is not gated by ``CORTEXSIM_STRICT_REFS``: it is
        about the internal consistency of one file, not about the index
        snapshot. ``cgo_anchor`` is the causality-graph LABEL and
        ``app_identity.name`` is the RUNTIME FACT (the process the manifest
        actually renames PID 1 to). If they disagree the Causality View shows a
        process the cluster never ran — confidently wrong in front of a
        customer. So drift is made structurally impossible: when only one is
        set, the other is BACK-FILLED from it; when both are set and differ, the
        scenario is rejected.
        """
        cp = self.cluster_posture
        if cp is None:
            return self

        anchor = self.cgo_anchor.image_name if self.cgo_anchor else None
        app = cp.app_identity.name if cp.app_identity else None

        if anchor and app and anchor != app:
            raise ValueError(
                f"S-17 cgo_anchor.image_name={anchor!r} disagrees with "
                f"cluster_posture.app_identity.name={app!r}. These name the SAME "
                f"process — the graph label and the runtime fact — and a mismatch "
                f"guarantees a Causality View showing a process the cluster never ran."
            )
        if anchor and not app:
            object.__setattr__(
                cp, "app_identity", AppIdentitySpec(name=anchor),
            )
        elif app and not anchor:
            object.__setattr__(
                self, "cgo_anchor",
                CgoAnchorSchema(image_name=app, primary_username="root"),
            )

        in_cluster = any(
            {"k8s", "container"} & set(s.platforms or []) for s in self.steps
        )
        if not in_cluster:
            logger.warning(
                "S-18 scenario=%s declares cluster_posture but no step lists "
                "'k8s' or 'container' in platforms — the posture is emitted but "
                "nothing is marked as running there",
                self.scenario_id,
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
            raw: Any = yaml.load(fh, Loader=_YAML_LOADER)
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

    # One SELECT for the whole upsert instead of one per file. The loader is a
    # boot-path stage, so 161 round-trips is 161 chances to be slow on a
    # jumpbox's disk; the corpus comfortably fits in memory.
    existing_by_id: dict[str, Scenario] = {
        s.scenario_id: s for s in (await db.execute(select(Scenario))).scalars().all()
    }

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

        # Upsert against the prefetched map. Newly-added scenarios are folded
        # back in so a duplicate scenario_id across two files still collapses
        # onto one row (the old per-file SELECT saw pending adds via autoflush).
        existing: Optional[Scenario] = existing_by_id.get(schema.scenario_id)

        scenario_dict = _schema_to_orm_kwargs(schema)

        if existing is None:
            scenario = Scenario(**scenario_dict)
            db.add(scenario)
            existing_by_id[schema.scenario_id] = scenario
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


def _derive_entitlements(schema: "ScenarioSchema") -> dict[str, list[str]]:
    """Resolve what a tenant must license to run this scenario.

    Derived from the whole ``tc_refs`` evidence set (a scenario is only runnable
    if the tenant can license everything it claims to prove, so the requirement
    is the union). An explicit declaration in the YAML wins — that escape hatch
    exists for scenarios whose index binding under-describes their real
    dependencies — and an empty derivation is left empty rather than guessed.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    base, addons = registry.entitlements_for_many(schema.tc_refs or [schema.tc_ref])
    return {
        "required_base_platform": schema.required_base_platform or base,
        "required_addons": schema.required_addons or addons,
    }


def validate_index_refs(
    *,
    uc_ref: str,
    tc_ref: str,
    tc_refs: list[str],
    artifact_id: str,
    source: str,
    codes: tuple[str, str, str, str] = ("S-10", "S-11", "S-12", "S-15"),
) -> tuple[Any, list[str]]:
    """Validate an artifact's ``(uc_ref, tc_ref, tc_refs)`` as a foreign key
    into the master UC/TC index.

    This is the ONE implementation of the index FK path. The scenario loader
    calls it with the ``S-1x`` code family; the assertion loader calls it with
    ``A-1x``. Anything binding to the index goes through here — a second,
    weaker ref path is how the join silently rots and the coverage number
    inflates without anyone noticing.

    Returns ``(test_case_or_None, errors)`` where ``errors`` are the hard FK
    violations. The CALLER decides what to do with them (both loaders gate on
    ``CORTEXSIM_STRICT_REFS``); a ``None`` test case with no errors means the
    index snapshot is absent and validation degraded to advisory.
    """
    from engine.uctc_registry import registry  # noqa: PLC0415

    dangling_tc, dangling_uc, fk_mismatch, dangling_set = codes
    verdict = registry.validate_ref(uc_ref, tc_ref)

    if verdict.is_error:
        code = {
            "dangling_tc": dangling_tc,
            "dangling_uc": dangling_uc,
            "fk_mismatch": fk_mismatch,
        }[verdict.status]
        return None, [
            f"{code} {artifact_id} uc_ref={uc_ref} tc_ref={tc_ref}: "
            f"{verdict.detail} (from {source})"
        ]

    tc = verdict.test_case
    if tc is None:  # unverified — no snapshot loaded
        return None, []

    # The rest of the evidence set. An artifact claims to prove every TC in
    # tc_refs, so a dangling entry inflates the coverage number silently.
    dangling = [ref for ref in tc_refs if ref != tc_ref and registry.tc(ref) is None]
    if dangling:
        return tc, [
            f"{dangling_set} {artifact_id} tc_refs entries not in the "
            f"v{registry.version} index: {', '.join(dangling)} (from {source})"
        ]
    return tc, []


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
    ``S-15`` a non-primary ``tc_refs[]`` entry is not in the index — ERROR
    ``S-16`` ``pov_scenario_id`` names no payload in the index    — WARNING

    Returns a rejection reason when ``CORTEXSIM_STRICT_REFS`` is on and an
    S-10/S-11/S-12 fired; otherwise ``None`` (the scenario still loads and the
    condition is logged). The registry degrades to ``unverified`` when the
    snapshot is absent, so a stripped deployment never rejects on this path.
    """
    from config import settings  # noqa: PLC0415
    from engine.uctc_registry import registry  # noqa: PLC0415

    strict = bool(getattr(settings, "CORTEXSIM_STRICT_REFS", False))

    # S-10/S-11/S-12/S-15 come from the shared FK path so scenarios and
    # assertions cannot drift into two different notions of a valid ref.
    tc, ref_errors = validate_index_refs(
        uc_ref=schema.uc_ref,
        tc_ref=schema.tc_ref,
        tc_refs=schema.tc_refs,
        artifact_id=f"scenario={schema.scenario_id}",
        source=filepath,
    )
    if ref_errors:
        if strict:
            return ref_errors[0]
        for detail in ref_errors:
            logger.warning("%s [advisory — CORTEXSIM_STRICT_REFS is off]", detail)

    if tc is None:  # unverified — no snapshot loaded, or a hard FK error above
        return None

    # S-16: the payload this scenario instantiates. Advisory in both modes —
    # a scenario may legitimately be a net-new payload the index has not
    # absorbed yet, and that gap is content for the v2.3 proposal, not a
    # boot failure.
    if schema.pov_scenario_id and registry.payload(schema.pov_scenario_id) is None:
        logger.warning(
            "S-16 scenario=%s declares pov_scenario_id=%s which names no "
            "payload in the v%s index (from %s)",
            schema.scenario_id, schema.pov_scenario_id, registry.version, filepath,
        )

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
    kwargs = {
        "scenario_id": schema.scenario_id,
        "name": schema.name,
        "version": schema.version,
        "status": schema.status,
        "plane": schema.plane,
        "detection_types": schema.detection_types,
        "uc_ref": schema.uc_ref,
        "tc_ref": schema.tc_ref,
        "tc_refs": schema.tc_refs,
        "pov_scenario_id": schema.pov_scenario_id,
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
        # Measurement contract — validated since the v2.0 pass, persisted since
        # Phase 2. Without these a run can report observed/MTTD but cannot say
        # whether the test case PASSED.
        "validation_methodology": schema.validation_methodology,
        "methodology_family": schema.methodology_family,
        "primary_kpi": schema.primary_kpi,
        "threshold": schema.threshold.model_dump() if schema.threshold else None,
        "success_criteria": schema.success_criteria,
        "moat_tier": schema.moat_tier,
        "correlation_window_seconds": schema.correlation_window_seconds,
        "stitching_key": schema.stitching_key,
        "required_planes_in_incident": schema.required_planes_in_incident,
        **_derive_entitlements(schema),
        "created_at": datetime.utcnow(),
    }
    # The `cluster_posture` ORM column is owned by a separate track
    # (core/models.py + `ALTER TABLE scenarios ADD COLUMN cluster_posture JSON`).
    # Guard on the attribute so this loader lands independently and starts
    # persisting the moment the column exists — no coordinated deploy, and no
    # TypeError on a Scenario model that has not caught up yet.
    from models import Scenario  # noqa: PLC0415  (import cycle: models -> engine)

    if schema.cluster_posture is not None and hasattr(Scenario, "cluster_posture"):
        kwargs["cluster_posture"] = schema.cluster_posture.model_dump(mode="json")
    return kwargs
