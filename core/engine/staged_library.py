"""
staged_library — the extended library: the staged draft pairs, read only.

``drafts/detection-library/`` holds generated card + scenario pairs that are not
corpus content (its README states what staging means). SimCore does not load
them as scenarios: they are not launchable, not counted as coverage and not
shipped in the image. This module only READS them, so the console's Scenarios
page can list them below the corpus as the extended library with the mapping
each pair's own files carry:

  * UC/TC — the scenario file's ``uc_ref`` / ``tc_ref`` / ``tc_refs`` as
    written. A staged pair is generated without them, so they read absent.
    Nothing is inferred.
  * MITRE — the scenario file's techniques, read the same way as a corpus row.
  * Detection source — the ingestion doors its detections land on, derived by
    :func:`engine.door_resolution.scenario_readiness` exactly as for the corpus,
    against a catalog built from the staged cards (the loaded corpus catalog
    does not hold them).

An absent staging directory (the image does not ship ``drafts/``) answers
``loaded: false`` with a code: degraded, never "the extended library is empty".
A pair the manifest lists whose scenario file is missing, or a file that is not
the expected shape, raises :class:`StagedLibraryError`.
"""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

import yaml

from engine.dataset_requirements import load as load_registry
from engine.door_resolution import DoorResolutionError, scenario_readiness
from engine.ttp_catalog import TtpCatalog

STAGING_RELPATH = Path("drafts") / "detection-library"

ABSENT = "STAGED_LIBRARY_ABSENT"
REGISTRY_ABSENT = "REGISTRY_ABSENT"
DOOR_DERIVATION_FAILED = "DOOR_DERIVATION_FAILED"


class StagedLibraryError(ValueError):
    """The staged library is present but a file in it is missing or unreadable."""


def staging_dir(base_dir: str | os.PathLike) -> Path:
    return Path(base_dir) / STAGING_RELPATH


def _stamp(root: Path) -> tuple[int, ...]:
    """Changes when the manifest is regenerated or a pair is added or removed."""
    return tuple(p.stat().st_mtime_ns for p in (root / "MANIFEST.json", root / "scenarios", root / "cards")
                 if p.exists())


def read(base_dir: str | os.PathLike) -> dict[str, Any]:
    """The staged pairs under ``base_dir``, each with its scenario document and doors.

    Returns ``{"loaded": False, "code": ABSENT, ...}`` when there is no manifest.
    The answer is cached until the staging directory changes; callers must not
    mutate it.
    """
    root = staging_dir(base_dir)
    if not (root / "MANIFEST.json").is_file():
        return {
            "loaded": False, "code": ABSENT,
            "detail": f"{STAGING_RELPATH.as_posix()}/MANIFEST.json is not on this SimCore "
                      "(the image does not ship drafts/)",
            "registry_loaded": None, "pairs": [],
        }
    return _read(str(root), _stamp(root))


@functools.lru_cache(maxsize=2)
def _read(root_str: str, _stamp_key: tuple[int, ...]) -> dict[str, Any]:
    root = Path(root_str)
    manifest = _json(root / "MANIFEST.json")
    pairs = manifest.get("pairs") if isinstance(manifest, dict) else None
    if not isinstance(pairs, list):
        raise StagedLibraryError(f"{root / 'MANIFEST.json'}: 'pairs' must be a list")

    scenarios: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "scenarios").glob("*.yml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise StagedLibraryError(f"{path}: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("scenario_id"), str):
            raise StagedLibraryError(f"{path}: a staged scenario must be a mapping with a scenario_id")
        scenarios[doc["scenario_id"]] = doc

    registry = load_registry()
    catalog = TtpCatalog()
    catalog.load(str(root / "cards"))

    out: list[dict[str, Any]] = []
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get("scenario"), str):
            raise StagedLibraryError(f"{root / 'MANIFEST.json'}: a pair must name its scenario, got {pair!r}")
        doc = scenarios.get(pair["scenario"])
        if doc is None:
            raise StagedLibraryError(f"{root}: the manifest lists {pair['scenario']} but no scenario file holds it")
        # One unreadable pair is reported on that pair (doors null, with its
        # code and the reason), never as an empty source and never by blanking
        # the other pairs: the staged pairs are unreviewed generated content.
        doors: list[str] | None = None
        code: str | None = None
        detail: str | None = None
        if registry is None:
            code = REGISTRY_ABSENT
        else:
            try:
                doors = scenario_readiness(doc, set(), agent_id=None, catalog=catalog, registry=registry)["doors"]
            except DoorResolutionError as exc:
                code, detail = DOOR_DERIVATION_FAILED, str(exc)
        out.append({
            "scenario": doc,
            "card": pair.get("card"),
            "platform": pair.get("platform"),
            "source": pair.get("source"),
            "findings": list(pair.get("findings") or []),
            "doors": doors,
            "doors_code": code,
            "doors_detail": detail,
        })
    return {"loaded": True, "code": None, "detail": None, "registry_loaded": registry is not None, "pairs": out}


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagedLibraryError(f"{path}: {exc}") from exc
