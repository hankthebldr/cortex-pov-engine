"""Dataset requirement registry loader: an unrecognised shape raises."""
from __future__ import annotations

import copy

import pytest

from config import settings
from engine.dataset_requirements import (
    DatasetRequirementsError,
    Verified,
    load,
    parse_registry,
    registry_source,
)
from tests.engine._dataset_requirements_fixtures import GOOD


@pytest.fixture(autouse=True)
def _fresh_registry_cache():
    """Clear load()'s cache around every test, so a failing assertion in a test that points
    CORTEXSIM_BASE_DIR at a tmp path cannot leave that registry cached for other files."""
    load.cache_clear()
    yield
    load.cache_clear()


def test_parses_a_valid_entry():
    reg = parse_registry(GOOD, "p")
    row = reg["panw_ngfw_traffic_raw"]
    assert row.produced_by == "strata-ngfw"
    assert row.cortex_side[0].name is None
    assert row.source_side[0].verified.state == "from documentation"
    assert row.to_dict()["dataset"] == "panw_ngfw_traffic_raw"


def test_verified_needs_all_three_fields():
    assert Verified("hr", "2026-11-12", "").state == "from documentation"
    assert Verified("hr", "2026-11-12", "saw it in deathstar").state == "confirmed in a tenant"


def test_empty_source_side_valid_but_empty_cortex_side_not():
    data = copy.deepcopy(GOOD)
    data["panw_ngfw_traffic_raw"]["source_side"] = []
    assert parse_registry(data, "p")["panw_ngfw_traffic_raw"].source_side == ()
    data["panw_ngfw_traffic_raw"]["cortex_side"] = []
    with pytest.raises(DatasetRequirementsError, match="cortex_side"):
        parse_registry(data, "p")


@pytest.mark.parametrize("mutate, needle", [
    (lambda d: d["panw_ngfw_traffic_raw"].update(produced_by="strata"), "produced_by"),
    (lambda d: d["panw_ngfw_traffic_raw"]["cortex_side"][0].update(kind="pack"), "kind"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(requirement="logs"), "'logs' is not a requirement for"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(detail=" "), "detail"),
    (lambda d: d["panw_ngfw_traffic_raw"].update(extra=1), "extra"),
    (lambda d: d["panw_ngfw_traffic_raw"]["cortex_side"][0].update(name=""), "name"),
    (lambda d: d["panw_ngfw_traffic_raw"]["cortex_side"][0].update(kind=[]), "kind"),
    (lambda d: d["panw_ngfw_traffic_raw"]["cortex_side"][0].update(kind={}), "kind"),
    (lambda d: d["panw_ngfw_traffic_raw"].update(produced_by={}), "produced_by"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(requirement=[]), "requirement"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0]["verified"].update(zzz=1), "zzz"),
    (lambda d: d["panw_ngfw_traffic_raw"].update(emulated_by=[" "]), "emulated_by"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(product="okta", requirement="globalprotect_logs"), "is not a requirement for okta"),
    (lambda d: d["panw_ngfw_traffic_raw"]["source_side"][0].update(product="nope"), "product"),
])
def test_rejects_unrecognised_shape(mutate, needle):
    data = copy.deepcopy(GOOD)
    mutate(data)
    with pytest.raises(DatasetRequirementsError, match=needle):
        parse_registry(data, "p")


@pytest.mark.parametrize("root", [None, [], "x", {}])
def test_rejects_non_mapping_or_empty_root(root):
    with pytest.raises(DatasetRequirementsError):
        parse_registry(root, "p")


def test_absent_file_is_none_not_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    assert load() is None
    assert registry_source()["source"] == "absent"


def test_present_but_unparseable_raises(tmp_path, monkeypatch):
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "dataset_requirements.yml").write_text(": [")
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    with pytest.raises(DatasetRequirementsError):
        load()


def test_source_is_file_when_valid_file_present(tmp_path, monkeypatch):
    import yaml
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "dataset_requirements.yml").write_text(yaml.safe_dump(GOOD))
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    assert registry_source()["source"] == "file"


def test_source_follows_cached_load_not_disk(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    assert load() is None
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "dataset_requirements.yml").write_text(yaml.safe_dump(GOOD))
    assert registry_source()["source"] == "absent"  # what is served, not what is on disk now


_UNQUOTED_ON = """\
panw_ngfw_traffic_raw:
  produced_by: strata-ngfw
  cortex_side:
    - kind: data_source
      name: null
      verified: {by: "", on: "", how: ""}
  source_side: []
  emulated_by: []
"""


def test_unquoted_on_key_names_the_cause(tmp_path, monkeypatch):
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "dataset_requirements.yml").write_text(_UNQUOTED_ON)
    monkeypatch.setattr(settings, "CORTEXSIM_BASE_DIR", str(tmp_path))
    with pytest.raises(DatasetRequirementsError,
                       match=r"key True is not a string .*quote it .*a bare `on:` is read as a boolean"):
        load()


def test_non_string_dataset_name_at_root_raises():
    data = {1: copy.deepcopy(GOOD["panw_ngfw_traffic_raw"])}
    with pytest.raises(DatasetRequirementsError, match="key 1 is not a string"):
        parse_registry(data, "p")
