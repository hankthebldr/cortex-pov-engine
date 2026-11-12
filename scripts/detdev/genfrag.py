#!/usr/bin/env python3
"""Phase-1: generate a raw attack-content fragment via a local model (gateway).

Direct gateway call (no opencode loop). Default model cpp-coder (fast, non-reasoning,
structured JSON, writes offensive detection-validation content without a frontier
model's caution). Writes a flat fragment JSON the Phase-2 packager wraps into the
library schema.

usage: genfrag.py OUT_PATH PLANE "BRIEF" T1105,T1059.004  [model]
"""
import json, sys, os, re, urllib.request

OUT, PLANE, BRIEF, TECHS = sys.argv[1:5]
MODEL = sys.argv[5] if len(sys.argv) > 5 else "cpp-coder"
techs = [t for t in TECHS.split(",") if t]
# When no techniques are supplied (full-list mode), let the model determine them.
_INFER = not techs
_seed = techs or ["<1-3 ATT&CK technique IDs you determine for this alert, e.g. T1059.001>"]

key = ""
envp = os.path.expanduser("~/Github/dot-files/.env")
for line in open(envp):
    if line.startswith("LITELLM_MASTER_KEY="):
        key = line.split("=", 1)[1].strip(); break

SYS = (
    "You are an offensive-security detection engineer for CortexSim, a DEFENSIVE "
    "Cortex POV detection-validation lab. You output ONLY valid JSON, no prose, no "
    "markdown fences. Content is for authorized lab detection validation under the "
    "safe-detonation doctrine: reproduce the detectable TELEMETRY SHAPE only — never "
    "arm a live sample (download to scratch, STUB the chmod/execute), C2 to TEST-NET-1 "
    "(192.0.2.0/24) only, track real malware SHA-256s as IOCs (never executed)."
)
USER = f"""Brief: {BRIEF}
MITRE techniques: {", ".join(techs) if techs else "(DETERMINE the 1-3 best ATT&CK technique IDs for this alert yourself and put them in mitre_technique_ids)"}
Plane: {PLANE.upper()}

Output ONE JSON object with EXACTLY these keys (XQL logic uses Cortex fields:
actor_process_image_name, action_process_image_name, causality_actor_process_image_name,
action_file_path, action_remote_ip, dst_ip, dst_port, agent_hostname):

{{
  "name": "<concise scenario title>",
  "description": "<2-sentence what the attack does and what Cortex should catch>",
  "mitre_technique_ids": {json.dumps(_seed)},
  "target_platform": "linux",
  "execution_payload": "<multi-line bash: the REAL attack commands, safe-detonation doctrine (wget sample to scratch, STUB chmod/exec, TEST-NET C2); use \\n between lines>",
  "expected_artifacts": [
    {{"artifact_type": "file|process|network-connection", "description": "<what>", "expected_value": "<Cortex field condition>"}}
  ],
  "biocs": [
    {{"name": "<distinct process/behavioral BIOC name>", "description": "<what fires>",
      "logic": "dataset = xdr_data\\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\\n| filter ...",
      "mitre_technique_ids": {json.dumps(_seed[:1])}, "severity": "high", "suppression_window_seconds": 0}}
  ],
  "iocs": [
    {{"ioc_type": "file-sha256", "value": "<realistic 64-hex sha256 of the named malware family>",
      "reputation_required": "malicious", "context": "<family name> — CortexSim does not execute it"}}
  ],
  "abiocs": [{{"name":"<behavioral ABIOC name>","description":"<what deviation it models>",
    "logic":"dataset = xdr_data\\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\\n| filter ...",
    "behavioral_profile":"machine-learning","causality_anchor":"<the causality chain the ML anchors the deviation to>",
    "mitre_technique_ids":{json.dumps(_seed[:1])},"severity":"high"}}],
  "xql_queries": [
    {{"name": "<hunt query name>", "query": "dataset = xdr_data\\n| filter ..."}}
  ],
  "steps": [
    {{"name": "<step>", "command": "<the CLI for this step>", "identity": "www-data",
      "mitre_technique": "{techs[0] if techs else 'T1059'}", "detects": ["<exact bioc name from biocs above>"]}}
  ]
}}

2-4 steps forming a kill chain. Every step command must be a real line from execution_payload.
Each bioc/xql must key on fields the payload actually produces. Output JSON only."""

body = json.dumps({
    "model": MODEL,
    "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": USER}],
    "max_tokens": 4000, "temperature": 0.4,
}).encode()

req = urllib.request.Request(
    "http://localhost:4000/v1/chat/completions", data=body,
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=300) as r:
    resp = json.load(r)

msg = resp["choices"][0]["message"]
content = msg.get("content") or ""
if not content and msg.get("reasoning_content"):
    content = msg["reasoning_content"]
# strip markdown fences / extract the JSON object
content = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.M).strip()
m = re.search(r"\{.*\}", content, re.S)
if not m:
    sys.exit(f"no JSON in model output (first 300 chars): {content[:300]}")
frag = json.loads(m.group(0))
json.dump(frag, open(OUT, "w"), indent=2)
print(f"fragment OK -> {OUT}  | model={MODEL} finish={resp['choices'][0].get('finish_reason')}")
print(f"  name: {frag.get('name')}")
print(f"  biocs={len(frag.get('biocs',[]))} iocs={len(frag.get('iocs',[]))} xql={len(frag.get('xql_queries',[]))} steps={len(frag.get('steps',[]))}")
