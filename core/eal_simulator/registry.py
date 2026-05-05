"""
Plugin registry — dynamic loader for EAL simulation plugins.

The registry imports every Python module under a configured plugins directory
(default: ``core/eal_simulator/plugins``) at startup and registers every
``BaseSimulation`` subclass it finds. New plugins are added simply by dropping
a ``.py`` file into the directory; no code changes to the registry are
required.

Lookup is case-insensitive on the plugin's ``Meta.name``.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
from pathlib import Path
from typing import Any, Iterable, Optional

from .base import BaseSimulation


logger = logging.getLogger("cortexsim.eal.registry")


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, type[BaseSimulation]] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def load_package(self, package_name: str) -> int:
        """Import every submodule of a Python package and register subclasses.

        Returns the number of plugins registered during this call.
        """
        try:
            pkg = importlib.import_module(package_name)
        except ModuleNotFoundError:
            logger.warning("plugin package %s not found", package_name)
            return 0

        before = len(self._plugins)
        for _, modname, ispkg in pkgutil.iter_modules(pkg.__path__, prefix=pkg.__name__ + "."):
            if ispkg:
                continue
            try:
                module = importlib.import_module(modname)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("plugin %s failed to import: %s", modname, exc)
                continue
            self._register_from_module(module)
        return len(self._plugins) - before

    def load_directory(self, directory: str | Path) -> int:
        """Load every ``*.py`` file inside *directory* as an isolated module.

        Used for out-of-tree plugin drops that don't sit under a Python package.
        Filenames must be valid Python identifiers (no dashes).
        """
        before = len(self._plugins)
        path = Path(directory)
        if not path.is_dir():
            logger.warning("plugin directory %s does not exist", directory)
            return 0

        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"_eal_external_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                logger.warning("could not build import spec for %s", py_file)
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("plugin %s failed to load: %s", py_file, exc)
                continue
            self._register_from_module(module)
        return len(self._plugins) - before

    def _register_from_module(self, module: Any) -> None:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseSimulation:
                continue
            if not issubclass(obj, BaseSimulation):
                continue
            # Skip imports (only register classes defined IN this module).
            if obj.__module__ != module.__name__:
                continue
            try:
                meta = obj.metadata()
            except TypeError as exc:
                logger.error(
                    "plugin %s skipped — invalid Meta: %s", obj.__name__, exc
                )
                continue
            self.register(obj, name=meta["name"])

    # ------------------------------------------------------------------
    # Direct registration (used by tests and out-of-tree plugins)
    # ------------------------------------------------------------------

    def register(self, plugin_cls: type[BaseSimulation], *, name: Optional[str] = None) -> None:
        key = (name or plugin_cls.Meta.name).lower()
        existing = self._plugins.get(key)
        if existing is not None and existing is not plugin_cls:
            logger.warning(
                "plugin %s already registered (%s) — replacing with %s",
                key,
                existing.__name__,
                plugin_cls.__name__,
            )
        self._plugins[key] = plugin_cls
        logger.info("registered plugin '%s' (%s)", key, plugin_cls.__name__)

    def unregister(self, name: str) -> None:
        self._plugins.pop(name.lower(), None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> type[BaseSimulation]:
        try:
            return self._plugins[name.lower()]
        except KeyError as exc:
            available = sorted(self._plugins)
            raise KeyError(
                f"No EAL simulator plugin named '{name}'. Available: {available}"
            ) from exc

    def has(self, name: str) -> bool:
        return name.lower() in self._plugins

    def names(self) -> list[str]:
        return sorted(self._plugins)

    def manifest(self) -> list[dict[str, Any]]:
        out = []
        for cls in self._plugins.values():
            try:
                out.append(cls.metadata())
            except TypeError:
                continue
        out.sort(key=lambda m: m["name"])
        return out

    def __iter__(self) -> Iterable[type[BaseSimulation]]:
        return iter(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)


# ---------------------------------------------------------------------------
# Default registry — populated lazily so importing this module is cheap.
# ---------------------------------------------------------------------------


_DEFAULT_REGISTRY: Optional[PluginRegistry] = None


def get_default_registry() -> PluginRegistry:
    """Return the process-wide default registry, loading built-ins on first use.

    Built-in plugins live under ``core/eal_simulator/plugins`` (an importable
    Python package). Out-of-tree plugin drops can be loaded by callers via
    ``PluginRegistry.load_directory``.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        reg = PluginRegistry()
        # Try the in-tree package first; fall back to direct path for tests
        # that import the module via spec_from_file_location.
        loaded = reg.load_package("eal_simulator.plugins")
        if loaded == 0:
            # When SimCore is not yet on sys.path we can still load directly
            # from the filesystem so tests work without monkeypatching.
            here = Path(__file__).resolve().parent / "plugins"
            reg.load_directory(here)
        _DEFAULT_REGISTRY = reg
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """Test helper — drop the cached default registry."""
    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None
