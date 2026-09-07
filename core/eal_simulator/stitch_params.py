"""stitch_params — map a resolved Stitch Context binding onto an EAL plugin's
own parameter fields (Phase 3b).

WHY THIS IS NARROW, AND HONEST ABOUT IT
---------------------------------------
The eight EAL *analytics* emitters (``ngfw_eal_emitter``, ``cloud_audit_emitter``,
``azure_audit_emitter``, ``k8s_audit_emitter``, ``m365_activity_emitter``,
``ad_windows_emitter``, ``idp_signin_emulator``, ``cloud_storage_compute_emitter``)
all subclass :class:`AnalyticsEmitterParams` and BUILD their record fields
internally — none of them accepts a ``src_ip`` / ``dst_ip`` / port / protocol
parameter. Their one shared-entity injection point is ``canary_token``, which
each emitter plants (via its ``CANARY_FIELDS``) into the record's user fields.
Network-egress plugins (``c2_http_beacon`` etc.) POST from SimCore's OWN process,
so their source is not the lab endpoint either.

So the ONLY cross-channel entity an in-process EAL step can honestly share with
the agent's endpoint signal is the **identity principal** (the same account
string in both), planted here as ``canary_token``. A shared network 5-tuple is
NOT achievable for in-process EAL and is deliberately not injected — that leg of
a stitch comes from an *agent*-channel step doing real endpoint network activity
(see docs/superpowers/specs/2026-09-04-composer-workflow-design.md §8.2).

The function skips any entity a plugin has no field for; it never invents a
parameter a plugin does not declare.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from eal_simulator.analytics_emitter import AnalyticsEmitterParams

# The canary_token validator (analytics_emitter._canary_token_safe) accepts the
# intersection of a k8s name, an email local part, a sAMAccountName and an XQL
# literal: lowercase alphanumerics and hyphens. Only an account of that shape can
# be planted as a token; anything else is skipped rather than coerced.
_TOKEN_SHAPE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _binding_get(binding: Any, key: str) -> Optional[Any]:
    """Read a key from a StitchBinding (dataclass with attrs + ``.get``) or a
    plain dict, without assuming which shape the caller passed."""
    if binding is None:
        return None
    if hasattr(binding, "get"):
        try:
            return binding.get(key)
        except Exception:  # pragma: no cover - defensive
            pass
    return getattr(binding, key, None)


def _params_model(plugin_cls: Any) -> Optional[type]:
    meta = getattr(plugin_cls, "Meta", None)
    return getattr(meta, "params_model", None)


def _is_analytics_emitter(plugin_cls: Any) -> bool:
    pm = _params_model(plugin_cls)
    return isinstance(pm, type) and issubclass(pm, AnalyticsEmitterParams)


def stitch_binding_to_eal_params(binding: Any, plugin_cls: Any) -> dict[str, Any]:
    """Return the params to MERGE onto an EAL step for a given plugin, derived
    from the resolved stitch binding.

    Empty ``{}`` when there is nothing honest to inject (no binding, a non-token
    account, or a plugin with no field for any resolved entity). The caller
    merges this OVER the step's authored ``eal.params`` — an explicit authored
    value is not clobbered because the adapter only sets keys it can justify.
    """
    if binding is None or plugin_cls is None:
        return {}

    out: dict[str, Any] = {}

    # Identity principal -> canary_token, for the analytics-emitter family only.
    account = _binding_get(binding, "account")
    if (
        account
        and _is_analytics_emitter(plugin_cls)
        and _TOKEN_SHAPE.match(str(account))
    ):
        out["canary_token"] = str(account)

    return out
