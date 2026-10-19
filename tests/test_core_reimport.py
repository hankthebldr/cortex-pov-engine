"""Guards for tests/_core_reimport.py: a re-imported core/ must not outlive its test.

The defect these pin: five fixtures evicted ``config`` / ``database`` /
``models`` and friends from ``sys.modules``, re-imported them against a
``tmp_path`` BASE_DIR, and never put them back. ``engine.identity_spec``,
imported later and lazily, then bound to the dead tmp config, fell back to its
hard-coded identity list and changed 174 bash bundle digests. It surfaced only
when tests/integration or tests/security ran before tests/engine, which
alphabetical order never does, so CI was green while the suite was
order-dependent.

Three layers, because any one alone leaves a gap:

* behaviour: the restore really puts the originals back, drops newcomers and
  rebinds parent-package attributes;
* refusal: evicting core/ outside a restore is an error, not a leak;
* structure: no test file evicts ``sys.modules`` by hand, so the next copy of
  the old loop cannot land beside the helper.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from tests._core_reimport import core_modules_restored, is_core_module, reimport_core

TESTS_DIR = Path(__file__).resolve().parent


def test_the_helper_recognises_core_modules_in_this_layout():
    """If it cannot see core/, every restore is a silent no-op."""
    import config  # noqa: PLC0415

    assert is_core_module(config), config.__file__


def test_a_reimported_config_does_not_outlive_the_block(tmp_path, monkeypatch):
    import config as original  # noqa: PLC0415

    with core_modules_restored():
        monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
        reimport_core("config")
        import config as throwaway  # noqa: PLC0415

        assert throwaway is not original
        assert throwaway.settings.CORTEXSIM_BASE_DIR == str(tmp_path)

    assert sys.modules["config"] is original


def test_a_module_imported_after_the_block_binds_the_real_config(tmp_path, monkeypatch):
    """The exact failure: identity_spec, imported late, read a tmp BASE_DIR."""
    import config as original  # noqa: PLC0415

    with core_modules_restored():
        monkeypatch.setenv("CORTEXSIM_BASE_DIR", str(tmp_path))
        reimport_core("config")
        import config  # noqa: F401, PLC0415

    with core_modules_restored():
        reimport_core("engine.identity_spec")
        from engine import identity_spec  # noqa: PLC0415

        assert identity_spec.settings is original.settings
        assert os.path.isfile(identity_spec._spec_path())


def test_a_parent_package_that_stays_is_rebound_to_the_original_child():
    """_build_app evicts ``api.*`` but keeps ``api`` itself."""
    import api  # noqa: PLC0415
    import api.xsiam as original  # noqa: PLC0415

    with core_modules_restored():
        reimport_core(prefixes=("api.",))
        import api.xsiam as fresh  # noqa: PLC0415

        assert fresh is not original
        assert api.xsiam is fresh

    assert sys.modules["api.xsiam"] is original
    assert api.xsiam is original, "from api import xsiam would return the throwaway"


def test_a_core_module_first_imported_inside_the_block_is_dropped():
    """A newcomer is bound to whatever config the block had, so it cannot stay."""
    name = "engine.identity_spec"
    with core_modules_restored():  # puts back whatever existed before this test
        reimport_core(name)
        import engine  # noqa: PLC0415

        with core_modules_restored():
            from engine import identity_spec  # noqa: PLC0415

            assert sys.modules[name] is identity_spec

        assert name not in sys.modules
        assert not hasattr(engine, "identity_spec")


def test_evicting_core_outside_a_restore_is_refused():
    import config  # noqa: PLC0415

    with pytest.raises(RuntimeError, match="restore_core_modules"):
        reimport_core("config")
    assert sys.modules["config"] is config


def _hand_rolled_evictions(tree: ast.AST) -> list[int]:
    """Line numbers of ``sys.modules.pop/popitem/clear(...)`` and ``del sys.modules[...]``."""
    def is_sys_modules(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute) and node.attr == "modules"
            and isinstance(node.value, ast.Name) and node.value.id == "sys"
        )

    lines = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("pop", "popitem", "clear")
                and is_sys_modules(node.func.value)):
            lines.append(node.lineno)
        elif isinstance(node, ast.Delete):
            lines.extend(
                t.lineno for t in node.targets
                if isinstance(t, ast.Subscript) and is_sys_modules(t.value)
            )
    return lines


def test_no_test_evicts_sys_modules_by_hand():
    """Every eviction goes through reimport_core, which refuses without a restore."""
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.name == "_core_reimport.py":
            continue
        for lineno in _hand_rolled_evictions(ast.parse(path.read_text(), str(path))):
            offenders.append(f"{path.relative_to(TESTS_DIR.parent)}:{lineno}")
    assert not offenders, (
        "these evict sys.modules by hand and nothing puts the originals back; "
        "use tests._core_reimport.reimport_core with the restore_core_modules "
        "fixture instead: " + ", ".join(offenders)
    )
