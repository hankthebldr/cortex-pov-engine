"""Deterministic closure-mechanism decision procedure for v2.2 test cases.

The mechanism is a property of the test case, computed from its index row
(`validation_class` + `target_dataset`), not guessed at authoring time. See
docs/superpowers/specs/2026-09-04-uctc-full-coverage-design.md sections 3-4.
"""
from __future__ import annotations

# Middle-dot-joined tokens in `target_dataset` -> a coarse signal family.
FAMILY_BY_DATASET: dict[str, str] = {
    "xdr_data": "endpoint",
    "incidents": "incidents",
    "network_story": "network",
    "pan_dns": "network",
    "panw_ngfw_traffic_raw": "network",
    "panw_ngfw_threat_raw": "network",
    "cloud_audit_logs": "cloud_audit",
    "container_events": "cloud_audit",
    "cloud_inventory": "posture",
    "posture_findings": "posture",
    "asm_assets": "asm",
    "asm_issues": "asm",
    "okta_sso": "identity_saas",
    "saas_okta_raw": "identity_saas",
    "msft_azure_ad_signin": "identity_saas",
    "msft_o365": "email",
    "msft_o365_audit": "email",
    "proofpoint_tap_raw": "email",
}

# DET/HNT family -> mechanism (+ controllability suffix on M2).
_DETHNT_MECHANISM_BY_FAMILY: dict[str, str] = {
    "endpoint": "M1",
    "network": "M2-quick",
    "incidents": "M2-quick",
    "cloud_audit": "M2-longterm",
    "identity_saas": "M2-longterm",
    "email": "M2-longterm",
    "posture": "M2-longterm",
    "asm": "M2-longterm",
    "other": "M2-longterm",
}

_CATEGORY_BY_FAMILY: dict[str, str] = {
    "endpoint": "none",
    "network": "none",
    "incidents": "none",
    "cloud_audit": "cloud",
    "posture": "cloud",
    "identity_saas": "identity",
    "email": "email",
    "asm": "external-surface",
    "other": "unknown",
}


def dataset_family(target_dataset: str) -> str:
    """Return the family of the first recognized dataset token, else 'other'."""
    if not target_dataset:
        return "other"
    for tok in (t.strip() for t in target_dataset.split("·")):
        fam = FAMILY_BY_DATASET.get(tok)
        if fam:
            return fam
    return "other"


def mechanism_for(validation_class: str, target_dataset: str) -> str:
    """Assign exactly one closure mechanism. Raise on an unknown class."""
    vc = (validation_class or "").strip().upper()
    if vc == "POS":
        return "M3"
    if vc == "PLT":
        return "M4"
    if vc == "AUT":
        return "M5"
    if vc in ("DET", "HNT"):
        return _DETHNT_MECHANISM_BY_FAMILY[dataset_family(target_dataset)]
    raise ValueError(f"unknown validation_class: {validation_class!r}")


def platform_category(validation_class: str, target_dataset: str) -> str:
    """Coarse external-platform category (refined per-TC by the runbook)."""
    vc = (validation_class or "").strip().upper()
    fam = dataset_family(target_dataset)
    if vc == "POS":
        return _CATEGORY_BY_FAMILY.get(fam, "cloud")
    return _CATEGORY_BY_FAMILY[fam]
