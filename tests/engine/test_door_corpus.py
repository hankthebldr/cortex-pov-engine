"""TRIPWIRE: the corpus join of expected detections to ingestion doors.

Pins the counts the ingestion-doors spec reports in §1. 1,411 / 199 / 502 were
measured on dev 8c90739 by an independent script. The per-door counts are of
the corpus as of feature/api-ingestion-doors f6fd90f, measured on 2026-11-11
with ``engine.door_resolution`` as committed in c4e52a2. A scenario, card or registry edit that moves a detection
between doors moves a number here, on purpose: update the spec with it.

Re-measured on the beta-readiness merge (24bbe240): the beta's 25c3ba8b keeps
only the Windows detections a lab run can fire in SIM-EDR-013/017/021, which
removes 8 detections (1,411 -> 1,403): 5 off ``xdr_agent`` (EDR-013 2,
EDR-021 3), 1 off ``ngfw_stitch`` (EDR-017, so 10 -> 9 scenarios) and 2
``NO_DATASET`` (EDR-013, EDR-021).

Re-pinned 2026-11-11 for SIM-KOI-012 (TTP-2026-0203): its 8 expected
detections take the total to 1,419. Seven are xdr_data XQL or correlation rows
that need the agent and nothing else (xdr_agent 502 -> 509, 103 -> 104
scenarios); the eighth is a Prevention row with no dataset (NO_DATASET 225 ->
226). No collector-route count moves.

Re-measured 2026-11-11 on the merge of dev abf0d808 into the beta, which
carries both changes above: total 1,411, xdr_agent 504 (104 scenarios),
XDR_AGENT_SOLE 503, NO_DATASET 224, ngfw_stitch 11 (9 scenarios).

Re-pinned 2026-11-13 for SIM-CDR-030 (TTP-2026-0550): its 9 expected
detections (8 XQL, 1 Correlation) sit on 8 collector steps, so all 9 take the
collector route: total 1,411 -> 1,420, collector 199 -> 208 (XQL 63 -> 71,
Correlation 21 -> 22). No door count and no underived count moves. The
scenario is collector-only, so it reads ``needs_doors`` with every door open
and with none (10 -> 11, 191 -> 192).

Re-pinned 2026-11-13 for SIM-EDR-025 step-05: its BTP Prevention row (no
dataset) became the built-in "Suspicious data encryption" ABIOC, which reads
xdr_data and needs only the agent: xdr_agent 504 -> 505 (still 104 scenarios),
XDR_AGENT_SOLE 503 -> 504, NO_DATASET 224 -> 223. Total, collector and states
unchanged.

Re-pinned 2026-11-13 for SIM-MP-038 (TTP-2026-0551), the first scenario with
``channel: eal`` steps: 10 expected detections take the total to 1,430. The 8
on its three eal steps take the collector route (collector 208 -> 216: XQL
71 -> 77, Correlation 22 -> 24). Step-01 is an agent step: its xdr_data XQL
needs the agent alone (xdr_agent 505 -> 506, 104 -> 105 scenarios;
XDR_AGENT_SOLE 504 -> 505) and its panw_ngfw_threat_raw XQL needs ngfw_threat
(6 -> 7, 2 -> 3 scenarios). No underived count moves. With every door open
the scenario reads ``partial`` (2 scorable, 8 route-blocked; partial 30 -> 31);
with none it reads ``needs_doors`` (192 -> 193).

Re-pinned 2026-11-13 for SIM-EDR-027 (TTP-2026-0553), promoted from the staged draft
library: its 7 expected detections (6 XQL, 1 Correlation) all read xdr_data on agent
steps and need the agent alone: total 1,430 -> 1,437, xdr_agent 506 -> 513
(105 -> 106 scenarios), XDR_AGENT_SOLE 505 -> 512. No collector, door or
underived count moves. With every door open it reads ``ready`` (151 -> 152);
with none, ``needs_doors`` (193 -> 194).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

from config import settings
from engine.dataset_requirements import load
from engine.door_resolution import UNDERIVED_CODES, detection_requirement, scenario_readiness
from engine.ingestion_doors import DOORS, door_key
from engine.ttp_catalog import TtpCatalog, default_corpus_dir

REPO = Path(__file__).resolve().parents[2]


def _corpus_scenarios():
    """Same walk as ``test_signal_path._corpus_steps``."""
    for path in sorted((REPO / "scenarios").rglob("*.yml")):
        if path.name.startswith("_") or {"probes", "packages", "campaigns"} & set(path.parts):
            continue
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict) and "scenario_id" in doc and isinstance(doc.get("steps"), list):
            yield doc


@pytest.fixture(scope="module")
def corpus():
    load.cache_clear()
    cat = TtpCatalog()
    assert cat.load(default_corpus_dir(settings.CORTEXSIM_BASE_DIR)) > 0
    registry = load()
    assert registry is not None, "spec/dataset_requirements.yml is absent"
    yield cat, registry, list(_corpus_scenarios())
    load.cache_clear()


# The corpus as of f6fd90f plus SIM-KOI-012, measured 2026-11-11 with the code at c4e52a2.
# {door: (detections needing it, scenarios needing it)}; a detection needing two
# doors counts once under each. Collector-route and underived detections are
# counted below, not here.
DOOR_COUNTS: dict[str, tuple[int, int]] = {
    "xdr_agent": (513, 106),
    "ngfw_traffic": (136, 49),
    "cloud_aws": (67, 21),
    "saas_okta": (24, 14),
    "prisma_browser": (23, 6),
    "windows_events": (22, 8),
    "xdr_agent_xth": (22, 8),
    "saas_o365": (19, 9),
    "saas_entra": (15, 9),
    "xpanse": (15, 8),
    "tenant_incidents": (14, 4),
    "ngfw_stitch": (11, 9),
    "cloud_gcp": (9, 3),
    "ngfw_globalprotect": (8, 3),
    "ngfw_threat": (7, 3),
    "proofpoint_tap": (5, 5),
    "cloud_azure": (3, 2),
    "koi_aes": (3, 2),
    "syslog_cisco_asa": (3, 1),
    "saas_google_workspace": (2, 2),
    "syslog_ivanti_connect_secure": (1, 1),
}
# Every code, zeros included. No corpus detection names a missing card, and every
# dataset a card reads has a registry row. NO_DATASET is the 293 of spec §11 less
# the 68 on collector steps (47 IOC, 21 correlation), less the 2 the beta merge
# removed, plus SIM-KOI-012's Prevention row, less SIM-EDR-025's (now an ABIOC).
UNDERIVED: dict[str, int] = {
    "CARD_NOT_FOUND": 0, "NO_DATASET": 223, "DOORS_UNKNOWN": 61, "NO_PROVIDER": 39,
    "UNREGISTERED_DATASET": 0,
}
COLLECTOR_BY_TYPE: dict[str, int] = {
    "XQL": 77, "IOC": 47, "ABIOC": 38, "Analytics": 21, "Correlation": 24, "BIOC": 9,
}
# 514 xdr_data detections less the 5 on collector steps need the XDR agent. One of
# them, SIM-MP-015 step-03's ABIOC (TTP-2026-0130), inner-joins xdr_data with
# msft_azure_ad_signin, so under rule 2 it needs saas_entra too. The dev 8c90739
# measurement read only the first anchor (spec §11) and counted it as agent-only.
# The beta merge removed 5 agent-only detections and SIM-KOI-012 added 7 (see the
# module docstring); measured on the merged tree. SIM-EDR-025 step-05's ABIOC
# adds 1 (2026-11-13) and SIM-MP-038 step-01 one more.
# SIM-EDR-027 adds 7 (2026-11-13).
XDR_AGENT_SOLE = 512


def test_corpus_door_counts(corpus):
    cat, registry, scenarios = corpus
    total = collector = xdr_sole = 0
    underived: Counter = Counter()
    collector_types: Counter = Counter()
    door_dets: Counter = Counter()
    door_scen: dict[str, set] = {}
    for sc in scenarios:
        for step in sc["steps"]:
            for det in step.get("expected_detections") or []:
                total += 1
                req = detection_requirement(step, det, catalog=cat, registry=registry)
                if req.route == "collector":
                    collector += 1
                    collector_types[det["type"]] += 1
                    continue
                if req.underived:
                    underived[req.underived] += 1
                    continue
                if req.all_of == (("xdr_agent",),):
                    xdr_sole += 1
                for door in {d for element in req.all_of for d in element}:
                    door_dets[door] += 1
                    door_scen.setdefault(door, set()).add(sc["scenario_id"])
    assert total == 1437
    assert collector == 216
    assert door_dets["xdr_agent"] == 513, (
        "509 (514 xdr_data less the 5 on collector steps) less 5 the beta merge removed, "
        "plus SIM-EDR-025 step-05's ABIOC and SIM-MP-038 step-01"
        ", plus SIM-EDR-027's 7")
    assert xdr_sole == XDR_AGENT_SOLE
    assert {d: (door_dets[d], len(door_scen[d])) for d in door_dets} == DOOR_COUNTS
    assert set(underived) <= set(UNDERIVED_CODES)
    assert {code: underived[code] for code in UNDERIVED_CODES} == UNDERIVED
    assert dict(collector_types) == COLLECTOR_BY_TYPE


# With every door open for one agent, a scenario still falls short only on what no
# door can open here: collector routes (no target resolved) and underived rows.
# needs_doors 11 are collector-only scenarios; not_evaluated 8 derive no detection.
ALL_OPEN_STATES: dict[str, int] = {"ready": 152, "partial": 31, "needs_doors": 11, "not_evaluated": 8}
NONE_OPEN_STATES: dict[str, int] = {"needs_doors": 194, "not_evaluated": 8}


def test_corpus_readiness(corpus):
    cat, registry, scenarios = corpus
    every = {door_key(d.id, "a1") for d in DOORS if d.scope != "target"}
    sums: Counter = Counter()
    all_open: Counter = Counter()
    none_open: Counter = Counter()
    for sc in scenarios:
        out = scenario_readiness(sc, every, agent_id="a1", catalog=cat, registry=registry)
        all_open[out["state"]] += 1
        for key in ("total", "scorable", "underived", "route_blocked", "unevaluated"):
            sums[key] += out[key]
        none_open[scenario_readiness(sc, set(), agent_id="a1", catalog=cat, registry=registry)["state"]] += 1
    assert sums["total"] == 1437
    assert sums["route_blocked"] == 216
    assert sums["unevaluated"] == 0
    assert sums["underived"] == sum(UNDERIVED.values())
    # Every derived detection off the collector route is scorable when every door is open.
    assert sums["scorable"] == 1437 - 216 - sum(UNDERIVED.values())
    assert dict(all_open) == ALL_OPEN_STATES
    assert dict(none_open) == NONE_OPEN_STATES
