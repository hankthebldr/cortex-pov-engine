"""
CortexSim Push Script Generator — Section 4.4 spec.

Generates self-contained execution bundles from scenario YAML dicts:
  generate_bash(scenario)        → bash script string       (target=posix)
  generate_powershell(scenario)  → PowerShell script string (target=windows)
  generate_k8s(scenario)         → Kubernetes YAML manifest string

Platform awareness
------------------
A scenario is not a single artifact — it is a set of steps, each of which may
carry a POSIX command, a ``platform_variants['windows']`` command, both, or
neither.  ``resolve_target()`` decides, per target, whether the scenario can
produce a bundle that will actually RUN, and which command text each step
contributes.  ``emittable_targets()`` is the caller-facing summary.

The rule is per-TARGET refusal, never per-scenario refusal: a scenario whose
steps span both platforms still emits a POSIX bundle if every step has a POSIX
command.  Refusing on the ``platforms`` LABEL rather than on the command TEXT
would withdraw working bundles from ~13 scenarios that mislabel POSIX steps as
``[windows]``, for zero fidelity gain — so resolution judges the command, and
the label mismatch is left to the scenario linter.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger("cortexsim.push_generator")

# ---------------------------------------------------------------------------
# Platform resolution — which target(s) can this scenario actually emit?
# ---------------------------------------------------------------------------

Target = Literal["posix", "windows"]
Shell = Literal["bash", "powershell", "cmd"]

#: Stable reason codes.  These are surfaced verbatim in the API's 409 body, so
#: a DC (or a script) can tell "this scenario has no Windows command" apart
#: from "this scenario has no POSIX command" without parsing prose.
REASON_POSIX_UNAVAILABLE = "POSIX_COMMAND_UNAVAILABLE"
REASON_WINDOWS_UNAVAILABLE = "WINDOWS_COMMAND_UNAVAILABLE"

_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_COMMENT_RE = re.compile(r"(?m)(?:(?<=^)|(?<=\s))#[^\n]*")


def _strip_heredocs(text: str) -> str:
    """Blank the BODY of every heredoc, keeping line numbering intact.

    Heredoc bodies are attacker-narrative payloads (python programs, JSON, C
    source).  They are data, not shell, so they must not contribute platform
    markers — a heredoc containing ``C:\\Windows`` does not make the step a
    Windows step.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _HEREDOC_RE.search(line)
        if not m:
            continue
        term = m.group(2)
        while i < len(lines) and lines[i].strip() != term:
            out.append("")
            i += 1
        if i < len(lines):
            out.append("")
            i += 1
    return "\n".join(out)


def _strip_quoted(text: str) -> tuple[str, bool]:
    """Replace single/double-quoted regions with spaces.

    Returns ``(stripped, balanced)``.  ``balanced`` is False when a quote was
    left open at end-of-input, which almost always means an apostrophe inside a
    ``#`` comment desynchronised the scan — the caller retries comment-first.
    """
    out: list[str] = []
    state: str | None = None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if state is None:
            if c == "\\" and i + 1 < n:
                out.append(" ")
                out.append("\n" if text[i + 1] == "\n" else " ")
                i += 2
                continue
            if c in ("'", '"'):
                state = c
                out.append(" ")
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if state == '"' and c == "\\" and i + 1 < n:
            out.append("  ")
            i += 2
            continue
        if c == state:
            state = None
            out.append(" ")
            i += 1
            continue
        out.append("\n" if c == "\n" else " ")
        i += 1
    return "".join(out), state is None


def code_only(command: str) -> str:
    """Return ``command`` with all string literals, heredoc bodies and comments
    blanked out.

    Every platform-marker match runs against this projection.  Without it, the
    13 scenarios that ``echo '…powershell.exe…'`` as a SAFE-MODE narrative
    marker classify as Windows-native — a false positive rate of 13/14 measured
    against this corpus.  Narrative text is not execution.
    """
    if not command:
        return ""
    stripped, balanced = _strip_quoted(_strip_heredocs(command))
    if not balanced:
        # Unterminated quote — retry with comments removed first.
        stripped, _ = _strip_quoted(_COMMENT_RE.sub(" ", _strip_heredocs(command)))
    return _COMMENT_RE.sub(" ", stripped)


_CMDLET_VERBS = (
    "Get|Set|New|Remove|Add|Start|Stop|Invoke|Test|Out|Write|Select|Where|"
    "ForEach|Import|Export|Copy|Move|Enable|Disable|Register|Unregister|"
    "ConvertTo|ConvertFrom|Join|Split|Resolve|Restart"
)
_CMDLET_RE = re.compile(rf"(?<![\w-])(?:{_CMDLET_VERBS})-[A-Z][A-Za-z]+")
_PS_AUTOVAR_RE = re.compile(r"\$env:|\$PSVersionTable|\$ErrorActionPreference", re.IGNORECASE)
_PS_INVOKE_RE = re.compile(r"(?<![\w./-])(?:powershell|pwsh)(?:\.exe)?\s+-", re.IGNORECASE)
_WIN_EXE_RE = re.compile(
    r"(?<![\w./-])(?:cmd|reg|certutil|rundll32|schtasks|wmic|vssadmin|bcdedit|"
    r"wevtutil|nltest|klist|mshta|regsvr32)\.exe(?![\w.])",
    re.IGNORECASE,
)
_DRIVE_PATH_RE = re.compile(r"(?<![\w])[A-Za-z]:\\")
_WIN_ENVVAR_RE = re.compile(
    r"%(?:SystemRoot|TEMP|TMP|APPDATA|ProgramData|ProgramFiles|USERPROFILE|"
    r"COMPUTERNAME|USERNAME|windir|PATH)%",
    re.IGNORECASE,
)
_BATCH_FOR_RE = re.compile(r"(?<![\w-])for\s+/[LFDR]\b", re.IGNORECASE)
_BATCH_TIMEOUT_RE = re.compile(r"(?<![\w-])timeout\s+/t\b", re.IGNORECASE)
_CMD_SLASH_C_RE = re.compile(r"(?<![\w./-])cmd(?:\.exe)?\s+/c\b", re.IGNORECASE)

#: Sufficient evidence that a command is Windows-native.
#:
#: A bare ``powershell`` / ``pwsh`` TOKEN is deliberately NOT here — it is a
#: weak marker.  SIM-EDR-018 step-02 is valid bash that guards an optional
#: `pwsh -NoProfile -Command …` behind `command -v pwsh`; treating the token as
#: strong would withdraw that scenario's working POSIX bundle.  `_PS_INVOKE_RE`
#: requires the `-flag` form that only appears when PowerShell is the shell
#: being driven, and even that is checked after the POSIX escape hatch below.
_STRONG_WINDOWS_MARKERS = (
    _CMDLET_RE,
    _PS_AUTOVAR_RE,
    _WIN_EXE_RE,
    _DRIVE_PATH_RE,
    _WIN_ENVVAR_RE,
    _BATCH_FOR_RE,
    _BATCH_TIMEOUT_RE,
)

#: POSIX constructs that no cmd/PowerShell dialect executes.  Their presence
#: outranks a Windows marker: a bash script that happens to mention `cmd.exe`
#: is still a bash script.
_POSIX_ANCHOR_RE = re.compile(
    r"(?<![\w-])(?:"
    r"mkdir\s+-p|chmod\s+[0-7+ugoa]|printf\s|command\s+-v|/dev/(?:null|zero|urandom|tcp)|"
    r"/tmp/|/bin/(?:sh|bash)|/etc/|/proc/|/var/|"
    r"\|\s*(?:head|tail|grep|awk|sed|tr|wc|xargs|base64|sort|cut)\b|"
    r"2>/dev/null|>/dev/null|&>/dev/null"
    r")"
)


def has_strong_windows_marker(command: str) -> bool:
    """True when ``command``'s executable text is unambiguously Windows-native."""
    code = code_only(command)
    return any(rx.search(code) for rx in _STRONG_WINDOWS_MARKERS)


def is_posix_shaped(command: str) -> bool:
    """True when ``command`` can be handed to ``bash -c`` and mean something.

    A command is POSIX-shaped unless it carries a strong Windows marker with no
    POSIX anchor to outrank it.  Empty/whitespace commands are POSIX-shaped —
    the bash generator substitutes ``true``.
    """
    if not command or not command.strip():
        return True
    code = code_only(command)
    if not any(rx.search(code) for rx in _STRONG_WINDOWS_MARKERS):
        return True
    return bool(_POSIX_ANCHOR_RE.search(code))


_NEUTRAL_STMT_RE = re.compile(r"^echo\s+'[^']*'$")
_NEUTRAL_FORBIDDEN = ("|", "<", ">", "`", "$(", "&", "\\\n")


def is_shell_neutral(command: str) -> bool:
    """True when ``command`` means the SAME thing in bash and in PowerShell.

    This is a whitelist with a proof, never a heuristic: every statement must
    be exactly ``echo '<single-quoted literal>'``.  ``echo`` is an alias for
    ``Write-Output`` in PowerShell and a single-quoted string is literal in
    both shells, so such a statement round-trips byte-for-byte.

    Deliberately narrow.  Widening it to ``printf`` or to double quotes would
    silently emit PowerShell that interpolates ``$…`` or mishandles ``\\n``.
    """
    if not command or not command.strip():
        return False
    if any(tok in command for tok in _NEUTRAL_FORBIDDEN):
        return False
    statements = [
        s.strip()
        for line in command.split("\n")
        for s in line.split(";")
    ]
    statements = [s for s in statements if s and not s.startswith("#")]
    if not statements:
        return False
    return all(_NEUTRAL_STMT_RE.match(s) for s in statements)


def infer_windows_shell(command: str) -> Shell:
    """Infer whether a Windows command text is cmd-batch or PowerShell.

    The corpus' ``platform_variants['windows']`` entries are NOT all
    PowerShell — measured, roughly 60 PowerShell / 15 cmd-batch / 26
    ambiguous.  Pasting ``for /L %i in (1,1,10) do @curl.exe …`` into a .ps1
    yields a script that PARSES under pwsh and then fails at runtime: exactly
    the defect being fixed, in the other direction.

    Ambiguous resolves to ``cmd`` on purpose: ``cmd.exe /c "foo.exe args"``
    executes a bare exe invocation correctly, PowerShell's parser may not.
    """
    code = code_only(command)
    # PowerShell-only syntax outranks a cmd marker, because PowerShell drives
    # cmd but not the reverse.  SIM-EDR-017 step-03 is
    # `$s = Join-Path $env:TEMP …; cmd.exe /c "md $s\386354"` — a PowerShell
    # block that shells out; classifying it cmd because it mentions `cmd /c`
    # would hand a `$s = …` assignment to cmd.exe, which cannot parse it.
    if _CMDLET_RE.search(code) or _PS_AUTOVAR_RE.search(code):
        return "powershell"
    if (
        _CMD_SLASH_C_RE.search(code)
        or _WIN_ENVVAR_RE.search(code)
        or _BATCH_FOR_RE.search(code)
        or _BATCH_TIMEOUT_RE.search(code)
    ):
        return "cmd"
    if _PS_INVOKE_RE.search(code):
        return "powershell"
    return "cmd"


@dataclass(frozen=True)
class StepResolution:
    """One scenario step, resolved to the command text a given target runs."""

    step_id: str
    identity: str
    shell: Shell
    command: str
    #: "primary" | "variant:windows" | "variant:linux" | "variant:macos" | "neutral"
    source: str
    note: str | None = None


@dataclass(frozen=True)
class TargetResolution:
    """The outcome of resolving a whole scenario against one target."""

    target: Target
    emittable: bool
    steps: tuple[StepResolution, ...]
    #: ``(step_id, REASON_CODE)`` for every step this target cannot satisfy.
    unresolved: tuple[tuple[str, str], ...]
    #: Cleanup commands this target can actually run, paired with their shell.
    cleanup: tuple[tuple[str, Shell], ...] = ()
    #: Cleanup commands dropped because they are not runnable on this target.
    skipped_cleanup: tuple[str, ...] = ()


class BundleTargetUnsatisfiable(Exception):
    """A bundle was requested for a target the scenario cannot satisfy.

    Carries the offending step ids + reason codes so the API can name them in
    a structured error instead of emitting something broken.
    """

    def __init__(self, scenario_id: str, target: str, unresolved: tuple[tuple[str, str], ...]):
        self.scenario_id = scenario_id
        self.target = target
        self.unresolved = tuple(unresolved)
        super().__init__(
            f"scenario={scenario_id} cannot emit a {target} bundle; "
            f"unresolved={[s for s, _ in unresolved]}"
        )

    def to_error(self) -> dict[str, Any]:
        """Structured-JSON error body per the repo convention."""
        steps = ", ".join(f"{sid} ({reason})" for sid, reason in self.unresolved)
        hint = (
            "declare platform_variants.windows on those steps"
            if self.target == "windows"
            else "declare platform_variants.linux on those steps"
        )
        return {
            "error": "Scenario cannot emit a bundle for this target",
            "code": "BUNDLE_TARGET_UNSATISFIABLE",
            "detail": (
                f"target={self.target} scenario={self.scenario_id} "
                f"unresolved=[{steps}] — {hint}"
            ),
        }


def _variant(step: dict[str, Any], key: str) -> str | None:
    variants = step.get("platform_variants") or {}
    if not isinstance(variants, dict):
        return None
    value = variants.get(key)
    if isinstance(value, dict):  # tolerate a future {shell, command} form
        value = value.get("command")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _declared_platforms(step: dict[str, Any]) -> set[str]:
    raw = step.get("platforms") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(p).strip().lower() for p in raw if str(p).strip()}


def _resolve_step_posix(step: dict[str, Any]) -> StepResolution | tuple[str, str]:
    step_id = step.get("id", "step-??")
    identity = step.get("identity", "direct")
    command = step.get("command", "") or ""

    # Primary FIRST (not the linux variant): for the 158 POSIX-emittable
    # scenarios the primary command IS the Linux command, and preferring the
    # variant would rewrite every existing bundle for no fidelity gain.
    if is_posix_shaped(command):
        return StepResolution(step_id, identity, "bash", command or "true", "primary")
    for key in ("linux", "macos"):
        alt = _variant(step, key)
        if alt is not None and is_posix_shaped(alt):
            return StepResolution(step_id, identity, "bash", alt, f"variant:{key}")
    return (step_id, REASON_POSIX_UNAVAILABLE)


def _resolve_step_windows(step: dict[str, Any]) -> StepResolution | tuple[str, str]:
    step_id = step.get("id", "step-??")
    identity = step.get("identity", "direct")
    command = step.get("command", "") or ""

    variant = _variant(step, "windows")
    if variant is not None:
        shell = infer_windows_shell(variant)
        return StepResolution(
            step_id, identity, shell, variant, "variant:windows",
            note=f"dialect inferred: {shell}",
        )
    if _declared_platforms(step) == {"windows"} and has_strong_windows_marker(command):
        shell = infer_windows_shell(command)
        return StepResolution(
            step_id, identity, shell, command, "primary",
            note=f"dialect inferred: {shell}",
        )
    if is_shell_neutral(command):
        return StepResolution(step_id, identity, "powershell", command, "neutral")
    return (step_id, REASON_WINDOWS_UNAVAILABLE)


def _resolve_cleanup(
    scenario: dict[str, Any], target: Target
) -> tuple[tuple[tuple[str, Shell], ...], tuple[str, ...]]:
    commands = ((scenario.get("cleanup") or {}).get("commands")) or []
    kept: list[tuple[str, Shell]] = []
    skipped: list[str] = []
    for cmd in commands:
        if not isinstance(cmd, str) or not cmd.strip():
            continue
        if target == "posix":
            # Cleanup never gates emittability — a bundle that runs its steps
            # and warns on cleanup still delivers the signal the POV needs.
            kept.append((cmd, "bash"))
            continue
        if is_shell_neutral(cmd):
            kept.append((cmd, "powershell"))
        elif has_strong_windows_marker(cmd):
            kept.append((cmd, infer_windows_shell(cmd)))
        else:
            skipped.append(cmd)
    return tuple(kept), tuple(skipped)


def resolve_target(scenario: dict[str, Any], target: Target) -> TargetResolution:
    """Resolve every step of ``scenario`` against ``target``.

    ``emittable`` is True only when EVERY step contributed a command the target
    can execute.  A partially-resolved bundle is worse than no bundle: it runs
    green, emits a subset of the attack, and the POV report reads the gap as
    "the customer's stack detected nothing".
    """
    if target not in ("posix", "windows"):
        raise ValueError(f"unknown bundle target: {target!r}")

    resolver = _resolve_step_posix if target == "posix" else _resolve_step_windows
    steps: list[StepResolution] = []
    unresolved: list[tuple[str, str]] = []
    for step in scenario.get("steps") or []:
        outcome = resolver(step)
        if isinstance(outcome, StepResolution):
            steps.append(outcome)
        else:
            unresolved.append(outcome)

    cleanup, skipped = _resolve_cleanup(scenario, target)
    return TargetResolution(
        target=target,
        emittable=bool(steps) and not unresolved,
        steps=tuple(steps),
        unresolved=tuple(unresolved),
        cleanup=cleanup,
        skipped_cleanup=skipped,
    )


def emittable_targets(scenario: dict[str, Any]) -> tuple[Target, ...]:
    """Targets this scenario can emit a runnable bundle for, POSIX first."""
    return tuple(t for t in ("posix", "windows") if resolve_target(scenario, t).emittable)


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

_IDENTITY_HARNESS_TMPL = """\
# ---------------------------------------------------------------------------
# Identity Harness — every TTP step runs through this function for consistent
# process causality chains and logging, regardless of identity.
#
# The `direct` and service-account allowlists below are generated from the
# canonical spec (spec/identity_harness.json) so push (this bash) and pull (the
# Go beacon's identity.ResolveIdentity) resolve a scenario's identity string
# identically. GAP-PUSH-001.
# ---------------------------------------------------------------------------

run_as() {{
    local identity="$1"
    local cmd="$2"
    local step_id="$3"

    log INFO "STEP $step_id identity=$identity cmd=$cmd"

    case "$identity" in
        {direct_arms})
            # Direct execution — still logged through harness
            bash -c "$cmd"
            ;;
        {service_arms})
            # Prefer runuser (cleanest causality), fall back to sudo -u, then su
            if command -v runuser &>/dev/null; then
                runuser -l "$identity" -c "$cmd" 2>/dev/null || \\
                    sudo -u "$identity" bash -c "$cmd" 2>/dev/null || \\
                    su -s /bin/bash "$identity" -c "$cmd"
            elif command -v sudo &>/dev/null; then
                sudo -u "$identity" bash -c "$cmd"
            else
                su -s /bin/bash "$identity" -c "$cmd"
            fi
            ;;
        *)
            # Unknown identity — best-effort runuser (mirrors the Go beacon's
            # unknown-username path), falling back to direct with a warning.
            log WARN "Unknown identity '$identity' — best-effort runuser then direct"
            if command -v runuser &>/dev/null; then
                runuser -l "$identity" -c "$cmd" 2>/dev/null || bash -c "$cmd"
            else
                bash -c "$cmd"
            fi
            ;;
    esac

    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        log WARN "Step $step_id exited with code $exit_code (non-zero — may be expected)"
    else
        log INFO "Step $step_id completed successfully"
    fi
    return $exit_code
}}
"""


def _build_identity_harness() -> str:
    """Render the bash ``run_as`` harness with the allowlists from the canonical
    identity spec (single source of truth shared with the Go beacon)."""
    from engine.identity_spec import direct_identities, service_accounts  # noqa: PLC0415

    direct_arms = "|".join(direct_identities())
    service_arms = "|".join(service_accounts())
    return _IDENTITY_HARNESS_TMPL.format(
        direct_arms=direct_arms,
        service_arms=service_arms,
    )

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
    script += _build_identity_harness()

    # --- Dependency checks ---
    script += _DEP_CHECK_TMPL
    deps_seen: set[str] = {"curl", "bash"}  # always check basics
    for tool in external_tools:
        tool_name = tool.get("name", "")
        # Tools backed by a tool adapter (adapter_ref) are installed by the
        # adapter-install section below, so they must NOT be hard-checked here.
        if tool.get("adapter_ref"):
            continue
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

    # --- Tool-adapter installs ---
    # Resolve each external_tools[].adapter_ref against the catalog and emit a
    # self-contained install for runtime-fetched (tier-4) tools, so a downloaded
    # bundle installs its own dependencies on a clean host. C2 frameworks are
    # never auto-staged from a push bundle (safety); tier 1/2/3 tools must be
    # pre-provisioned (in-tree build / submodule / IaC lab) and are noted.
    from tools.adapter_catalog import catalog as adapter_catalog  # local import avoids cycle
    adapter_lines: list[str] = []
    for tool in external_tools:
        ref = tool.get("adapter_ref")
        if not ref:
            continue
        ad = adapter_catalog.find(ref)
        if ad is None:
            adapter_lines.append(f'log WARN "Adapter {ref} not in catalog — install skipped"')
            continue
        if ad.safety_class == "c2-framework":
            adapter_lines.append(
                f'log WARN "Adapter {ad.name} is a C2 framework — NOT auto-installed '
                f'from a push bundle; provision manually on an authorized target"'
            )
            continue
        cmd = ad.install.runtime_install_command if ad.install else None
        if ad.tier == 4 and cmd:
            adapter_lines.append(f'log INFO "Installing adapter {ad.name} v{ad.version} (tier 4)"')
            adapter_lines.append(cmd)
        else:
            adapter_lines.append(
                f'log INFO "Adapter {ad.name} v{ad.version} (tier {ad.tier}, {ad.category}) '
                f'must be pre-provisioned on the target"'
            )
    if adapter_lines:
        script += "# ---------------------------------------------------------------------------\n"
        script += "# Tool adapter installs (resolved from external_tools[].adapter_ref)\n"
        script += "# ---------------------------------------------------------------------------\n"
        script += "\n".join(adapter_lines) + "\n\n"

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
# PowerShell generator (target=windows)
# ---------------------------------------------------------------------------
#
# Rendered with @@PLACEHOLDER@@ substitution rather than str.format(), for the
# same reason core/api/agents.py::_render does: PowerShell is full of literal
# braces (hashtables, script blocks, "$($x)") and str.format() detonates on
# them.  Substitution is single-pass so an injected value containing @@FOO@@ is
# never re-expanded.

_PS_PLACEHOLDER_RE = re.compile(r"@@([A-Z0-9_]+)@@")

#: UTF-8 BOM.  Windows PowerShell 5.1 reads a BOM-less file as ANSI, which
#: mangles the em-dashes and box characters the scenario corpus puts inside
#: string literals (e.g. SIM-EDR-017 step-04's Write-Host banner).  Mojibake in
#: a marker string breaks the very grep a DC runs to prove the step executed.
_PS_BOM = "﻿"


def _render(template: str, fields: dict[str, Any]) -> str:
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in fields:
            raise KeyError(f"push_generator: unbound PowerShell template field @@{key}@@")
        return str(fields[key])

    return _PS_PLACEHOLDER_RE.sub(_sub, template)


def _ps_literal(text: str) -> str:
    """Render ``text`` as a PowerShell string literal that needs no escaping.

    Prefers a literal here-string (``@'…'@``) so 17 KB of authored attacker
    text survives verbatim with no ``$`` interpolation and no backtick
    escaping.  Falls back to a single-quoted string with doubled quotes when
    the text contains a line that would terminate the here-string early.
    """
    body = text.rstrip("\n")
    if not any(line.startswith("'@") or line.startswith('"@') for line in body.split("\n")):
        return "@'\n" + body + "\n'@"
    return "'" + body.replace("'", "''") + "'"


def _ps_inline(text: str, limit: int = 160) -> str:
    """Single-quoted, single-line literal for log messages."""
    flat = " ".join(text.split())
    if len(flat) > limit:
        flat = flat[: limit - 3] + "..."
    return "'" + flat.replace("'", "''") + "'"


def _ps_var(step_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]", "_", step_id)
    return f"$CsCmd_{safe}"


_PS_HEADER_TMPL = """\
# =============================================================================
# CortexSim Detection Simulation Bundle — Windows / PowerShell
# =============================================================================
# Scenario ID   : @@SCENARIO_ID@@
# Name          : @@NAME@@
# Version       : @@VERSION@@
# Plane         : @@PLANE@@
#
# UC Reference  : @@UC_REF@@ - @@UC_NAME@@
# TC Reference  : @@TC_REF@@ - @@TC_NAME@@
#
# MITRE Tactic  : @@MITRE_TACTIC@@ - @@MITRE_TACTIC_NAME@@
# MITRE Technique: @@MITRE_TECHNIQUE@@ - @@MITRE_TECHNIQUE_NAME@@
#
# Expected Detections:
@@EXPECTED_DETECTIONS_COMMENT@@#
# Threat Report : @@THREAT_REPORT@@
# Author        : @@AUTHOR@@
#
# Resolved execution plan (step: source -> shell):
@@STEP_PLAN_COMMENT@@#
# IMPORTANT: Self-contained — no SimCore dependency at runtime.
#            Windows PowerShell 5.1 floor (Server 2016+ / Windows 10+): no
#            PS7-only syntax, no Install-Module, no PSGallery, no callback to
#            SimCore. Nothing is installed; missing tools are logged as WARN.
#
# Run with:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\cortexsim-@@SCENARIO_ID@@.ps1
#
# A downloaded file carries Mark-of-the-Web. If your policy requires it, run
# `Unblock-File .\\cortexsim-@@SCENARIO_ID@@.ps1` first — this bundle never calls
# Set-ExecutionPolicy (a machine-scope mutation that also fails unelevated).
# =============================================================================

# Warn-and-continue, mirroring the bash bundle's intent: a step exiting
# non-zero is often EXPECTED (a blocked LOLBin is a detection, not a failure).
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

$CortexSimScenarioId = '@@SCENARIO_ID@@'
$CortexSimStamp      = (Get-Date).ToString('yyyyMMdd_HHmmss')
$CortexSimWorkRoot   = if ($env:TEMP) { $env:TEMP } else { (Get-Location).Path }
$CortexSimLog        = Join-Path $CortexSimWorkRoot ('cortexsim-' + $CortexSimScenarioId + '-' + $CortexSimStamp + '.log')
$CortexSimStage      = Join-Path $CortexSimWorkRoot ('cortexsim-' + $CortexSimScenarioId + '-' + $CortexSimStamp + '.d')

function Write-CsLog {
    param([string]$Level, [string]$Message)
    Write-Host ('[' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + '] [' + $Level + '] ' + $Message)
}

# Transcription is disabled by policy on some hardened hosts; degrade to
# console logging rather than aborting the simulation.
$CortexSimTranscript = $false
try {
    Start-Transcript -Path $CortexSimLog -Append -ErrorAction Stop | Out-Null
    $CortexSimTranscript = $true
} catch {
    Write-CsLog WARN 'Start-Transcript unavailable (policy or already running) — console logging only'
}

New-Item -ItemType Directory -Force -Path $CortexSimStage -ErrorAction SilentlyContinue | Out-Null

Write-CsLog INFO ('CortexSim bundle starting — scenario=' + $CortexSimScenarioId)
Write-CsLog INFO ('Log file: ' + $CortexSimLog)
"""

_PS_HARNESS_TMPL = """\
# ---------------------------------------------------------------------------
# Identity Harness (Windows)
#
# HONEST CONSTRAINT: Windows has no credential-free, non-interactive
# impersonation. `runas` prompts for a password; `Start-Process -Credential`
# needs a credential a self-contained bundle must never carry. So a step that
# declares a POSIX service account (www-data, postgres, …) CANNOT achieve that
# identity here — it runs as the invoking user and says so, loudly, via a
# `cortexsim-identity-degraded` marker line. Never silently pretend: that
# marker is what keeps the POV's causality story honest about which steps did
# not reach their declared identity.
#
# The direct-identity allowlist below is generated from the canonical spec
# (spec/identity_harness.json) — the same file the bash harness and the Go
# beacon read — so the three cannot drift. GAP-PUSH-001.
# ---------------------------------------------------------------------------

$CortexSimDirectIdentities = @(@@DIRECT_IDENTITIES@@)

# Windows always ships powershell.exe (the 5.1 floor this bundle targets), so
# that is the interpreter steps run under. The fallback exists so a DC can
# DRY-RUN the bundle under pwsh on a jumpbox before taking it to the customer:
# without it the bundle is unverifiable anywhere except the target host.
$CortexSimPwsh = 'powershell.exe'
if (-not (Get-Command $CortexSimPwsh -ErrorAction SilentlyContinue)) {
    $CortexSimPwsh = (Get-Process -Id $PID).Path
}

function Test-CsElevated {
    try {
        $wi = [Security.Principal.WindowsIdentity]::GetCurrent()
        $wp = New-Object Security.Principal.WindowsPrincipal($wi)
        return $wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Resolve-CsIdentity {
    param([string]$Identity)
    if ([string]::IsNullOrWhiteSpace($Identity)) { return 'direct' }
    $lower = $Identity.ToLower()
    if ($lower -eq 'direct') { return 'direct' }
    # root / container-runtime / administrator / SYSTEM all assert elevation.
    if ($CortexSimDirectIdentities -contains $lower -or $lower -eq 'administrator' -or $lower -eq 'system') {
        if (Test-CsElevated) { return 'elevated' }
        return 'degraded'
    }
    return 'degraded'
}

function Invoke-CsShell {
    param([string]$Name, [string]$Shell, [string]$Command)

    # The command text is written to a file and the interpreter is invoked on
    # that file, never on an argument string. Two reasons: (1) it sidesteps
    # Windows PowerShell 5.1's well-known mangling of embedded quotes when
    # passing arguments to a native exe — SIM-EDR-017 step-02 nests doubled
    # quotes inside a cmd /c argument and would not survive it;
    # (2) it produces one real child process per step, matching the bash
    # bundle's `bash -c` causality shape.
    $ext = if ($Shell -eq 'cmd') { '.cmd' } else { '.ps1' }
    $file = Join-Path $CortexSimStage ($Name + $ext)
    if ($Shell -eq 'cmd') {
        # cmd.exe cannot read a BOM; OEM keeps it byte-clean.
        Set-Content -LiteralPath $file -Value $Command -Encoding Oem -Force
        & $env:ComSpec /c $file
    } else {
        # PowerShell's default ErrorActionPreference is 'Continue', so a cmdlet
        # that fails NON-terminatingly writes to stderr and still exits 0 —
        # which made Invoke-CsStep log "completed successfully" for a step that
        # did nothing. That is a manufactured false green: the DC reads it as
        # "the TTP ran and Cortex missed it". 'Stop' is the PowerShell analogue
        # of the bash bundle's `set -e`, and it is scoped to the step script so
        # the harness itself keeps its own preference.
        $stepBody = "$ErrorActionPreference = 'Stop'" + [Environment]::NewLine + $Command
        Set-Content -LiteralPath $file -Value $stepBody -Encoding UTF8 -Force
        # $IsLinux/$IsMacOS are PS6+ automatic variables; on the 5.1 floor they
        # are simply undefined -> $null -> falsy, which is the Windows branch.
        if ($IsLinux -or $IsMacOS) {
            # -ExecutionPolicy is a Windows-only switch; pwsh rejects it there.
            & $CortexSimPwsh -NoProfile -NonInteractive -File $file
        } else {
            & $CortexSimPwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $file
        }
    }
}

function Invoke-CsStep {
    param([string]$StepId, [string]$Identity, [string]$Shell, [string]$Command)

    $mode = Resolve-CsIdentity -Identity $Identity
    Write-CsLog INFO ('STEP ' + $StepId + ' identity=' + $Identity + ' shell=' + $Shell + ' mode=' + $mode)
    if ($mode -eq 'degraded') {
        Write-CsLog WARN ('cortexsim-identity-degraded step=' + $StepId + ' declared=' + $Identity +
                          ' actual=' + $env:USERNAME + ' reason=no-credential-free-impersonation-on-windows')
    }

    $global:LASTEXITCODE = 0
    try {
        Invoke-CsShell -Name $StepId -Shell $Shell -Command $Command
    } catch {
        Write-CsLog WARN ('Step ' + $StepId + ' threw: ' + $_.Exception.Message)
        return
    }
    if ($LASTEXITCODE -ne 0) {
        Write-CsLog WARN ('Step ' + $StepId + ' exited with code ' + $LASTEXITCODE + ' (non-zero — may be expected)')
    } else {
        Write-CsLog INFO ('Step ' + $StepId + ' completed successfully')
    }
}

# ---------------------------------------------------------------------------
# Dependency checks — probe only. Installing would break the cardinal
# invariant (self-contained, no network dependency, no module install).
# ---------------------------------------------------------------------------
function Test-CsDep {
    param([string]$Name)
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Write-CsLog INFO ('Dependency OK: ' + $Name)
    } else {
        Write-CsLog WARN ('Dependency not found: ' + $Name + ' — some steps may fail')
    }
}

"""


def generate_powershell(scenario: dict[str, Any]) -> str:
    """Generate a self-contained PowerShell bundle for the ``windows`` target.

    Raises :class:`BundleTargetUnsatisfiable` when any step lacks a Windows
    command, naming the offending steps.  Emitting a partial bundle would be
    worse: it would run green, emit a subset of the attack, and the POV report
    would read the gap as "the customer's stack detected nothing".
    """
    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    resolution = resolve_target(scenario, "windows")
    if not resolution.emittable:
        raise BundleTargetUnsatisfiable(scenario_id, "windows", resolution.unresolved)

    steps_by_id = {s.get("id", "step-??"): s for s in (scenario.get("steps") or [])}
    external_tools = scenario.get("external_tools") or []

    expected_lines: list[str] = []
    for step in scenario.get("steps") or []:
        for det in step.get("expected_detections", []):
            expected_lines.append(
                f"#   [{det.get('plane','?')}] {det.get('type','?')}: {det.get('description','')}"
            )
    expected_detections_comment = (
        "\n".join(expected_lines) + "\n" if expected_lines else "#   (none specified)\n"
    )

    plan_lines = [
        f"#   {r.step_id}: {r.source} -> {r.shell}"
        + (f"  ({r.note})" if r.note else "")
        for r in resolution.steps
    ]
    step_plan_comment = "\n".join(plan_lines) + "\n"

    from engine.identity_spec import direct_identities  # noqa: PLC0415

    # Whether Windows can honour an impersonated identity is a property of the
    # canonical spec, not of this generator — the same key the Go beacon reads.
    # Today it is False (no unattended impersonation primitive exists), which is
    # why every non-direct identity emits a degradation marker below. If the
    # spec ever flips it, this generator has no implementation for it, and
    # emitting the degraded harness anyway would be exactly the silent lie both
    # execution paths exist to prevent — so fail loudly instead.
    try:
        from engine.identity_spec import supports_impersonation  # noqa: PLC0415

        win_impersonation = supports_impersonation("windows")
    except ImportError:  # spec loader predates the platform key
        win_impersonation = False
    if win_impersonation:
        raise NotImplementedError(
            "spec/identity_harness.json declares Windows impersonation supported, "
            "but push_generator emits no impersonation wrapper for it — implement "
            "one before flipping the spec, or the bundle will claim an identity "
            "it never achieved"
        )

    direct_list = ", ".join(f"'{i.lower()}'" for i in direct_identities())

    script = _render(
        _PS_HEADER_TMPL,
        {
            "SCENARIO_ID": scenario_id,
            "NAME": scenario.get("name", ""),
            "VERSION": scenario.get("version", "1.0"),
            "PLANE": scenario.get("plane", ""),
            "UC_REF": scenario.get("uc_ref", ""),
            "UC_NAME": scenario.get("uc_name", ""),
            "TC_REF": scenario.get("tc_ref", ""),
            "TC_NAME": scenario.get("tc_name", ""),
            "MITRE_TACTIC": scenario.get("mitre_tactic", ""),
            "MITRE_TACTIC_NAME": scenario.get("mitre_tactic_name", ""),
            "MITRE_TECHNIQUE": scenario.get("mitre_technique", ""),
            "MITRE_TECHNIQUE_NAME": scenario.get("mitre_technique_name", ""),
            "EXPECTED_DETECTIONS_COMMENT": expected_detections_comment,
            "THREAT_REPORT": scenario.get("threat_report") or "N/A",
            "AUTHOR": scenario.get("author") or "CortexSim",
            "STEP_PLAN_COMMENT": step_plan_comment,
        },
    )
    script += _render(_PS_HARNESS_TMPL, {"DIRECT_IDENTITIES": direct_list})

    # --- Dependency probes ---
    deps = {"powershell.exe", "cmd.exe"}
    for tool in external_tools:
        if tool.get("adapter_ref"):
            continue
        tname = tool.get("name", "")
        if tname and tool.get("type") == "binary" and not tool.get("install_inline", False):
            deps.add(tname)
    for dep in sorted(deps):
        script += f"Test-CsDep {_ps_inline(dep)}\n"
    script += "\n"

    # --- Tool adapters: NOTE only, never install ---
    # The bash bundle self-installs tier-4 adapters with their POSIX
    # runtime_install_command. That command is meaningless on Windows and
    # running it would violate the cardinal invariant, so the Windows bundle
    # reports what the target must already have instead.
    from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415

    adapter_lines: list[str] = []
    for tool in external_tools:
        ref = tool.get("adapter_ref")
        if not ref:
            continue
        ad = adapter_catalog.find(ref)
        if ad is None:
            adapter_lines.append(
                f"Write-CsLog WARN {_ps_inline(f'Adapter {ref} not in catalog — install skipped')}"
            )
            continue
        if ad.safety_class == "c2-framework":
            adapter_lines.append(
                "Write-CsLog WARN "
                + _ps_inline(
                    f"Adapter {ad.name} is a C2 framework — NOT auto-installed from a "
                    f"push bundle; provision manually on an authorized target"
                )
            )
            continue
        adapter_lines.append(
            "Write-CsLog INFO "
            + _ps_inline(
                f"Adapter {ad.name} v{ad.version} (tier {ad.tier}, {ad.category}) must be "
                f"pre-provisioned on the Windows target — no runtime install from a bundle"
            )
        )
    if adapter_lines:
        script += "# ---------------------------------------------------------------------------\n"
        script += "# Tool adapter notes (resolved from external_tools[].adapter_ref)\n"
        script += "# ---------------------------------------------------------------------------\n"
        script += "\n".join(adapter_lines) + "\n\n"

    # --- Cleanup function ---
    script += "# ---------------------------------------------------------------------------\n"
    script += "# Cleanup / Teardown — runs from the script-scope finally block below, the\n"
    script += "# PowerShell analogue of bash's `trap cleanup EXIT`. Register-EngineEvent\n"
    script += "# PowerShell.Exiting is NOT used: it does not fire for a script run via -File.\n"
    script += "# ---------------------------------------------------------------------------\n"
    script += "function Invoke-CsCleanup {\n"
    script += "    Write-CsLog INFO ('Running cleanup for scenario=' + $CortexSimScenarioId)\n"
    for idx, (cmd, shell) in enumerate(resolution.cleanup, start=1):
        script += f"    try {{ Invoke-CsShell -Name 'cleanup-{idx:02d}' -Shell '{shell}' -Command {_ps_literal(cmd)} }}\n"
        script += (
            f"    catch {{ Write-CsLog WARN ('cleanup-{idx:02d} threw: ' + $_.Exception.Message) }}\n"
        )
    for cmd in resolution.skipped_cleanup:
        # Named, not silently dropped: an operator must be able to see which
        # teardown the Windows bundle could not perform.
        script += (
            "    Write-CsLog WARN "
            + _ps_inline(f"cleanup step skipped — POSIX-only command not runnable on Windows: {cmd}")
            + "\n"
        )
    if not resolution.cleanup and not resolution.skipped_cleanup:
        script += "    Write-CsLog INFO 'No cleanup commands defined for this scenario'\n"
    script += "    try { Remove-Item -LiteralPath $CortexSimStage -Recurse -Force -ErrorAction SilentlyContinue } catch { }\n"
    script += "    Write-CsLog INFO 'Cleanup complete'\n"
    script += "}\n\n"

    # --- Steps, inside a script-scope try/finally ---
    script += "# ---------------------------------------------------------------------------\n"
    script += "# TTP Execution Steps\n"
    script += "# ---------------------------------------------------------------------------\n"
    script += "try {\n\n"
    for res in resolution.steps:
        raw = steps_by_id.get(res.step_id, {})
        script += f"# --- {res.step_id}: {raw.get('name', '')}\n"
        if raw.get("mitre_technique"):
            script += f"# MITRE: {raw['mitre_technique']}\n"
        script += f"# Command source: {res.source}"
        script += f" ({res.note})\n" if res.note else "\n"
        for det in raw.get("expected_detections", []):
            script += (
                f"# Expected: [{det.get('plane','?')}] {det.get('type','?')}: "
                f"{det.get('description','')}\n"
            )
        var = _ps_var(res.step_id)
        script += f"{var} = {_ps_literal(res.command)}\n"
        script += (
            f"Invoke-CsStep -StepId '{res.step_id}' -Identity '{res.identity}' "
            f"-Shell '{res.shell}' -Command {var}\n\n"
        )
    script += "    Write-CsLog INFO ('CortexSim bundle complete — scenario=' + $CortexSimScenarioId)\n"
    script += "    Write-CsLog INFO 'Review detections in Cortex XDR/XSIAM console.'\n"
    script += "}\n"
    script += "finally {\n"
    script += "    Invoke-CsCleanup\n"
    script += "    if ($CortexSimTranscript) { try { Stop-Transcript | Out-Null } catch { } }\n"
    script += "}\n"

    logger.info(
        "Generated PowerShell bundle for scenario=%s (%d steps)",
        scenario_id, len(resolution.steps),
    )
    return _PS_BOM + script


# ---------------------------------------------------------------------------
# K8s manifest generator (target=posix, delivered into a cluster)
# ---------------------------------------------------------------------------
#
# The object graph, the posture model, the finding vocabulary and the header
# live in core/engine/k8s_manifest.py. This function is the seam: it resolves
# the delivery mode, enforces consent when a consent dict is supplied, and
# renders.
#
# NOTHING ABOVE THIS LINE IS TOUCHED. generate_bash and generate_powershell hold
# the self-contained invariant that 169 scenarios depend on, and a snapshot test
# (tests/engine/test_push_generator_invariant.py) proves their output is
# byte-identical for the whole corpus.


def generate_k8s(
    scenario: dict[str, Any],
    *,
    delivery: str | None = None,
    simcore_url: str = "",
    consent: dict[str, Any] | None = None,
    allow_privileged_k8s: bool = True,
    now: "datetime | None" = None,
    fetch_image: str | None = None,
    reaper_image: str | None = None,
) -> str:
    """Generate a Kubernetes manifest that delivers ``scenario`` into a cluster.

    All keyword arguments are optional, so the pre-existing single-positional
    call site (``generate_k8s(scenario_dict)``) keeps working unchanged.

    ``delivery`` — ``"embedded"`` (default) keeps the manifest SELF-CONTAINED:
    the payload travels inline in a ConfigMap and the pod needs nothing from
    SimCore at runtime. ``"served"`` is the one documented exception and is
    selected automatically when the posture declares staged tool ``payloads``,
    which only SimCore can serve. Making embedded the default is a deliberate
    choice: it confines the relaxed invariant to the case that actually needs
    it, rather than imposing cluster→SimCore reachability on every k8s POV.

    ``consent`` — when supplied, the gate is enforced here as defence in depth
    and raises :class:`~engine.k8s_manifest.ClusterConsentRequired`. When
    ``None`` the caller has taken responsibility for gating. The HTTP surface is
    the real boundary: the dangerous artifact is the file that leaves SimCore,
    and a generator-level gate is bypassable by any in-process caller anyway.

    Raises :class:`BundleTargetUnsatisfiable` when the scenario cannot resolve a
    POSIX target — identical to ``format=bash``, so a pod never receives a
    script that parses and then dies in front of a customer.
    """
    from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
    from engine import k8s_manifest as km  # noqa: PLC0415

    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    posture = km.parse_posture(scenario)

    if delivery is None:
        # Staged tool artifacts can only come from the SimCore payload shelf.
        delivery = "served" if (posture and posture.payloads) else "embedded"
    if delivery not in ("embedded", "served"):
        raise ValueError(
            f"delivery must be 'embedded' or 'served' (got {delivery!r}); "
            f"'image' — the payload baked into an image in the customer's own "
            f"registry — is a known future value, not yet implemented"
        )
    if delivery == "served" and not simcore_url:
        simcore_url = "http://SET-CORTEXSIM_PUBLIC_URL:8888"

    if consent is not None:
        km.check_cluster_consent(
            scenario_id, posture, consent,
            allow_privileged_k8s=allow_privileged_k8s,
        )

    now = now or _dt.now(tz=_tz.utc)
    objects = km.build_objects(
        scenario,
        posture=posture,
        delivery=delivery,
        simcore_url=simcore_url,
        now=now,
        fetch_image=fetch_image or km.DEFAULT_FETCH_IMAGE,
        reaper_image=reaper_image or km.DEFAULT_REAPER_IMAGE,
    )
    header = km.build_header(
        scenario,
        posture=posture,
        objects=objects,
        delivery=delivery,
        simcore_url=simcore_url,
        now=now,
        reaper_image=reaper_image or km.DEFAULT_REAPER_IMAGE,
        consent_recorded=consent,
    )
    logger.info(
        "Generated K8s manifest scenario=%s objects=%d delivery=%s risk_tier=%s "
        "findings=%s",
        scenario_id, len(objects), delivery,
        (posture.risk_tier() if posture else "baseline"),
        ",".join(km.emitted_finding_slugs(objects)) or "-",
    )
    return km.render(objects, header)
