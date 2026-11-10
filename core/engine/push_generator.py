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

import base64
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
#: The step's Windows command mixes cmd syntax (infix ``&``, ``%VAR%``) with
#: PowerShell syntax, so it runs correctly under NEITHER interpreter. Refused
#: rather than guessed: handed to cmd, the PowerShell half fails; handed to
#: Windows PowerShell 5.1, the cmd ``&`` is a parse error and nothing runs.
REASON_MIXED_DIALECT = "MIXED_DIALECT"

# ---------------------------------------------------------------------------
# Interpolation safety — every scenario-sourced value that lands in a bundle
# ---------------------------------------------------------------------------
#
# Scenario YAML (and, through the composer, a draft a DC typed) is the input.
# A value that reaches a ``#`` comment line must not be able to END that line:
# a CR/LF in a name, step name or detection description would otherwise start a
# new, uncommented line — a top-level command in the artifact a DC runs on a
# customer host, invisible to ``bash -n`` and to the PowerShell parser.

#: C0/C1 controls except TAB, plus the Unicode line/paragraph separators. TAB is
#: kept because it cannot end a comment in either shell and corpus commands use it.
_COMMENT_UNSAFE_RE = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]")


def _comment_safe(value: Any) -> str:
    """Render ``value`` for interpolation into a single bash/PowerShell comment line.

    CR, LF and every other control character become a space, so the value can
    never terminate the ``#`` line it is placed on. ``str()`` semantics are
    kept (``None`` renders ``None``, as the f-strings this replaced did) so the
    output for every clean value is byte-identical.
    """
    return _COMMENT_UNSAFE_RE.sub(" ", str(value))


def _expected_comment(prefix: str, det: dict[str, Any]) -> str:
    """One ``expected_detections`` entry as a comment line (bash and PowerShell)."""
    return (
        f"{prefix}[{_comment_safe(det.get('plane', '?'))}] "
        f"{_comment_safe(det.get('type', '?'))}: {_comment_safe(det.get('description', ''))}"
    )


def _safe_token(value: Any, fallback: str = "UNKNOWN") -> str:
    """A value used in CODE position (a file name, a log message, a variable):
    restricted to ``[A-Za-z0-9_.-]``. Corpus scenario ids already are."""
    out = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))
    return out or fallback


def _bash_dq(value: Any) -> str:
    """Escape ``value`` for the inside of a bash DOUBLE-quoted string."""
    s = _comment_safe(value)
    for ch in ("\\", '"', "$", "`"):
        s = s.replace(ch, "\\" + ch)
    return s


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


#: The STRONG-MARKER verb list (``has_strong_windows_marker`` /
#: ``is_posix_shaped``). Deliberately NOT widened with the dialect classifier's
#: full ``Get-Verb`` list below: widening it would change which steps the POSIX
#: and Windows resolvers accept — i.e. which scenarios emit at all — which is a
#: content decision, not a classifier fix.
_CMDLET_VERBS = (
    "Get|Set|New|Remove|Add|Start|Stop|Invoke|Test|Out|Write|Select|Where|"
    "ForEach|Import|Export|Copy|Move|Enable|Disable|Register|Unregister|"
    "ConvertTo|ConvertFrom|Join|Split|Resolve|Restart"
)
_CMDLET_RE = re.compile(rf"(?<![\w-])(?:{_CMDLET_VERBS})-[A-Z][A-Za-z]+")
_PS_AUTOVAR_RE = re.compile(r"\$env:|\$PSVersionTable|\$ErrorActionPreference", re.IGNORECASE)
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

#: Sufficient evidence that a command is Windows-native.
#:
#: A bare ``powershell`` / ``pwsh`` TOKEN is deliberately NOT here — it is a
#: weak marker.  SIM-EDR-018 step-02 is valid bash that guards an optional
#: `pwsh -NoProfile -Command …` behind `command -v pwsh`; treating the token as
#: strong would withdraw that scenario's working POSIX bundle.
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


#: What ``infer_windows_shell`` can answer. ``mixed`` is not a runnable shell:
#: a step that classifies ``mixed`` is unresolved (``REASON_MIXED_DIALECT``).
Dialect = Literal["powershell", "cmd", "mixed"]

# ── PowerShell STRUCTURE (each is syntax cmd.exe has no meaning for) ─────────
#
# Verbs: every verb `Get-Verb` lists on Windows PowerShell 5.1 and PowerShell 7,
# plus the non-approved verbs of built-in cmdlets (ForEach-/Where-/Sort-/Tee-
# Object). The old, hand-picked 28-verb list missed Clear-, Use-, Compress-,
# Rename-, Format-, Expand-, Measure- … so `… & Clear-History` was sent to
# cmd.exe and `Use-STSRoleWithSAML …` to cmd.exe as-is.
_PS_VERBS = (
    "Add|Approve|Assert|Backup|Block|Build|Checkpoint|Clear|Close|Compare|Complete|"
    "Compress|Confirm|Connect|ConvertFrom|ConvertTo|Convert|Copy|Debug|Deny|Deploy|"
    "Disable|Disconnect|Dismount|Edit|Enable|Enter|Exit|Expand|Export|Find|ForEach|"
    "Format|Get|Grant|Group|Hide|Import|Initialize|Install|Invoke|Join|Limit|Lock|"
    "Measure|Merge|Mount|Move|New|Open|Optimize|Out|Ping|Pop|Protect|Publish|Push|"
    "Read|Receive|Redo|Register|Remove|Rename|Repair|Request|Reset|Resize|Resolve|"
    "Restart|Restore|Resume|Revoke|Save|Search|Select|Send|Set|Show|Skip|Sort|Split|"
    "Start|Step|Stop|Submit|Suspend|Switch|Sync|Tee|Test|Trace|Unblock|Undo|"
    "Uninstall|Unlock|Unprotect|Unpublish|Unregister|Update|Use|Wait|Watch|Where|Write"
)
_PS_VERB_NOUN_RE = re.compile(rf"(?<![\w./\\-])(?:{_PS_VERBS})-[A-Z][A-Za-z0-9]*")
#: `$name =`, `$o.Prop =`, `${x} +=` — assignment. cmd assigns with `set`.
_PS_ASSIGN_RE = re.compile(r"\$(?:[A-Za-z_]\w*|\{[^}\n]+\})(?:\.[A-Za-z_]\w*)*\s*[-+*/%]?=(?!=)")
#: `[Type]::Member` — static member access.
_PS_STATIC_RE = re.compile(r"\[[A-Za-z_][\w.]*(?:\[\])?\]::")
_PS_ENV_REF_RE = re.compile(r"\$env:|\$\{env:", re.IGNORECASE)
_PS_AUTOVARS_RE = re.compile(
    r"\$(?:_|PSItem|PSVersionTable|ErrorActionPreference|true|false|null|"
    r"LASTEXITCODE|PSScriptRoot|args|input|Error)\b",
    re.IGNORECASE,
)
_PS_ARRAY_HASH_RE = re.compile(r"@[({]")
#: `& $x` / `& 'path'` at the START of a statement — the call operator. A
#: leading `&` is a syntax error to cmd.exe ("& was unexpected").
_PS_CALL_OP_RE = re.compile(r"(?m)(?:^|[;|{(])[ \t]*&(?!&)[ \t]*\S")
#: `;` statement separator. cmd.exe has none — `whoami /all; net user` hands
#: `/all;` to whoami and never runs `net user`.
_PS_SEMICOLON_RE = re.compile(r";[ \t]+\S")
_PS_KEYWORD_RE = re.compile(
    r"(?<![\w-])(?:foreach|while|switch)\s*\("
    r"|(?<![\w-])for\s*\(\s*\$"
    r"|(?<![\w-])(?:try|finally)\s*\{"
    r"|(?<![\w-])catch\s*(?:\[[^\]\n]*\]\s*)?\{"
    r"|(?<![\w-])function\s+[\w-]+\s*\{"
    r"|(?<![\w-])if\s*\(\s*(?:\$|-not\b|!)",
    re.IGNORECASE,
)
_PS_RANGE_RE = re.compile(r"(?<![\w.])\d+\.\.\d+(?![\w.])")
_PS_ALIAS_BLOCK_RE = re.compile(r"\|\s*[%?]\s*\{")
#: A `#` comment (line start or after whitespace). cmd.exe has no `#` comment:
#: it hands `# …` to the program as arguments.
_PS_HASH_COMMENT_RE = re.compile(r"(?m)(?:^|(?<=\s))#")

_PS_STRUCTURE = (
    _PS_VERB_NOUN_RE, _PS_ASSIGN_RE, _PS_STATIC_RE, _PS_ENV_REF_RE,
    _PS_AUTOVARS_RE, _PS_ARRAY_HASH_RE, _PS_CALL_OP_RE, _PS_SEMICOLON_RE,
    _PS_KEYWORD_RE, _PS_RANGE_RE, _PS_ALIAS_BLOCK_RE,
)

# ── cmd.exe STRUCTURE (each is syntax Windows PowerShell 5.1 cannot run) ─────
#: `%NAME%` expansion — any name, not a whitelist. PowerShell passes it literally.
_CMD_ENVVAR_RE = re.compile(r"%[A-Za-z_][\w()#$\-]*%")
_CMD_FOR_RE = re.compile(r"(?<![\w-])for\s+(?:/[LFDR]\b|%%?[A-Za-z]\s+in\b)", re.IGNORECASE)
#: Infix `&` command separator (`a & b`). Excludes `&&`, the call operator
#: (statement start), and redirections such as `2>&1`.
_CMD_INFIX_AMP_RE = re.compile(r"[^\s;|{(&=<>][ \t]*&(?!&)")
#: `&&` / `||` — cmd conditional chaining; PowerShell 7-only, so on the 5.1
#: floor they are cmd syntax.
_CMD_ANDOR_RE = re.compile(r"&&|\|\|")
_CMD_NUL_RE = re.compile(r">\s*nul\b", re.IGNORECASE)
_CMD_SET_RE = re.compile(r"(?i)(?:^|[&(\s])set\s+(?:/[ap]\s+)?\"?[A-Za-z_]\w*=")
_CMD_IF_RE = re.compile(r"(?<![\w-])if\s+(?:/i\s+)?(?:not\s+)?(?:exist|errorlevel|defined)\b", re.IGNORECASE)
_CMD_MISC_RE = re.compile(
    r"(?im)(?<![\w-])(?:goto|call)\s+:\w|^\s*@?echo\s+(?:off|on)\b|^\s*rem\s|^\s*::"
)

_CMD_STRUCTURE = (
    _CMD_ENVVAR_RE, _CMD_FOR_RE, _BATCH_TIMEOUT_RE, _CMD_INFIX_AMP_RE,
    _CMD_ANDOR_RE, _CMD_NUL_RE, _CMD_SET_RE, _CMD_IF_RE, _CMD_MISC_RE,
)


def _has_ps_hash_comment(command: str) -> bool:
    """True when ``command`` carries a ``#`` comment outside string literals."""
    text = _strip_heredocs(command)
    stripped, balanced = _strip_quoted(text)
    if not balanced:
        # An apostrophe inside a `#` comment desynchronised the quote scan —
        # which itself means there IS a comment.
        return bool(_PS_HASH_COMMENT_RE.search(text))
    return bool(_PS_HASH_COMMENT_RE.search(stripped))


def infer_windows_shell(command: str) -> Dialect:
    """Classify a Windows command text as ``cmd``, ``powershell`` or ``mixed``.

    STRUCTURE, not vocabulary. The corpus' ``platform_variants['windows']``
    entries are not all PowerShell (measured: 80 PowerShell / 47 cmd), and the
    previous classifier keyed on a hand-picked list of cmdlet verbs, so a step
    like ``wevtutil cl Security & wevtutil cl System & Clear-History`` (a
    ``Clear-`` verb the list lacked) went to cmd.exe as-is.

    * Any PowerShell structure — an assignment, ``[Type]::``, an approved
      Verb-Noun, ``$env:``, a statement-start ``&``, a ``;`` separator, a ``#``
      comment, a script-block keyword — means ``powershell``.
    * Any cmd structure — ``%VAR%``, ``for /L``, an infix ``&``, ``&&``,
      ``set X=`` … — means ``cmd``.
    * BOTH means ``mixed``: it runs correctly under neither interpreter, so the
      resolver refuses the step (``MIXED_DIALECT``) instead of guessing.
    * Neither: a provably shell-neutral ``echo '…'`` is ``powershell``; any other
      bare native invocation — including ``powershell.exe -Command "…"`` — is
      ``cmd``. cmd.exe hands the double-quoted ``-Command`` string through
      literally (and expands the ``%VAR%`` an author put inside it); an OUTER
      PowerShell would interpolate every ``$x`` inside those double quotes to
      empty before the inner interpreter ever saw it.

    ``cmd.exe /c "…"`` is deliberately NOT a cmd marker: both interpreters run
    a native invocation, and SIM-EDR-017 step-03 is a PowerShell block that
    shells out with it (`$s = …; cmd.exe /c "md $s\\386354"`).
    """
    code = code_only(command)
    ps = any(rx.search(code) for rx in _PS_STRUCTURE) or _has_ps_hash_comment(command)
    cmd = any(rx.search(code) for rx in _CMD_STRUCTURE)
    if ps and cmd:
        return "mixed"
    if ps:
        return "powershell"
    if cmd:
        return "cmd"
    if is_shell_neutral(command):
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
        reasons = {reason for _, reason in self.unresolved}
        hints: list[str] = []
        if reasons - {REASON_MIXED_DIALECT} or not reasons:
            hints.append(
                "declare platform_variants.windows on those steps"
                if self.target == "windows"
                else "declare platform_variants.linux on those steps"
            )
        if REASON_MIXED_DIALECT in reasons:
            hints.append(
                "rewrite each MIXED_DIALECT step's Windows command in ONE dialect "
                "(cmd or PowerShell) — cmd `&`/%VAR% mixed with PowerShell syntax "
                "runs correctly under neither interpreter"
            )
        hint = "; ".join(hints)
        return {
            "error": "Scenario cannot emit a bundle for this target",
            "code": "BUNDLE_TARGET_UNSATISFIABLE",
            "detail": (
                f"target={self.target} scenario={self.scenario_id} "
                f"unresolved=[{steps}] — {hint}"
            ),
        }


class PushNotSupported(Exception):
    """The scenario declares ``push_supported: false`` -- no push bundle is offered.

    Distinct from :class:`BundleTargetUnsatisfiable`: that one says the CONTENT
    cannot satisfy the requested target; this one says the author withdrew push
    delivery altogether (e.g. an agent-only chain whose legs span incompatible
    targets). Same 409 envelope so callers handle both uniformly.
    """

    def __init__(self, scenario_id: str):
        self.scenario_id = scenario_id
        super().__init__(f"scenario={scenario_id} declares push_supported: false")

    def to_error(self) -> dict[str, Any]:
        return {
            "error": "Scenario does not offer a push bundle",
            "code": "PUSH_NOT_SUPPORTED",
            "detail": (
                f"scenario={self.scenario_id} declares push_supported: false — "
                "run it through an enrolled agent (pull mode) instead"
            ),
        }


def push_not_supported(scenario: dict[str, Any]) -> bool:
    """True only for an explicit ``push_supported: false`` (absent means unknown)."""
    return scenario.get("push_supported") is False


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
        if shell == "mixed":
            return (step_id, REASON_MIXED_DIALECT)
        return StepResolution(
            step_id, identity, shell, variant, "variant:windows",
            note=f"dialect inferred: {shell}",
        )
    if _declared_platforms(step) == {"windows"} and has_strong_windows_marker(command):
        shell = infer_windows_shell(command)
        if shell == "mixed":
            return (step_id, REASON_MIXED_DIALECT)
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
            dialect = infer_windows_shell(cmd)
            if dialect == "mixed":
                # Runnable under neither interpreter — named as residue, never
                # run and silently failed.
                skipped.append(cmd)
            else:
                kept.append((cmd, dialect))
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
#            Run as root (or with a configured sudoers) so the identity harness
#            can impersonate service accounts; without it, service-account steps
#            fall back to the invoking user and the causality chain is degraded.
#            Each step is per-step non-fatal — an expected non-zero exit (a
#            blocked LOLBin, a denied read) is logged, not fatal to the bundle.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/cortexsim-{safe_scenario_id}-${{TIMESTAMP}}.log"
exec > >(tee -a "${{LOG_FILE}}") 2>&1

log() {{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$1] $2"
}}

log INFO "CortexSim bundle starting — scenario={safe_scenario_id}"
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
    # Teardown is best-effort and MUST run to completion. The bundle runs under
    # `set -euo pipefail`, so without disabling errexit here the FIRST cleanup
    # command that exits non-zero — `pkill` with no match, `rm` of an absent
    # file, `docker rm` of a container that never started — would abort this
    # function and skip every later teardown, leaving attack artifacts on the
    # customer's host and never logging completion. Each command is attempted;
    # a failing teardown command is the teardown's concern, not fatal to it.
    set +e
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
            expected_lines.append(_expected_comment("#   ", det))
    expected_detections_comment = "\n".join(expected_lines) + "\n" if expected_lines else "#   (none specified)\n"

    # --- Header ---
    # Every comment-line value is _comment_safe (SEC-13): a newline in a name or
    # description must not start an uncommented line. The scenario id is also
    # used in CODE position (log path, log messages) so it goes through
    # _safe_token there — byte-identical for every corpus id.
    script = _BASH_HEADER_TMPL.format(
        scenario_id=_comment_safe(scenario_id),
        safe_scenario_id=_safe_token(scenario_id),
        name=_comment_safe(name),
        version=_comment_safe(version),
        plane=_comment_safe(plane),
        uc_ref=_comment_safe(uc_ref),
        uc_name=_comment_safe(uc_name),
        tc_ref=_comment_safe(tc_ref),
        tc_name=_comment_safe(tc_name),
        mitre_tactic=_comment_safe(mitre_tactic),
        mitre_tactic_name=_comment_safe(mitre_tactic_name),
        mitre_technique=_comment_safe(mitre_technique),
        mitre_technique_name=_comment_safe(mitre_technique_name),
        expected_detections_comment=expected_detections_comment,
        threat_report=_comment_safe(threat_report),
        author=_comment_safe(author),
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
        script += f'check_dep "{_bash_dq(dep)}"\n'
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
            script += f'log INFO "Downloading inline tool: {_bash_dq(tname)}"\n'
            script += (
                f'curl -sSLo "/tmp/{_bash_dq(tname)}" "{_bash_dq(tsource)}" '
                f'|| log WARN "Failed to download {_bash_dq(tname)}"\n'
            )
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
            adapter_lines.append(f'log WARN "Adapter {_bash_dq(ref)} not in catalog — install skipped"')
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
            # Tier-4 installs fetch from the public internet ON THE TARGET, which a
            # default-deny customer lab blocks. Emit the install UNGUARDED and a
            # blocked fetch aborts the whole bundle under `set -e` (the apt shape)
            # or leaves a later step running tool-less (the curl shape). Wrap it so
            # a failure WARNs honestly and the bundle continues; steps that need an
            # absent tool then log their own non-zero via run_as rather than
            # manufacturing a silent false negative. (Stage the tool on the payload
            # shelf, or pre-install it on the target, to remove the egress need.)
            adapter_lines.append(
                "{ " + cmd + "; } || log WARN "
                + f'"Adapter {ad.name} install failed (blocked egress or missing '
                + f'package) — steps needing {ad.name} will run tool-less and '
                + 'produce no signal"'
            )
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
        scenario_id=_safe_token(scenario_id),
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
        mitre_tech = step.get("mitre_technique", "")
        expected = step.get("expected_detections", [])

        # Resolve through the SAME function `resolve_target(..., "posix")` uses,
        # so what this bundle CONTAINS cannot disagree with what the resolver
        # says it contains. Reading `step["command"]` directly (as this did)
        # ignored `platform_variants.linux` entirely: a step whose primary is
        # Windows-shaped resolved to its clean Linux variant for the
        # emittability decision, then shipped the Windows-shaped primary in the
        # bash bundle. `generate_powershell` already resolves this way; only
        # this loop did not, and that asymmetry is the bug.
        #
        # Behaviour-preserving for every scenario whose primary is already
        # POSIX-shaped: `_resolve_step_posix` returns the primary first, by
        # design, so the golden digests are unchanged.
        resolved = _resolve_step_posix(step)
        if isinstance(resolved, StepResolution):
            identity = resolved.identity
            command = resolved.command
        else:
            # Unresolvable for POSIX. Callers are expected to have consulted
            # resolve_target and returned 409 BUNDLE_TARGET_UNSATISFIABLE, so
            # reaching here means a direct generate_bash() call on a scenario
            # that cannot satisfy this target. Emit the primary rather than
            # silently dropping the step: a visibly wrong line is debuggable, a
            # missing attack step reads in a POV as "Cortex detected nothing".
            identity = step.get("identity", "direct")
            command = step.get("command", "true")

        script += f"# --- {_comment_safe(step_id)}: {_comment_safe(step_name)}\n"
        if mitre_tech:
            script += f"# MITRE: {_comment_safe(mitre_tech)}\n"
        for det in expected:
            script += _expected_comment("# Expected: ", det) + "\n"
        # Escape EVERY value embedded in the run_as call, not just the command.
        # identity and step_id are single-quoted positional args too, and a quote
        # in either breaks out and injects a top-level command into the bundle a
        # DC runs on a customer host — invisibly, since `bash -n` stays clean.
        # These come from scenario YAML; the generator must still never emit an
        # injectable artifact.
        escaped_cmd = command.replace("'", "'\\''")
        escaped_identity = identity.replace("'", "'\\''")
        escaped_step_id = step_id.replace("'", "'\\''")
        # `|| true` is load-bearing, not sloppiness: under `set -euo pipefail` a
        # bare `run_as` call aborts the ENTIRE bundle the moment any step exits
        # non-zero — and a non-zero exit is routinely EXPECTED here (a blocked
        # LOLBin, a `grep` with no match, a denied `cat /etc/shadow` — often the
        # detection working). A partial detonation reads in a POV as "Cortex
        # detected nothing". Guarding the call also suppresses `set -e` INSIDE
        # run_as (bash disables -e in a function invoked in a tested context),
        # so the harness reaches its own per-step exit-code logging instead of
        # dying mid-step. This mirrors the PowerShell path's per-step isolation.
        script += f"run_as '{escaped_identity}' '{escaped_cmd}' '{escaped_step_id}' || true\n\n"

    # --- Footer ---
    script += _FOOTER.format(scenario_id=_safe_token(scenario_id))

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
#
# THE STEP TEXT NEVER APPEARS AS CODE IN THE BUNDLE. Each step and cleanup
# command travels as a base64 literal of its UTF-8 bytes, which the harness
# decodes at run time and writes into that step's own child script. There is
# therefore no quoting layer around author text to escape out of: not a
# double-quoted string (the bug that made every PowerShell step a no-op from
# ea5a52f until this change — see tests/engine/test_push_ps_execution.py), not
# a here-string (whose terminator a typographic quote can forge), not a
# single-quoted literal. The decoded text is ALSO printed as a `#   | ` comment
# listing above each call, so a DC reviewing the .ps1 still reads every line
# that will run.

_PS_PLACEHOLDER_RE = re.compile(r"@@([A-Z0-9_]+)@@")

#: UTF-8 BOM.  Windows PowerShell 5.1 reads a BOM-less file as ANSI, which
#: mangles the em-dashes and box characters the scenario corpus puts inside
#: string literals (e.g. SIM-EDR-017 step-04's Write-Host banner).  Mojibake in
#: a marker string breaks the very grep a DC runs to prove the step executed.
_PS_BOM = "\ufeff"

#: A step's timeout when it declares no ``timeout_seconds``.
_PS_DEFAULT_TIMEOUT = 300
#: Each cleanup command's timeout.
_PS_CLEANUP_TIMEOUT = 120


def _render(template: str, fields: dict[str, Any]) -> str:
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in fields:
            raise KeyError(f"push_generator: unbound PowerShell template field @@{key}@@")
        return str(fields[key])

    return _PS_PLACEHOLDER_RE.sub(_sub, template)


#: Every character PowerShell's tokenizer treats as a SINGLE QUOTE: ASCII plus
#: the typographic quotes U+2018..U+201B. An author pasting from a document
#: produces `don’t` — and inside '…' that ’ CLOSES the literal exactly as an
#: ASCII quote would; at the start of a line, `’@` closes a here-string.
_PS_SQ_CHARS = "'\u2018\u2019\u201a\u201b"
_PS_SQ_RE = re.compile(f"([{_PS_SQ_CHARS}])")
_PS_HERE_TERMINATOR_RE = re.compile(f"^[{_PS_SQ_CHARS}]@")
_PS_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")


def _ps_sq(text: Any) -> str:
    """A PowerShell single-quoted literal of ``text``.

    Every single-quote-class character (ASCII and U+2018..U+201B) is doubled —
    PowerShell's escape inside '…' — so no quote in ``text`` can end it.
    """
    return "'" + _PS_SQ_RE.sub(r"\1\1", str(text)) + "'"


def _ps_literal(text: str) -> str:
    """Render ``text`` as a PowerShell string literal that needs no escaping.

    Prefers a literal here-string (``@'…'@``) so authored text survives
    verbatim with no ``$`` interpolation and no backtick escaping. Falls back
    to :func:`_ps_sq` when any line would terminate the here-string early —
    which a typographic quote does too (``’@``), and which CR alone can start.

    ``generate_powershell`` no longer embeds step bodies with this (they travel
    base64-encoded); it is kept, hardened, for any literal embedding of
    multi-line text.
    """
    body = text.rstrip("\n")
    if not any(_PS_HERE_TERMINATOR_RE.match(line) for line in _PS_LINE_SPLIT_RE.split(body)):
        return "@'\n" + body + "\n'@"
    return _ps_sq(body)


def _ps_inline(text: Any, limit: int = 160) -> str:
    """Single-quoted, single-line literal for log messages."""
    flat = " ".join(_comment_safe(text).split())
    if len(flat) > limit:
        flat = flat[: limit - 3] + "..."
    return _ps_sq(flat)


def _ps_array(items: list[str]) -> str:
    """``@('a', 'b')`` — every element a single-quoted literal."""
    return "@(" + ", ".join(_ps_sq(i) for i in items) + ")"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _ps_listing(command: str) -> str:
    """The decoded command, as review-only comment lines (one per source line)."""
    lines = _PS_LINE_SPLIT_RE.split(command.rstrip("\r\n"))
    return "".join(f"#   | {_comment_safe(line)}\n" for line in lines)


# ── per-step preflight: prerequisites, operator inputs, elevation ───────────

_PS_KEYWORDS = frozenset(
    "begin break catch class continue data default define do dynamicparam else elseif end "
    "enum exit filter finally for foreach from function hidden if in inlinescript "
    "parallel param process return sequence static switch throw trap try until "
    "using var while workflow".split()
)
_CMD_BUILTINS = frozenset(
    "assoc break call cd chdir cls color copy date del dir echo endlocal erase exit "
    "for ftype goto if md mkdir mklink move path pause popd prompt pushd rd rem ren "
    "rename rmdir set setlocal shift start time title type ver verify vol".split()
)
_TOOL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\\/:-]+$")
#: A statement head that is unmistakably a command even mid-body: a cmdlet,
#: a file with an executable extension, or a path.
_EXE_EXT_RE = re.compile(r"\.(?:exe|com|bat|cmd|ps1)$", re.IGNORECASE)
_STMT_SPLIT_PS_RE = re.compile(r"\n|;|\|")
_STMT_SPLIT_CMD_RE = re.compile(r"\n|&&|\|\||&|\|")
_GET_PREREQS_RE = re.compile(r"(?<![\w-])-GetPrereqs\b", re.IGNORECASE)


def _is_tool_token(tok: str, dialect: str) -> bool:
    """Conservative: only a token that is unmistakably a command NAME.

    Over-reporting is the dangerous direction — a token wrongly judged a
    missing tool stops a step that would have run, manufacturing a
    not-executed. So variables, parameters, numbers, ranges, keywords and
    interpreter built-ins are never probed.
    """
    if not tok or not _TOOL_TOKEN_RE.match(tok):
        return False
    if tok.startswith(("-", ":")) or re.fullmatch(r"[\d.]+", tok):
        return False
    if tok.startswith(".") and not tok.startswith((".\\", "./")):
        return False
    lower = tok.lower()
    if lower in _PS_KEYWORDS and dialect != "cmd":
        return False
    if lower in _CMD_BUILTINS and dialect == "cmd":
        return False
    return lower != "echo"


def _step_prerequisites(command: str, dialect: str, adapter_binaries: list[str]) -> list[str]:
    """Binaries/cmdlets a step names, to probe with ``Get-Command`` before it runs.

    * the FIRST token of the command (the program the step is built around);
    * for cmd, every statement head (``a & b | c`` — cmd has no other grammar
      a bare word could be);
    * for PowerShell, a later statement head only when it is unmistakably a
      command — a Verb-Noun cmdlet, a file with an executable extension, or a
      path. A bare word mid-body may be a hashtable key or a ``switch`` label,
      and probing it would refuse a step that would have run;
    * any scenario adapter's ``install.binary`` the step's code names.

    Quoted text, comments and here-strings are never probed.
    """
    code = code_only(command)
    splitter = _STMT_SPLIT_CMD_RE if dialect == "cmd" else _STMT_SPLIT_PS_RE
    found: list[str] = []
    first = True
    for piece in splitter.split(code):
        piece = piece.strip()
        if dialect == "cmd":
            piece = piece.lstrip("@").strip()
        elif piece.startswith("&"):
            piece = piece[1:].strip()
        if not piece:
            continue
        tok = piece.split()[0]
        is_first, first = first, False
        if piece[len(tok):].lstrip().startswith("="):
            continue  # `Name = value` — an assignment/hashtable entry, not a command
        if not _is_tool_token(tok, dialect):
            continue
        if (
            is_first
            or dialect == "cmd"
            or _PS_VERB_NOUN_RE.fullmatch(tok)
            or _EXE_EXT_RE.search(tok)
            or "/" in tok
            or "\\" in tok
        ):
            found.append(tok)
    for binary in adapter_binaries:
        if re.search(rf"(?<![\w.-]){re.escape(binary)}(?![\w.-])", code, re.IGNORECASE):
            if _is_tool_token(binary, dialect):
                found.append(binary)
    seen: set[str] = set()
    out: list[str] = []
    for tok in found:
        if tok.lower() not in seen:
            seen.add(tok.lower())
            out.append(tok)
    return out


def _adapter_binaries(external_tools: list[dict[str, Any]]) -> list[str]:
    """Basenames of the ``install.binary`` of every adapter the scenario wires."""
    from tools.adapter_catalog import catalog as adapter_catalog  # noqa: PLC0415

    names: list[str] = []
    for tool in external_tools:
        ref = tool.get("adapter_ref") if isinstance(tool, dict) else None
        if not ref:
            continue
        ad = adapter_catalog.find(ref)
        binary = getattr(getattr(ad, "install", None), "binary", None) if ad else None
        if binary:
            base = re.split(r"[\\/]", str(binary))[-1]
            if base:
                names.append(base)
    return names


#: Variables Windows defines for every session, plus cmd's dynamic variables.
#: Referencing one is not an operator input. USERDNSDOMAIN is deliberately NOT
#: here: it exists only on a domain-joined host, which is exactly what a step
#: referencing it needs.
_SYSTEM_ENV_VARS = frozenset(
    "ALLUSERSPROFILE APPDATA CMDCMDLINE CMDEXTVERSION COMMONPROGRAMFILES "
    "COMMONPROGRAMFILES(X86) COMMONPROGRAMW6432 COMPUTERNAME COMSPEC CD DATE "
    "DRIVERDATA ERRORLEVEL HOMEDRIVE HOMEPATH LOCALAPPDATA LOGONSERVER "
    "NUMBER_OF_PROCESSORS OS PATH PATHEXT PROCESSOR_ARCHITECTURE "
    "PROCESSOR_IDENTIFIER PROCESSOR_LEVEL PROCESSOR_REVISION PROGRAMDATA "
    "PROGRAMFILES PROGRAMFILES(X86) PROGRAMW6432 PSMODULEPATH PUBLIC RANDOM "
    "SESSIONNAME SYSTEMDRIVE SYSTEMROOT TEMP TIME TMP USERDOMAIN USERNAME "
    "USERPROFILE WINDIR".split()
)
_ENV_REF_PS_RE = re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]*)|\$\{env:([^}\r\n]+)\}", re.IGNORECASE)
_ENV_REF_CMD_RE = re.compile(r"%([A-Za-z_][\w()#$\-]*)%")
_ENV_ASSIGN_PS_RE = re.compile(r"\$env:([A-Za-z_]\w*)\s*=(?!=)|\$\{env:([^}\r\n]+)\}\s*=(?!=)", re.IGNORECASE)
_ENV_ASSIGN_CMD_RE = re.compile(r"(?i)(?<![\w-])set\s+\"?([A-Za-z_]\w*)=")
_PS_SINGLE_QUOTED_RE = re.compile(r"'(?:[^']|'')*'")


def _operator_inputs(command: str) -> tuple[list[str], list[str]]:
    """Environment variables a step references → ``(required, optional)``.

    ``$env:NAME`` / ``${env:NAME}`` (outside single quotes, where PowerShell
    would not expand them) and ``%NAME%`` (which cmd expands everywhere).
    Exempt: Windows' own session variables and anything the step assigns
    itself. A reference guarded by ``if ($env:NAME)`` / ``if defined NAME`` has
    a fallback, so it is OPTIONAL — listed for the operator, never enforced:
    refusing such a step would manufacture a not-executed.
    """
    view = _PS_SINGLE_QUOTED_RE.sub(lambda m: " " * len(m.group(0)), command)
    refs = [m.group(1) or m.group(2) for m in _ENV_REF_PS_RE.finditer(view)]
    refs += [m.group(1) for m in _ENV_REF_CMD_RE.finditer(command)]
    assigned = {(m.group(1) or m.group(2)).upper() for m in _ENV_ASSIGN_PS_RE.finditer(view)}
    assigned |= {m.group(1).upper() for m in _ENV_ASSIGN_CMD_RE.finditer(command)}
    required: list[str] = []
    optional: list[str] = []
    seen: set[str] = set()
    for name in refs:
        up = name.upper()
        if up in seen or up in _SYSTEM_ENV_VARS or up in assigned:
            continue
        seen.add(up)
        esc = re.escape(name)
        guarded = re.search(
            rf"if\s*\(\s*(?:-not\s+|!\s*)?(?:\$env:{esc}\b|\$\{{env:{esc}\}})", view, re.IGNORECASE,
        ) or re.search(rf"if\s+(?:not\s+)?defined\s+{esc}\b", command, re.IGNORECASE)
        (optional if guarded else required).append(name)
    return required, optional


def _step_timeout(step: dict[str, Any]) -> int:
    raw = step.get("timeout_seconds")
    if isinstance(raw, bool):
        return _PS_DEFAULT_TIMEOUT
    if isinstance(raw, (int, float)) and raw > 0:
        return max(1, int(raw))
    if isinstance(raw, str) and raw.strip().isdigit() and int(raw) > 0:
        return int(raw)
    return _PS_DEFAULT_TIMEOUT


@dataclass(frozen=True)
class _PsStepPlan:
    index: int
    res: StepResolution
    raw: dict[str, Any]
    file_name: str
    tools: tuple[str, ...]
    inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...]
    timeout: int
    needs_elevation: bool
    network_fetch: bool


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
# Preflight — checked before EACH step. A step that fails a check is NOT run
# and is reported `not-executed` with the reason in brackets:
@@PREFLIGHT_COMMENT@@#
@@RESIDUE_COMMENT@@#
# Exit codes (this script's process exit code):
#   0  every step ran and exited 0, and every cleanup command ran clean
#   1  a step exited non-zero, threw, or hit its timeout
#   2  refused before any step ran: RESIDUE not acknowledged (see above)
#   3  a step was NOT executed (prerequisite, operator input or elevation missing)
#   4  every step ran clean but a cleanup command failed (artifacts may remain)
# Every run ends with a per-step outcome table:
#   ran-ok / ran-nonzero / not-executed / threw / timed-out.
#
# IMPORTANT: Self-contained — no SimCore dependency at runtime.
#            Windows PowerShell 5.1 floor (Server 2016+ / Windows 10+): no
#            PS7-only syntax, no Install-Module, no PSGallery, no callback to
#            SimCore. The bundle itself installs nothing: a step whose tool is
#            absent is reported not-executed [PREREQ_MISSING], never run
#            tool-less. Network fetches a step makes at run time are listed
#            under Preflight above.
#            Each step runs in its own child process with a timeout (the
#            step's timeout_seconds, else @@DEFAULT_TIMEOUT@@ s; its process tree
#            is killed on expiry) and its output is copied into the log. Step
#            and cleanup text travels base64-encoded; it is shown decoded, as
#            comments, above each call.
#
# Run with@@RUN_ELEVATED_NOTE@@:
@@RUN_WITH_COMMENT@@#
# A downloaded file carries Mark-of-the-Web. If your policy requires it, run
# `Unblock-File .\\cortexsim-@@FILE_ID@@.ps1` first — this bundle never calls
# Set-ExecutionPolicy (a machine-scope mutation that also fails unelevated).
# =============================================================================

# Warn-and-continue in the HARNESS: a step exiting non-zero is often EXPECTED
# (a blocked LOLBin is a detection, not a failure) and must not abort the run.
# Each STEP runs in its own child process under its own wrapper (below).
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

$CortexSimScenarioId    = @@SCENARIO_ID_LITERAL@@
$CortexSimStamp         = (Get-Date).ToString('yyyyMMdd_HHmmss')
$CortexSimWorkRoot      = if ($env:TEMP) { $env:TEMP } else { (Get-Location).Path }
$CortexSimLog           = Join-Path $CortexSimWorkRoot ('cortexsim-' + $CortexSimScenarioId + '-' + $CortexSimStamp + '.log')
$CortexSimStage         = Join-Path $CortexSimWorkRoot ('cortexsim-' + $CortexSimScenarioId + '-' + $CortexSimStamp + '.d')
# $IsLinux/$IsMacOS are PS6+ automatic variables; on the 5.1 floor they are
# undefined, so null and falsy, which is the Windows branch. Kept to the one
# shape scripts/ci/ps51-gate.ps1 allows: `if ($IsLinux -or $IsMacOS)`.
$CortexSimIsWindows     = $true
if ($IsLinux -or $IsMacOS) { $CortexSimIsWindows = $false }
$CortexSimTranscript    = $false
$CortexSimOutcomes      = New-Object System.Collections.ArrayList
$CortexSimCleanupFailed = $false
$CortexSimExitCode      = 1
$CortexSimPlannedSteps  = @@PLANNED_STEPS@@
$CortexSimElevatedSteps = @@ELEVATED_STEPS@@
$CortexSimResidue       = @@RESIDUE_ITEMS@@

function Write-CsLine {
    param([string]$Line)
    Write-Host $Line
    # Without a transcript the log file would otherwise not exist at all.
    if (-not $CortexSimTranscript) {
        try { Add-Content -LiteralPath $CortexSimLog -Value $Line -Encoding UTF8 -ErrorAction Stop } catch { }
    }
}

function Write-CsLog {
    param([string]$Level, [string]$Message)
    Write-CsLine ('[' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + '] [' + $Level + '] ' + $Message)
}

# Transcription is disabled by policy on some hardened hosts; degrade to
# writing the log file directly rather than aborting the simulation.
try {
    Start-Transcript -Path $CortexSimLog -Append -ErrorAction Stop | Out-Null
    $CortexSimTranscript = $true
} catch {
    Write-CsLog WARN 'Start-Transcript unavailable (policy or already running) - writing the log file directly'
}

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
# A step that declares an ELEVATED identity (root, container-runtime,
# administrator, SYSTEM) is a different case: it needs an elevated session,
# and unelevated it is NOT run at all (reason=NOT_ELEVATED) — running it
# unelevated would emit the access-denied version of the TTP and report it.
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
$CortexSimPwsh = $null
$CortexSimPwshInfo = Get-Command powershell.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($CortexSimPwshInfo) { $CortexSimPwsh = $CortexSimPwshInfo.Path }
if (-not $CortexSimPwsh) { $CortexSimPwsh = (Get-Process -Id $PID).Path }

function Test-CsElevated {
    if ($CortexSimIsWindows) {
        try {
            $wi = [Security.Principal.WindowsIdentity]::GetCurrent()
            $wp = New-Object Security.Principal.WindowsPrincipal($wi)
            return [bool]$wp.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        } catch {
            return $false
        }
    }
    # A dry run on a Linux/macOS jumpbox: root is the elevated analogue.
    try { return ([string](& id -u) -eq '0') } catch { return $false }
}

$CortexSimElevated = Test-CsElevated

function Resolve-CsIdentity {
    param([string]$Identity)
    if ([string]::IsNullOrWhiteSpace($Identity)) { return 'direct' }
    $lower = $Identity.ToLower()
    if ($lower -eq 'direct') { return 'direct' }
    # root / container-runtime / administrator / SYSTEM all assert elevation.
    if ($CortexSimDirectIdentities -contains $lower -or $lower -eq 'administrator' -or $lower -eq 'system') {
        if ($CortexSimElevated) { return 'elevated' }
        return 'not-elevated'
    }
    return 'degraded'
}

# ---------------------------------------------------------------------------
# Step wrapper — every PowerShell step runs inside it, which gives a push step
# the SAME exit semantics as the Go beacon's Windows wrapper
# (agent/executor/script.go::powerShellExitWrapper), the canonical truth table:
#   Write-Output                       -> 0
#   last native command exits 3        -> 3
#   a failing cmdlet                   -> 1
#   a native writes stderr, exits 0    -> 0
# ErrorActionPreference stays Continue (sh -c semantics: a non-fatal error
# does not abandon the rest of the step). $? and $LASTEXITCODE are snapshotted
# on the line straight after the body, because any statement overwrites $?.
# Windows PowerShell 5.1 wraps a native tool's stderr as a NativeCommandError
# when the stream is redirected - it is, here - which clears $? although the
# tool exited 0; that one shape is a success.
# ---------------------------------------------------------------------------
$CortexSimStepPrelude = @'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Error.Clear()
$global:LASTEXITCODE = $null
'@
$CortexSimStepEpilogue = @'
$__cs_ok = $?
$__cs_native = $LASTEXITCODE
if ($env:CORTEXSIM_STEP_SENTINEL) { try { [System.IO.File]::WriteAllText($env:CORTEXSIM_STEP_SENTINEL, '1') } catch { } }
if ((-not $__cs_ok) -and ($__cs_native -eq 0) -and ($Error.Count -gt 0) -and ([string]$Error[0].FullyQualifiedErrorId -like 'NativeCommandError*')) { $__cs_ok = $true }
if (($null -ne $__cs_native) -and ($__cs_native -ne 0)) { exit $__cs_native }
if (-not $__cs_ok) { exit 1 }
exit 0
'@

function Copy-CsOutput {
    param([string]$Path, [string]$Name, [string]$Stream, [string]$Encoding)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($line in @(Get-Content -LiteralPath $Path -Encoding $Encoding -ErrorAction SilentlyContinue)) {
        Write-CsLine ('    ' + $Name + ' ' + $Stream + '| ' + $line)
    }
}

function Invoke-CsProcess {
    param([string]$Name, [string]$Shell, [string]$Text, [int]$TimeoutSeconds)

    $out = Join-Path $CortexSimStage ($Name + '.stdout.txt')
    $err = Join-Path $CortexSimStage ($Name + '.stderr.txt')
    $in  = Join-Path $CortexSimStage ($Name + '.stdin.txt')
    # An empty stdin: a step that prompts (ssh, runas) reads end-of-file
    # instead of hanging the unattended bundle on the console.
    [System.IO.File]::WriteAllText($in, '')
    # Reaching the epilogue is a positive signal: it writes this file. A step
    # body that ends early (a top-level return / break / continue / exit 0)
    # skips the epilogue's exit-code check and exits 0 whatever failed, so a
    # 0 without the file is not a success.
    $sentinel = $null

    if ($Shell -eq 'cmd') {
        if ((-not $CortexSimIsWindows) -or [string]::IsNullOrEmpty($env:ComSpec)) {
            return @{ Outcome = 'not-executed'; Code = $null; Detail = 'reason=SHELL_UNAVAILABLE shell=cmd' }
        }
        $exe = $env:ComSpec
        $encoding = 'Oem'
        if ($Text.Contains([string][char]10)) {
            # Multi-line cmd text runs as a batch file: batch-file rules apply.
            $file = Join-Path $CortexSimStage ($Name + '.cmd')
            Set-Content -LiteralPath $file -Value $Text -Encoding Oem -Force
            $argLine = '/d /v:off /c "' + $file + '"'
        } else {
            # One line runs in command-line context, exactly as typed at a
            # prompt: a for-loop %i and an undefined %VAR% behave as authored.
            $argLine = '/d /v:off /s /c "' + $Text + '"'
        }
    } else {
        $file = Join-Path $CortexSimStage ($Name + '.ps1')
        $sentinel = Join-Path $CortexSimStage ($Name + '.epilogue')
        if (Test-Path -LiteralPath $sentinel) { Remove-Item -LiteralPath $sentinel -Force }
        $nl = [Environment]::NewLine
        $body = $CortexSimStepPrelude + $nl + $Text + $nl + $CortexSimStepEpilogue + $nl
        # UTF-8 WITH a BOM on 5.1 and 7 alike: 5.1 reads a BOM-less file as ANSI.
        [System.IO.File]::WriteAllText($file, $body, (New-Object System.Text.UTF8Encoding($true)))
        $exe = $CortexSimPwsh
        $encoding = 'UTF8'
        if ($CortexSimIsWindows) {
            $argLine = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $file + '"'
        } else {
            # -ExecutionPolicy is a Windows-only switch; pwsh rejects it elsewhere.
            $argLine = '-NoProfile -NonInteractive -File "' + $file + '"'
        }
    }

    $startArgs = @{
        FilePath               = $exe
        ArgumentList           = $argLine
        NoNewWindow            = $true
        PassThru               = $true
        RedirectStandardOutput = $out
        RedirectStandardError  = $err
        RedirectStandardInput  = $in
        ErrorAction            = 'Stop'
    }
    if ($sentinel) { $env:CORTEXSIM_STEP_SENTINEL = $sentinel }
    try { $p = Start-Process @startArgs }
    finally { Remove-Item -LiteralPath 'env:CORTEXSIM_STEP_SENTINEL' -ErrorAction SilentlyContinue }
    # Windows PowerShell 5.1: without a cached handle, ExitCode reads null
    # once the process has exited. Touching .Handle caches it.
    $null = $p.Handle
    if ($p.WaitForExit($TimeoutSeconds * 1000)) {
        $p.WaitForExit()
        $code = $p.ExitCode
        if ($null -eq $code) {
            $result = @{ Outcome = 'threw'; Code = $null; Detail = 'exit code unavailable' }
        } elseif ($code -eq 0 -and $sentinel -and -not (Test-Path -LiteralPath $sentinel)) {
            $result = @{ Outcome = 'threw'; Code = 0; Detail = 'reason=EPILOGUE_NOT_REACHED (a top-level return, break, continue or exit 0 ended the step before its result was read)' }
        } elseif ($code -eq 0) {
            $result = @{ Outcome = 'ran-ok'; Code = 0; Detail = '' }
        } else {
            $result = @{ Outcome = 'ran-nonzero'; Code = $code; Detail = '' }
        }
    } else {
        if ($CortexSimIsWindows) {
            # Windows has no signalable process group: kill the whole tree.
            & taskkill.exe /T /F /PID $p.Id 2>&1 | Out-Null
        } else {
            try { $p.Kill($true) } catch { try { $p.Kill() } catch { } }
        }
        try { $null = $p.WaitForExit(10000) } catch { }
        $result = @{ Outcome = 'timed-out'; Code = $null; Detail = ('after=' + $TimeoutSeconds + 's') }
    }
    Copy-CsOutput -Path $out -Name $Name -Stream 'stdout' -Encoding $encoding
    Copy-CsOutput -Path $err -Name $Name -Stream 'stderr' -Encoding $encoding
    return $result
}

function Add-CsOutcome {
    param([string]$StepId, [string]$Outcome, $Code, [string]$Detail)
    $exitText = '-'
    if ($null -ne $Code) { $exitText = [string]$Code }
    [void]$CortexSimOutcomes.Add([pscustomobject]@{ Step = $StepId; Outcome = $Outcome; Exit = $exitText; Detail = $Detail })
    Write-CsLog INFO ('cortexsim-step-result step=' + $StepId + ' outcome=' + $Outcome + ' exit=' + $exitText)
}

function Complete-CsStep {
    param([string]$StepId, $Result)
    $outcome = [string]$Result.Outcome
    $code = $Result.Code
    $detail = [string]$Result.Detail
    if ($outcome -eq 'ran-ok') {
        Write-CsLog INFO ('Step ' + $StepId + ' completed successfully (exit 0)')
    } elseif ($outcome -eq 'ran-nonzero') {
        Write-CsLog WARN ('Step ' + $StepId + ' exited ' + $code)
    } elseif ($outcome -eq 'timed-out') {
        Write-CsLog WARN ('cortexsim-step-timeout step=' + $StepId + ' ' + $detail + ' - process tree killed')
    } elseif ($outcome -eq 'not-executed') {
        Write-CsLog WARN ('cortexsim-step-not-executed step=' + $StepId + ' ' + $detail)
    } else {
        $outcome = 'threw'
        Write-CsLog WARN ('Step ' + $StepId + ' threw: ' + $detail)
    }
    Add-CsOutcome -StepId $StepId -Outcome $outcome -Code $code -Detail $detail
}

function Skip-CsStep {
    param([string]$StepId, [string]$Detail)
    Complete-CsStep -StepId $StepId -Result @{ Outcome = 'not-executed'; Code = $null; Detail = $Detail }
}

function Invoke-CsStep {
    param(
        [string]$StepId,
        [string]$FileName,
        [string]$Identity,
        [string]$Shell,
        [string]$CommandB64,
        [string[]]$Tools = @(),
        [string[]]$Inputs = @(),
        [int]$TimeoutSeconds = @@DEFAULT_TIMEOUT@@
    )

    $mode = Resolve-CsIdentity -Identity $Identity
    Write-CsLog INFO ('STEP ' + $StepId + ' identity=' + $Identity + ' shell=' + $Shell + ' mode=' + $mode + ' timeout=' + $TimeoutSeconds + 's')

    # Preflight. Each refusal is a not-executed outcome with a stable reason,
    # never a step run tool-less, input-less or unelevated and logged as a pass.
    if ($mode -eq 'not-elevated') {
        Skip-CsStep -StepId $StepId -Detail ('reason=NOT_ELEVATED declared=' + $Identity + ' - re-run from an elevated session')
        return
    }
    foreach ($inputName in $Inputs) {
        if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($inputName))) {
            Skip-CsStep -StepId $StepId -Detail ('reason=OPERATOR_INPUT_MISSING var=' + $inputName)
            return
        }
    }
    foreach ($tool in $Tools) {
        if (-not (Get-Command -Name $tool -ErrorAction SilentlyContinue)) {
            Skip-CsStep -StepId $StepId -Detail ('reason=PREREQ_MISSING tool=' + $tool)
            return
        }
    }
    if ($mode -eq 'degraded') {
        Write-CsLog WARN ('cortexsim-identity-degraded step=' + $StepId + ' declared=' + $Identity +
                          ' actual=' + [Environment]::UserName + ' reason=no-credential-free-impersonation-on-windows')
    }

    try {
        $text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($CommandB64))
        $result = Invoke-CsProcess -Name $FileName -Shell $Shell -Text $text -TimeoutSeconds $TimeoutSeconds
    } catch {
        $result = @{ Outcome = 'threw'; Code = $null; Detail = [string]$_.Exception.Message }
    }
    Complete-CsStep -StepId $StepId -Result $result
}

function Invoke-CsCleanupCommand {
    param([string]$Name, [string]$Shell, [string]$CommandB64)
    try {
        $text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($CommandB64))
        $result = Invoke-CsProcess -Name $Name -Shell $Shell -Text $text -TimeoutSeconds @@CLEANUP_TIMEOUT@@
    } catch {
        $result = @{ Outcome = 'threw'; Code = $null; Detail = [string]$_.Exception.Message }
    }
    if ($result.Outcome -eq 'ran-ok') {
        Write-CsLog INFO ($Name + ' ok')
    } else {
        $script:CortexSimCleanupFailed = $true
        Write-CsLog WARN ('cortexsim-cleanup-failed name=' + $Name + ' outcome=' + $result.Outcome + ' exit=' + $result.Code + ' ' + $result.Detail)
    }
}

function Write-CsSummary {
    $reported = @{}
    foreach ($o in $CortexSimOutcomes) { $reported[[string]$o.Step] = $true }
    foreach ($s in $CortexSimPlannedSteps) {
        if (-not $reported.ContainsKey($s)) {
            Add-CsOutcome -StepId $s -Outcome 'not-executed' -Code $null -Detail 'reason=NOT_REACHED'
        }
    }
    Write-CsLog INFO 'Step outcome summary:'
    Write-CsLine ('    ' + 'STEP'.PadRight(28) + 'OUTCOME'.PadRight(14) + 'EXIT'.PadRight(7) + 'DETAIL')
    foreach ($o in $CortexSimOutcomes) {
        Write-CsLine ('    ' + ([string]$o.Step).PadRight(28) + ([string]$o.Outcome).PadRight(14) + ([string]$o.Exit).PadRight(7) + [string]$o.Detail)
    }
}

function Get-CsExitCode {
    $notRun = 0
    $failed = 0
    foreach ($o in $CortexSimOutcomes) {
        if ($o.Outcome -eq 'not-executed') { $notRun++ }
        elseif ($o.Outcome -ne 'ran-ok') { $failed++ }
    }
    if ($notRun -gt 0) { return 3 }
    if ($failed -gt 0) { return 1 }
    if ($CortexSimCleanupFailed) { return 4 }
    return 0
}

# ---------------------------------------------------------------------------
# Dependency checks — probe only. Installing would break the cardinal
# invariant (self-contained, no network dependency, no module install). The
# per-step Get-Command preflight above is what decides whether a step runs.
# ---------------------------------------------------------------------------
function Test-CsDep {
    param([string]$Name)
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Write-CsLog INFO ('Dependency OK: ' + $Name)
    } else {
        Write-CsLog WARN ('Dependency not found: ' + $Name)
    }
}

Write-CsLog INFO ('Step interpreter: ' + $CortexSimPwsh)
Write-CsLog INFO ('Elevated session: ' + $CortexSimElevated)
if ((-not $CortexSimElevated) -and ($CortexSimElevatedSteps.Count -gt 0)) {
    Write-CsLog WARN ('Not elevated - these steps declare an elevated identity and will NOT run (reason=NOT_ELEVATED): ' + ($CortexSimElevatedSteps -join ', '))
}

# ---------------------------------------------------------------------------
# RESIDUE gate — teardown this bundle cannot perform is named up front, and
# nothing runs until the operator accepts it.
# ---------------------------------------------------------------------------
if ($CortexSimResidue.Count -gt 0) {
    Write-CsLog WARN ('RESIDUE: ' + $CortexSimResidue.Count + ' teardown command(s) of this scenario cannot run on Windows (POSIX-only, or mixed cmd and PowerShell). What they would remove is LEFT on this host:')
    foreach ($residueItem in $CortexSimResidue) { Write-CsLog WARN ('  residue: ' + $residueItem) }
    if ($env:CORTEXSIM_ACCEPT_RESIDUE -ne '1') {
        Write-CsLog ERROR 'cortexsim-refused reason=RESIDUE_NOT_ACKNOWLEDGED - no step was run. To run anyway set the environment variable CORTEXSIM_ACCEPT_RESIDUE to 1, and remove the residue above by hand afterwards.'
        if ($CortexSimTranscript) { try { Stop-Transcript | Out-Null } catch { } }
        exit 2
    }
    Write-CsLog WARN 'CORTEXSIM_ACCEPT_RESIDUE=1 - proceeding; the operator has accepted the residue above.'
}

New-Item -ItemType Directory -Force -Path $CortexSimStage -ErrorAction SilentlyContinue | Out-Null

"""


def generate_powershell(scenario: dict[str, Any]) -> str:
    """Generate a self-contained PowerShell bundle for the ``windows`` target.

    Raises :class:`BundleTargetUnsatisfiable` when any step lacks a Windows
    command, naming the offending steps.  Emitting a partial bundle would be
    worse: it would run green, emit a subset of the attack, and the POV report
    would read the gap as "the customer's stack detected nothing".

    What the emitted bundle guarantees, each proven by EXECUTION in
    tests/engine/test_push_ps_execution.py (under pwsh; the Windows-only
    behaviour is exercised by that file on a Windows host):

    * every step's text actually runs, in its own child process, with the
      beacon's exit-code semantics, a timeout and a killed process tree;
    * a step whose tool, operator input or elevation is missing is not run and
      is reported ``not-executed`` with a stable reason;
    * teardown the bundle cannot perform is named in the header and refused at
      run time until ``CORTEXSIM_ACCEPT_RESIDUE=1``; "Cleanup complete" is
      printed only when it is true;
    * the process exit code and a closing outcome table tell the truth.
    """
    scenario_id = scenario.get("scenario_id", "UNKNOWN")
    resolution = resolve_target(scenario, "windows")
    if not resolution.emittable:
        raise BundleTargetUnsatisfiable(scenario_id, "windows", resolution.unresolved)

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

    direct = [i.lower() for i in direct_identities()]
    elevated_ids = ({"administrator", "system"} | set(direct)) - {"direct"}
    safe_id = _safe_token(scenario_id)
    raw_steps = list(scenario.get("steps") or [])
    external_tools = scenario.get("external_tools") or []
    adapter_bins = _adapter_binaries(external_tools)

    # emittable ⇒ exactly one resolution per scenario step, in order.
    plans: list[_PsStepPlan] = []
    for idx, (res, raw) in enumerate(zip(resolution.steps, raw_steps), start=1):
        required, optional = _operator_inputs(res.command)
        plans.append(_PsStepPlan(
            index=idx,
            res=res,
            raw=raw if isinstance(raw, dict) else {},
            file_name=f"{idx:02d}-{_safe_token(res.step_id, 'step')[:48]}",
            tools=tuple(_step_prerequisites(res.command, res.shell, adapter_bins)),
            inputs=tuple(required),
            optional_inputs=tuple(optional),
            timeout=_step_timeout(raw if isinstance(raw, dict) else {}),
            needs_elevation=str(res.identity).lower() in elevated_ids,
            network_fetch=bool(_GET_PREREQS_RE.search(code_only(res.command))),
        ))

    # --- header blocks ---------------------------------------------------
    expected_lines = [
        _expected_comment("#   ", det)
        for step in raw_steps if isinstance(step, dict)
        for det in step.get("expected_detections", []) or []
        if isinstance(det, dict)
    ]
    expected_detections_comment = (
        "\n".join(expected_lines) + "\n" if expected_lines else "#   (none specified)\n"
    )
    step_plan_comment = "".join(
        f"#   {_comment_safe(p.res.step_id)}: {p.res.source} -> {p.res.shell}"
        + (f"  ({p.res.note})" if p.res.note else "")
        + f"  [timeout {p.timeout}s]\n"
        for p in plans
    )

    def _block(title: str, rows: list[str]) -> str:
        body = "".join(f"#     {_comment_safe(r)}\n" for r in rows) if rows else "#     (none)\n"
        return f"#   {title}\n{body}"

    inputs_by_var: dict[str, list[str]] = {}
    optional_by_var: dict[str, list[str]] = {}
    for p in plans:
        for v in p.inputs:
            inputs_by_var.setdefault(v, []).append(p.res.step_id)
        for v in p.optional_inputs:
            optional_by_var.setdefault(v, []).append(p.res.step_id)
    preflight_comment = (
        _block(
            "Prerequisites, probed with Get-Command [PREREQ_MISSING]:",
            [f"{p.res.step_id}: {', '.join(p.tools)}" for p in plans if p.tools],
        )
        + _block(
            "Required operator inputs — environment variables to set first [OPERATOR_INPUT_MISSING]:",
            [f"{v:<28} <- {', '.join(sids)}" for v, sids in inputs_by_var.items()],
        )
        + _block(
            "Optional operator inputs (the step falls back to a default when unset):",
            [f"{v:<28} <- {', '.join(sids)}" for v, sids in optional_by_var.items()],
        )
        + _block(
            "Elevation — needs an elevated (Run as Administrator) session [NOT_ELEVATED]:",
            [f"{p.res.step_id} (identity {p.res.identity})" for p in plans if p.needs_elevation],
        )
        + _block(
            "Network fetches at run time (outside the TTP itself):",
            [
                f"{p.res.step_id}: Invoke-AtomicTest -GetPrereqs downloads that test's "
                f"prerequisites from the public internet"
                for p in plans if p.network_fetch
            ],
        )
    )

    residue = list(resolution.skipped_cleanup)
    if residue:
        residue_comment = (
            "# RESIDUE — teardown this bundle CANNOT perform on Windows (POSIX-only or\n"
            "# mixed-dialect commands in the scenario's cleanup). What they would remove\n"
            "# is LEFT on the host. The bundle refuses to run any step (exit 2) until\n"
            "# you accept this by setting CORTEXSIM_ACCEPT_RESIDUE=1:\n"
            + "".join(f"#     - {_comment_safe(' '.join(c.split()))}\n" for c in residue)
        )
    else:
        residue_comment = "# RESIDUE: none — every declared cleanup command runs on Windows.\n"

    run_with = ""
    if residue:
        run_with += "#   $env:CORTEXSIM_ACCEPT_RESIDUE = '1'   # only once you accept the RESIDUE above\n"
    run_with += f"#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\cortexsim-{safe_id}.ps1\n"

    script = _render(
        _PS_HEADER_TMPL,
        {
            "SCENARIO_ID": _comment_safe(scenario_id),
            "SCENARIO_ID_LITERAL": _ps_sq(safe_id),
            "FILE_ID": safe_id,
            "NAME": _comment_safe(scenario.get("name", "")),
            "VERSION": _comment_safe(scenario.get("version", "1.0")),
            "PLANE": _comment_safe(scenario.get("plane", "")),
            "UC_REF": _comment_safe(scenario.get("uc_ref", "")),
            "UC_NAME": _comment_safe(scenario.get("uc_name", "")),
            "TC_REF": _comment_safe(scenario.get("tc_ref", "")),
            "TC_NAME": _comment_safe(scenario.get("tc_name", "")),
            "MITRE_TACTIC": _comment_safe(scenario.get("mitre_tactic", "")),
            "MITRE_TACTIC_NAME": _comment_safe(scenario.get("mitre_tactic_name", "")),
            "MITRE_TECHNIQUE": _comment_safe(scenario.get("mitre_technique", "")),
            "MITRE_TECHNIQUE_NAME": _comment_safe(scenario.get("mitre_technique_name", "")),
            "EXPECTED_DETECTIONS_COMMENT": expected_detections_comment,
            "THREAT_REPORT": _comment_safe(scenario.get("threat_report") or "N/A"),
            "AUTHOR": _comment_safe(scenario.get("author") or "CortexSim"),
            "STEP_PLAN_COMMENT": step_plan_comment,
            "PREFLIGHT_COMMENT": preflight_comment,
            "RESIDUE_COMMENT": residue_comment,
            "DEFAULT_TIMEOUT": _PS_DEFAULT_TIMEOUT,
            "RUN_ELEVATED_NOTE": (
                " (from an ELEVATED prompt — some steps need it)"
                if any(p.needs_elevation for p in plans) else ""
            ),
            "RUN_WITH_COMMENT": run_with,
            "PLANNED_STEPS": _ps_array([p.res.step_id for p in plans]),
            "ELEVATED_STEPS": _ps_array([p.res.step_id for p in plans if p.needs_elevation]),
            "RESIDUE_ITEMS": "@(" + ", ".join(_ps_inline(c, limit=240) for c in residue) + ")",
        },
    )
    script += _render(
        _PS_HARNESS_TMPL,
        {
            "DIRECT_IDENTITIES": ", ".join(_ps_sq(i) for i in direct),
            "DEFAULT_TIMEOUT": _PS_DEFAULT_TIMEOUT,
            "CLEANUP_TIMEOUT": _PS_CLEANUP_TIMEOUT,
        },
    )

    # --- Dependency probes ---
    deps = {"powershell.exe", "cmd.exe"}
    for tool in external_tools:
        if not isinstance(tool, dict) or tool.get("adapter_ref"):
            continue
        tname = tool.get("name", "")
        if tname and tool.get("type") == "binary" and not tool.get("install_inline", False):
            deps.add(str(tname))
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
        ref = tool.get("adapter_ref") if isinstance(tool, dict) else None
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
    script += "# Each command runs like a step (own child process, timeout, logged outcome).\n"
    script += "# 'Cleanup complete' is printed only when it is true.\n"
    script += "# ---------------------------------------------------------------------------\n"
    script += "function Invoke-CsCleanup {\n"
    script += "    Write-CsLog INFO ('Running cleanup for scenario=' + $CortexSimScenarioId)\n"
    for idx, (cmd, shell) in enumerate(resolution.cleanup, start=1):
        script += f"    # cleanup-{idx:02d} ({shell}):\n"
        script += "".join("    " + line + "\n" for line in _ps_listing(cmd).splitlines())
        script += (
            f"    Invoke-CsCleanupCommand -Name 'cleanup-{idx:02d}' -Shell '{shell}' "
            f"-CommandB64 '{_b64(cmd)}'\n"
        )
    if not resolution.cleanup and not residue:
        script += "    Write-CsLog INFO 'No cleanup commands defined for this scenario'\n"
    script += "    try { Remove-Item -LiteralPath $CortexSimStage -Recurse -Force -ErrorAction SilentlyContinue } catch { }\n"
    script += "    if ($CortexSimResidue.Count -gt 0) {\n"
    script += "        Write-CsLog WARN ('RESIDUE: cleanup is INCOMPLETE - ' + $CortexSimResidue.Count + ' teardown command(s) could not run on Windows. Remove what they would have removed by hand:')\n"
    script += "        foreach ($residueItem in $CortexSimResidue) { Write-CsLog WARN ('  residue: ' + $residueItem) }\n"
    script += "    }\n"
    script += "    if ($CortexSimCleanupFailed) {\n"
    script += "        Write-CsLog WARN 'cleanup is INCOMPLETE - a cleanup command failed, see cortexsim-cleanup-failed above'\n"
    script += "    }\n"
    script += "    if (($CortexSimResidue.Count -eq 0) -and (-not $CortexSimCleanupFailed)) {\n"
    script += "        Write-CsLog INFO 'Cleanup complete'\n"
    script += "    }\n"
    script += "}\n\n"

    # --- Steps, inside a script-scope try/finally ---
    script += "# ---------------------------------------------------------------------------\n"
    script += "# TTP Execution Steps\n"
    script += "# ---------------------------------------------------------------------------\n"
    script += "try {\n\n"
    for p in plans:
        res, raw = p.res, p.raw
        script += f"# --- {_comment_safe(res.step_id)}: {_comment_safe(raw.get('name', ''))}\n"
        if raw.get("mitre_technique"):
            script += f"# MITRE: {_comment_safe(raw['mitre_technique'])}\n"
        script += f"# Command source: {res.source}"
        script += f" ({res.note})\n" if res.note else "\n"
        for det in raw.get("expected_detections", []) or []:
            if isinstance(det, dict):
                script += _expected_comment("# Expected: ", det) + "\n"
        script += "# Command (runs exactly as decoded from the base64 literal below):\n"
        script += _ps_listing(res.command)
        var = f"$CsCmd_{p.index:02d}"
        # Every scenario-sourced value is a single-quoted literal with ALL
        # single-quote-class characters doubled (ASCII and U+2018..U+201B), so
        # none can close its literal and inject into the call. res.shell is a
        # fixed enum; the command itself is base64 and needs no escaping.
        script += f"{var} = '{_b64(res.command)}'\n"
        script += (
            f"Invoke-CsStep -StepId {_ps_sq(res.step_id)} -FileName '{p.file_name}' "
            f"-Identity {_ps_sq(res.identity)} -Shell '{res.shell}' -CommandB64 {var} "
            f"-Tools {_ps_array(list(p.tools))} -Inputs {_ps_array(list(p.inputs))} "
            f"-TimeoutSeconds {p.timeout}\n\n"
        )
    script += "    Write-CsLog INFO ('CortexSim bundle steps finished — scenario=' + $CortexSimScenarioId)\n"
    script += "}\n"
    script += "finally {\n"
    script += "    Invoke-CsCleanup\n"
    script += "    Write-CsSummary\n"
    script += "    $CortexSimExitCode = Get-CsExitCode\n"
    script += "    Write-CsLog INFO ('CortexSim bundle complete — scenario=' + $CortexSimScenarioId + ' exit=' + $CortexSimExitCode)\n"
    script += "    Write-CsLog INFO 'Review detections in Cortex XDR/XSIAM console.'\n"
    script += "    if ($CortexSimTranscript) { try { Stop-Transcript | Out-Null } catch { } }\n"
    script += "}\n"
    script += "exit $CortexSimExitCode\n"

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
