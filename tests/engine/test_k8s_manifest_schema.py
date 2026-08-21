"""Scenario-loader side of the K8s delivery contract.

``cluster_posture`` is optional and additive: the whole corpus must keep loading
without it. Where it IS declared, the graph label (``cgo_anchor.image_name``)
and the runtime fact (``cluster_posture.app_identity.name``) are made
structurally incapable of drifting.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from engine.scenario_loader import (
    ScenarioSchema,
    _find_yaml_files,
    _parse_and_validate,
    _schema_to_orm_kwargs,
)


@pytest.fixture
def base_scenario(repo_root: Path) -> dict:
    # SIM-CDR-004 now ships a `cluster_posture` block (the wildcard-RBAC lateral-
    # movement fixture — the docs §9 worked example). This fixture wants a
    # posture-FREE base so the optional/back-fill/S-17 tests exercise the empty
    # and author-supplied paths, so the on-disk block is stripped here; the
    # pre-existing cgo_anchor is left intact for the S-17 and back-fill tests.
    data = yaml.safe_load(
        (repo_root / "scenarios" / "cdr" / "cdr-004-k8s-lateral.yml").read_text()
    )
    data.pop("cluster_posture", None)
    return data


def sc(base: dict, **over) -> dict:
    d = copy.deepcopy(base)
    d.update(over)
    return d


# ---------------------------------------------------------------------------
# back-compat
# ---------------------------------------------------------------------------


def test_whole_corpus_still_loads(repo_root: Path) -> None:
    files = _find_yaml_files(str(repo_root / "scenarios"))
    rejected = []
    ok = 0
    for f in files:
        schema, err = _parse_and_validate(f)
        if err:
            rejected.append((f, err))
        else:
            ok += 1
    assert not rejected, f"cluster_posture broke {len(rejected)} scenarios: {rejected[:3]}"
    assert ok >= 169, f"only {ok} scenarios loaded"


def test_posture_is_optional(base_scenario: dict) -> None:
    s = ScenarioSchema(**copy.deepcopy(base_scenario))
    assert s.cluster_posture is None


def test_orm_kwargs_omit_posture_when_absent(base_scenario: dict) -> None:
    """A scenario without the block must not start writing a NULL to a column
    that may not exist yet on the ORM."""
    s = ScenarioSchema(**copy.deepcopy(base_scenario))
    assert "cluster_posture" not in _schema_to_orm_kwargs(s)


def test_orm_kwargs_are_json_serialisable_when_present(base_scenario: dict) -> None:
    import json  # noqa: PLC0415

    from models import Scenario  # noqa: PLC0415

    s = ScenarioSchema(**sc(
        base_scenario,
        # The corpus scenario already declares cgo_anchor: containerd-shim, so
        # naming a different app_identity here would (correctly) trip S-17.
        cgo_anchor={"image_name": "ci-runner", "primary_username": "root"},
        cluster_posture={"workload": "deployment", "app_identity": {"name": "ci-runner"}},
    ))
    kwargs = _schema_to_orm_kwargs(s)
    if hasattr(Scenario, "cluster_posture"):
        json.dumps(kwargs["cluster_posture"])  # must round-trip through a JSON column
    else:
        # The column is owned by another track; the guard keeps this loader
        # landable on its own.
        assert "cluster_posture" not in kwargs


# ---------------------------------------------------------------------------
# S-17 · the graph label and the runtime fact cannot drift
# ---------------------------------------------------------------------------


def test_s17_rejects_a_mismatch(base_scenario: dict) -> None:
    with pytest.raises(ValidationError, match="S-17"):
        ScenarioSchema(**sc(
            base_scenario,
            cgo_anchor={"image_name": "ci-runner"},
            cluster_posture={"app_identity": {"name": "other-name"}},
        ))


def test_s17_accepts_agreement(base_scenario: dict) -> None:
    s = ScenarioSchema(**sc(
        base_scenario,
        cgo_anchor={"image_name": "ci-runner", "primary_username": "root"},
        cluster_posture={"app_identity": {"name": "ci-runner"}},
    ))
    assert s.cgo_anchor.image_name == s.cluster_posture.app_identity.name


def test_back_fill_app_identity_from_cgo_anchor(base_scenario: dict) -> None:
    s = ScenarioSchema(**sc(
        base_scenario,
        cgo_anchor={"image_name": "ci-runner", "primary_username": "root"},
        cluster_posture={"workload": "job"},
    ))
    assert s.cluster_posture.app_identity.name == "ci-runner"


def test_back_fill_cgo_anchor_from_app_identity(base_scenario: dict) -> None:
    d = copy.deepcopy(base_scenario)
    d.pop("cgo_anchor", None)
    d["cluster_posture"] = {"app_identity": {"name": "ci-runner"}}
    s = ScenarioSchema(**d)
    assert s.cgo_anchor.image_name == "ci-runner"
    assert s.cgo_anchor.primary_username == "root"


def test_neither_declared_is_fine(base_scenario: dict) -> None:
    d = copy.deepcopy(base_scenario)
    d.pop("cgo_anchor", None)
    d["cluster_posture"] = {"workload": "job"}
    s = ScenarioSchema(**d)
    assert s.cluster_posture.app_identity is None
    assert s.cgo_anchor is None


# ---------------------------------------------------------------------------
# refusals reach the loader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_posture", [
    {"namespace": "kube-system"},
    {"namespace": "default"},
    {"namespace": "my-lab"},
    {"ttl_seconds": 5},
    {"host_access": {"host_port": 6443}},
    {"host_access": {"host_paths": [
        {"name": "h", "host_path": "/etc", "mount_path": "/h", "read_only": False}]}},
    {"workload": "statefulset"},
    {"libc": "uclibc"},
])
def test_loader_rejects_a_forbidden_posture(base_scenario: dict, bad_posture: dict) -> None:
    """A content commit can never smuggle a refusal past the loader."""
    with pytest.raises(ValidationError):
        ScenarioSchema(**sc(base_scenario, cluster_posture=bad_posture))
