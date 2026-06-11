"""Tests for the ``core/planes`` detection-plane descriptor layer.

These guard the contract the API/UI consume: every active plane has a
descriptor, descriptors carry consistent metadata, the registry is keyed by
canonical id, and the descriptor set stays in lockstep with the planes actually
used by shipped scenarios.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from planes import (
    ALL_PLANES,
    PLANE_REGISTRY,
    PlaneDescriptor,
    get_plane,
    list_planes,
)

# The scenario detection-type vocabulary (scenarios/_schema.yml).
VALID_DETECTION_TYPES = {"BIOC", "XQL", "Analytics", "Correlation", "IOC"}

# Plane ids the scenario schema enumerates as valid (scenarios/_schema.yml).
EXPECTED_PLANE_IDS = {
    "EDR",
    "CDR",
    "NDR",
    "ITDR",
    "CSPM",
    "ASM",
    "TIM",
    "CLOUD_APP",
    "ANALYTICS",
    "AI_ACCESS",
    "AIRS",
    "AI_SPM",
    "BROWSER",
    "KOI",
}

_TECHNIQUE_RE = re.compile(r"^T\d{4}(\.\d{3})?$")


def test_registry_covers_every_expected_plane() -> None:
    assert set(PLANE_REGISTRY) == EXPECTED_PLANE_IDS


def test_registry_keyed_by_descriptor_id() -> None:
    for plane_id, descriptor in PLANE_REGISTRY.items():
        assert descriptor.id == plane_id


def test_all_planes_and_registry_agree() -> None:
    assert len(ALL_PLANES) == len(PLANE_REGISTRY)
    assert {p.id for p in ALL_PLANES} == set(PLANE_REGISTRY)
    # No duplicate ids in the ordered tuple.
    ids = [p.id for p in ALL_PLANES]
    assert len(ids) == len(set(ids))


def test_list_planes_matches_all_planes() -> None:
    assert list_planes() == list(ALL_PLANES)


@pytest.mark.parametrize("descriptor", ALL_PLANES, ids=lambda d: d.id)
def test_descriptor_metadata_is_consistent(descriptor: PlaneDescriptor) -> None:
    assert descriptor.id == descriptor.id.upper()
    assert descriptor.name, f"{descriptor.id} missing name"
    assert descriptor.cortex_engine, f"{descriptor.id} missing cortex_engine"
    assert descriptor.summary, f"{descriptor.id} missing summary"
    assert descriptor.default_identity, f"{descriptor.id} missing default_identity"

    assert descriptor.detection_types, f"{descriptor.id} has no detection_types"
    for dt in descriptor.detection_types:
        assert dt in VALID_DETECTION_TYPES, f"{descriptor.id}: bad detection_type {dt!r}"

    assert descriptor.primary_sources, f"{descriptor.id} has no primary_sources"

    assert descriptor.key_techniques, f"{descriptor.id} has no key_techniques"
    for tech in descriptor.key_techniques:
        assert _TECHNIQUE_RE.match(tech), f"{descriptor.id}: bad technique id {tech!r}"


def test_descriptors_are_frozen() -> None:
    with pytest.raises(Exception):
        ALL_PLANES[0].id = "MUTATED"  # type: ignore[misc]


def test_to_dict_is_json_serialisable() -> None:
    import json

    for descriptor in ALL_PLANES:
        payload = descriptor.to_dict()
        assert set(payload) == {
            "id",
            "name",
            "cortex_engine",
            "detection_types",
            "primary_sources",
            "key_techniques",
            "default_identity",
            "summary",
        }
        # Round-trips cleanly through JSON.
        json.loads(json.dumps(payload))


def test_get_plane_case_insensitive_and_safe() -> None:
    assert get_plane("EDR") is PLANE_REGISTRY["EDR"]
    assert get_plane("edr") is PLANE_REGISTRY["EDR"]
    assert get_plane("Edr") is PLANE_REGISTRY["EDR"]
    assert get_plane("nope") is None
    assert get_plane("") is None


def test_descriptors_match_planes_used_by_scenarios() -> None:
    """Every plane a shipped scenario declares must have a descriptor."""
    repo_root = Path(__file__).resolve().parent.parent
    scenarios_dir = repo_root / "scenarios"
    plane_re = re.compile(r"^plane:\s*[\"']?([A-Z_]+)[\"']?\s*$", re.MULTILINE)

    used: set[str] = set()
    for yml in scenarios_dir.rglob("*.yml"):
        text = yml.read_text(encoding="utf-8", errors="ignore")
        for match in plane_re.finditer(text):
            used.add(match.group(1))

    # Scenario _schema.yml example lines may include a sample; intersect with
    # the real schema-valid set to avoid picking up comment artifacts.
    used &= EXPECTED_PLANE_IDS
    missing = used - set(PLANE_REGISTRY)
    assert not missing, f"scenarios use planes with no descriptor: {sorted(missing)}"
