"""Loader lint: a step's declared ``platforms`` must match what resolves there.

Before this guard, 100 steps in 28 scenarios declared ``windows`` while carrying
only POSIX text and no ``platform_variants.windows`` — and 11 more (SIM-MP-026,
SIM-MP-029) carried Windows primaries the Windows resolver refuses because the
step also declares ``linux``. The ``platforms`` label is what the console,
the coverage counts ("71 scenarios declare windows") and the DC read, so a
label nothing on that OS can execute is a coverage claim with no command behind
it. The loader never said so.

Three WARNINGS, never errors — boot must not fail on them:

  * ``S-28`` a step declares ``windows`` but neither its primary nor its
    windows variant resolves through ``push_generator.resolve_target(...,
    'windows')``;
  * ``S-29`` the mirror: a step declares a POSIX platform (linux, macos,
    container, k8s) but resolves for no POSIX target;
  * ``S-30`` a windows variant carries a doubled backslash outside a UNC
    prefix — single-quoted YAML keeps ``\\\\`` literally, so ``C:\\\\Windows``
    reaches ``reg.exe``/``schtasks.exe`` as typed (SIM-EDR-003, PUSH-15).
"""
from __future__ import annotations

import asyncio
import logging
import textwrap
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from engine import scenario_loader as sl

REPO = Path(__file__).resolve().parents[2]

TEMPLATE = """
scenario_id: {sid}
name: Lint fixture
version: "1.0"
status: active
plane: EDR
detection_types: [BIOC]
uc_ref: UC-1
tc_ref: TC-1
uc_name: Use Case
tc_name: Test Case
mitre_tactic: TA0006
mitre_tactic_name: Credential Access
mitre_technique: T1003
mitre_technique_name: OS Credential Dumping
execution_identity:
  default: root
  options: [root]
push_supported: true
pull_supported: true
steps:
{steps}
cleanup:
  commands: ["echo 'done'"]
"""


def _step(sid: str, command: str, platforms: list[str] | None, variants: dict | None = None) -> str:
    lines = [
        f"  - id: {sid}",
        "    name: s",
        "    command: |",
        textwrap.indent(command, "      "),
        "    identity: root",
        "    mitre_technique: T1003",
    ]
    if platforms is not None:
        lines.append(f"    platforms: {platforms!r}".replace("'", '"'))
    if variants:
        lines.append("    platform_variants:")
        for k, v in variants.items():
            lines.append(f"      {k}: |")
            lines.append(textwrap.indent(v, "        "))
    lines += [
        "    expected_detections:",
        "      - plane: EDR",
        "        type: BIOC",
        "        description: credential dumping observed",
    ]
    return "\n".join(lines)


@pytest.fixture(autouse=True)
def _relax_ref_strictness(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CORTEXSIM_STRICT_REFS", False)


@pytest.fixture
def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _init():
        from database import Base
        import models  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return Session


def _load(tmp_path, db_factory, caplog, *steps: str, sid: str = "SIM-EDR-950"):
    (tmp_path / "s.yml").write_text(TEMPLATE.format(sid=sid, steps="\n".join(steps)))

    async def _run():
        async with db_factory() as db:
            return await sl.load_scenarios(str(tmp_path), db)

    with caplog.at_level(logging.WARNING, logger=sl.logger.name):
        loaded = asyncio.run(_run())
    return loaded, [r for r in caplog.records if r.name == sl.logger.name]


def _codes(records, code):
    return [r for r in records if r.getMessage().startswith(code)]


# --- S-28 -------------------------------------------------------------------

def test_s28_warns_when_a_windows_declared_step_has_only_posix_text(tmp_path, db_factory, caplog):
    loaded, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "cat /etc/passwd | head -3", ["linux", "windows"]),
    )
    assert loaded == ["SIM-EDR-950"], "S-28 is a WARNING: the scenario must still load"
    hits = _codes(recs, "S-28")
    assert len(hits) == 1 and "step-01" in hits[0].getMessage()
    assert hits[0].levelno == logging.WARNING


def test_s28_is_silent_when_a_windows_variant_resolves(tmp_path, db_factory, caplog):
    _, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "cat /etc/passwd | head -3", ["linux", "windows"],
              {"windows": "Get-LocalUser | Select-Object -First 3"}),
    )
    assert not _codes(recs, "S-28")


def test_s28_is_silent_for_a_windows_only_step_with_a_windows_primary(tmp_path, db_factory, caplog):
    _, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "Get-Process -Name lsass | Select-Object Id", ["windows"]),
    )
    assert not _codes(recs, "S-28")
    assert not _codes(recs, "S-29")


def test_s28_fires_for_a_windows_primary_the_resolver_refuses(tmp_path, db_factory, caplog):
    """The SIM-MP-026/029 shape: Windows primary + linux variant, both declared.
    The Windows resolver only takes a primary on a windows-ONLY step."""
    _, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "Get-Process -Name lsass | Select-Object Id", ["windows", "linux"],
              {"linux": "pgrep -a sshd | head -1"}),
    )
    assert _codes(recs, "S-28")
    assert not _codes(recs, "S-29")


# --- S-29 (the POSIX mirror) -------------------------------------------------

def test_s29_warns_when_a_linux_declared_step_has_only_windows_text(tmp_path, db_factory, caplog):
    loaded, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "Get-Process -Name lsass | Select-Object Id", ["linux"]),
    )
    assert loaded == ["SIM-EDR-950"]
    hits = _codes(recs, "S-29")
    assert len(hits) == 1 and "step-01" in hits[0].getMessage()


def test_s29_is_silent_when_a_linux_variant_resolves(tmp_path, db_factory, caplog):
    _, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "Get-Process -Name lsass | Select-Object Id", ["linux", "windows"],
              {"linux": "pgrep -a sshd | head -1"}),
    )
    assert not _codes(recs, "S-29")


def test_undeclared_platforms_are_not_linted(tmp_path, db_factory, caplog):
    _, recs = _load(tmp_path, db_factory, caplog, _step("step-01", "id", None))
    assert not _codes(recs, "S-28") and not _codes(recs, "S-29")


# --- S-30 -------------------------------------------------------------------

def test_s30_warns_on_a_doubled_backslash_in_a_windows_variant(tmp_path, db_factory, caplog):
    loaded, recs = _load(
        tmp_path, db_factory, caplog,
        _step("step-01", "id", ["linux", "windows"],
              {"windows": 'reg add "HKCU\\\\Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Run" /v X /d Y /f'}),
    )
    assert loaded == ["SIM-EDR-950"]
    assert _codes(recs, "S-30")


@pytest.mark.parametrize("variant", [
    'cmd.exe /c "net use \\\\$h\\C$ /persistent:no & dir \\\\$h\\C$"',
    'Remove-Item "\\\\$t\\C$\\Users\\Public\\Music\\x"',
    "Select-String -Path $env:TEMP\\x.js -Pattern '\\.aws\\\\credentials'",
    'reg add "HKCU\\Software\\X" /v Y /d Z /f',
])
def test_s30_is_silent_for_unc_prefixes_regex_operands_and_single_backslashes(
    tmp_path, db_factory, caplog, variant
):
    _, recs = _load(tmp_path, db_factory, caplog,
                    _step("step-01", "id", ["linux", "windows"], {"windows": variant}))
    assert not _codes(recs, "S-30")


# --- the shipped corpus -----------------------------------------------------

def test_the_shipped_corpus_carries_no_platform_label_findings():
    """Every declared platform label in the corpus resolves to runnable text."""
    findings: list[str] = []
    logging.disable(logging.CRITICAL)
    try:
        for path in sl._find_yaml_files(str(REPO / "scenarios")):
            schema, err = sl._parse_and_validate(path)
            if err or schema is None:
                continue
            findings += sl.platform_label_findings(schema)
    finally:
        logging.disable(logging.NOTSET)
    assert not findings, f"{len(findings)} findings:\n  " + "\n  ".join(findings)
