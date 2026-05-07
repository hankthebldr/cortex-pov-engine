"""
Probe loader — reads YAML files into ``Probe`` objects.

Supports two input shapes:

  * ``load_probes_from_dir(path)``    — recursively load *.yml / *.yaml
  * ``load_probes_from_paths(globs)`` — explicit shell-glob list

Both apply Pydantic validation; invalid files are collected into a
``LoaderResult`` rather than raised, so a CLI run can report all errors
at once.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable

import yaml
from pydantic import ValidationError

from .probes import Probe


@dataclasses.dataclass
class LoaderError:
    path: Path
    message: str


@dataclasses.dataclass
class LoaderResult:
    probes: list[Probe]
    errors: list[LoaderError]

    @property
    def ok(self) -> bool:
        return not self.errors

    def __len__(self) -> int:
        return len(self.probes)


def _parse_one(path: Path) -> tuple[Probe | None, LoaderError | None]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, LoaderError(path=path, message=f"yaml parse error: {exc}")
    except OSError as exc:
        return None, LoaderError(path=path, message=f"read error: {exc}")
    if not isinstance(raw, dict):
        return None, LoaderError(path=path, message="root must be a mapping")
    try:
        probe = Probe.model_validate(raw)
        return probe, None
    except ValidationError as exc:
        return None, LoaderError(path=path, message=f"schema invalid:\n{exc}")


def load_probes_from_dir(directory: str | Path) -> LoaderResult:
    base = Path(directory)
    if not base.is_dir():
        return LoaderResult(
            probes=[],
            errors=[LoaderError(path=base, message="directory does not exist")],
        )
    paths = sorted(
        list(base.rglob("*.yml")) + list(base.rglob("*.yaml")),
        key=lambda p: str(p),
    )
    return _load_paths(paths)


def load_probes_from_paths(paths: Iterable[str | Path]) -> LoaderResult:
    expanded: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            expanded.extend(sorted(list(p.rglob("*.yml")) + list(p.rglob("*.yaml"))))
        elif "*" in str(raw) or "?" in str(raw):
            base = Path(".") if not p.parent.parts else p.parent
            expanded.extend(sorted(base.glob(p.name)))
        elif p.is_file():
            expanded.append(p)
        else:
            # Defer the missing-file error to _parse_one so it lands in
            # LoaderResult.errors instead of raising here.
            expanded.append(p)
    return _load_paths(expanded)


def _load_paths(paths: list[Path]) -> LoaderResult:
    probes: list[Probe] = []
    errors: list[LoaderError] = []
    seen_names: dict[str, Path] = {}
    for path in paths:
        probe, err = _parse_one(path)
        if err is not None:
            errors.append(err)
            continue
        assert probe is not None
        prior = seen_names.get(probe.name)
        if prior is not None:
            errors.append(LoaderError(
                path=path,
                message=f"duplicate probe name '{probe.name}' (also in {prior})",
            ))
            continue
        seen_names[probe.name] = path
        probes.append(probe)
    return LoaderResult(probes=probes, errors=errors)
