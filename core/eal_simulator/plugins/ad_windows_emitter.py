"""
ad_windows_emitter — analytics log-streamer for the Active Directory /
Windows Security data source (Windows Security Event Log ->
``msft_windows_security``).

Built on the ``analytics_emitter`` spine. It POSTs shape-true Windows
Security Event Log records (the 4xxx audit events an on-DC / WEF collector
forwards) to an operator-supplied collector so a customer can validate that
their Cortex XSIAM **Analytics / ABIOC** Active-Directory detections fire —
without touching a real domain controller, forging a real Kerberos ticket, or
running any real credential-access tradecraft. Every record is a JSON blob
shaped *like* a ``WinEventLog:Security`` event and carries a
``cortexsim_run_id`` marker so SOC analysts can filter the simulator traffic.

The plugin implements only ``build_events`` — the shared driver
(``AnalyticsLogEmitter``) owns the transport, the ``ctx.authorise``-before-emit
gate, the dry-run short-circuit, budget charging, batching/gzip and the
``SimulationResult`` aggregation.

Event-shape presets (parameter ``event_pattern``) — each maps to a real XSIAM
Active-Directory analytics alert:

  ============================== ======================================================
  preset                         analytics / ABIOC alert it exercises
  ============================== ======================================================
  kerberoast_weak_ticket         Analytics — "A user received multiple weakly
                                  encrypted service tickets" (4769 TGS-REQ burst with
                                  RC4/0x17 ticket encryption) — T1558.003
  service_ticket_volume          Analytics — "A user requested multiple service
                                  tickets" (4769 burst across many distinct SPNs) —
                                  T1558.003
  pass_the_hash                  Analytics — "Possible Pass-the-Hash" (4624 network
                                  logon, NTLM / NtLmSsp, seclogo) — T1550.002
  ldap_enumeration               Analytics — "A user executed multiple LDAP
                                  enumeration queries" (4662 directory-object reads,
                                  SharpHound/BloodHound shape) — T1087.002 / T1069.002
  dcsync                         ABIOC — "Possible DCSync from a non domain
                                  controller" (4662 with the DS-Replication-Get-Changes
                                  control-access GUIDs from a workstation) — T1003.006
  dc_login_anomaly               ABIOC — "Abnormal User Login to Domain Controller"
                                  (4624 RemoteInteractive logon to a DC by an unusual
                                  user) — T1078.002
  ============================== ======================================================

All identifiers are synthetic: the reserved ``.invalid`` TLD and the RFC 5737
documentation IP ranges guarantee nothing here resolves on a real network or
maps to a real domain.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field, field_validator

from ..analytics_emitter import AnalyticsEmitterParams, AnalyticsLogEmitter


logger = logging.getLogger("cortexsim.eal.plugins.ad_windows_emitter")


# The XSIAM dataset the Windows Security event channel normalises to.
_DATASET = "msft_windows_security"

# Synthetic, non-resolving directory. The reserved .invalid TLD and the RFC
# 5737 documentation IP ranges guarantee nothing here touches a real domain.
_DOMAIN_FQDN = "cortexsim-canary.invalid"
_DOMAIN_NETBIOS = "CORTEXSIM-CANARY"
_DC_HOST = "DC01.cortexsim-canary.invalid"
_DC_IP = "203.0.113.10"
_WORKSTATION_HOST = "WKSTN-07.cortexsim-canary.invalid"
_WORKSTATION_IP = "203.0.113.55"
_ATTACKER_USER = "cortexsim-canary"

# The security-auditing provider WinEventLog:Security events carry.
_PROVIDER_NAME = "Microsoft-Windows-Security-Auditing"
_PROVIDER_GUID = "{54849625-5478-4994-A5BA-3E3B0328C30D}"

# Roastable service accounts (weak-crypto SPNs) the Kerberoast presets target.
_ROASTABLE_SPNS = (
    ("svc-sql", "MSSQLSvc/sql01.cortexsim-canary.invalid:1433"),
    ("svc-web", "HTTP/web01.cortexsim-canary.invalid"),
    ("svc-backup", "CIFS/backup01.cortexsim-canary.invalid"),
    ("svc-fileshare", "HOST/files01.cortexsim-canary.invalid"),
    ("svc-report", "MSSQLSvc/rpt01.cortexsim-canary.invalid:1433"),
    ("svc-app", "HTTP/app01.cortexsim-canary.invalid"),
)

# Control-access-right GUIDs that make a 4662 read a DCSync (directory
# replication) rather than an ordinary object read.
_DCSYNC_PROPERTY_GUIDS = (
    "{1131f6aa-9c07-11d1-f79f-00c04fc2dcd2}",  # DS-Replication-Get-Changes
    "{1131f6ad-9c07-11d1-f79f-00c04fc2dcd2}",  # DS-Replication-Get-Changes-All
)


# --------------------------------------------------------------------------
# Windows Security Event Log record builder
# --------------------------------------------------------------------------


def _win_security_event(
    *,
    event_id: int,
    marker: str,
    sim_run_id: str,
    computer: str,
    event_data: dict[str, Any],
    keywords: str = "Audit Success",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Shape a ``WinEventLog:Security`` record (subset of the Windows Security
    Event Log schema that XSIAM normalises to ``msft_windows_security``)."""
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "dataset": _DATASET,
        "Channel": "Security",
        "Provider": {"Name": _PROVIDER_NAME, "Guid": _PROVIDER_GUID},
        "EventID": event_id,
        "Version": 0,
        "Level": 0,
        "Task": event_id,
        "Opcode": 0,
        "Keywords": keywords,
        "TimeCreated": now,
        "EventRecordID": secrets.randbelow(9_000_000) + 1_000_000,
        "Computer": computer,
        # EventData is the parsed key/value block XSIAM lifts fields from.
        "EventData": event_data,
        marker: True,
        "cortexsim_run_id": sim_run_id,
    }
    if extra:
        body.update(extra)
    return body


# --------------------------------------------------------------------------
# Per-pattern builders
# --------------------------------------------------------------------------


def _kerberos_tgs_event(
    *,
    marker: str,
    sim_run_id: str,
    account: str,
    service_name: str,
    service_spn: str,
    encryption_type: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """4769 — A Kerberos service ticket (TGS) was requested. ``encryption_type``
    ``0x17`` is RC4-HMAC (weak); ``0x12`` is AES256 (strong)."""
    return _win_security_event(
        event_id=4769,
        marker=marker,
        sim_run_id=sim_run_id,
        computer=_DC_HOST,
        event_data={
            "TargetUserName": f"{account}@{_DOMAIN_FQDN}",
            "TargetDomainName": _DOMAIN_FQDN,
            "ServiceName": service_name,
            "ServiceSid": f"S-1-5-21-1111111111-2222222222-3333333333-{secrets.randbelow(4000) + 1104}",
            "TicketOptions": "0x40810000",
            "TicketEncryptionType": encryption_type,
            "Status": "0x0",
            "IpAddress": f"::ffff:{_WORKSTATION_IP}",
            "IpPort": str(secrets.randbelow(20000) + 40000),
            "LogonGuid": "{00000000-0000-0000-0000-000000000000}",
            "TransmittedServices": "-",
        },
        extra={"service_principal_name": service_spn, **(extra or {})},
    )


def _network_logon_event(
    *,
    marker: str,
    sim_run_id: str,
    target_user: str,
    logon_type: int,
    auth_package: str,
    computer: str,
    source_ip: str,
    logon_process: str = "NtLmSsp ",
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """4624 — An account was successfully logged on."""
    return _win_security_event(
        event_id=4624,
        marker=marker,
        sim_run_id=sim_run_id,
        computer=computer,
        event_data={
            "SubjectUserSid": "S-1-0-0",
            "SubjectUserName": "-",
            "SubjectDomainName": "-",
            "TargetUserSid": f"S-1-5-21-1111111111-2222222222-3333333333-{secrets.randbelow(4000) + 1104}",
            "TargetUserName": target_user,
            "TargetDomainName": _DOMAIN_NETBIOS,
            "LogonType": logon_type,
            "LogonProcessName": logon_process,
            "AuthenticationPackageName": auth_package,
            "WorkstationName": _WORKSTATION_HOST.split(".")[0],
            "LogonGuid": "{00000000-0000-0000-0000-000000000000}",
            "IpAddress": source_ip,
            "IpPort": str(secrets.randbelow(20000) + 40000),
            "ImpersonationLevel": "%%1833",
            "ElevatedToken": "%%1842",
        },
        extra=extra,
    )


def _dir_service_access_event(
    *,
    marker: str,
    sim_run_id: str,
    account: str,
    properties: str,
    object_name: str,
    access_mask: str,
    computer: str,
    source_ip: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """4662 — An operation was performed on an Active Directory object."""
    return _win_security_event(
        event_id=4662,
        marker=marker,
        sim_run_id=sim_run_id,
        computer=computer,
        event_data={
            "SubjectUserSid": f"S-1-5-21-1111111111-2222222222-3333333333-{secrets.randbelow(4000) + 1104}",
            "SubjectUserName": account,
            "SubjectDomainName": _DOMAIN_NETBIOS,
            "ObjectServer": "DS",
            "ObjectType": "%{19195a5b-6da0-11d0-afd3-00c04fd930c9}",
            "ObjectName": object_name,
            "OperationType": "Object Access",
            "AccessMask": access_mask,
            "Properties": properties,
            "IpAddress": source_ip,
        },
        extra=extra,
    )


# --------------------------------------------------------------------------
# Event patterns — each maps to one XSIAM AD analytics alert
# --------------------------------------------------------------------------


_PATTERN_MARKER = {
    "kerberoast_weak_ticket": "weak_service_ticket_marker",
    "service_ticket_volume": "service_ticket_volume_marker",
    "pass_the_hash": "pass_the_hash_marker",
    "ldap_enumeration": "ldap_enumeration_marker",
    "dcsync": "dcsync_marker",
    "dc_login_anomaly": "dc_login_anomaly_marker",
}

_EVENT_PATTERNS = tuple(_PATTERN_MARKER)


def _list_event_patterns() -> list[str]:
    return sorted(_EVENT_PATTERNS)


# --------------------------------------------------------------------------
# Params — subclass the shared base, add only event_pattern.
# --------------------------------------------------------------------------


class ADWindowsEmitterParams(AnalyticsEmitterParams):
    event_pattern: str = Field(
        default="kerberoast_weak_ticket",
        description="Active-Directory analytics alert to exercise: "
                    "kerberoast_weak_ticket | service_ticket_volume | "
                    "pass_the_hash | ldap_enumeration | dcsync | dc_login_anomaly.",
    )
    target_account: str = Field(
        default=_ATTACKER_USER,
        description="Synthetic AD account the simulated activity is attributed "
                    "to (SubjectUserName / TargetUserName in the emitted records).",
    )

    @property
    def dataset(self) -> str:
        """The XSIAM dataset the Windows Security channel normalises to."""
        return _DATASET

    @field_validator("event_pattern")
    @classmethod
    def _pattern_known(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _EVENT_PATTERNS:
            raise ValueError(
                f"event_pattern must be one of {sorted(_EVENT_PATTERNS)}, got '{v}'"
            )
        return v


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class ADWindowsEmitter(AnalyticsLogEmitter):
    # TargetUserName carries the canonical canary principal so one human
    # resolves across AD, Entra and Okta. WorkstationName only exists on 4624,
    # and a path that is absent is skipped rather than created.
    CANARY_FIELDS = {
        "EventData.TargetUserName": "{principal}",
        "EventData.WorkstationName": "{value}-{account}",
    }

    class Meta:
        name = "ad_windows_emitter"
        version = "1.0.0"
        description = (
            "Emits shape-true Windows Security Event Log records (4769 / 4624 / "
            "4662, msft_windows_security dataset) into an operator-supplied "
            "collector so Cortex XSIAM exercises its Active-Directory Analytics "
            "/ ABIOC detections (Kerberoasting, Pass-the-Hash, LDAP enumeration, "
            "DCSync, abnormal DC logon) without touching a real domain "
            "controller or running real credential-access tradecraft."
        )
        # Drawn from the AD analytics alerts this plugin fires. None are
        # Command-and-Control (TA0011) techniques, so live campaigns need only
        # simulation_authorized (not c2_authorized) per the SafetyPolicy
        # classifier.
        mitre_techniques = [
            "T1558.003",  # Kerberoasting (weak service tickets / ticket volume)
            "T1550.002",  # Pass the Hash
            "T1087.002",  # Account Discovery: Domain Account (LDAP enum)
            "T1069.002",  # Permission Groups Discovery: Domain Groups (LDAP enum)
            "T1003.006",  # OS Credential Dumping: DCSync
            "T1078.002",  # Valid Accounts: Domain Accounts (abnormal DC logon)
        ]
        eal_targets = [
            "Analytics — user received multiple weakly encrypted service tickets (Kerberoast RC4)",
            "Analytics — user requested multiple service tickets (Kerberoast volume)",
            "Analytics — Possible Pass-the-Hash (NTLM network logon)",
            "Analytics — user executed multiple LDAP enumeration queries (SharpHound/BloodHound)",
            "ABIOC — Possible DCSync from a non domain controller",
            "ABIOC — Abnormal User Login to Domain Controller",
            "NGFW EAL — outbound POST to XSIAM log-collector App-ID match",
        ]
        ecs_category = "authentication"
        params_model = ADWindowsEmitterParams

    def build_events(
        self, params: ADWindowsEmitterParams, *, sim_run_id: str, iteration: int,
    ) -> list[dict[str, Any]]:
        marker = _PATTERN_MARKER[params.event_pattern]
        account = params.target_account
        events: list[dict[str, Any]] = []

        if params.event_pattern in ("kerberoast_weak_ticket", "service_ticket_volume"):
            # A burst of 4769 TGS requests across distinct SPNs. For the
            # weak-ticket alert every ticket is RC4 (0x17); for the volume alert
            # we mix in a strong-crypto ticket so it fires on request COUNT, not
            # on the weak-encryption signal.
            weak_only = params.event_pattern == "kerberoast_weak_ticket"
            for i in range(params.burst_count):
                svc_name, svc_spn = _ROASTABLE_SPNS[i % len(_ROASTABLE_SPNS)]
                enc = "0x17" if (weak_only or i % 3 != 0) else "0x12"
                events.append(_kerberos_tgs_event(
                    marker=marker,
                    sim_run_id=sim_run_id,
                    account=account,
                    service_name=f"{svc_name}",
                    service_spn=svc_spn,
                    encryption_type=enc,
                    extra={"weak_encryption": enc == "0x17"},
                ))
            return events

        if params.event_pattern == "pass_the_hash":
            # NTLM network logon (type 3) with NtLmSsp — the Pass-the-Hash shape:
            # a hash-backed authentication with no preceding Kerberos exchange.
            events.append(_network_logon_event(
                marker=marker,
                sim_run_id=sim_run_id,
                target_user=account,
                logon_type=3,
                auth_package="NTLM",
                computer=_DC_HOST,
                source_ip=_WORKSTATION_IP,
                logon_process="NtLmSsp ",
                extra={"ntlm_version": "NTLM V2", "key_length": 128},
            ))
            return events

        if params.event_pattern == "ldap_enumeration":
            # A burst of 4662 directory-object reads across the domain naming
            # context — the SharpHound / BloodHound collection shape.
            ldap_objects = (
                f"DC={_DOMAIN_FQDN.replace('.', ',DC=')}",
                f"CN=Users,DC={_DOMAIN_FQDN.replace('.', ',DC=')}",
                f"CN=Computers,DC={_DOMAIN_FQDN.replace('.', ',DC=')}",
                f"OU=Domain Controllers,DC={_DOMAIN_FQDN.replace('.', ',DC=')}",
            )
            for i in range(params.burst_count):
                obj = ldap_objects[i % len(ldap_objects)]
                events.append(_dir_service_access_event(
                    marker=marker,
                    sim_run_id=sim_run_id,
                    account=account,
                    properties="{e48d0154-bcf8-11d1-8702-00c04fb96050}",  # Public-Information
                    object_name=obj,
                    access_mask="0x100",  # Control Access / read property
                    computer=_DC_HOST,
                    source_ip=_WORKSTATION_IP,
                    extra={"ldap_search_scope": "subtree"},
                ))
            return events

        if params.event_pattern == "dcsync":
            # 4662 carrying the DS-Replication-Get-Changes control-access GUIDs,
            # sourced from a workstation (a non-DC) — the DCSync signature. The
            # SubjectUserName is a workstation-driven principal, not another DC.
            events.append(_dir_service_access_event(
                marker=marker,
                sim_run_id=sim_run_id,
                account=account,
                properties=" ".join(_DCSYNC_PROPERTY_GUIDS),
                object_name=f"DC={_DOMAIN_FQDN.replace('.', ',DC=')}",
                access_mask="0x100",  # Control Access
                computer=_DC_HOST,
                source_ip=_WORKSTATION_IP,
                extra={
                    "replication_from_non_dc": True,
                    "source_host": _WORKSTATION_HOST,
                },
            ))
            return events

        if params.event_pattern == "dc_login_anomaly":
            # 4624 RemoteInteractive (type 10 / RDP) logon TO the domain
            # controller by a user who does not normally log on there.
            events.append(_network_logon_event(
                marker=marker,
                sim_run_id=sim_run_id,
                target_user=account,
                logon_type=10,
                auth_package="Kerberos",
                computer=_DC_HOST,
                source_ip=_WORKSTATION_IP,
                logon_process="User32 ",
                extra={
                    "logon_to_domain_controller": True,
                    "logon_type_name": "RemoteInteractive",
                },
            ))
            return events

        raise ValueError(  # pragma: no cover — gated by the pydantic validator
            f"unknown event_pattern {params.event_pattern!r}"
        )
