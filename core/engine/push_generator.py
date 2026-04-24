"""
CortexSim Push Script Generator — Section 4.4 spec.

Generates self-contained execution bundles from scenario YAML dicts:
  generate_bash(scenario)  → bash script string
  generate_k8s(scenario)   → Kubernetes YAML manifest string
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("cortexsim.push_generator")


# ---------------------------------------------------------------------------
# Bash generator
# ---------------------------------------------------------------------------

_BASH_HEADER_TMPL = """\
#!/usr/bin/env bash
# =============================================================================
# CortexSim Detection Simulation Bundle
# =============================================================================
# Scenario ID   : {scenario_id}
# Name          : {name}
# Version       : {version}
# Plane         : {plane}
#
# UC Reference  : {uc_ref} — {uc_name}
# TC Reference  : {tc_ref} — {tc_name}
#
# MITRE Tactic  : {mitre_tactic} — {mitre_tactic_name}
# MITRE Technique: {mitre_technique} — {mitre_technique_name}
#
# Expected Detections:
{expected_detections_comment}#
# Threat Report : {threat_report}
# Author        : {author}
#
# IMPORTANT: Self-contained — no SimCore dependency at runtime.
#            Requires Ubuntu 22.04+ or equivalent Linux distribution.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/cortexsim-{scenario_id}-${{TIMESTAMP}}.log"
exec > >(tee -a "${{LOG_FILE}}") 2>&1

log() {{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$1] $2"
}}

log INFO "CortexSim bundle starting — scenario={scenario_id}"
log INFO "Log file: ${{LOG_FILE}}"
"""

_IDENTITY_HARNESS = """\
# ---------------------------------------------------------------------------
# Identity Harness — every TTP step runs through this function for consistent
# process causality chains and logging, regardless of identity.
# ---------------------------------------------------------------------------

run_as() {
    local identity="$1"
    local cmd="$2"
    local step_id="$3"

    log INFO "STEP $step_id identity=$identity cmd=$cmd"

    case "$identity" in
        root|container-runtime|direct)
            # Direct execution — still logged through harness
            bash -c "$cmd"
            ;;
        www-data|postgres|mysql|node|python3|nobody|svc-backup)
            # Prefer runuser (cleanest causality), fall back to sudo -u, then su
            if command -v runuser &>/dev/null; then
                runuser -l "$identity" -c "$cmd" 2>/dev/null || \
                    sudo -u "$identity" bash -c "$cmd" 2>/dev/null || \
                    su -s /bin/bash "$identity" -c "$cmd"
            elif command -v sudo &>/dev/null; then
                sudo -u "$identity" bash -c "$cmd"
            else
                su -s /bin/bash "$identity" -c "$cmd"
            fi
            ;;
        *)
            # Unknown identity — attempt direct with a warning
            log WARN "Unknown identity '$identity' — executing directly"
            bash -c "$cmd"
            ;;
    esac

    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        log WARN "Step $step_id exited with code $exit_code (non-zero — may be expected)"
    else
        log INFO "Step $step_id completed successfully"
    fi
    return $exit_code
}
"""

_DEP_CHECK_TMPL = """\
# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
check_dep() {
    local dep="$1"
    if ! command -v "$dep" &>/dev/null; then
        log WARN "Dependency not found: $dep — some steps may fail"
    else
        log INFO "Dependency OK: $dep"
    fi
}

"""

_CLEANUP_TMPL = """\
# ---------------------------------------------------------------------------
# Cleanup / Teardown
# ---------------------------------------------------------------------------
cleanup() {{
    log INFO "Running cleanup for scenario={scenario_id}"
{cleanup_commands}
    log INFO "Cleanup complete"
}}

# Register cleanup to run on EXIT so it fires even on error
trap cleanup EXIT
"""

_FOOTER = """\

log INFO "CortexSim bundle complete — scenario={scenario_id}"
log INFO "Review detections in Cortex XDR/XSIAM console."
"""


def generate_bash(scenario: dict[str, Any]) -> str:
    """
    Generate a self-contained bash execution bundle from a scenario dict.

    The bundle:
    1. Header comment block with scenario metadata and expected detections
    2. set -euo pipefail + logging to /tmp/cortexsim-{id}-{timestamp}.log
    3. Identity harness functions (run_as wrapper)
    4. Dependency checks for all required external_tools
    5. Ordered TTP steps — each wrapped via run_as identity harness
    6. Cleanup/teardown section
    """
    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    name = scenario.get("name", "")
    version = scenario.get("version", "1.0")
    plane = scenario.get("plane", "")
    uc_ref = scenario.get("uc_ref", "")
    uc_name = scenario.get("uc_name", "")
    tc_ref = scenario.get("tc_ref", "")
    tc_name = scenario.get("tc_name", "")
    mitre_tactic = scenario.get("mitre_tactic", "")
    mitre_tactic_name = scenario.get("mitre_tactic_name", "")
    mitre_technique = scenario.get("mitre_technique", "")
    mitre_technique_name = scenario.get("mitre_technique_name", "")
    threat_report = scenario.get("threat_report") or "N/A"
    author = scenario.get("author") or "CortexSim"
    steps = scenario.get("steps") or []
    external_tools = scenario.get("external_tools") or []
    cleanup_data = scenario.get("cleanup") or {}

    # Build expected detections comment block
    expected_lines: list[str] = []
    for step in steps:
        for det in step.get("expected_detections", []):
            expected_lines.append(
                f"#   [{det.get('plane','?')}] {det.get('type','?')}: {det.get('description','')}"
            )
    expected_detections_comment = "\n".join(expected_lines) + "\n" if expected_lines else "#   (none specified)\n"

    # --- Header ---
    script = _BASH_HEADER_TMPL.format(
        scenario_id=scenario_id,
        name=name,
        version=version,
        plane=plane,
        uc_ref=uc_ref,
        uc_name=uc_name,
        tc_ref=tc_ref,
        tc_name=tc_name,
        mitre_tactic=mitre_tactic,
        mitre_tactic_name=mitre_tactic_name,
        mitre_technique=mitre_technique,
        mitre_technique_name=mitre_technique_name,
        expected_detections_comment=expected_detections_comment,
        threat_report=threat_report,
        author=author,
    )

    # --- Identity harness ---
    script += _IDENTITY_HARNESS

    # --- Dependency checks ---
    script += _DEP_CHECK_TMPL
    deps_seen: set[str] = {"curl", "bash"}  # always check basics
    for tool in external_tools:
        tool_name = tool.get("name", "")
        if tool_name and tool.get("type") == "binary" and not tool.get("install_inline", False):
            deps_seen.add(tool_name)
    for dep in sorted(deps_seen):
        script += f'check_dep "{dep}"\n'
    script += "\n"

    # --- Inline tool downloads (install_inline tools) ---
    inline_tools = [t for t in external_tools if t.get("install_inline") and t.get("source")]
    if inline_tools:
        script += "# ---------------------------------------------------------------------------\n"
        script += "# Inline tool downloads\n"
        script += "# ---------------------------------------------------------------------------\n"
        for tool in inline_tools:
            tname = tool.get("name", "tool")
            tsource = tool.get("source", "")
            script += f'log INFO "Downloading inline tool: {tname}"\n'
            script += f'curl -sSLo "/tmp/{tname}" "{tsource}" || log WARN "Failed to download {tname}"\n'
        script += "\n"

    # --- Cleanup registration ---
    cleanup_cmds = cleanup_data.get("commands", [])
    cleanup_lines = "\n".join(f'    {cmd}' for cmd in cleanup_cmds) if cleanup_cmds else "    true  # no cleanup commands defined"
    script += _CLEANUP_TMPL.format(
        scenario_id=scenario_id,
        cleanup_commands=cleanup_lines,
    )
    script += "\n"

    # --- TTP execution steps ---
    script += "# ---------------------------------------------------------------------------\n"
    script += "# TTP Execution Steps\n"
    script += "# ---------------------------------------------------------------------------\n\n"

    for step in steps:
        step_id = step.get("id", "step-??")
        step_name = step.get("name", "")
        identity = step.get("identity", "direct")
        command = step.get("command", "true")
        mitre_tech = step.get("mitre_technique", "")
        expected = step.get("expected_detections", [])

        script += f"# --- {step_id}: {step_name}\n"
        if mitre_tech:
            script += f"# MITRE: {mitre_tech}\n"
        for det in expected:
            script += f"# Expected: [{det.get('plane','?')}] {det.get('type','?')}: {det.get('description','')}\n"
        # Escape the command for embedding in the run_as call
        escaped_cmd = command.replace("'", "'\\''")
        script += f"run_as '{identity}' '{escaped_cmd}' '{step_id}'\n\n"

    # --- Footer ---
    script += _FOOTER.format(scenario_id=scenario_id)

    logger.info("Generated bash bundle for scenario=%s (%d steps)", scenario_id, len(steps))
    return script


# ---------------------------------------------------------------------------
# K8s YAML generator
# ---------------------------------------------------------------------------

_K8S_HEADER = """\
# =============================================================================
# CortexSim Kubernetes Simulation Bundle
# Generated by CortexSim push_generator
# =============================================================================
# Scenario   : {scenario_id} — {name}
# Plane      : {plane}
# UC/TC      : {uc_ref} / {tc_ref}
# MITRE      : {mitre_technique} — {mitre_technique_name}
#
# Apply with: kubectl apply -f <this-file>
# Simulation begins automatically on pod scheduling.
# =============================================================================
---
apiVersion: v1
kind: Namespace
metadata:
  name: cortexsim-{scenario_id_lower}
  labels:
    cortexsim/scenario: "{scenario_id}"
    cortexsim/plane: "{plane_lower}"
---
"""

_K8S_JOB_TMPL = """\
apiVersion: batch/v1
kind: Job
metadata:
  name: {step_id_safe}-{scenario_id_lower}
  namespace: cortexsim-{scenario_id_lower}
  labels:
    cortexsim/scenario: "{scenario_id}"
    cortexsim/step: "{step_id}"
    cortexsim/mitre-technique: "{mitre_technique}"
spec:
  ttlSecondsAfterFinished: 300
  template:
    metadata:
      labels:
        cortexsim/scenario: "{scenario_id}"
        cortexsim/step: "{step_id}"
    spec:
      restartPolicy: Never
      containers:
        - name: {step_id_safe}
          image: ubuntu:22.04
          command: ["/bin/bash", "-c"]
          args:
            - |
              set -euo pipefail
              echo "[$(date)] CortexSim step={step_id} identity={identity}"
              # Identity harness inline
              if [ "{identity}" = "root" ] || [ "{identity}" = "container-runtime" ] || [ "{identity}" = "direct" ]; then
                bash -c '{command_escaped}'
              elif command -v runuser &>/dev/null; then
                runuser -l "{identity}" -c '{command_escaped}' 2>/dev/null || bash -c '{command_escaped}'
              elif command -v sudo &>/dev/null; then
                sudo -u "{identity}" bash -c '{command_escaped}' || bash -c '{command_escaped}'
              else
                su -s /bin/bash "{identity}" -c '{command_escaped}' || bash -c '{command_escaped}'
              fi
          resources:
            limits:
              cpu: "500m"
              memory: "256Mi"
            requests:
              cpu: "100m"
              memory: "64Mi"
---
"""


def generate_k8s(scenario: dict[str, Any]) -> str:
    """
    Generate a Kubernetes YAML manifest from a scenario dict.

    Each scenario step becomes one Kubernetes Job.  All jobs share a dedicated
    Namespace named cortexsim-{scenario_id_lower}.  The manifest is self-contained
    and begins execution immediately on kubectl apply.

    Extends the cdr_base.yml pattern from sources/CDR/cdr.yml.
    """
    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    name = scenario.get("name", "")
    plane = scenario.get("plane", "")
    uc_ref = scenario.get("uc_ref", "")
    tc_ref = scenario.get("tc_ref", "")
    mitre_technique = scenario.get("mitre_technique", "")
    mitre_technique_name = scenario.get("mitre_technique_name", "")
    steps = scenario.get("steps") or []

    scenario_id_lower = scenario_id.lower().replace("_", "-")
    plane_lower = plane.lower().replace("_", "-")

    manifest = _K8S_HEADER.format(
        scenario_id=scenario_id,
        scenario_id_lower=scenario_id_lower,
        name=name,
        plane=plane,
        plane_lower=plane_lower,
        uc_ref=uc_ref,
        tc_ref=tc_ref,
        mitre_technique=mitre_technique,
        mitre_technique_name=mitre_technique_name,
    )

    for step in steps:
        step_id = step.get("id", "step-01")
        identity = step.get("identity", "direct")
        command = step.get("command", "true")
        mitre_tech = step.get("mitre_technique", mitre_technique)

        step_id_safe = step_id.replace("_", "-").replace(" ", "-").lower()
        command_escaped = command.replace("'", "'\\''")

        manifest += _K8S_JOB_TMPL.format(
            scenario_id=scenario_id,
            scenario_id_lower=scenario_id_lower,
            step_id=step_id,
            step_id_safe=step_id_safe,
            identity=identity,
            command_escaped=command_escaped,
            mitre_technique=mitre_tech,
        )

    logger.info("Generated K8s manifest for scenario=%s (%d steps)", scenario_id, len(steps))
    return manifest
