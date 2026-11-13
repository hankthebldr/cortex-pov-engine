#!/usr/bin/env python3
"""List what each staged draft pair still needs before it can be promoted.

usage: manifest.py [--apply]

Reads ``drafts/detection-library/`` and derives, per pair, the facts a reviewer
needs to choose what to promote first: platform, recorded source, declared rows
by type, and findings read from the content (a step that declares nothing, a
command that is a placeholder, a host the commands would really reach, a
technique the reviewed corpus has never named, a dataset nothing registers, a
tool or a file the pair depends on).

The findings are facts about the text, not verdicts. What every pair needs
regardless (review, a tested cleanup, UC/TC refs and a measurement contract, a
lab run) is stated once in the README, not repeated per pair.

Without --apply it prints the summary and says whether MANIFEST.json and
INDEX.md on disk are current. With --apply it writes both.
"""
from __future__ import annotations

import argparse
import collections
import ipaddress
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library as lib  # noqa: E402

RESERVED_SUFFIXES = (".invalid", ".test", ".localhost", ".local", ".internal", ".lan")
_URL_HOST = re.compile(r"https?://\[?([A-Za-z0-9._-]+)")
_AT_HOST = re.compile(r"[A-Za-z0-9_-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
_REPO_FILE = re.compile(r"\b(core/[\w/]+\.py)\b")
_REPO_MODULE = re.compile(r"-m\s+(core(?:\.\w+)+)")
_XMLNS = re.compile(r"xmlns(?::\w+)?\s*=\s*\\*[\"']https?://[^\"'\\\s]*")
_DATASET = re.compile(r"\b(?:dataset|preset)\s*=\s*([A-Za-z0-9_]+)")


def routable_hosts(text: str) -> list[str]:
    """Hosts a command names that traffic would really leave the lab for."""
    found: set[str] = set()
    text = _XMLNS.sub("", text)  # an XML namespace is a name, not a destination
    for match in _IPV4.finditer(text):
        if match.group(1).endswith(".0.0.0"):
            continue  # a version string (Chrome/120.0.0.0), never a host
        try:
            if ipaddress.ip_address(match.group(1)).is_global:
                found.add(match.group(1))
        except ValueError:
            pass
    for host in _URL_HOST.findall(text) + _AT_HOST.findall(text):
        host = host.lower().rstrip(".")
        if _IPV4.fullmatch(host) or "." not in host:
            continue  # addresses are judged above; a bare name is a lab host
        if host == "localhost" or host.endswith(RESERVED_SUFFIXES):
            continue
        found.add(host)
    return sorted(found)


def _not_runnable(command: str) -> str | None:
    text = command.strip()
    if not text:
        return "empty command"
    if re.match(r"(?i)stub\b", text):
        return "placeholder (starts with 'stub')"
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if lines and all(re.match(r"(?i)(echo|write-host|write-output)\b", line) for line in lines):
        return "only prints text"
    return None


def pair_facts(card: dict, scenario: dict, *, registered: set[str], packs: dict[str, dict],
               repo: Path = lib.REPO) -> dict:
    """The facts and findings for one staged pair."""
    platform = card["execution"]["target_platform"]
    primary = next((ref for ref in card["references"] if ref.get("primary")), None)
    detections = card["detections"]
    rows: collections.Counter = collections.Counter()
    findings: list[str] = []

    if platform == "windows":
        findings.append("Windows content, no Windows host in the lab")
    if primary is None:
        findings.append("source not recorded")
    unresolved = [t.get("subtechnique_id") or t["technique_id"]
                  for t in card["mitre_attack"]["techniques"] if t["tactic_ids"] == ["TA0000"]]
    if unresolved:
        findings.append("technique not named by the reviewed corpus: " + ", ".join(unresolved))

    commands = []
    for step in scenario["steps"]:
        commands.append(step.get("command") or "")
        for row in step["expected_detections"]:
            rows[row["type"]] += 1
        if not step["expected_detections"]:
            findings.append(f"{step['id']} declares no detection")
        reason = _not_runnable(step.get("command") or "")
        if reason:
            findings.append(f"{step['id']} is not runnable: {reason}")
    text = "\n".join(commands)
    hosts = routable_hosts(text)
    if hosts:
        findings.append("commands name a routable host: " + ", ".join(hosts))
    missing = sorted({path for path in _REPO_FILE.findall(text) if not (repo / path).is_file()} |
                     {mod for mod in _REPO_MODULE.findall(text)
                      if not (repo / (mod.replace(".", "/") + ".py")).is_file()})
    if missing:
        findings.append("runs repo code that is not on this branch: " + ", ".join(missing))

    queries = [d.get("logic") or "" for d in detections["biocs"] + detections["abiocs"]]
    queries += [q.get("query") or "" for q in detections["xql_queries"]]
    datasets = sorted({name for q in queries for name in _DATASET.findall(q)} - registered)
    if datasets:
        findings.append("dataset not in the registry: " + ", ".join(datasets))
    if any(re.search(r"(?<![a-z_])causality_id\b", q) for q in queries):
        findings.append("query uses bare causality_id")
    if any("to_timestamp(" in q for q in queries):
        findings.append("query uses to_timestamp()")

    for tool in scenario.get("external_tools") or []:
        ref = tool.get("adapter_ref")
        if not ref:
            continue
        pack = packs.get(ref)
        if pack is None:
            findings.append(f"adapter {ref} has no pack on this branch")
        elif pack.get("safety_class") == "c2-framework":
            findings.append(f"adapter {ref} is a C2 framework (launch consent required)")
        else:
            findings.append(f"depends on adapter {ref}")

    return {
        "card": card["id"],
        "scenario": scenario["scenario_id"],
        "name": card["identity"]["name"],
        "platform": platform,
        "source": ({"alert": primary["title"].split(": ", 1)[-1], "url": primary["url"]}
                   if primary else None),
        "techniques": [t.get("subtechnique_id") or t["technique_id"]
                       for t in card["mitre_attack"]["techniques"]],
        "steps": len(scenario["steps"]),
        "rows": dict(sorted(rows.items())),
        "findings": findings,
    }


def build(staging: Path | None = None) -> dict:
    """The whole manifest for the staged library on disk."""
    staging = lib.STAGING if staging is None else staging
    registered = set(yaml.safe_load((lib.REPO / "spec" / "dataset_requirements.yml").read_text()))
    packs = {}
    for path in sorted((lib.REPO / "tools" / "packs").glob("*.yml")):
        pack = yaml.safe_load(path.read_text()) or {}
        if pack.get("adapter_id"):
            packs[pack["adapter_id"]] = pack
    scenarios = {}
    for path in sorted((staging / "scenarios").glob("*.yml")):
        scenario = yaml.safe_load(path.read_text())
        scenarios[scenario["scenario_id"]] = scenario
    pairs = []
    for path in sorted((staging / "cards").glob("*.json")):
        card = json.loads(path.read_text())
        scenario = scenarios[f"SIM-DRAFT-{int(card['id'][-4:]):03d}"]
        pairs.append(pair_facts(card, scenario, registered=registered, packs=packs))

    def kind(finding: str) -> str:
        finding = re.sub(r"^step-\d+ ", "a step ", finding)
        finding = re.sub(r"^adapter \S+ ", "adapter ", finding)
        finding = re.sub(r"^depends on adapter \S+$", "depends on an adapter", finding)
        return finding.split(":", 1)[0]

    by_finding: collections.Counter = collections.Counter()
    rows: collections.Counter = collections.Counter()
    for pair in pairs:
        by_finding.update({kind(f) for f in pair["findings"]})
        rows.update(pair["rows"])
    return {
        "generated_by": "scripts/detdev/manifest.py",
        "summary": {
            "pairs": len(pairs),
            "by_platform": dict(sorted(collections.Counter(p["platform"] for p in pairs).items())),
            "by_source": {
                "cortex analytics alert": sum(1 for p in pairs if p["source"]),
                "not recorded": sum(1 for p in pairs if not p["source"]),
            },
            "declared_rows": {**{t: 0 for t in ("Correlation", "Prevention")}, **dict(sorted(rows.items()))},
            "pairs_by_finding": dict(sorted(by_finding.items(), key=lambda kv: (-kv[1], kv[0]))),
            "pairs_with_no_finding": [p["card"] for p in pairs if not p["findings"]],
        },
        "pairs": pairs,
    }


def render_index(manifest: dict) -> str:
    s = manifest["summary"]
    out = [
        "# Staged detection library: index",
        "",
        "Generated by `scripts/detdev/manifest.py --apply`. Do not edit by hand.",
        "",
        f"**{s['pairs']} draft pairs** · " + " · ".join(f"{k} {v}" for k, v in s["by_platform"].items()),
        "",
        "Source: " + " · ".join(f"{k} {v}" for k, v in s["by_source"].items()),
        "",
        "Declared rows: " + " · ".join(f"{k} {v}" for k, v in sorted(s["declared_rows"].items())),
        "",
        "## What the content shows, by number of pairs",
        "",
        "| finding | pairs |",
        "|---|---:|",
    ]
    out += [f"| {name} | {count} |" for name, count in s["pairs_by_finding"].items()]
    out += [
        "",
        f"Pairs with none of these findings: **{len(s['pairs_with_no_finding'])}**"
        + (" (" + ", ".join(s["pairs_with_no_finding"]) + ")" if s["pairs_with_no_finding"] else ""),
        "",
        "Every pair, with or without a finding here, still needs what the README lists.",
        "",
        "## Pairs",
        "",
        "| card | scenario | platform | name | alert it targets | steps | rows | findings |",
        "|---|---|---|---|---|---:|---|---|",
    ]
    for p in manifest["pairs"]:
        rows = ", ".join(f"{k} {v}" for k, v in p["rows"].items()) or "none"
        alert = f"[{p['source']['alert']}]({p['source']['url']})" if p["source"] else "not recorded"
        name = p["name"].replace("|", "\\|")
        findings = "<br>".join(f.replace("|", "\\|") for f in p["findings"]) or "none"
        out.append(f"| {p['card']} | {p['scenario']} | {p['platform']} | {name} | {alert} | "
                   f"{p['steps']} | {rows} | {findings} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write MANIFEST.json and INDEX.md")
    args = ap.parse_args(argv)

    manifest = build()
    files = {
        lib.STAGING / "MANIFEST.json": json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
        lib.STAGING / "INDEX.md": render_index(manifest),
    }
    s = manifest["summary"]
    print(f"{s['pairs']} pairs · " + " · ".join(f"{k} {v}" for k, v in s["by_platform"].items()))
    for name, count in s["pairs_by_finding"].items():
        print(f"  {count:4d}  {name}")
    print(f"  {len(s['pairs_with_no_finding']):4d}  pairs with none of these findings")
    stale = [path.name for path, text in files.items()
             if not path.is_file() or path.read_text() != text]
    if args.apply:
        for path, text in files.items():
            path.write_text(text)
        print("wrote MANIFEST.json and INDEX.md")
        return 0
    print("on disk: " + ("current" if not stale else "STALE (" + ", ".join(stale) + "); run with --apply"))
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
