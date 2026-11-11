"""
Signal path — the mechanism that produces a scenario step's evidence.

The Composer's door badge, Lanes lens and the Runs topology used to place a step
by its PLANE, which names the product that should OBSERVE the signal rather than
how the step produces it. On the 2026-11-10 corpus that put 427 of 796 steps
behind a door they are not launched through. A path is read from what the step
does:

    COLLECTOR  an EAL collector-class plugin POSTs records to a collector
    WIRE       an EAL wire-class plugin generates traffic a sensor sees
    CLOUD      the command calls a cloud or cluster API (aws, gcloud, gsutil,
               az, bq, kubectl); the provider's audit log carries the evidence
    NONE       the command only echoes, prints, sleeps or comments
    ENDPOINT   anything else: the beacon runs it on the host

A step whose path cannot be read is ``unplaced`` with a reason, kept in
ENDPOINT so it stays visible, and never guessed into another lane.

This module is the Python half. The browser derives the same thing in
``ui/src/components/console/composerSignal.js`` so the canvas follows an edited
command; both assert ``spec/signal_path_cases.json``. Here it backs the
loader's S-28 lint and the corpus tripwire, and it is the ONE definition of the
EAL plugin classes (``scenario_loader.COLLECTOR_POST_PLUGINS`` re-exports
``COLLECTOR_PLUGINS``). ``tests/engine/test_signal_path.py`` pins both sets to
the live registry.

Pure: stdlib only, no registry import, so the loader stays cheap to import.

Design: ``docs/superpowers/specs/2026-11-10-composer-doors-design.md``.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "COLLECTOR_PLUGINS",
    "SIGNAL_PATHS",
    "WIRE_PLUGINS",
    "eal_run_problem",
    "plugin_class",
    "signal_of",
]

SIGNAL_PATHS: tuple[str, ...] = ("ENDPOINT", "COLLECTOR", "WIRE", "CLOUD", "NONE")

# EAL plugins that POST records to a collector (a ``collector_url`` param).
COLLECTOR_PLUGINS: frozenset[str] = frozenset({
    "ad_windows_emitter", "azure_audit_emitter", "cloud_audit_emitter",
    "cloud_storage_compute_emitter", "duo_auth_emitter", "email_emitter",
    "globalprotect_emitter", "idp_signin_emulator", "k8s_audit_emitter",
    "m365_activity_emitter", "ngfw_eal_emitter", "ngfw_threat_emitter",
    "third_party_alert_emitter", "third_party_firewall_emitter", "third_party_vpn_emitter",
})

# EAL plugins that generate live traffic.
WIRE_PLUGINS: frozenset[str] = frozenset({
    "agentic_egress", "airs_prompt_attack", "browser_attack_runner", "bulk_https_exfil",
    "c2_http_beacon", "dns_tunnel_exfil", "ftp_egress", "llm_provider_egress",
    "oauth_grant_emulator", "smb_rpc_sweep", "ssh_egress", "stratum_tcp_connect",
})

_UNPLACED_REASON = {
    "no-step": "No step to read.",
    "no-command": "The step's command is not known here, and the signal path is read from it.",
    "no-plugin-named": "The step runs the EAL simulator but names no plugin.",
    "unknown-plugin": "The step names an EAL plugin the registry does not know",
    "mixed-plugins": "The step runs EAL plugins of both classes (collector and wire)",
}

_EAL_RUN = re.compile(r"eal_simulator(?:\.cli)?[ \t]+run\b")
_PLUGIN_LINE = re.compile(r"^[ \t]*-?[ \t]*plugin:[ \t]*[\"']?([A-Za-z0-9_]+)", re.M)
_CLOUD_CLI = re.compile(r"(?<![A-Za-z0-9_./-])(aws|gcloud|gsutil|az|bq|kubectl)(?![A-Za-z0-9_.-])")
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_SEPARATOR = re.compile(r"&&|\|\||;")
_QUIET = frozenset({"echo", "printf", "sleep", "true", ":", "date"})
_WS = " \t\r\n"


def plugin_class(name: Any) -> str | None:
    """``collector`` | ``wire`` for a registered plugin, else None."""
    if name in COLLECTOR_PLUGINS:
        return "collector"
    if name in WIRE_PLUGINS:
        return "wire"
    return None


def _is_comment(line: str) -> bool:
    return line.lstrip(" \t").startswith("#")


def _calls_cloud(command: str) -> bool:
    return any(
        not _is_comment(line) and _CLOUD_CLI.search(_QUOTED.sub("Q", line))
        for line in command.split("\n")
    )


def _does_nothing(command: str) -> bool:
    """True when every line only echoes, prints, sleeps or is a comment. A
    redirect, a pipe or a command substitution (which runs even inside double
    quotes) is an action. Certain or ENDPOINT, never a guessed NONE."""
    for line in command.split("\n"):
        if _is_comment(line):
            continue
        if "$(" in line or "`" in line:
            return False
        for segment in _SEPARATOR.split(_QUOTED.sub("Q", line)):
            t = segment.strip(_WS)
            if not t or t.startswith("#"):
                continue
            if t.split()[0] not in _QUIET:
                return False
            if re.search(r"[<>|]", t):
                return False
    return True


def _plugin_path(plugins: list[str], runs_on: str) -> dict[str, Any]:
    if not plugins:
        return {"path": "ENDPOINT", "runs_on": runs_on, "plugins": plugins, "unplaced": "no-plugin-named"}
    unknown = [p for p in plugins if plugin_class(p) is None]
    if unknown:
        return {"path": "ENDPOINT", "runs_on": runs_on, "plugins": plugins,
                "unplaced": "unknown-plugin", "unknown": unknown}
    classes = {plugin_class(p) for p in plugins}
    if len(classes) > 1:
        return {"path": "ENDPOINT", "runs_on": runs_on, "plugins": plugins, "unplaced": "mixed-plugins"}
    path = "COLLECTOR" if "collector" in classes else "WIRE"
    return {"path": path, "runs_on": runs_on, "plugins": plugins, "unplaced": None}


def _with_reason(sig: dict[str, Any]) -> dict[str, Any]:
    unknown = sig.pop("unknown", None)
    reason = _UNPLACED_REASON.get(sig["unplaced"]) if sig["unplaced"] else None
    if sig["unplaced"] == "unknown-plugin":
        reason = f"{reason}: {', '.join(unknown or [])}."
    elif sig["unplaced"] == "mixed-plugins":
        reason = f"{reason}: {', '.join(sig['plugins'])}."
    sig["reason"] = reason
    return sig


def signal_of(step: Any) -> dict[str, Any]:
    """The signal path of one step (a scenario-YAML or API step dict).

    Returns ``{path, runs_on, plugins, unplaced, reason}``; ``runs_on`` is
    ``AGT`` (the beacon) or ``SIM`` (SimCore's own process, ``channel: eal``).
    """
    if not isinstance(step, dict):
        return _with_reason({"path": "ENDPOINT", "runs_on": "AGT", "plugins": [], "unplaced": "no-step"})
    if step.get("channel") == "eal":
        eal = step.get("eal") if isinstance(step.get("eal"), dict) else {}
        plugin = eal.get("plugin").strip(_WS) if isinstance(eal.get("plugin"), str) else ""
        return _with_reason(_plugin_path([plugin] if plugin else [], "SIM"))
    command = step.get("command")
    if not isinstance(command, str):
        return _with_reason({"path": "ENDPOINT", "runs_on": "AGT", "plugins": [], "unplaced": "no-command"})
    if _EAL_RUN.search(command):
        names = sorted(set(_PLUGIN_LINE.findall(command)))
        return _with_reason(_plugin_path(names, "AGT"))
    if _calls_cloud(command):
        path = "CLOUD"
    elif _does_nothing(command):
        path = "NONE"
    else:
        path = "ENDPOINT"
    return _with_reason({"path": path, "runs_on": "AGT", "plugins": [], "unplaced": None})


def eal_run_problem(command: Any) -> str | None:
    """S-28: why a command that runs the EAL CLI cannot be placed, or None.

    Only a command-level campaign run is judged; an eal-channel step's plugin is
    checked by the step's own channel validator.
    """
    if not isinstance(command, str) or not _EAL_RUN.search(command):
        return None
    names = sorted(set(_PLUGIN_LINE.findall(command)))
    sig = _plugin_path(names, "AGT")
    if sig["unplaced"] == "no-plugin-named":
        return "S-28: the command runs the EAL simulator but names no plugin (no `plugin:` line)"
    if sig["unplaced"] == "unknown-plugin":
        return f"S-28: the command names EAL plugin(s) the registry does not know: {', '.join(sig['unknown'])}"
    if sig["unplaced"] == "mixed-plugins":
        return (f"S-28: the command runs EAL plugins of both classes (collector and wire): "
                f"{', '.join(names)}; split them into two steps")
    return None
