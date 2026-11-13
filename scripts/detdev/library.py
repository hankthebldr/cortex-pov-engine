#!/usr/bin/env python3
"""Rules for the staged draft library the detection-authoring pipeline writes.

The pipeline turns a local-model fragment into a TTP card and a scenario. Its
first packager cloned a live exemplar pair and replaced only the content
fields, so every generated pair inherited the exemplar's subject: its threat
actors, its Unit 42 source, its technique, its identity, its cleanup line. This
module replaces that with one builder that takes NOTHING from an exemplar:

    fragment + source  ->  build_card(), build_scenario()

and one reader that recovers a fragment from an already packaged pair, so pairs
packaged the old way can be rebuilt by the same rules (``restage.py``).

Everything built here is a DRAFT. It is written under ``drafts/detection-library/``,
a tracked directory outside ``scenarios/`` and ``detection_scanner/``: the boot
loader, the validators, the coverage report, the corpus pins and the image
build do not read it. (``detection_scanner/ttps/_drafts/`` is git-ignored by a
rule that says candidate cards there are never committed, so it cannot hold a
reviewed-in-the-open library.) Ids are provisional (``TTP-0000-NNNN`` /
``SIM-DRAFT-NNN``); real ids are assigned when a pair is promoted.

Pure functions and stdlib only (plus PyYAML at the call sites).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
TTPS_DIR = REPO / "detection_scanner" / "ttps"
STAGING = REPO / "drafts" / "detection-library"
STAGED_CARDS = STAGING / "cards"
STAGED_SCENARIOS = STAGING / "scenarios"
TARGET_LISTS = (
    Path(__file__).resolve().parent / "targets-windows-analytics.txt",
    Path(__file__).resolve().parent / "targets-linux-analytics.txt",
)

PROVISIONAL_CARD_RE = re.compile(r"^TTP-0000-\d{4}$")
PROVISIONAL_SCENARIO_RE = re.compile(r"^SIM-DRAFT-\d{3,}$")

GENERATOR = "detdev pipeline (local model, unreviewed)"
PLATFORMS = ("windows", "linux", "macos")

# What a step runs as. Windows has no unattended impersonation: the spec
# (spec/identity_harness.json) collapses every non-direct identity to the
# beacon's own account, so a Windows draft says ``direct`` and claims nothing
# else. Linux and macOS drafts run as root, a direct identity that exists on
# every host (a service account such as www-data may have no login shell).
IDENTITY = {"windows": "direct", "linux": "root", "macos": "root"}
# The process SimCore's own causality graph labels as the chain owner. A
# neutral platform default: the pipeline does not know the real initial access.
CGO_ANCHOR = {
    "windows": {"image_name": "explorer.exe"},
    "linux": {"image_name": "sshd", "primary_username": "root"},
    "macos": {"image_name": "launchd", "primary_username": "root"},
}
PAYLOAD_INTERPRETER = {"windows": "powershell", "linux": "bash", "macos": "bash"}
PRIVILEGE = {"windows": "administrator", "linux": "root", "macos": "root"}

CLEANUP_PLACEHOLDER = (
    "echo '[CortexSim draft] cleanup not authored: write and test it before promotion'"
)
# A hash is a claim about a real file. One with no source is not carried.
HASH_IOC_TYPES = frozenset({
    "file-sha256", "file-sha1", "file-md5", "file-hash", "sha256", "sha1", "md5",
    "imphash", "ssdeep",
})

# Reference hosts a generated draft may cite without a human having checked the
# link against the content: the vendor page the target list names, and the
# ATT&CK page of a technique the draft carries.
ALLOWED_REFERENCE_HOSTS = frozenset({
    "cortex-docs.paloaltonetworks.com",
    "docs-cortex.paloaltonetworks.com",
    "attack.mitre.org",
})

_ALERT_MODULE_RE = re.compile(
    r"^Cortex XSIAM Analytics \(XDR Agent\) — (?P<name>.+?); Required Data: XDR Agent"
    r"(?:; ATT&CK (?P<attack>[T0-9., ]+))?\. Ref: (?P<url>\S+)$"
)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def url_host(url: str) -> str:
    return re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/", 1)[0].split(":", 1)[0]


def provisional_ids(index: int) -> tuple[str, str]:
    """1-based position in the staged library -> (card id, scenario id)."""
    return f"TTP-0000-{index:04d}", f"SIM-DRAFT-{index:03d}"


# --------------------------------------------------------------------------- sources


def load_target_list(paths: Iterable[Path] = TARGET_LISTS) -> dict[str, str]:
    """``{alert reference url: alert name}`` from the pipeline's target lists."""
    out: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if "\t" not in line:
                continue
            name, url = line.split("\t", 1)
            out[url.strip()] = name.strip()
    return out


def source_of(analytics_modules: Iterable[Any], targets: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Where a pair's subject comes from, read from what the pipeline recorded.

    The pipeline writes one ``analytics_modules`` string per target alert with
    the alert's exact name and a reference. That string is the only record of a
    pair's real source, and it counts only when the pipeline's own target list
    (``targets``, url -> name) confirms it: either the recorded URL is a listed
    URL for that name, or the recorded name is a listed name, whose listed URL
    is then used. Anything else has no recorded source, and the builder then
    cites none: it never falls back to an exemplar's.
    """
    targets = load_target_list() if targets is None else targets
    by_name = {name.casefold(): url for url, name in targets.items()}
    for entry in analytics_modules or []:
        if not isinstance(entry, str):
            continue
        m = _ALERT_MODULE_RE.match(entry.strip())
        if not m:
            continue
        name, url = m["name"].strip(), m["url"].rstrip(".")
        if targets.get(url, "").casefold() == name.casefold():
            return {"kind": "cortex-analytics-alert", "name": targets[url], "url": url}
        if name.casefold() in by_name:
            listed = by_name[name.casefold()]
            return {"kind": "cortex-analytics-alert", "name": targets[listed], "url": listed}
    return {"kind": "not-recorded"}


# --------------------------------------------------------------------------- ATT&CK names


def technique_index(ttps_dir: Path = TTPS_DIR) -> dict[str, dict[str, Any]]:
    """``{technique or sub-technique id: {name, tactic_ids, tactic_names}}`` from live cards.

    The repository carries no ATT&CK table, so names and tactics come from the
    reviewed corpus. A technique the corpus has never used stays unresolved,
    and the builder says so instead of guessing a tactic.
    """
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(ttps_dir.glob("*.json")):
        try:
            card = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for tech in (card.get("mitre_attack") or {}).get("techniques") or []:
            tid = tech.get("subtechnique_id") or tech.get("technique_id")
            name = tech.get("name") or ""
            if not tid or not name or re.fullmatch(r"T\d{4}(\.\d{3})?", name):
                continue  # a name that is only an id teaches nothing
            index.setdefault(tid, {
                "name": name,
                "tactic_ids": list(tech.get("tactic_ids") or []),
                "tactic_names": list(tech.get("tactic_names") or []),
            })
    return index


def attack_url(technique_id: str) -> str:
    return "https://attack.mitre.org/techniques/" + technique_id.replace(".", "/") + "/"


# --------------------------------------------------------------------------- fragments


def filter_iocs(iocs: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Split IOCs into (kept, dropped). A hash with no ``source_url`` is dropped."""
    kept, dropped = [], []
    for ioc in iocs or []:
        is_hash = str(ioc.get("ioc_type", "")).lower() in HASH_IOC_TYPES
        (dropped if is_hash and not ioc.get("source_url") else kept).append(ioc)
    return kept, dropped


def fragment_from_pair(card: dict, scenario: dict) -> dict:
    """Recover the model's fragment from a pair the first packager wrote.

    Only fields that packager filled from the fragment are read. Everything it
    left as the exemplar's (metadata, threat context, product mapping,
    references, the scenario's source, technique, identity, anchor, UC/TC
    refs, KPI and cleanup) is ignored here and rebuilt from nothing by the
    builders. A tool declaration is kept only when a step runs that tool: 329
    of the 366 pairs carried their exemplar's tool with no step using it.
    """
    identity = card.get("identity") or {}
    execution = card.get("execution") or {}
    detections = card.get("detections") or {}
    techniques = [
        t.get("subtechnique_id") or t.get("technique_id")
        for t in (card.get("mitre_attack") or {}).get("techniques") or []
        if t.get("subtechnique_id") or t.get("technique_id")
    ]
    steps = []
    for step in scenario.get("steps") or []:
        steps.append({
            "name": step.get("name") or "",
            "command": step.get("command") or "",
            "mitre_technique": step.get("mitre_technique") or (techniques[0] if techniques else ""),
            "expected_detections": [
                {k: row[k] for k in ("type", "description", "detection_id") if k in row}
                for row in step.get("expected_detections") or []
            ],
        })
    return {
        "name": identity.get("name") or scenario.get("name") or "",
        "description": identity.get("description") or identity.get("summary") or "",
        "mitre_technique_ids": techniques,
        "target_platform": execution.get("target_platform") or "linux",
        "execution_payload": (execution.get("payload") or {}).get("code") or "",
        "expected_artifacts": list(execution.get("expected_artifacts") or []),
        "biocs": list(detections.get("biocs") or []),
        "abiocs": list(detections.get("abiocs") or []),
        "xql_queries": list(detections.get("xql_queries") or []),
        "iocs": list(detections.get("iocs") or []),
        "analytics_modules": list(detections.get("analytics_modules") or []),
        "plane": scenario.get("plane") or "EDR",
        "external_tools": tools_a_step_runs(scenario.get("external_tools"), steps),
        "steps": steps,
    }


# How a tool declaration shows up in a command when its name does not.
_TOOL_COMMAND_ALIASES = {"cortexsim-eal-cli": "eal_simulator"}


def tools_a_step_runs(external_tools: Iterable[dict], steps: Iterable[dict]) -> list[dict]:
    """The declared tools some step command actually names."""
    commands = "\n".join(step.get("command") or "" for step in steps).lower()
    kept = []
    for tool in external_tools or []:
        name = (tool.get("name") or "").lower()
        keys = {name, name.split(" ")[0], _TOOL_COMMAND_ALIASES.get(name, ""),
                (tool.get("adapter_ref") or "").lower().removeprefix("tool-")}
        if any(key and key in commands for key in keys):
            kept.append(tool)
    return kept


# --------------------------------------------------------------------------- builders


def _techniques(ids: list[str], index: dict) -> tuple[list[dict], list[str]]:
    out, unresolved = [], []
    for tid in ids:
        base = tid.split(".")[0]
        known = index.get(tid)
        entry: dict[str, Any] = {"technique_id": base}
        if "." in tid:
            entry["subtechnique_id"] = tid
        if known:
            entry["name"] = known["name"]
            entry["tactic_ids"] = list(known["tactic_ids"])
            if known["tactic_names"]:
                entry["tactic_names"] = list(known["tactic_names"])
        else:
            # Unknown to the reviewed corpus: keep the id as its own name and a
            # placeholder tactic the staging check reports, never a guess that
            # reads as a fact.
            entry["name"] = tid
            entry["tactic_ids"] = ["TA0000"]
            unresolved.append(tid)
        out.append(entry)
    return out, unresolved


def _references(source: dict, technique_ids: list[str], extra: Iterable[dict] = ()) -> list[dict]:
    refs: list[dict] = []
    if source["kind"] == "cortex-analytics-alert":
        refs.append({
            "title": f"Cortex XDR Analytics Alert Reference: {source['name']}",
            "url": source["url"],
            "publisher": "Palo Alto Networks",
            "primary": True,
        })
    seen = {r["url"] for r in refs}
    for ref in extra:
        url = ref.get("url") or ""
        if url in seen or url_host(url) not in ALLOWED_REFERENCE_HOSTS or url_host(url) == "attack.mitre.org":
            continue
        refs.append({"title": ref.get("title") or url, "url": url,
                     "publisher": ref.get("publisher") or "Palo Alto Networks"})
        seen.add(url)
    for tid in technique_ids:
        url = attack_url(tid)
        if url not in seen:
            refs.append({"title": f"MITRE ATT&CK {tid}", "url": url,
                         "publisher": "MITRE", "publisher_id": "SRC-MITRE-ATTACK"})
            seen.add(url)
    return refs


def build_card(fragment: dict, *, card_id: str, source: dict, generated_on: str,
               index: dict, extra_references: Iterable[dict] = ()) -> dict:
    """A schema-shaped draft card built from the fragment and its source only."""
    platform = fragment["target_platform"]
    technique_ids = list(fragment.get("mitre_technique_ids") or [])
    techniques, _ = _techniques(technique_ids, index)
    iocs, _ = filter_iocs(fragment.get("iocs"))
    description = fragment.get("description") or ""
    tags = ["generated-draft", f"platform-{platform}", "endpoint",
            "cortex-analytics-alert" if source["kind"] == "cortex-analytics-alert"
            else "source-not-recorded"]
    xql = [{
        "purpose": q.get("purpose", "validation"),
        "name": q.get("name", "hunt query"),
        "query": q.get("query", ""),
        "expected_rows_min": q.get("expected_rows_min", 1),
        "dataset": q.get("dataset", "xdr_data"),
    } for q in fragment.get("xql_queries") or []]
    stamp = f"{generated_on}T00:00:00Z"
    return {
        "id": card_id,
        "schema_version": "1.0.0",
        "entry_version": "0.1.0",
        "status": "draft",
        "metadata": {
            "created_at": stamp,
            "updated_at": stamp,
            "authors": [{"name": GENERATOR, "role": "generator"}],
            "tags": tags,
            "pov_engine": {
                "auto_load": False,
                "simulation_class": "endpoint",
                "destructive": False,
                "platforms": [platform],
                "cleanup_required": True,
                "safety_class": "lab-only",
            },
        },
        "identity": {
            "name": fragment["name"],
            "summary": description[:200],
            "description": description,
        },
        "mitre_attack": {"matrix": "enterprise", "techniques": techniques},
        "execution": {
            "target_platform": platform,
            "execution_framework": "custom-cortex",
            "privilege_required": PRIVILEGE[platform],
            "payload": {
                "interpreter": PAYLOAD_INTERPRETER[platform],
                "code": fragment.get("execution_payload") or "",
                "input_variables": [],
                "timeout_seconds": 120,
            },
            "expected_artifacts": list(fragment.get("expected_artifacts") or []),
            # What the model proposes is recorded, never installed as the
            # scenario's cleanup: nothing has run it.
            "cleanup": {"manual_steps": [
                f"Proposed by the generator, not run: {command}"
                for command in fragment.get("cleanup_commands") or []
            ] or ["Not authored. Write and test cleanup before promotion."]},
        },
        "detections": {
            "biocs": list(fragment.get("biocs") or []),
            "abiocs": list(fragment.get("abiocs") or []),
            "xql_queries": xql,
            "correlation_rules": [],
            "iocs": iocs,
            "analytics_modules": list(fragment.get("analytics_modules") or []),
        },
        "panw_mapping": {
            "products": [{"module": "cortex-xdr", "submodule": "analytics",
                          "coverage_tier": "detection"}],
        },
        "references": _references(source, technique_ids, extra_references),
        "changelog": [{
            "entry_version": "0.1.0",
            "date": generated_on,
            "author": GENERATOR,
            "change": "Generated draft. Built from the model fragment and its recorded "
                      "source only; not reviewed, not run.",
        }],
    }


def _success_criteria(source: dict, platform: str) -> str:
    lines = [
        "DRAFT, generated by a local model and not reviewed. It is staged outside the",
        "corpus: it does not load, is not counted as coverage and cannot be launched.",
    ]
    if source["kind"] == "cortex-analytics-alert":
        lines += [
            f"Subject: the Cortex analytics alert \"{source['name']}\".",
            "Whether that alert can fire from these steps has not been checked against",
            "its reference page (activation period, training period, required data).",
        ]
    else:
        lines.append("Its source was not recorded by the pipeline and must be established at review.")
    if platform == "windows":
        lines.append("Windows content: the lab has no Windows host, so it cannot be lab-run.")
    lines += [
        "Before promotion: a real cleanup, a sandboxed command path, UC/TC refs and a",
        "measurement contract, and declared rows limited to what a tenant can fire.",
    ]
    return "\n".join(lines) + "\n"


def build_scenario(fragment: dict, *, scenario_id: str, card_id: str, source: dict,
                   generated_on: str, index: dict) -> dict:
    """A draft scenario built from the fragment and its source only."""
    platform = fragment["target_platform"]
    technique_ids = list(fragment.get("mitre_technique_ids") or [])
    primary = technique_ids[0]
    known = index.get(primary) or {}
    identity = IDENTITY[platform]
    plane = fragment.get("plane") or "EDR"
    steps: list[dict] = []
    types: list[str] = []
    previous: Optional[str] = None
    for n, raw in enumerate(fragment.get("steps") or [], 1):
        sid = f"step-{n:02d}"
        rows = []
        for row in raw.get("expected_detections") or []:
            rows.append({"plane": plane, "type": row["type"],
                         "description": row.get("description", ""),
                         "ttp_ref": card_id,
                         **({"detection_id": row["detection_id"]} if row.get("detection_id") else {})})
            if row["type"] not in types:
                types.append(row["type"])
        step: dict[str, Any] = {
            "id": sid,
            "name": raw.get("name") or sid,
            "command": raw.get("command") or "",
            "identity": identity,
            "mitre_technique": raw.get("mitre_technique") or primary,
            "platforms": [platform],
            "expected_detections": rows,
        }
        if previous:
            step["causality"] = {"parent_step": previous, "pivot": "process_lineage"}
        steps.append(step)
        previous = sid
    scenario: dict[str, Any] = {
        "scenario_id": scenario_id,
        "name": fragment["name"],
        "version": "0.1",
        "status": "draft",
        "plane": plane,
        "detection_types": types or ["XQL"],
        "mitre_tactic": (known.get("tactic_ids") or ["TA0000"])[0],
        "mitre_tactic_name": (known.get("tactic_names") or ["unresolved"])[0],
        "mitre_technique": primary,
        "mitre_technique_name": known.get("name") or primary,
    }
    if source["kind"] == "cortex-analytics-alert":
        scenario["threat_report"] = f"Cortex XDR Analytics Alert Reference: {source['name']}"
        scenario["threat_report_url"] = source["url"]
    scenario.update({
        "cgo_anchor": dict(CGO_ANCHOR[platform]),
        "execution_identity": {"default": identity, "options": [identity]},
        "push_supported": True,
        "pull_supported": True,
        "success_criteria": _success_criteria(source, platform),
    })
    if fragment.get("external_tools"):
        scenario["external_tools"] = list(fragment["external_tools"])
    scenario.update({
        "steps": steps,
        "cleanup": {"commands": [CLEANUP_PLACEHOLDER]},
        "author": GENERATOR,
        "created": generated_on,
        "last_updated": generated_on,
        "tags": ["generated-draft", platform, "endpoint"],
    })
    return scenario


def rows_from_detects(fragment: dict) -> dict:
    """Turn the generator's per-step ``detects`` names into expected-detection rows.

    A fragment from ``genfrag.py`` names the detections a step should raise; a
    fragment recovered from a packaged pair already carries rows. This returns
    a copy in the second shape. A name that matches no detection in the
    fragment is dropped: a row must resolve to a detection object on the card.
    """
    by_name: dict[str, tuple[str, str]] = {}
    for kind, typ, prefix in (("biocs", "BIOC", "bioc-"), ("xql_queries", "XQL", "xql-"),
                              ("abiocs", "ABIOC", "abioc-")):
        for det in fragment.get(kind) or []:
            if det.get("name"):
                by_name[det["name"]] = (typ, prefix + slug(det["name"]))
    out = dict(fragment)
    steps = []
    for step in fragment.get("steps") or []:
        step = dict(step)
        if "expected_detections" not in step:
            step["expected_detections"] = [
                {"type": by_name[n][0], "description": n, "detection_id": by_name[n][1]}
                for n in step.get("detects") or [] if n in by_name
            ]
        steps.append(step)
    out["steps"] = steps
    return out


def build_pair(fragment: dict, *, index_position: int, generated_on: str,
               technique_names: dict, extra_references: Iterable[dict] = (),
               targets: Optional[dict[str, str]] = None) -> tuple[dict, dict, dict]:
    """fragment -> (card, scenario, source). The one entry point both CLIs use."""
    card_id, scenario_id = provisional_ids(index_position)
    source = source_of(fragment.get("analytics_modules"), targets)
    card = build_card(fragment, card_id=card_id, source=source, generated_on=generated_on,
                      index=technique_names, extra_references=extra_references)
    scenario = build_scenario(fragment, scenario_id=scenario_id, card_id=card_id, source=source,
                              generated_on=generated_on, index=technique_names)
    return card, scenario, source


def next_position(cards_dir: Optional[Path] = None) -> int:
    """The next free 1-based position in the staged library."""
    cards_dir = STAGED_CARDS if cards_dir is None else cards_dir
    taken = [int(p.name[9:13]) for p in cards_dir.glob("TTP-0000-[0-9][0-9][0-9][0-9]-*.json")]
    return max(taken, default=0) + 1


def staged_paths(card: dict, scenario: dict) -> tuple[Path, Path]:
    name = slug(card["identity"]["name"])[:60].strip("-")
    return (STAGED_CARDS / f"{card['id']}-{name}.json",
            STAGED_SCENARIOS / f"{scenario['scenario_id'].lower()}-{name}.yml")


def dump_card(card: dict) -> str:
    return json.dumps(card, indent=2, ensure_ascii=False) + "\n"
