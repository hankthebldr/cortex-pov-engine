"""
CortexSim ORM models (SQLAlchemy 2.0 mapped_column style).

Tables:
  Scenario        — loaded from YAML, never user-created
  Run             — execution record per launch
  Result          — detection outcome per run
  Assertion       — POS/PLT/AUT proof artifact, loaded from YAML
  AssertionRun    — execution record per assertion evaluation
  AssertionCheck  — per-check outcome within an assertion run
  ToolInstance    — managed external-tool lifecycle state
  Agent           — pull-model beacon agents
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import mapped_column, Mapped, relationship

from database import Base


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # active | draft | deprecated
    plane: Mapped[str] = mapped_column(String, nullable=False)   # EDR | CDR | NDR | ITDR | CLOUD_APP | ANALYTICS

    detection_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    uc_ref: Mapped[str] = mapped_column(String, nullable=False)
    tc_ref: Mapped[str] = mapped_column(String, nullable=False)
    uc_name: Mapped[str] = mapped_column(String, nullable=False)
    tc_name: Mapped[str] = mapped_column(String, nullable=False)

    # UC/TC payload join (v2.2 index). The master index binds one POV-SC
    # *payload* to many test cases — a single scenario evidences a SET of TCs,
    # not one. ``tc_ref`` stays the primary binding for back-compat; ``tc_refs``
    # carries the full evidence set and ``pov_scenario_id`` names the index
    # payload this scenario is an instance of.
    # NOTE: prod needs the idempotent ADD COLUMN in database.py to have run.
    pov_scenario_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tc_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    mitre_tactic: Mapped[str] = mapped_column(String, nullable=False)
    mitre_tactic_name: Mapped[str] = mapped_column(String, nullable=False)
    mitre_technique: Mapped[str] = mapped_column(String, nullable=False)
    mitre_technique_name: Mapped[str] = mapped_column(String, nullable=False)

    # GAP-5 — secondary MITRE techniques exercised beyond the primary one.
    # Stored as a list of {technique, name} dicts (name may be "") so the
    # coverage heatmap (/api/mitre/coverage) can fuse them in. Default [].
    additional_techniques: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    threat_report: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    threat_report_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    execution_identity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    push_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pull_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    external_tools: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    cleanup: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # Causality contract — the scenario-level Causality Group Owner anchor
    # ({image_name, primary_username}) that labels the CGO root node in the
    # causality graph. Nullable JSON; dev DB is create_all + disposable so no
    # migration. NOTE: prod needs `ALTER TABLE scenarios ADD COLUMN cgo_anchor JSON`.
    cgo_anchor: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ── Stitch Context (Phase 2 — composer cross-surface stitching) ─────────
    # The AUTHORED intent: a flat JSON object keyed by entity key, each value
    # {literal:<v>} or {resolve:<directive>} (validated by
    # engine.stitch_context.StitchContextSchema). Additive/optional — NULL for
    # every corpus scenario and every context-less draft. Nullable JSON, same
    # cgo_anchor pattern; see _migrate_scenarios_columns in database.py. The
    # per-run RESOLVED values live on Run.stitch_binding, deliberately distinct.
    stitch_context: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ── Measurement contract (v2.0 KPI block) ──────────────────────────────
    # The scenario loader validated these for several releases and then dropped
    # them, so a run could report observed/not-observed and MTTD but never
    # answer "did this test case PASS its threshold". Persisted now so
    # verifier.py can score a run and the POV report can state a verdict.
    # All nullable; see _migrate_scenarios_columns in database.py.
    validation_methodology: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    methodology_family: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # F1..F10
    primary_kpi: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    threshold: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)   # {kpi, op, value, unit}
    success_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    moat_tier: Mapped[Optional[str]] = mapped_column(String, nullable=True)            # MOAT | LEAD | PARITY
    correlation_window_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stitching_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    required_planes_in_incident: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # ── License gating (Phase 3) ───────────────────────────────────────────
    # What a tenant must own to run this scenario. DERIVED at load time from
    # tc_refs -> use case -> product/add-on row, never hand-authored: an
    # authored copy of index-owned data is the drift this work exists to remove.
    # Values are capability names that key into sku_catalog.csv.
    required_base_platform: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    required_addons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    author: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    runs: Mapped[list["Run"]] = relationship("Run", back_populates="scenario_rel", foreign_keys="[Run.scenario_id]", primaryjoin="Scenario.scenario_id == Run.scenario_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scenario_id": self.scenario_id,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "plane": self.plane,
            "detection_types": self.detection_types,
            "uc_ref": self.uc_ref,
            "tc_ref": self.tc_ref,
            "tc_refs": self.tc_refs or ([self.tc_ref] if self.tc_ref else []),
            "pov_scenario_id": self.pov_scenario_id,
            "uc_name": self.uc_name,
            "tc_name": self.tc_name,
            "mitre_tactic": self.mitre_tactic,
            "mitre_tactic_name": self.mitre_tactic_name,
            "mitre_technique": self.mitre_technique,
            "mitre_technique_name": self.mitre_technique_name,
            "additional_techniques": self.additional_techniques,
            "threat_report": self.threat_report,
            "threat_report_url": self.threat_report_url,
            "execution_identity": self.execution_identity,
            "push_supported": self.push_supported,
            "pull_supported": self.pull_supported,
            "external_tools": self.external_tools,
            "steps": self.steps,
            "cleanup": self.cleanup,
            "tags": self.tags,
            "author": self.author,
            "cgo_anchor": self.cgo_anchor,
            "stitch_context": self.stitch_context,
            "validation_methodology": self.validation_methodology,
            "methodology_family": self.methodology_family,
            "primary_kpi": self.primary_kpi,
            "threshold": self.threshold,
            "success_criteria": self.success_criteria,
            "moat_tier": self.moat_tier,
            "correlation_window_seconds": self.correlation_window_seconds,
            "stitching_key": self.stitching_key,
            "required_planes_in_incident": self.required_planes_in_incident or [],
            "required_base_platform": self.required_base_platform or [],
            "required_addons": self.required_addons or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String, ForeignKey("scenarios.scenario_id"), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)           # pull | push
    target: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    identity_context: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # pending | running | complete | failed | aborted | staged
    #   staged — terminal state for a push-mode run: the self-contained bundle
    #   was generated and SimCore has no further role (the bundle never phones
    #   home). See orchestrator._handle_push (GAP-API-004 / GAP-PUSH-001).
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Run-level test-case verdict (Phase 2). Distinct from `status`: status is
    # "did the run execute", tc_verdict is "did the test case PASS its
    # threshold". Set by verifier.score_run.
    tc_verdict: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # pass|fail|pending|not_applicable
    tc_verdict_detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ── Stitch Context binding (Phase 2 — the RESOLVED values used) ─────────
    # The per-run home for engine.stitch_context.resolve_stitch_context(...).values
    # — the 9-key dict of the REAL concrete entities (5-tuple / UPN / host /
    # container / cloud resource) deterministically derived from this run id and
    # injected into its {stitch:*} step commands. Distinct from
    # Scenario.stitch_context (authored intent): this is what actually executed,
    # so the report / Run lens can quote the exact values. NULL for runs of
    # scenarios/drafts without a stitch_context. Set by the orchestrator (next
    # unit); nullable JSON, added by _migrate_scenarios_columns in database.py.
    stitch_binding: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ── Runtime-dependency posture (docs/design/agent-runtime-dependencies.md) ─
    # runtime_install_authorized mirrors CORTEXSIM_XSIAM_ALLOW_WRITE's posture:
    # an explicit, per-run, off-by-default record of whether THIS run was
    # permitted to have the beacon attempt a package-manager install to
    # satisfy a step's declared `requires_interpreters`. Recorded regardless of
    # whether any step actually needed it, so "was this run allowed to mutate
    # the target" is answerable from the run record alone, not reconstructed
    # from log lines.
    runtime_install_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # runtime_dependency_gaps is the PREFLIGHT snapshot (advisory — see
    # engine.runtime_preflight) taken at launch time: which steps declared an
    # interpreter the target agent's last-registered roster did not have.
    # None when nothing was declared/checked; [] when checked and clean.
    runtime_dependency_gaps: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)

    # Relationships
    scenario_rel: Mapped["Scenario"] = relationship("Scenario", back_populates="runs", foreign_keys=[scenario_id])
    results: Mapped[list["Result"]] = relationship("Result", back_populates="run_rel")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "mode": self.mode,
            "target": self.target,
            "identity_context": self.identity_context,
            "status": self.status,
            "tc_verdict": self.tc_verdict,
            "tc_verdict_detail": self.tc_verdict_detail,
            "stitch_binding": self.stitch_binding,
            "runtime_install_authorized": self.runtime_install_authorized,
            "runtime_dependency_gaps": self.runtime_dependency_gaps,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "output": self.output,
        }


class Result(Base):
    __tablename__ = "results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.run_id"), nullable=False, index=True)
    step_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)       # e.g. "step-01"
    step_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)      # human-readable step name
    plane: Mapped[str] = mapped_column(String, nullable=False)
    tool_used: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    signal_type: Mapped[str] = mapped_column(String, nullable=False)      # BIOC | IOC | Analytics
    expected_detection: Mapped[str] = mapped_column(String, nullable=False)
    observed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    # MTTD timing fields
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)   # when the TTP step ran
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)   # when DC confirmed detection in XSIAM

    # Phase 1 — TTP detection card linkage. Populated by the orchestrator at
    # seed time when the scenario step references a card in
    # detection_scanner/ttps/. Lets the report renderer embed the
    # deployable XQL / BIOC / correlation logic alongside the expected
    # detection description so the DC leaves the POV with content in hand.
    ttp_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    detection_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    detection_kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # bioc | xql | correlation | ioc
    detection_logic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detection_severity: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mitre_technique: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Verification (Phase 2) ─────────────────────────────────────────────
    # `observed` answers "did we see it". These answer "did it MEET the bar".
    # kpi_verdict is deliberately four-valued: `not_applicable` is what an
    # unscoreable test case gets (57 of the index's DET/HNT rows carry no
    # measurable threshold), because a silent `pass` on one of those produces a
    # green POV readout that means nothing.
    verification_xql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kpi_contribution: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    kpi_verdict: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # pass|fail|pending|not_applicable
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    run_rel: Mapped["Run"] = relationship("Run", back_populates="results")

    @property
    def mttd_seconds(self) -> Optional[float]:
        """Mean Time To Detect — seconds between execution and observation."""
        if self.executed_at and self.observed_at:
            return (self.observed_at - self.executed_at).total_seconds()
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "step_name": self.step_name,
            "plane": self.plane,
            "tool_used": self.tool_used,
            "signal_type": self.signal_type,
            "expected_detection": self.expected_detection,
            "observed": self.observed,
            "notes": self.notes,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "mttd_seconds": self.mttd_seconds,
            "verification_xql": self.verification_xql,
            "kpi_contribution": self.kpi_contribution,
            "kpi_verdict": self.kpi_verdict,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "ttp_ref": self.ttp_ref,
            "detection_id": self.detection_id,
            "detection_kind": self.detection_kind,
            "detection_logic": self.detection_logic,
            "detection_severity": self.detection_severity,
            "mitre_technique": self.mitre_technique,
        }


# ---------------------------------------------------------------------------
# Assertion substrate — POS / PLT / AUT proof artifacts
#
# A Scenario proves a DETECTION fired. An Assertion proves something the
# engine cannot make an attack scenario say: that a planted posture finding was
# discovered (POS), that a platform capability is present and working (PLT), or
# that an automation outcome occurred inside a budget (AUT).
#
# The three tables mirror Scenario / Run / Result one-for-one — same column
# names for the verdict fields (`tc_verdict`, `tc_verdict_detail`,
# `kpi_verdict`, `kpi_contribution`, `verified_at`) — so the EXISTING verifier
# scores an assertion run with no parallel scoring path, and a downstream
# renderer can treat a check and a Result identically.
#
# They are deliberately NOT rows in `scenarios`: an assertion has no MITRE
# technique, no steps and no execution identity, and materializing one into a
# Scenario row would inject a blank technique into the coverage heatmap and
# offer an unrunnable artifact to the launcher.
# ---------------------------------------------------------------------------


class Assertion(Base):
    """One authored assertion artifact, loaded from YAML. Mirrors Scenario."""

    __tablename__ = "assertions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assertion_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False, default="1.0")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    # POS | PLT | AUT — must equal the bound test case's index validation_class.
    validation_class: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # state | outcome. A state assertion is idempotent (probe standing state);
    # an outcome assertion measures a causally-raised condition inside a budget.
    kind: Mapped[str] = mapped_column(String, nullable=False, default="state")
    plane: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Index binding — the SAME validated FK the scenario corpus uses.
    uc_ref: Mapped[str] = mapped_column(String, nullable=False)
    tc_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    tc_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    uc_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tc_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # What the index says about the bound TC, snapshotted at load so a readout
    # can print the authored bar next to the index's own words.
    index_meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # False when the index carries no measurable threshold for the bound TC —
    # the run-level PASS clamp in verifier.score_run reads this.
    tc_scoreable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # MANDATORY, non-empty. What this assertion does NOT prove. Rendered beside
    # every verdict; a partial proof that hides its edges is a false claim.
    scope_limitations: Mapped[str] = mapped_column(Text, nullable=False)

    primary_kpi: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    threshold: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    methodology_family: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    moat_tier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    success_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Derived from tc_refs at load, exactly as Scenario does it.
    required_base_platform: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    required_addons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    # The full validated artifact (stimulus, checks, negative controls). Kept
    # whole so the runner and the API never re-read the YAML.
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    author: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_file: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "assertion_id": self.assertion_id,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "validation_class": self.validation_class,
            "kind": self.kind,
            "plane": self.plane,
            "uc_ref": self.uc_ref,
            "tc_ref": self.tc_ref,
            "tc_refs": self.tc_refs or ([self.tc_ref] if self.tc_ref else []),
            "uc_name": self.uc_name,
            "tc_name": self.tc_name,
            "index_meta": self.index_meta or {},
            "tc_scoreable": self.tc_scoreable,
            "scope_limitations": self.scope_limitations,
            "primary_kpi": self.primary_kpi,
            "threshold": self.threshold,
            "methodology_family": self.methodology_family,
            "moat_tier": self.moat_tier,
            "success_criteria": self.success_criteria,
            "required_base_platform": self.required_base_platform or [],
            "required_addons": self.required_addons or [],
            "spec": self.spec or {},
            "tags": self.tags or [],
            "author": self.author,
            "source_file": self.source_file,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AssertionRun(Base):
    """One execution of an assertion against (or without) a tenant. Mirrors Run.

    ``tc_verdict`` carries the SAME four-valued vocabulary as ``Run.tc_verdict``
    and is produced by the same ``verifier.score_run``.
    """

    __tablename__ = "assertion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    assertion_id: Mapped[str] = mapped_column(
        String, ForeignKey("assertions.assertion_id"), nullable=False, index=True,
    )
    # pending | running | complete | failed
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    tenant: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The trigger run this assertion was evaluated against (outcome kind).
    trigger_run_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    # Substitutions rendered into the queries (nonce, run_id, author vars).
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    tc_verdict: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tc_verdict_detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # Machine-readable why, for the pending/not_applicable cases. A DC must be
    # able to tell "still owed" from "unscoreable by construction".
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    checks: Mapped[list["AssertionCheck"]] = relationship(
        "AssertionCheck", back_populates="run_rel",
        primaryjoin="AssertionRun.run_id == AssertionCheck.run_id",
        foreign_keys="[AssertionCheck.run_id]",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "assertion_id": self.assertion_id,
            "status": self.status,
            "tenant": self.tenant,
            "trigger_run_id": self.trigger_run_id,
            "context": self.context or {},
            "tc_verdict": self.tc_verdict,
            "tc_verdict_detail": self.tc_verdict_detail,
            "reason": self.reason,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class AssertionCheck(Base):
    """One check within an assertion run. Mirrors Result.

    Column names match ``Result`` where the semantics match (``kpi_verdict``,
    ``kpi_contribution``, ``verification_xql``, ``verified_at``) so
    ``verifier.score_run`` — which reads its inputs entirely through
    ``getattr`` — aggregates these with no change.
    """

    __tablename__ = "assertion_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("assertion_runs.run_id"), nullable=False, index=True,
    )
    check_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    probe: Mapped[str] = mapped_column(String, nullable=False)

    # The RENDERED query — visible in the readout even when no tenant ran it,
    # which is the whole offline-day-one value of the mechanism.
    verification_xql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    measured_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    measured_unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    taxonomy_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remediation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # What this check returns FAIL for. Authored, proven at load, rendered here.
    negative_control: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    kpi_contribution: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    kpi_verdict: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Bounded evidence table (<= 20 rows) so a readout can show the actual data.
    sample_rows: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # Reuses Result's identity convention so downstream renderers can key on it.
    detection_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    run_rel: Mapped["AssertionRun"] = relationship(
        "AssertionRun", back_populates="checks", foreign_keys=[run_id],
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "check_id": self.check_id,
            "title": self.title,
            "probe": self.probe,
            "verification_xql": self.verification_xql,
            "measured_value": self.measured_value,
            "measured_unit": self.measured_unit,
            "taxonomy_code": self.taxonomy_code,
            "remediation": self.remediation,
            "detail": self.detail,
            "negative_control": self.negative_control,
            "kpi_contribution": self.kpi_contribution,
            "kpi_verdict": self.kpi_verdict,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "sample_rows": self.sample_rows or [],
            "detection_id": self.detection_id,
        }


class ToolInstance(Base):
    __tablename__ = "tool_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool_name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    install_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="not_installed")  # not_installed | installed | running | stopped
    port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_health_check: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    installed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "install_path": self.install_path,
            "pid": self.pid,
            "status": self.status,
            "port": self.port,
            "last_health_check": self.last_health_check.isoformat() if self.last_health_check else None,
            "installed_at": self.installed_at.isoformat() if self.installed_at else None,
        }


class QueuedTask(Base):
    """Durable pull-mode task queue (GAP-API-005).

    The orchestrator keeps an in-memory queue for the hot path, but a SimCore
    restart would otherwise drop every undelivered task while the durable Run
    row stays ``running`` forever. Each enqueued Task is mirrored here so the
    queue can be rehydrated on boot; rows are deleted on dequeue/abort/complete.

    The full Task payload (steps + identity context) is stored as JSON so the
    orchestrator can reconstruct an identical ``Task`` dataclass on rehydrate
    without re-reading the scenario.
    """

    __tablename__ = "queued_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    scenario_id: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "scenario_id": self.scenario_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    os: Mapped[str] = mapped_column(String, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # The beacon's own honest snapshot of which logical interpreters
    # (executor.AvailableLogicalNames(), e.g. ["python"]) resolve on ITS host
    # right now, sent at registration. Consumed by
    # engine.runtime_preflight.evaluate_runtime_readiness — advisory only; the
    # beacon re-checks live at execution time regardless (see
    # docs/design/agent-runtime-dependencies.md), so a stale value here can
    # under-report readiness but can never cause a false success.
    interpreters: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    registered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String, nullable=False, default="online")  # online | stale | offline (derived from last_seen at read time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "os": self.os,
            "capabilities": self.capabilities,
            "interpreters": self.interpreters,
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "status": self.status,
        }


class EnrollmentToken(Base):
    """A one-time-ish enrollment credential for agent self-onboarding.

    Replaces the "build the Go binary yourself and invent an --id" flow: a DC
    mints a token in the console, then runs ONE line on the jumpbox
    (``curl <server>/api/agents/install?token=... | bash``). The installer
    redeems the token via ``POST /api/agents/enroll``; SimCore assigns the
    agent_id (so identities are server-controlled and traceable), registers the
    agent, and decrements the token's remaining uses.

    Tokens are bounded by ``expires_at`` and ``max_uses`` and can be revoked.
    The token value is high-entropy and only its tail is ever shown after
    creation (the full value is returned exactly once, at mint time).
    """

    __tablename__ = "enrollment_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    label: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def is_valid(self, now: datetime) -> bool:
        """True if the token can still be redeemed at ``now``."""
        if self.revoked:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        return self.used_count < self.max_uses

    def to_dict(self, *, reveal: bool = False) -> dict[str, Any]:
        # Never echo the full token after mint — only a tail for identification.
        token_display = self.token if reveal else f"...{self.token[-6:]}"
        return {
            "id": self.id,
            "token": token_display,
            "label": self.label,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "max_uses": self.max_uses,
            "used_count": self.used_count,
            "remaining_uses": max(0, self.max_uses - self.used_count),
            "revoked": self.revoked,
        }


# ---------------------------------------------------------------------------
# EAL Traffic Simulator persistence (campaign history + run audit trail)
# ---------------------------------------------------------------------------


class EalCampaign(Base):
    """Persisted declarative campaign — equivalent of a Scenario for the EAL
    simulator subsystem. Stored so the UI can render history without re-reading
    the original YAML."""

    __tablename__ = "eal_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    authorized_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    simulation_authorized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    target_allowlist: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    runs: Mapped[list["EalCampaignRun"]] = relationship(
        "EalCampaignRun", back_populates="campaign_rel",
        primaryjoin="EalCampaign.campaign_id == EalCampaignRun.campaign_id",
        foreign_keys="[EalCampaignRun.campaign_id]",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "campaign_id": self.campaign_id,
            "name": self.name,
            "description": self.description,
            "spec": self.spec,
            "authorized_by": self.authorized_by,
            "simulation_authorized": self.simulation_authorized,
            "target_allowlist": self.target_allowlist,
            "tags": self.tags,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class EalCampaignRun(Base):
    """One execution of an EAL campaign. Step-level results are stored as a
    JSON list to keep the schema flat — granular querying lives in the audit
    log rather than the relational store."""

    __tablename__ = "eal_campaign_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    campaign_id: Mapped[str] = mapped_column(
        String, ForeignKey("eal_campaigns.campaign_id"), nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    operator: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    step_results: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    campaign_rel: Mapped["EalCampaign"] = relationship(
        "EalCampaign", back_populates="runs", foreign_keys=[campaign_id],
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "campaign_id": self.campaign_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "operator": self.operator,
            "step_results": self.step_results,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Credentials layer (Phase 9 foundation)
#
# Hybrid model per the 2026-05-15 design decision:
#   * Secret  — opaque encrypted blob, addressed by (name, type_hint).
#   * IntegrationCredential — typed metadata for an external integration that
#     references a Secret by FK. Future per-integration tables (xsiam_tenant,
#     aws_credential, slack_webhook, ...) follow the same pattern.
#
# All reads/writes go through core/security/credentials.py so encryption and
# decryption stay in one place; ORM rows never touch plaintext.
# ---------------------------------------------------------------------------


class Secret(Base):
    """Encrypted opaque value addressed by name.

    `ciphertext` holds a Fernet token (urlsafe base64). Plaintext never lives
    on disk and is never logged. See core/security/credentials.py.
    """

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    type_hint: Mapped[str] = mapped_column(String, nullable=False, default="generic")

    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)

    preview_tail: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rotation_reminder_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        """Public dict — NEVER includes ciphertext or plaintext."""
        return {
            "id": self.id,
            "name": self.name,
            "type_hint": self.type_hint,
            "preview_tail": self.preview_tail,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            "rotation_reminder_at": self.rotation_reminder_at.isoformat() if self.rotation_reminder_at else None,
        }


class IntegrationCredential(Base):
    """Typed metadata for an external-integration credential.

    Each row is one configured integration (e.g. one XSIAM tenant, one AWS
    account, one Slack workspace). The actual secret value lives in the Secret
    table referenced by `secret_id` so encryption stays in one place.

    `config` holds non-sensitive JSON metadata specific to the integration kind
    (XSIAM tenant URL + region + auth_mode; AWS access key ID + region; etc.).
    Anything sensitive belongs in the referenced Secret.
    """

    __tablename__ = "integration_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)

    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    secret_id: Mapped[int] = mapped_column(Integer, ForeignKey("secrets.id"), nullable=False)

    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_verified_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    last_verified_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    secret_rel: Mapped["Secret"] = relationship("Secret", foreign_keys=[secret_id])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "config": self.config,
            "secret_id": self.secret_id,
            "last_verified_at": self.last_verified_at.isoformat() if self.last_verified_at else None,
            "last_verified_ok": self.last_verified_ok,
            "last_verified_error": self.last_verified_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
