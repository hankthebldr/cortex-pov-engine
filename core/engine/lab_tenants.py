"""Which Cortex tenants are lab tenants.

Collector setup may write synthetic records into a tenant's native firewall
datasets only when the tenant is a lab one. ``CORTEXSIM_DOOR_PROBE_TENANTS`` is
a comma list of tenant API hosts (or URLs); it is read at call time so a test
or an operator edit is seen without a restart of this module.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from config import settings


def host_of(url: str) -> str | None:
    """Lowercased hostname of ``url``, or None when it has none."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def lab_tenant_hosts(raw: str | None = None) -> frozenset[str]:
    """The lab tenant hosts named by ``raw`` (default: the setting, read now)."""
    if raw is None:
        raw = getattr(settings, "CORTEXSIM_DOOR_PROBE_TENANTS", "") or ""
    hosts: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host = host_of(entry) if "://" in entry else entry.lower()
        if host:
            hosts.add(host)
    return frozenset(hosts)


def is_lab_tenant_host(host: str | None, raw: str | None = None) -> bool:
    if not host:
        return False
    return host.strip().lower() in lab_tenant_hosts(raw)
