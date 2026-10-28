"""
ngfw_threat_emitter — analytics log-streamer for the PAN-OS NGFW THREAT log
(WildFire verdict) data source -> ``panw_ngfw_threat_raw``.

Built on the ``analytics_emitter`` spine. It POSTs a shape-true WildFire
verdict record for a file the operator names by SHA-256, so a customer can
validate that the tenant's NGFW-threat enrichment and WildFire-verdict
detections fire on a known-bad digest — without a real download or a real
firewall.

Hard rule: a synthetic log may show **detection and enrichment, never
prevention**. ``action`` accepts only ``alert`` and ``allow``; any block-class
value (``block``, ``deny``, ``drop``, ``reset-both``, ``sinkhole`` ...) is
refused at validation, so this plugin can never fabricate evidence that a
firewall stopped something.

The plugin implements only ``build_events``; the shared driver
(``AnalyticsLogEmitter``) owns transport, authorisation, dry-run, budget and
result aggregation. It ships no negative control and declares no
``CANARY_FIELDS``: only the top-level canary field is planted.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.ngfw_threat_emitter")


# The XSIAM dataset PAN-OS NGFW THREAT logs normalise to.
_DATASET = "panw_ngfw_threat_raw"

# Synthetic, non-resolving constants (RFC 5737 / .invalid).
_FIREWALL_SERIAL = "007200001056"
_FIREWALL_HOSTNAME = "PA-CORTEXSIM-CANARY"
_SRC_HOST = "10.0.12.34"
_SRC_USER = "corp\\cortexsim-canary"
_ENDPOINT_DEVICE = "WKS-CORTEXSIM-0007"
_DEFAULT_DST_DOMAIN = "wildfire-canary.cortexsim.invalid"
_DEFAULT_DST_IP = "203.0.113.77"

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
_VERDICTS = ("malicious", "grayware", "benign")
_ALLOWED_ACTIONS = ("alert", "allow")
_EVENT_PATTERNS = ("wildfire_malicious_file", "vulnerability_inbound")
_VULN_DEFAULT_SRC = "198.51.100.23"
_VULN_DEFAULT_THREAT_NAME = "CortexSim synthetic web-application exploit attempt"


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


class NgfwThreatEmitterParams(AnalyticsEmitterParams):
    event_pattern: str = Field(
        default="wildfire_malicious_file",
        description="Alert to exercise: wildfire_malicious_file | "
                    "vulnerability_inbound.",
    )
    file_sha256: Optional[str] = Field(
        default=None,
        description="SHA-256 (64 hex chars) of the file the verdict names. "
                    "Required for wildfire_malicious_file.",
    )
    file_name: Optional[str] = Field(
        default=None, min_length=1,
        description="Observed file name. Required for wildfire_malicious_file.",
    )
    file_type: str = Field(default="PE", description="WildFire file type.")
    verdict: str = Field(
        default="malicious", description="malicious | grayware | benign.",
    )
    app: str = Field(default="web-browsing", description="PAN-OS App-ID.")
    dst_domain: Optional[str] = Field(
        default=None,
        description="Override the download host (dst_domain and url_domain). "
                    "Requires dst_ip.",
    )
    dst_ip: Optional[str] = Field(
        default=None,
        description="Override the destination IP (dst). May be set without "
                    "dst_domain, in which case dst_domain and url_domain are "
                    "blank.",
    )
    threat_id: int = Field(
        default=0, ge=0,
        description="PAN-OS threat id; 0 marks a synthetic signature "
                    "(vulnerability_inbound).",
    )
    uri: str = Field(
        default="/index.php",
        description="Request URI carried in misc (vulnerability_inbound).",
    )
    victim_ip: Optional[str] = Field(
        default=None,
        description="Inbound-session destination (dst) for vulnerability_inbound. "
                    "Dotted quad; default 10.0.12.34.",
    )
    victim_host: Optional[str] = Field(
        default=None,
        description="Override endpoint_device_name for vulnerability_inbound.",
    )
    dst_port: Optional[int] = Field(
        default=None, ge=1, le=65535,
        description="Override dst_port (default 443).",
    )
    src_ip: Optional[str] = Field(
        default=None,
        description="Override the session source IP (src). Dotted quad.",
    )
    src_host: Optional[str] = Field(
        default=None,
        description="Override the source endpoint hostname "
                    "(endpoint_device_name).",
    )
    src_user: Optional[str] = Field(
        default=None, description="Override the source user (user).",
    )
    threat_name: Optional[str] = Field(
        default=None,
        description="Override the threat_name field (default "
                    "'Virus/Win32.WGeneric.canary' for a malicious verdict, "
                    "'wildfire-<verdict>' otherwise). Non-empty when set.",
    )
    action: str = Field(
        default="alert",
        description="alert | allow. Block-class actions are refused: a "
                    "synthetic log may never claim prevention.",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset PAN-OS NGFW THREAT logs normalise to."""
        return _DATASET

    @field_validator("file_sha256")
    @classmethod
    def _sha_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not _SHA256_RE.match(v):
            raise ValueError("file_sha256 must be 64 hexadecimal characters")
        return v.lower()

    @field_validator("verdict")
    @classmethod
    def _verdict_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _VERDICTS:
            raise ValueError(f"verdict must be one of {list(_VERDICTS)}, got '{v}'")
        return v

    @field_validator("action")
    @classmethod
    def _action_never_a_block(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _ALLOWED_ACTIONS:
            raise ValueError("synthetic threat log may not claim a block")
        return v

    @field_validator("dst_domain", "dst_ip", "src_host", "src_user", "threat_name",
                     "victim_host")
    @classmethod
    def _non_empty_when_set(cls, v: Optional[str], info) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError(f"{info.field_name} must be non-empty when set")
        return v

    @field_validator("src_ip", "victim_ip")
    @classmethod
    def _src_ip_quad(cls, v: Optional[str], info) -> Optional[str]:
        if v is None:
            return v
        parts = v.split(".")
        if len(parts) != 4 or not all(
            p.isascii() and p.isdigit() and 0 <= int(p) <= 255 for p in parts
        ):
            raise ValueError(
                f"{info.field_name} must be a dotted-quad IPv4 address, got '{v}'"
            )
        return v

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v

    @model_validator(mode="after")
    def _dst_pair(self) -> "NgfwThreatEmitterParams":
        if self.dst_domain is not None and self.dst_ip is None:
            raise ValueError("dst_domain requires dst_ip")
        return self

    @model_validator(mode="after")
    def _wildfire_needs_file(self) -> "NgfwThreatEmitterParams":
        if self.event_pattern == "wildfire_malicious_file" and (
            self.file_sha256 is None or self.file_name is None
        ):
            raise ValueError(
                "file_sha256 and file_name are required for wildfire_malicious_file"
            )
        return self


class NgfwThreatEmitter(AnalyticsLogEmitter):
    class Meta:
        name = "ngfw_threat_emitter"
        data_sources = ["pan_firewall_threat_logs"]
        datasets = [_DATASET]
        detectors = [
            {
                "alert": "WildFire malicious verdict on downloaded file",
                "undocumented_reason": (
                    "The vendor analytics catalogue lists the PAN-OS threat-log "
                    "source but no WildFire-verdict alert; this emits the record "
                    "a custom BIOC / correlation rule on subtype=wildfire would "
                    "key on, not a documented Analytics detector."
                ),
                "dataset": _DATASET,
                "key_fields": ["filedigest", "verdict", "subtype"],
                "predicate": "subtype=wildfire AND verdict=malicious",
                "mitre": "T1204.002",
            },
            {
                "alert": "Vulnerability threat from a publicly routed source "
                         "against a web server",
                "undocumented_reason": (
                    "The vendor analytics catalogue lists the PAN-OS threat-log "
                    "source but no inbound-vulnerability alert; this emits the "
                    "record a custom BIOC / correlation rule on "
                    "subtype=vulnerability would key on, not a documented "
                    "Analytics detector."
                ),
                "dataset": _DATASET,
                "key_fields": ["subtype", "src", "threat_id"],
                "predicate": "subtype=vulnerability AND src is publicly routed",
                "mitre": "T1190",
            },
        ]
        version = "1.1.0"
        description = (
            "Emits a shape-true PAN-OS NGFW THREAT (WildFire verdict) log "
            "(panw_ngfw_threat_raw) for an operator-named file digest into an "
            "operator-supplied collector so Cortex XSIAM exercises its "
            "WildFire-verdict detections and IOC enrichment. Detection only: "
            "the action is restricted to alert/allow and a block is never "
            "claimed."
        )
        mitre_techniques = ["T1204.002", "T1105", "T1190"]
        eal_targets = [
            "Analytics — WildFire malicious verdict on downloaded file",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "malware"
        params_model = NgfwThreatEmitterParams

    def build_events(
        self, params: NgfwThreatEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        if params.dst_ip is None:
            domain = _DEFAULT_DST_DOMAIN
        else:
            domain = params.dst_domain or ""
        user = params.src_user if params.src_user is not None else _SRC_USER
        if params.event_pattern == "vulnerability_inbound":
            return [{
                "dataset": _DATASET,
                "type": "THREAT",
                "subtype": "vulnerability",
                "time_generated": now,
                "receive_time": now,
                "serial": _FIREWALL_SERIAL,
                "device_name": _FIREWALL_HOSTNAME,
                "vsys": "vsys1",
                "session_id": secrets.randbelow(9_000_000) + 1_000_000,
                "src": params.src_ip or _VULN_DEFAULT_SRC,
                "dst": params.victim_ip or _SRC_HOST,
                "src_port": secrets.randbelow(16383) + 49152,
                "dst_port": params.dst_port or 443,
                "proto": "tcp",
                "app": params.app,
                "rule": "allow-inbound-web",
                "action": params.action,
                "direction": "client-to-server",
                "src_zone": "untrust",
                "dst_zone": "trust",
                "category": "code-execution",
                "threat_id": params.threat_id,
                "threat_name": params.threat_name or _VULN_DEFAULT_THREAT_NAME,
                "misc": params.uri,
                "severity": "critical",
                "user": user,
                "endpoint_device_name": (
                    params.victim_host if params.victim_host is not None
                    else _ENDPOINT_DEVICE
                ),
                "vulnerability_inbound_marker": True,
                "cortexsim_run_id": sim_run_id,
            }]
        malicious = params.verdict == "malicious"
        return [{
            "dataset": _DATASET,
            "type": "THREAT",
            "subtype": "wildfire",
            "time_generated": now,
            "receive_time": now,
            "serial": _FIREWALL_SERIAL,
            "device_name": _FIREWALL_HOSTNAME,
            "vsys": "vsys1",
            "session_id": secrets.randbelow(9_000_000) + 1_000_000,
            "src": params.src_ip or _SRC_HOST,
            "dst": params.dst_ip or _DEFAULT_DST_IP,
            "src_port": secrets.randbelow(16383) + 49152,
            "dst_port": params.dst_port or 443,
            "proto": "tcp",
            "app": params.app,
            "rule": "allow-outbound",
            "action": params.action,
            "src_zone": "trust",
            "dst_zone": "untrust",
            "filedigest": params.file_sha256,
            "filename": params.file_name,
            "filetype": params.file_type,
            "verdict": params.verdict,
            "category": "wildfire-virus",
            "threat_name": params.threat_name if params.threat_name is not None else (
                "Virus/Win32.WGeneric.canary" if malicious else "wildfire-" + params.verdict
            ),
            "dst_domain": domain,
            "url_domain": domain,
            "severity": "critical" if malicious else "medium",
            "user": user,
            "endpoint_device_name": params.src_host if params.src_host is not None else _ENDPOINT_DEVICE,
            "wildfire_verdict_marker": True,
            "cortexsim_run_id": sim_run_id,
        }]
