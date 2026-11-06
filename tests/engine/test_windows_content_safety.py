"""Windows-resolved scenario content must undo what it does to the endpoint.

A beta DC runs these scenarios on a CUSTOMER's lab endpoint. Before this guard,
the Windows text the push generator resolves (and the text a Windows beacon
runs) created a SYSTEM scheduled task every 15 minutes, an auto-start service,
a local administrator with a password checked into this repo, a real
authorized_keys line and an HKCU Run key — with a cleanup block whose every
line was POSIX and was therefore skipped on Windows. Two scenarios cleared the
real Security, System and Windows PowerShell event logs, and one deleted every
Volume Shadow Copy and disabled boot recovery. None of that is recoverable by
the DC, and the event-log wipe destroys the very telemetry the POV measures.

The rule this module pins, per artifact the text creates:

  * a local user (``net user X ... /add`` / ``New-LocalUser``),
  * a service (``sc.exe create`` / ``New-Service``),
  * a scheduled task (``schtasks /create`` / ``Register-ScheduledTask``),
  * a Run / RunOnce value (``reg add ...\\Run`` / ``New-ItemProperty ...\\Run``),
  * an ``authorized_keys`` write,
  * a file under ``C:\\Windows\\Temp`` (and, for bundle-emittable scenarios, a
    literal file under ``$env:TEMP`` / ``%TEMP%``),

either the SAME step removes it after creating it, or a Windows-runnable
cleanup line names it. A bash cleanup line does not count: the Windows bundle
skips it, and the pull beacon never runs scenario cleanup at all — which is
why in-step teardown is the strong form.

And, for every Windows-shaped text anywhere in the corpus: no clearing of a
non-CortexSim event log, no shadow-copy deletion other than of the
all-zero (nonexistent) shadow id, no ``wbadmin delete``, no ``bcdedit /set``,
and no password literal from this repo.

The corpus is walked through the REAL loader (``_find_yaml_files`` +
``_parse_and_validate``) and classified with the REAL resolver
(``push_generator.resolve_target`` / ``emittable_targets``), so a scenario the
loader rejects or the resolver withdraws cannot silently escape the guard.

Nothing here executes on Windows. These are static guarantees about the text;
whether the text behaves on Windows PowerShell 5.1 is proven only by the
windows CI job.
"""
from __future__ import annotations

import fnmatch
import logging
import re
from pathlib import Path

import pytest

from engine import push_generator as pg
from engine import scenario_loader as sl

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Corpus — through the real loader
# ---------------------------------------------------------------------------

def _load_corpus() -> dict[str, dict]:
    logging.disable(logging.CRITICAL)
    try:
        out: dict[str, dict] = {}
        for path in sl._find_yaml_files(str(REPO / "scenarios")):
            schema, err = sl._parse_and_validate(path)
            if err or schema is None:
                continue
            out[schema.scenario_id] = schema.model_dump()
        return out
    finally:
        logging.disable(logging.NOTSET)


CORPUS = _load_corpus()
WINDOWS_EMITTABLE = sorted(
    sid for sid, d in CORPUS.items() if "windows" in pg.emittable_targets(d)
)


def test_the_corpus_and_the_windows_set_are_not_empty():
    """A zero is degraded, not ok: an empty walk would green every check below."""
    assert len(CORPUS) >= 150, f"loader returned only {len(CORPUS)} scenarios"
    assert len(WINDOWS_EMITTABLE) >= 8, WINDOWS_EMITTABLE


# ---------------------------------------------------------------------------
# What a Windows host runs
# ---------------------------------------------------------------------------

def _resolved_windows(d: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """(step_id, text) the Windows bundle runs, plus its Windows cleanup lines."""
    r = pg.resolve_target(d, "windows")
    return [(s.step_id, s.command) for s in r.steps], [c for c, _ in r.cleanup]


def _windows_shaped(d: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """Every Windows text in the scenario, emittable or not.

    A step's windows variant, else a primary that is Windows-native (the text a
    Windows beacon runs today, since the beacon executes ``command``). Cleanup
    lines are kept on the same rule the Windows bundle applies.
    """
    steps: list[tuple[str, str]] = []
    for st in d.get("steps") or []:
        v = (st.get("platform_variants") or {}).get("windows")
        prim = st.get("command") or ""
        if isinstance(v, str) and v.strip():
            steps.append((st["id"], v))
        elif pg.has_strong_windows_marker(prim) and not pg.is_posix_shaped(prim):
            steps.append((st["id"], prim))
    cleanup = [
        c for c in ((d.get("cleanup") or {}).get("commands") or [])
        if isinstance(c, str) and (pg.is_shell_neutral(c) or pg.has_strong_windows_marker(c))
    ]
    return steps, cleanup


# ---------------------------------------------------------------------------
# Artifact extraction
# ---------------------------------------------------------------------------

_COMMENT_LINE = re.compile(r"(?im)^\s*(?:#|::|rem\s).*$")
# Narrative: a single-quoted string with whitespace (PowerShell -Value '...'
# marker text), or a double-quoted string handed to an output cmdlet.
_NARRATIVE_DQ = re.compile(
    r"(?:Write-Host|Write-Output|Write-Error|echo|-Value|-Message)[ \t]+\"[^\"\n]*\""
    r"|(?:Write-Host|Write-Output|Write-Error|echo)[ \t]+\([^)\n]*\)",
    re.IGNORECASE,
)
_NAME = r"(?P<name>\"[^\"\n]+\"|'[^'\n]+'|[^\s\"'/;&|)]+)"


def _strip_comments(text: str) -> str:
    return _COMMENT_LINE.sub("", text)


def _single_quoted_spans(text: str) -> list[tuple[int, int]]:
    """Left-to-right pairing of '...' on one line ('' is an escaped quote)."""
    spans, i, n = [], 0, len(text)
    while i < n:
        if text[i] != "'":
            i += 1
            continue
        j = i + 1
        while j < n and text[j] != "\n":
            if text[j] == "'":
                if j + 1 < n and text[j + 1] == "'":
                    j += 2
                    continue
                break
            j += 1
        if j < n and text[j] == "'":
            spans.append((i, j + 1))
            i = j + 1
        else:
            i = j
    return spans


def _narrative_spans(text: str) -> list[tuple[int, int]]:
    spans = [(a, b) for a, b in _single_quoted_spans(text) if re.search(r"[ \t]", text[a:b])]
    return spans + [m.span() for m in _NARRATIVE_DQ.finditer(text)]


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def _unq(name: str) -> str:
    return name.strip().strip("\"'")


_CREATE = {
    "user": [
        re.compile(r"\bnet(?:\.exe)?\s+user\s+" + _NAME + r"\s+(?:\S+\s+)?/add\b", re.I),
        re.compile(r"\bNew-LocalUser\b[^\n;]*?-Name\s+" + _NAME, re.I),
    ],
    "service": [
        re.compile(r"\bsc(?:\.exe)?\s+create\s+" + _NAME, re.I),
        re.compile(r"\bNew-Service\b[^\n;]*?-Name\s+" + _NAME, re.I),
    ],
    "task": [
        re.compile(r"\bschtasks(?:\.exe)?\s+/create\b[^\n]*?/tn\s+" + _NAME, re.I),
        re.compile(r"\bRegister-ScheduledTask\b[^\n;]*?-TaskName\s+" + _NAME, re.I),
    ],
    "runkey": [
        re.compile(r"\breg(?:\.exe)?\s+add\s+\"?[^\"\n]*\\Run(?:Once)?\"?\s[^\n]*?/v\s+" + _NAME, re.I),
        re.compile(r"\b(?:New|Set)-ItemProperty\b[^\n;]*\\Run(?:Once)?\b[^\n;]*?-Name\s+" + _NAME, re.I),
    ],
}


def _teardown_res(kind: str, name: str) -> list[re.Pattern]:
    n = re.escape(_unq(name))
    q = r"[\"']?"
    if kind == "user":
        return [re.compile(rf"\bnet(?:\.exe)?\s+user\s+{q}{n}{q}\s+/del(?:ete)?\b", re.I),
                re.compile(rf"\bRemove-LocalUser\b[^\n;]*{q}{n}{q}", re.I)]
    if kind == "service":
        return [re.compile(rf"\bsc(?:\.exe)?\s+delete\s+{q}{n}{q}", re.I),
                re.compile(rf"\bRemove-Service\b[^\n;]*{q}{n}{q}", re.I),
                re.compile(rf"Win32_Service\b[^\n]*{q}{n}{q}[^\n]*\bInvoke-CimMethod\b[^\n]*-MethodName\s+Delete\b", re.I)]
    if kind == "task":
        return [re.compile(rf"\bschtasks(?:\.exe)?\s+/delete\b[^\n]*?/tn\s+{q}{n}{q}", re.I),
                re.compile(rf"\bUnregister-ScheduledTask\b[^\n;]*{q}{n}{q}", re.I)]
    if kind == "runkey":
        return [re.compile(rf"\breg(?:\.exe)?\s+delete\s+[^\n]*\\Run(?:Once)?\b[^\n]*?/v\s+{q}{n}{q}", re.I),
                re.compile(rf"\bRemove-ItemProperty\b[^\n;]*-Name\s+{q}{n}{q}", re.I)]
    raise AssertionError(kind)


_SYSTEM_TEMP = re.compile(
    r"(?:C:\\+Windows\\+Temp|\$env:(?:SystemRoot|windir)\\+Temp|%(?:SystemRoot|windir)%\\+Temp)"
    r"\\+(?P<name>[A-Za-z0-9_.\-*?]+)",
    re.I,
)
_SYSTEM_TEMP_JOIN = re.compile(
    r"Join-Path\s+\$env:(?:SystemRoot|windir)\s+[\"']Temp\\+(?P<name>[A-Za-z0-9_.\-*?]+)[\"']", re.I
)
_USER_TEMP = re.compile(r"(?:\$env:TEMP|%TEMP%|\$env:TMP|%TMP%)\\+(?P<name>[A-Za-z0-9_.\-*?]+)", re.I)
_USER_TEMP_JOIN = re.compile(
    r"Join-Path\s+\$env:(?:TEMP|TMP)\s+[\"'](?P<name>[A-Za-z0-9_.\-*?]+)", re.I
)
_REMOVAL = re.compile(r"\b(?:Remove-Item|ri|del|erase|rd|rmdir)\b", re.I)
_AK = re.compile(r"authorized_keys", re.I)
# What undoes an authorized_keys write: remove/replace the file, restore it from
# a backup, or rewrite it without the added line.
_AK_RESTORE_VERB = re.compile(
    r"\b(?:Remove-Item|Move-Item|WriteAllBytes|Where-Object)\b|-notmatch\b|\bfindstr\s+/v\b", re.I
)
# The same, plus the cmd forms a teardown may take: a cleanup line that reaches
# the bash bundle must not carry `$`, so it is cmd (`del`, or `copy /y /b` back
# from a .bak). Used only to recognise teardown, never to excuse a step's write:
# `copy /y k.pub ...\authorized_keys` in a step is still an artifact.
_AK_TEARDOWN_VERB = re.compile(
    _AK_RESTORE_VERB.pattern + r"|\b(?:del|erase)\s+/|\bcopy\s+/y\s+/b\s+\"?[^\"\s]*\.bak\b", re.I
)
_ASSIGN = re.compile(r"^\s*\$(?P<var>\w+)\s*=")


def _statements(text: str) -> list[tuple[int, str]]:
    """Split on newline / ; / && / single & (cmd) — keeping start offsets."""
    out, start = [], 0
    for m in re.finditer(r"\n|;|&&|(?<![&>|0-9])&(?!&)|\|\|", text):
        out.append((start, text[start:m.start()]))
        start = m.end()
    out.append((start, text[start:]))
    return out


def _stmt_at(stmts: list[tuple[int, str]], pos: int) -> str:
    return next((s for off, s in reversed(stmts) if off <= pos), "")


def _assigned_var(stmt: str) -> str | None:
    m = _ASSIGN.match(stmt)
    return m.group("var") if m else None


def _names(stmt: str, name: str, var: str | None) -> bool:
    if name.lower() in stmt.lower():
        return True
    return bool(var and re.search(rf"\${re.escape(var)}\b", stmt, re.I))


def _removes_file(stmt: str, name: str, var: str | None) -> bool:
    if not _REMOVAL.search(stmt):
        return False
    if _names(stmt, name, var):
        return True
    for tok in re.findall(r"[A-Za-z0-9_.\-*]*\*[A-Za-z0-9_.\-*]*", stmt):
        if fnmatch.fnmatch(name.lower(), tok.lower()):
            return True
    return False


def _artifacts(text: str, *, user_temp: bool) -> list[tuple[str, str, int, str | None]]:
    """(kind, name, end_offset, holding_var) for every artifact ``text`` creates.

    ``holding_var`` is set when the artifact's path is assigned to a variable
    (``$d = Join-Path $env:TEMP 'x'``), so a later ``Remove-Item $d`` counts.
    """
    code = _strip_comments(text)
    spans = _narrative_spans(code)
    stmts = _statements(code)
    found: list[tuple[str, str, int, str | None]] = []
    for kind, pats in _CREATE.items():
        for rx in pats:
            for m in rx.finditer(code):
                if not _in_spans(m.start(), spans):
                    found.append((kind, m.group("name"), m.end(), None))
    for m in _AK.finditer(code):
        if _in_spans(m.start(), spans):
            continue
        stmt = _stmt_at(stmts, m.start())
        if _AK_RESTORE_VERB.search(stmt):
            continue  # this mention IS the restore
        found.append(("authorized_keys", "authorized_keys", m.end(), _assigned_var(stmt)))
    file_res = [_SYSTEM_TEMP, _SYSTEM_TEMP_JOIN] + ([_USER_TEMP, _USER_TEMP_JOIN] if user_temp else [])
    for rx in file_res:
        for m in rx.finditer(code):
            if _in_spans(m.start(), spans) or re.search(r"[*?]", m.group("name")):
                continue
            stmt = _stmt_at(stmts, m.start())
            if _REMOVAL.search(stmt):
                continue  # a mention inside the removal is the teardown
            found.append(("file", m.group("name"), m.end(), _assigned_var(stmt)))
    return found


def _torn_down(kind: str, name: str, end: int, var: str | None,
               step_text: str, cleanup: list[str]) -> bool:
    """Removed later in the same step, or by one of ``cleanup``.

    The holding variable only counts inside the same step: a ``$d`` in another
    process (a cleanup line, a later step) is a different variable.
    """
    pools = [(_strip_comments(step_text)[end:], var)] + [(_strip_comments(c), None) for c in cleanup]
    for pool, v in pools:
        stmts = [s for _, s in _statements(pool)]
        if kind == "authorized_keys":
            if any(_AK_TEARDOWN_VERB.search(s) and _names(s, name, v) for s in stmts):
                return True
            continue
        if kind == "file":
            if any(_removes_file(s, name, v) for s in stmts):
                return True
            continue
        if any(rx.search(pool) for rx in _teardown_res(kind, name)):
            return True
    return False


def _teardown_gaps(steps: list[tuple[str, str]], cleanup: list[str], *, user_temp: bool) -> list[str]:
    gaps: list[str] = []
    for step_id, text in steps:
        for kind, name, end, var in _artifacts(text, user_temp=user_temp):
            if not _torn_down(kind, name, end, var, text, cleanup):
                gaps.append(f"{step_id}: {kind} {_unq(name)!r} is created and never removed on Windows")
    return gaps


# ---------------------------------------------------------------------------
# Destructive text
# ---------------------------------------------------------------------------

_ZERO_SHADOW = "{00000000-0000-0000-0000-000000000000}"
_LOG_CLEAR = [
    re.compile(r"\bwevtutil(?:\.exe)?\s+(?:cl|clear-log)\s+(?P<ch>\"[^\"\n]+\"|'[^'\n]+'|\S+)", re.I),
    re.compile(r"\bClear-EventLog\b(?:\s+-LogName)?\s+(?P<ch>\"[^\"\n]+\"|'[^'\n]+'|[^\s;]+)", re.I),
]
_ALWAYS_FORBIDDEN = [
    (re.compile(r"\bwbadmin(?:\.exe)?\s+delete\b", re.I), "wbadmin delete"),
    (re.compile(r"\bbcdedit(?:\.exe)?\s+/set\b", re.I), "bcdedit /set"),
    (re.compile(r"\bwmic(?:\.exe)?\s+shadowcopy\s+delete\b", re.I), "wmic shadowcopy delete"),
    (re.compile(r"Win32_ShadowCopy[^\n]*\|\s*Remove-(?:Wmi|Cim)", re.I), "Win32_ShadowCopy removal"),
]
_VSS_DELETE = re.compile(r"\bvssadmin(?:\.exe)?\s+delete\s+shadows\b(?P<args>[^\n;&|]*)", re.I)


def _repo_password_literals() -> set[str]:
    """Every password literal this repo checks in, so none can reach Windows text."""
    lits = {"TempP@ss123", "Summer2024"}
    pats = [
        re.compile(r"\bnet(?:\.exe)?\s+user\s+\S+\s+([^\s$%*/][^\s]*)\s+/add\b", re.I),
        re.compile(r"ConvertTo-SecureString\s+['\"]([^'\"$]{6,})['\"]", re.I),
        re.compile(r"-password\s+([A-Za-z0-9@!#%^&_]{6,})", re.I),
        re.compile(r"echo\s+'[^:'\s]+:([^'\s]{6,})'\s*\|\s*chpasswd", re.I),
    ]
    for root in (REPO / "scenarios", REPO / "infra" / "modules"):
        for p in root.rglob("*"):
            if p.suffix not in {".yml", ".yaml", ".tf", ".ps1", ".sh"} or not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for rx in pats:
                for m in rx.finditer(text):
                    lit = m.group(1)
                    if re.search(r"[A-Za-z]", lit) and re.search(r"\d", lit):
                        lits.add(lit)
    return lits


PASSWORD_LITERALS = _repo_password_literals()


def _destructive(text: str) -> list[str]:
    code = _strip_comments(text)
    spans = _narrative_spans(code)
    bad: list[str] = []
    for rx in _LOG_CLEAR:
        for m in rx.finditer(code):
            if _in_spans(m.start(), spans):
                continue
            ch = _unq(m.group("ch"))
            if not ch.lower().startswith("cortexsim"):
                bad.append(f"clears the real event log {ch!r}")
    for rx, label in _ALWAYS_FORBIDDEN:
        for m in rx.finditer(code):
            if not _in_spans(m.start(), spans):
                bad.append(label)
    for m in _VSS_DELETE.finditer(code):
        if _in_spans(m.start(), spans):
            continue
        args = m.group("args")
        if _ZERO_SHADOW not in args or re.search(r"/(?:all|for=|oldest)\b", args, re.I):
            bad.append(f"vssadmin delete shadows{args.rstrip()} (only /shadow={_ZERO_SHADOW} is safe)")
    for lit in PASSWORD_LITERALS:
        if lit in text:
            bad.append(f"carries the repo password literal {lit!r}")
    for m in re.finditer(r"\bnet(?:\.exe)?\s+user\s+\S+\s+(?P<pw>\S+)\s+/add\b", code, re.I):
        pw = m.group("pw")
        if not (pw.startswith("$") or (pw.startswith("%") and pw.endswith("%"))):
            bad.append(f"net user /add with a non-variable password {pw!r}")
    return bad


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sid", WINDOWS_EMITTABLE)
def test_windows_bundle_tears_down_every_artifact_it_creates(sid):
    steps, cleanup = _resolved_windows(CORPUS[sid])
    gaps = _teardown_gaps(steps, cleanup, user_temp=True)
    assert not gaps, f"{sid}:\n  " + "\n  ".join(gaps)


@pytest.mark.parametrize("sid", WINDOWS_EMITTABLE)
def test_windows_bundle_text_is_not_destructive(sid):
    steps, cleanup = _resolved_windows(CORPUS[sid])
    bad = [f"{s}: {b}" for s, t in steps for b in _destructive(t)]
    bad += [f"cleanup: {b}" for c in cleanup for b in _destructive(c)]
    assert not bad, f"{sid}:\n  " + "\n  ".join(bad)


#: A cleanup line its author meant for Windows: names a Windows exe, a cmdlet,
#: or a Windows environment variable.
_WINDOWS_INTENDED = re.compile(
    r"(?<![/\w.-])[\w-]+\.exe\b|(?<![\w-])(?:Get|Set|New|Remove|Stop|Unregister|Move)-[A-Z]\w+"
    r"|\$env:\w+|%\w+%",
)


@pytest.mark.parametrize("sid", WINDOWS_EMITTABLE)
def test_windows_bundle_does_not_skip_a_windows_teardown_line(sid):
    """`sc.exe delete X` / `net.exe user X /delete` carry no marker the resolver
    treats as Windows, so the bundle used to log them as "POSIX-only, skipped"
    and never run them. A teardown line written for Windows must run there."""
    r = pg.resolve_target(CORPUS[sid], "windows")
    skipped = [c for c in r.skipped_cleanup if _WINDOWS_INTENDED.search(c)]
    assert not skipped, f"{sid}: Windows teardown the bundle skips:\n  " + "\n  ".join(skipped)


def test_the_skip_detector_catches_an_unmarked_windows_teardown_line():
    """The guard above must be able to fail: `sc.exe delete` and `net.exe user
    /delete` carry no marker the resolver treats as Windows, so they are
    skipped — exactly what SIM-EDR-003's first cleanup draft did."""
    d = {
        "steps": [{"id": "s1", "platforms": ["windows"], "command": "Get-Service -Name Spooler"}],
        "cleanup": {"commands": ["sc.exe delete cortexsim-x", "net.exe user cortexsim-x /delete",
                                 "cmd.exe /c sc.exe delete cortexsim-x"]},
    }
    r = pg.resolve_target(d, "windows")
    assert [c for c in r.skipped_cleanup if _WINDOWS_INTENDED.search(c)] == [
        "sc.exe delete cortexsim-x", "net.exe user cortexsim-x /delete",
    ]


def test_no_windows_text_anywhere_in_the_corpus_is_destructive():
    """Wider than the bundle set: a windows variant on a scenario the bundle
    withdraws is still the text a Windows beacon (or a future resolver) runs."""
    bad: list[str] = []
    for sid, d in sorted(CORPUS.items()):
        steps, cleanup = _windows_shaped(d)
        bad += [f"{sid} {s}: {b}" for s, t in steps for b in _destructive(t)]
        bad += [f"{sid} cleanup: {b}" for c in cleanup for b in _destructive(c)]
    assert not bad, "\n  ".join(["destructive Windows text:"] + bad)


def test_no_windows_text_anywhere_leaves_a_system_artifact_behind():
    """Users, services, tasks, Run keys, authorized_keys and Windows\\Temp files,
    corpus-wide (the user-temp class is checked for bundle scenarios only)."""
    gaps: list[str] = []
    for sid, d in sorted(CORPUS.items()):
        steps, cleanup = _windows_shaped(d)
        gaps += [f"{sid} {g}" for g in _teardown_gaps(steps, cleanup, user_temp=False)]
    assert not gaps, "\n  ".join(["Windows artifacts with no Windows teardown:"] + gaps)


def _pull_windows_steps(d: dict) -> list[tuple[str, str]]:
    """Steps a Windows beacon runs today: the beacon executes ``command``, so a
    Windows-native primary is Windows text whatever the variants say."""
    return [
        (st["id"], st.get("command") or "")
        for st in d.get("steps") or []
        if pg.has_strong_windows_marker(st.get("command") or "")
        and not pg.is_posix_shaped(st.get("command") or "")
    ]


def test_pull_mode_windows_steps_tear_down_without_scenario_cleanup():
    """The pull beacon never runs ``cleanup.commands`` — only push bundles do —
    and SIM-MP-030 is pull-only. So for the text a Windows beacon executes,
    teardown must happen in the same step or a later step of the chain."""
    gaps: list[str] = []
    for sid, d in sorted(CORPUS.items()):
        steps = _pull_windows_steps(d)
        for i, (step_id, text) in enumerate(steps):
            later = [t for _, t in steps[i + 1:]]
            for kind, name, end, var in _artifacts(text, user_temp=True):
                if not _torn_down(kind, name, end, var, text, later):
                    gaps.append(f"{sid} {step_id}: {kind} {_unq(name)!r} outlives a pull run")
    assert not gaps, "\n  ".join(["Windows pull-mode residue:"] + gaps)


# ---------------------------------------------------------------------------
# The detector itself must be able to fail (a guard that cannot go red is not
# a guard): each base-corpus defect class, verbatim, is caught.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ('schtasks /create /tn "SystemHealthMonitor" /tr "C:\\Windows\\Temp\\beacon.exe" /sc minute /mo 15 /ru SYSTEM', "task"),
    ('sc.exe create SysHealthMon binPath= "C:\\Windows\\Temp\\beacon.exe" start= auto', "service"),
    ("net user cortexsim_testuser $pw /add", "user"),
    ('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v SysHealth /d "x" /f', "runkey"),
    ("Add-Content $env:USERPROFILE\\.ssh\\authorized_keys (Get-Content k.pub)", "authorized_keys"),
    ("Rubeus.exe kerberoast /outfile:C:\\Windows\\Temp\\tgs.txt", "file"),
])
def test_the_teardown_detector_catches_each_artifact_class(text, kind):
    gaps = _teardown_gaps([("s1", text)], [], user_temp=False)
    assert gaps and kind in gaps[0], gaps


@pytest.mark.parametrize("text,cleanup", [
    ("$t = 'cortexsim-x'; schtasks /create /tn $t /tr whoami.exe /sc once /st 23:59 /f; schtasks /delete /tn $t /f", []),
    ("sc.exe create cortexsim-x binPath= C:\\Windows\\System32\\whoami.exe", ["sc.exe delete cortexsim-x"]),
    ("Rubeus.exe kerberoast /outfile:C:\\Windows\\Temp\\cortexsim-tgs.txt; Remove-Item C:\\Windows\\Temp\\cortexsim-tgs.txt", []),
    ("Set-Content -Path $env:TEMP\\cortexsim-a.txt -Value x", ["Remove-Item -Force $env:TEMP\\cortexsim-*.txt"]),
    # The $-free cmd forms a cleanup line takes when it also reaches the bash bundle.
    ("Set-Content -Path $env:TEMP\\cortexsim-a.txt -Value x",
     ['cmd.exe /c if exist "%TEMP%\\cortexsim-a.txt" del /f /q "%TEMP%\\cortexsim-a.txt"']),
    ("Add-Content $env:USERPROFILE\\.ssh\\authorized_keys (Get-Content k.pub)",
     ['cmd.exe /c if exist "%TEMP%\\x\\authorized_keys.absent" del /f /q "%USERPROFILE%\\.ssh\\authorized_keys"']),
    ("Add-Content $env:USERPROFILE\\.ssh\\authorized_keys (Get-Content k.pub)",
     ['cmd.exe /c copy /y /b "%TEMP%\\x\\authorized_keys.bak" "%USERPROFILE%\\.ssh\\authorized_keys"']),
])
def test_the_teardown_detector_accepts_in_step_and_cleanup_teardown(text, cleanup):
    assert _teardown_gaps([("s1", text)], cleanup, user_temp=True) == []


def test_a_cmd_copy_into_authorized_keys_is_still_an_artifact():
    """The cmd teardown forms recognise teardown only; they never excuse a write."""
    gaps = _teardown_gaps([("s1", 'copy /y k.pub "%USERPROFILE%\\.ssh\\authorized_keys"')], [], user_temp=False)
    assert gaps and "authorized_keys" in gaps[0], gaps


# ---------------------------------------------------------------------------
# GUI-subsystem binaries: PowerShell does not wait for them
# ---------------------------------------------------------------------------
#
# rundll32.exe (like regsvr32, mshta, wscript) is a Windows GUI-subsystem
# executable. PowerShell's call operator returns as soon as such a process
# starts, so `& rundll32.exe comsvcs.dll, MiniDump ...` followed by
# `Test-Path $dmp` races the dump: the step can fail with "wrote no dump" and
# then the full LSASS dump lands on disk after the step (and its in-step
# delete) are gone. A pull run never executes scenario cleanup, so nothing
# removes it. cmd waits for a GUI program run from a batch file, so only the
# PowerShell dialect is checked. Waiting means `Start-Process ... -Wait`, or a
# pipe to Out-Null / Wait-Process.

_GUI_EXE = r"(?:rundll32|regsvr32|mshta|wscript)(?:\.exe)?"
_GUI_CALL = re.compile(
    rf"(?im)^[ \t]*(?:&[ \t]*)?[\"']?[^\s\"'|;]*\b{_GUI_EXE}[\"']?(?=\s|$)(?P<rest>[^\n]*)$"
)
_GUI_START = re.compile(rf"(?im)^[^\n#]*\bStart-Process\b[^\n]*\b{_GUI_EXE}\b[^\n]*$")
_WAITS = re.compile(r"\|\s*(?:Out-Null|Wait-Process)\b", re.I)


def _gui_races(text: str) -> list[str]:
    if pg.infer_windows_shell(text) != "powershell":
        return []
    code = _strip_comments(text)
    bad = [m.group(0).strip()[:120] for m in _GUI_CALL.finditer(code) if not _WAITS.search(m.group("rest"))]
    bad += [m.group(0).strip()[:120] for m in _GUI_START.finditer(code)
            if not re.search(r"(?<![\w-])-Wait\b", m.group(0), re.I)]
    return bad


def test_no_windows_text_races_a_gui_subsystem_binary():
    bad: list[str] = []
    for sid, d in sorted(CORPUS.items()):
        steps, _ = _windows_shaped(d)
        bad += [f"{sid} {s}: {line}" for s, t in steps for line in _gui_races(t)]
    assert not bad, "\n  ".join(
        ["PowerShell does not wait for these GUI-subsystem calls:"] + bad
    )


@pytest.mark.parametrize("text", [
    # SIM-EDR-006 step-02 at 7e6e91e, verbatim.
    '$lsass = (Get-Process -Name lsass).Id\n'
    '& "$env:SystemRoot\\System32\\rundll32.exe" "$env:SystemRoot\\System32\\comsvcs.dll," MiniDump $lsass $dmp full\n'
    'if (-not (Test-Path -LiteralPath $dmp)) { exit 1 }',
    "Start-Process -FilePath rundll32.exe -ArgumentList 'comsvcs.dll, MiniDump 1 x full'\nTest-Path x",
    "rundll32.exe comsvcs.dll, MiniDump 1 C:\\x.dmp full\nGet-Item C:\\x.dmp",
])
def test_the_gui_race_detector_catches_each_shape(text):
    assert _gui_races(text), text


@pytest.mark.parametrize("text", [
    "$p = Start-Process -FilePath \"$env:SystemRoot\\System32\\rundll32.exe\" "
    "-ArgumentList 'comsvcs.dll, MiniDump 1 x full' -NoNewWindow -Wait -PassThru",
    "& rundll32.exe comsvcs.dll, MiniDump 1 x full | Out-Null\nTest-Path x",
    "Set-Content -Path x -Value 'models mshta.exe proxying a remote fetch'",
    "rundll32.exe comsvcs.dll, MiniDump 1 C:\\x.dmp full",  # cmd dialect: batch waits
])
def test_the_gui_race_detector_accepts_waiting_forms(text):
    assert _gui_races(text) == []


def test_bash_cleanup_does_not_count_as_windows_teardown():
    """The MP-025/026 shape: a pwsh call wrapped in bash is skipped on Windows."""
    d = {
        "steps": [{"id": "s1", "platforms": ["windows"],
                   "command": "$bin = Join-Path $env:SystemRoot 'System32\\whoami.exe'; "
                              "sc.exe create cortexsim-x binPath= $bin"}],
        "cleanup": {"commands": [
            "command -v pwsh >/dev/null 2>&1 && pwsh -NoProfile -Command 'sc.exe delete cortexsim-x' || true",
        ]},
    }
    steps, cleanup = _windows_shaped(d)
    assert len(steps) == 1 and cleanup == []
    assert _teardown_gaps(steps, cleanup, user_temp=False)


@pytest.mark.parametrize("text", [
    "wevtutil cl Security & wevtutil cl System & Clear-History",
    'wevtutil cl Security & wevtutil cl "Windows PowerShell"',
    "vssadmin delete shadows /all /quiet; wbadmin delete catalog -quiet; bcdedit /set {default} recoveryenabled no",
    "Clear-EventLog -LogName Security",
    "net user cortexsim_testuser TempP@ss123 /add",
])
def test_the_destructive_detector_catches_each_base_defect(text):
    assert _destructive(text)


@pytest.mark.parametrize("text", [
    "wevtutil cl CortexSim-EDR004",
    f"vssadmin delete shadows /shadow={_ZERO_SHADOW} /quiet",
    "vssadmin list shadows",
    "$pw = 'Cx!' + [guid]::NewGuid().ToString('N'); net user cortexsim-x $pw /add",
    "Set-Content -Path x -Value 'models vssadmin delete shadows /all ahead of encryption'",
])
def test_the_destructive_detector_accepts_the_safe_forms(text):
    assert _destructive(text) == []
