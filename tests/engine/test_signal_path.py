"""engine.signal_path — the mechanism that produces a step's evidence.

The first half asserts ``spec/signal_path_cases.json``, the SAME fixture the
Composer's ``composerSignal.test.js`` asserts against the browser derivation,
so the Lanes lens and the loader's S-28 lint cannot disagree about a step.

The plugin classes in that fixture are pinned here to the live EAL registry,
in both of the ways the codebase already splits plugins, so a plugin added to
the registry without a class fails here before it reaches a lane.

The corpus tripwire pins the five counts the design spec reports.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from engine import signal_path as sp

REPO = Path(__file__).resolve().parents[2]
CASES = json.loads((REPO / "spec" / "signal_path_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["label"][:70] for c in CASES["cases"]])
def test_signal_of(case):
    sig = sp.signal_of(case["step"])
    assert {k: sig[k] for k in ("path", "runs_on", "plugins", "unplaced")} == case["expected"]


def test_paths_in_lane_order():
    assert list(sp.SIGNAL_PATHS) == CASES["paths"]


def test_plugin_sets_match_the_fixture():
    assert sorted(sp.COLLECTOR_PLUGINS) == CASES["plugins"]["collector"]
    assert sorted(sp.WIRE_PLUGINS) == CASES["plugins"]["wire"]
    assert not (sp.COLLECTOR_PLUGINS & sp.WIRE_PLUGINS)


def test_plugin_sets_match_the_live_registry_both_ways():
    """Two existing splits of the registry must agree with each other and with
    the fixture: the console's family (data_sources declared) and the params
    model (a collector_url field)."""
    from eal_simulator.registry import get_default_registry  # noqa: PLC0415

    by_family, by_param = {"collector": set(), "wire": set()}, {"collector": set(), "wire": set()}
    for m in get_default_registry().manifest():
        streamer = bool(m.get("data_sources") or m.get("data_sources_partial"))
        by_family["collector" if streamer else "wire"].add(m["name"])
        has_url = "collector_url" in ((m.get("params_schema") or {}).get("properties") or {})
        by_param["collector" if has_url else "wire"].add(m["name"])
    assert by_family == by_param
    assert by_family["collector"] == set(sp.COLLECTOR_PLUGINS)
    assert by_family["wire"] == set(sp.WIRE_PLUGINS)


def test_the_loader_reads_the_same_collector_set():
    """One definition: the loader's S-25 indicator check and the lanes read it."""
    from engine import scenario_loader  # noqa: PLC0415

    assert scenario_loader.COLLECTOR_POST_PLUGINS is sp.COLLECTOR_PLUGINS


def test_reason_names_the_unknown_plugin():
    cmd = "cat > /tmp/c.yml <<'YML'\nsteps:\n  - plugin: made_up_emitter\nYML\npython3 -m scripts.eal_simulator.cli run /tmp/c.yml"
    sig = sp.signal_of({"command": cmd})
    assert sig["unplaced"] == "unknown-plugin"
    assert "made_up_emitter" in sig["reason"]


# ---------------------------------------------------------------------------
# S-28 — an EAL run must name plugins the registry knows, all of one class
# ---------------------------------------------------------------------------


def _step(command: str) -> dict:
    return dict(id="step-01", name="n", identity="root", mitre_technique="T1071",
                command=command, expected_detections=[dict(plane="NDR", type="XQL", description="d")])


CAMPAIGN = "cat > /tmp/c.yml <<'YML'\nsteps:\n{plugins}YML\npython3 -m scripts.eal_simulator.cli run /tmp/c.yml --live"


def _campaign(*plugins: str) -> str:
    return CAMPAIGN.format(plugins="".join(f"  - plugin: {p}\n" for p in plugins))


@pytest.mark.parametrize("command,needle", [
    ("python3 -m scripts.eal_simulator.cli run /tmp/c.yml --live", "names no plugin"),
    (_campaign("made_up_emitter"), "made_up_emitter"),
    (_campaign("ngfw_eal_emitter", "c2_http_beacon"), "both classes"),
])
def test_s28_rejects_an_unreadable_eal_run(command, needle):
    from engine.scenario_loader import StepSchema  # noqa: PLC0415

    with pytest.raises(ValueError) as exc:
        StepSchema(**_step(command))
    assert "S-28" in str(exc.value)
    assert needle in str(exc.value)


@pytest.mark.parametrize("command", [
    _campaign("ngfw_eal_emitter"),
    _campaign("llm_provider_egress", "c2_http_beacon"),
    "python3 -m scripts.eal_simulator.cli describe llm_provider_egress",
    "echo hello",
])
def test_s28_accepts_a_readable_step(command):
    from engine.scenario_loader import StepSchema  # noqa: PLC0415

    StepSchema(**_step(command))


# ---------------------------------------------------------------------------
# Corpus tripwire
# ---------------------------------------------------------------------------


def _corpus_steps():
    for path in sorted((REPO / "scenarios").rglob("*.yml")):
        if path.name.startswith("_") or {"probes", "packages", "campaigns"} & set(path.parts):
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict) and "scenario_id" in doc and isinstance(doc.get("steps"), list):
            for step in doc["steps"]:
                yield doc["scenario_id"], step


def test_corpus_signal_paths():
    """TRIPWIRE: the counts the doors spec reports. A scenario edit that moves
    a step between paths moves a number here, on purpose.

    ENDPOINT was 533 on dev; the beta-readiness merge (24bbe240) carries the
    beta's 25c3ba8b, which drops 7 Windows endpoint steps a lab run cannot
    fire from SIM-EDR-013 (2), SIM-EDR-017 (1) and SIM-EDR-021 (4). Dev then
    added SIM-KOI-012's 7 endpoint steps (533 -> 540 there); the merge of dev
    abf0d808 into the beta carries both, measured 533 on the merged tree.

    COLLECTOR 89 -> 97 on 2026-11-13: SIM-CDR-030's 8 steps, every one a
    collector POST."""
    counts: dict[str, int] = {}
    unplaced = []
    for sid, step in _corpus_steps():
        sig = sp.signal_of(step)
        counts[sig["path"]] = counts.get(sig["path"], 0) + 1
        if sig["unplaced"]:
            unplaced.append((sid, step.get("id"), sig["unplaced"]))
    assert unplaced == []
    assert counts == {"ENDPOINT": 533, "COLLECTOR": 97, "WIRE": 49, "CLOUD": 41, "NONE": 111}
