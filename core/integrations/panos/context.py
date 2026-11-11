# core/integrations/panos/context.py
"""Shared state for one NGFW preflight run, and the two config-lookup helpers.

Lives apart from ``preflight.py`` so ``sources.py`` (per-agent checks) can use the
same context, xpath builder and object lookup without importing the runner that
calls it.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .client import PanosClient, allowed_xpath
from .exceptions import PanosShapeError
from .xmlparse import SystemInfo


@dataclass
class PreflightContext:
    client: PanosClient
    config: dict          # validated (defaults filled) panos_ngfw config
    report: Any           # FirewallPreflightReport (typed loosely: no import cycle)
    now: Optional[datetime]
    system: Optional[SystemInfo] = None


def xp_device(ctx: PreflightContext, tail: str) -> str:
    """Device-level running-config xpath, e.g. ``deviceconfig/setting/logging``."""
    return f"/config/devices/entry[@name='{ctx.config['device_name']}']/{tail.lstrip('/')}"


def xp(ctx: PreflightContext, tail: str) -> str:
    """vsys-scoped running-config xpath, e.g. ``log-settings/profiles``."""
    return xp_device(ctx, f"vsys/entry[@name='{ctx.config['vsys']}']/{tail.lstrip('/')}")


#: kind -> the tail under a scope root. Profiles share one shape per type.
_PROFILE_KINDS = ("virus", "spyware", "vulnerability", "url-filtering", "wildfire-analysis")


def _candidates(ctx: PreflightContext, kind: str, name: str) -> list[str]:
    vsys = ctx.config["vsys"]
    pvsys = f"/config/panorama/vsys/entry[@name='{vsys}']"
    ent = f"entry[@name='{name}']"
    if kind == "security-rule":
        # No shared rulebase on a firewall: local, then Panorama pre, then post.
        return [xp(ctx, f"rulebase/security/rules/{ent}"),
                f"{pvsys}/pre-rulebase/security/rules/{ent}",
                f"{pvsys}/post-rulebase/security/rules/{ent}"]
    if kind == "profile-group":
        tail = f"profile-group/{ent}"
    elif kind in _PROFILE_KINDS:
        tail = f"profiles/{kind}/{ent}"
    else:
        raise ValueError(f"unsupported object kind {kind!r}")
    return [xp(ctx, tail), f"/config/shared/{tail}", f"{pvsys}/{tail}",
            f"/config/panorama/shared/{tail}"]


async def find_object(ctx: PreflightContext, kind: str, name: str) -> Optional[ET.Element]:
    """The ``<result>`` holding the named object's ``<entry>``, looked up in order
    vsys -> /config/shared -> Panorama-pushed. ``None`` when it is in none of them
    (each scope answered an empty result). Predefined names are the caller's job.

    A non-empty reply without an ``<entry>`` raises ``PanosShapeError``: an
    unrecognised shape is never read as "not found"."""
    for xpath in _candidates(ctx, kind, name):
        if not allowed_xpath(xpath):
            raise PanosShapeError(
                f"{kind} name {name!r} cannot be expressed as an allowlisted xpath")
        result = await ctx.client.config_show(xpath)
        if result.find("entry") is not None:
            return result
        if len(result) == 0 and not (result.text or "").strip():
            continue
        raise PanosShapeError(f"config {xpath}: expected <entry> or an empty result")
    return None
