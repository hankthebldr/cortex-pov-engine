"""Re-import core/ against a throwaway BASE_DIR, then put the originals back.

Some suites need ``config`` / ``database`` / ``models`` and their dependents
built against a ``tmp_path`` BASE_DIR and a known master key, so they evict
those modules from ``sys.modules`` and import them fresh. Until 2026-10-19 five
fixtures did that by hand and none of them put the originals back.

The re-imported ``config`` then outlived its test. Any core module imported
LATER in the session bound to a ``settings`` whose BASE_DIR was a dead tmp dir.
``engine.identity_spec`` is imported lazily by the push generator, so it read
``<tmp>/spec/identity_harness.json``, found nothing, and ``lru_cache``d its
hard-coded fallback for the rest of the run. 174 bash bundle digests changed
and ``test_push_generator_invariant`` went red, but only when tests/integration
or tests/security ran before tests/engine. Alphabetical order never does that,
so CI was green. A subset run reorders the suite and exposes it.

So there is one mechanism instead of five copies of the eviction loop:

* :func:`core_modules_restored` snapshots every module under ``core/``. On exit
  it puts each original back, drops any core module first imported inside the
  block (that one is bound to the throwaway config), and fixes the parent
  package attribute either way, because ``from api import xsiam`` reads the
  attribute, not ``sys.modules``.
* :func:`reimport_core` is the only sanctioned eviction, and it refuses to run
  outside that restore. An eviction with no restore IS the defect, so it is an
  error rather than a convention.

Tests reach the restore through the ``restore_core_modules`` fixture in
tests/conftest.py. The guards live in tests/test_core_reimport.py.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

CORE_DIR = Path(__file__).resolve().parent.parent / "core"
_CORE_PREFIX = str(CORE_DIR) + os.sep

# Depth of open core_modules_restored() blocks; reimport_core() needs >= 1.
_active = 0


def is_core_module(mod: object) -> bool:
    """True when ``mod`` was loaded from a file under core/."""
    path = getattr(mod, "__file__", None)
    return isinstance(path, str) and os.path.abspath(path).startswith(_CORE_PREFIX)


def _parent(name: str) -> tuple[ModuleType | None, str]:
    parent, _, child = name.rpartition(".")
    return (sys.modules.get(parent) if parent else None), child


@contextmanager
def core_modules_restored() -> Iterator[None]:
    """Leave every core/ entry in ``sys.modules`` as this block found it."""
    global _active
    before = {n: m for n, m in list(sys.modules.items()) if is_core_module(m)}
    config = sys.modules.get("config")
    if config is not None and "config" not in before:
        # A layout this helper cannot recognise would make the restore a silent
        # no-op, which is the original defect wearing a fix's clothes.
        raise RuntimeError(
            f"config was imported from {getattr(config, '__file__', None)!r}, "
            f"outside {CORE_DIR}; core_modules_restored() cannot see it"
        )
    _active += 1
    try:
        yield
    finally:
        _active -= 1
        for name, mod in before.items():
            if sys.modules.get(name) is not mod:
                sys.modules[name] = mod
                pkg, child = _parent(name)
                if pkg is not None:
                    setattr(pkg, child, mod)
        added = [n for n, m in list(sys.modules.items()) if n not in before and is_core_module(m)]
        for name in added:
            mod = sys.modules.pop(name)
            pkg, child = _parent(name)
            if pkg is not None and getattr(pkg, child, None) is mod:
                delattr(pkg, child)


def reimport_core(*names: str, prefixes: tuple[str, ...] = ()) -> None:
    """Evict ``names`` and every module starting with one of ``prefixes``.

    The next import rebuilds them against the current environment. Only legal
    inside :func:`core_modules_restored`, which puts the originals back.
    """
    if not _active:
        raise RuntimeError(
            "reimport_core() outside core_modules_restored(): the re-imported "
            "modules would outlive this test and every later import would bind "
            "to them. Request the restore_core_modules fixture."
        )
    victims = set(names) | {m for m in list(sys.modules) if m.startswith(prefixes)}
    evicted = {n: sys.modules.pop(n) for n in victims if n in sys.modules}
    # A parent package that stays keeps the old child as an attribute, and
    # ``from api import xsiam`` would return it without re-importing.
    for name, mod in evicted.items():
        pkg, child = _parent(name)
        if pkg is not None and getattr(pkg, child, None) is mod:
            delattr(pkg, child)
