"""Loader tests — directory scan, glob, error collection."""
from __future__ import annotations

import textwrap

from cortex_prompt_attacker.loader import (
    load_probes_from_dir,
    load_probes_from_paths,
)


_PROBE_TEXT = textwrap.dedent(
    """
    schema_version: 1
    name: {name}
    type: prompt_injection
    severity: low
    owasp_id: LLM01
    prompt: hi there
    """
).strip()


def test_load_directory_recursive(probes_dir):
    sub = probes_dir / "llm01"
    sub.mkdir()
    (sub / "p1.yml").write_text(_PROBE_TEXT.format(name="p1"))
    (sub / "p2.yml").write_text(_PROBE_TEXT.format(name="p2"))
    result = load_probes_from_dir(probes_dir)
    assert result.ok
    assert {p.name for p in result.probes} == {"p1", "p2"}


def test_invalid_file_recorded_as_error(probes_dir):
    (probes_dir / "bad.yml").write_text("not: a: valid: yaml: at: all: nope:")
    result = load_probes_from_dir(probes_dir)
    assert not result.ok
    assert any("yaml" in e.message.lower() for e in result.errors)


def test_duplicate_probe_name_rejected(probes_dir):
    (probes_dir / "a.yml").write_text(_PROBE_TEXT.format(name="dup"))
    (probes_dir / "b.yml").write_text(_PROBE_TEXT.format(name="dup"))
    result = load_probes_from_dir(probes_dir)
    assert len(result.probes) == 1
    assert any("duplicate" in e.message for e in result.errors)


def test_missing_directory_returns_error(tmp_path):
    result = load_probes_from_dir(tmp_path / "nope")
    assert not result.ok


def test_load_from_explicit_paths(probes_dir):
    f1 = probes_dir / "x.yml"
    f1.write_text(_PROBE_TEXT.format(name="x"))
    result = load_probes_from_paths([str(f1)])
    assert result.ok
    assert result.probes[0].name == "x"
