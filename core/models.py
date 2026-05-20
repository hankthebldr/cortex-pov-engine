"""
CortexSim ORM models (SQLAlchemy 2.0 mapped_column style).

Tables:
  Scenario     — loaded from YAML, never user-created
  Run          — execution record per launch
  Result       — detection outcome per run
  ToolInstance — managed external-tool lifecycle state
  Agent        — pull-model beacon agents
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
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

    mitre_tactic: Mapped[str] = mapped_column(String, nullable=False)
    mitre_tactic_name: Mapped[str] = mapped_column(String, nullable=False)
    mitre_technique: Mapped[str] = mapped_column(String, nullable=False)
    mitre_technique_name: Mapped[str] = mapped_column(String, nullable=False)

    threat_report: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    threat_report_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    execution_identity: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    push_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pull_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    external_tools: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    steps: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    cleanup: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

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
            "uc_name": self.uc_name,
            "tc_name": self.tc_name,
            "mitre_tactic": self.mitre_tactic,
            "mitre_tactic_name": self.mitre_tactic_name,
            "mitre_technique": self.mitre_technique,
            "mitre_technique_name": self.mitre_technique_name,
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
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending | running | complete | failed
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
            "ttp_ref": self.ttp_ref,
            "detection_id": self.detection_id,
            "detection_kind": self.detection_kind,
            "detection_logic": self.detection_logic,
            "detection_severity": self.detection_severity,
            "mitre_technique": self.mitre_technique,
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


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    os: Mapped[str] = mapped_column(String, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    registered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String, nullable=False, default="online")  # online | offline

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "os": self.os,
            "capabilities": self.capabilities,
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "status": self.status,
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
