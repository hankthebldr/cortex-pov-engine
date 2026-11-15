#!/usr/bin/env python3
"""Phase 1: ask a local model (LiteLLM gateway) for a raw attack-content fragment.

usage: genfrag.py OUT_PATH PLANE "BRIEF" T1105,T1059.004 [MODEL]
                  [--platform linux|windows|macos] [--alert "<exact alert name>"]

Writes a flat fragment JSON that ``package.py`` turns into a staged draft pair.

--platform  the host the content targets; the prompt asks for that platform's
            shell and the fragment records it.
--alert     the exact name of a Cortex analytics alert from the target lists
            (targets-*-analytics.txt). The fragment then records that alert and
            its reference URL as its source, taken from the list, not typed.

The gateway key is read from the environment (LITELLM_MASTER_KEY); the gateway
address from DETDEV_GATEWAY_URL (default http://localhost:4000).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library as lib  # noqa: E402

SHELL = {"linux": "bash", "macos": "bash", "windows": "PowerShell"}
SCRATCH = {"linux": "/var/tmp/cortexsim-draft", "macos": "/var/tmp/cortexsim-draft",
           "windows": "C:\\ProgramData\\CortexSimDraft"}

SYSTEM = (
    "You write lab content for CortexSim, a DEFENSIVE Cortex POV detection-validation "
    "lab. You output ONLY valid JSON: no prose, no markdown fences. The content "
    "reproduces the telemetry an attack leaves, on a lab host its operator owns. Rules: "
    "never download or run a real malware sample; every network destination is a "
    "TEST-NET-1 address (192.0.2.0/24) or a host name ending in .invalid; every file the "
    "content creates lives under the scratch directory you are given; every command is "
    "real and runs as written, never a placeholder word or an angle-bracket token. "
    "Never invent an indicator. An IOC is listed only when the brief itself states it "
    "together with the URL that published it; otherwise the iocs list is empty."
)


def build_messages(brief: str, plane: str, techs: list[str], platform: str) -> tuple[str, str]:
    """(system prompt, user prompt) for one fragment."""
    shell = SHELL[platform]
    seed = techs or ["<1-3 ATT&CK technique IDs you determine for this alert, e.g. T1059.001>"]
    technique_line = (", ".join(techs) if techs else
                      "(DETERMINE the 1-3 best ATT&CK technique IDs for this alert yourself "
                      "and put them in mitre_technique_ids)")
    user = f"""Brief: {brief}
MITRE techniques: {technique_line}
Plane: {plane.upper()}
Target platform: {platform} (commands are {shell}; scratch directory {SCRATCH[platform]})

Output ONE JSON object with EXACTLY these keys (XQL logic uses Cortex fields:
actor_process_image_name, action_process_image_name, causality_actor_process_image_name,
action_file_path, action_remote_ip, dst_ip, dst_port, agent_hostname; aggregate with comp):

{{
  "name": "<concise scenario title>",
  "description": "<2 sentences: what the attack does and what Cortex should catch>",
  "mitre_technique_ids": {json.dumps(seed)},
  "target_platform": "{platform}",
  "execution_payload": "<multi-line {shell}: the commands that leave the telemetry, inside the scratch directory, network destinations in 192.0.2.0/24 or *.invalid; use \\n between lines>",
  "cleanup_commands": ["<{shell} command that removes what the payload created>"],
  "expected_artifacts": [
    {{"artifact_type": "file|process|network-connection", "description": "<what>", "expected_value": "<Cortex field condition>"}}
  ],
  "biocs": [
    {{"name": "<distinct process/behavioral BIOC name>", "description": "<what fires>",
      "logic": "dataset = xdr_data\\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\\n| filter ...",
      "mitre_technique_ids": {json.dumps(seed[:1])}, "severity": "high", "suppression_window_seconds": 0}}
  ],
  "iocs": [],
  "abiocs": [{{"name":"<behavioral ABIOC name>","description":"<what deviation it models>",
    "logic":"dataset = xdr_data\\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\\n| filter ...",
    "behavioral_profile":"machine-learning","causality_anchor":"<the causality chain the ML anchors the deviation to>",
    "mitre_technique_ids":{json.dumps(seed[:1])},"severity":"high"}}],
  "xql_queries": [
    {{"name": "<hunt query name>", "query": "dataset = xdr_data\\n| filter ..."}}
  ],
  "steps": [
    {{"name": "<step>", "command": "<one {shell} command line from execution_payload>",
      "mitre_technique": "{techs[0] if techs else 'T1059'}", "detects": ["<exact bioc name from biocs above>"]}}
  ]
}}

2-4 steps forming a kill chain. Every step command is a line of execution_payload and
runs as written. Each bioc/xql keys on fields the payload actually produces. Leave "iocs"
empty unless the brief gives an indicator and the URL that published it; then use
{{"ioc_type": "...", "value": "...", "source_url": "<that URL>"}}. Output JSON only."""
    return SYSTEM, user


def alert_module(name: str, technique_ids: list[str], targets: dict[str, str]) -> str | None:
    """The ``analytics_modules`` line for a listed alert, or None when it is not listed."""
    for url, listed in targets.items():
        if listed.casefold() == name.strip().casefold():
            attack = f"; ATT&CK {', '.join(technique_ids)}" if technique_ids else ""
            return (f"Cortex XSIAM Analytics (XDR Agent) — {listed}; "
                    f"Required Data: XDR Agent{attack}. Ref: {url}")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("plane")
    ap.add_argument("brief")
    ap.add_argument("techs", help="comma-separated ATT&CK ids, or '' to let the model choose")
    ap.add_argument("model", nargs="?", default="cpp-coder")
    ap.add_argument("--platform", choices=lib.PLATFORMS, default="linux")
    ap.add_argument("--alert", help="exact alert name from the target lists")
    args = ap.parse_args(argv)

    targets = lib.load_target_list()
    if args.alert and alert_module(args.alert, [], targets) is None:
        sys.exit(f"--alert {args.alert!r} is not a name in scripts/detdev/targets-*-analytics.txt")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not key:
        sys.exit("LITELLM_MASTER_KEY is not set in the environment")
    gateway = os.environ.get("DETDEV_GATEWAY_URL", "http://localhost:4000").rstrip("/")

    techs = [t for t in args.techs.split(",") if t]
    system, user = build_messages(args.brief, args.plane, techs, args.platform)
    body = json.dumps({
        "model": args.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 4000, "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(
        f"{gateway}/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.load(r)

    msg = resp["choices"][0]["message"]
    content = msg.get("content") or msg.get("reasoning_content") or ""
    content = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        sys.exit(f"no JSON in model output (first 300 chars): {content[:300]}")
    frag = json.loads(m.group(0))
    frag["target_platform"] = args.platform  # the caller's choice, whatever the model wrote
    if args.alert:
        frag["analytics_modules"] = [alert_module(args.alert, frag.get("mitre_technique_ids") or [], targets)]
    Path(args.out).write_text(json.dumps(frag, indent=2) + "\n")
    print(f"fragment OK -> {args.out}  | model={args.model} finish={resp['choices'][0].get('finish_reason')}")
    print(f"  name: {frag.get('name')}")
    print(f"  biocs={len(frag.get('biocs', []))} iocs={len(frag.get('iocs', []))} "
          f"xql={len(frag.get('xql_queries', []))} steps={len(frag.get('steps', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
