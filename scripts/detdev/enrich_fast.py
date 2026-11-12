#!/usr/bin/env python3
"""Fast reliable enrich: fill a BIOC-less card's detections keyed to its EXISTING
attack payload, via cpp-coder (direct gateway) + deterministic fill. No agent.

usage: enrich_fast.py CARD_PATH
"""
import json, sys, os, re, urllib.request

CARD = sys.argv[1]
MODEL = os.environ.get("ENRICH_MODEL", "cpp-coder")

key = ""
for line in open(os.path.expanduser("~/Github/dot-files/.env")):
    if line.startswith("LITELLM_MASTER_KEY="):
        key = line.split("=", 1)[1].strip(); break

d = json.load(open(CARD))
det = d.setdefault("detections", {})
ex = d.get("execution", {}) or {}
payload = ((ex.get("payload") or {}).get("code")) or ""
arts = ex.get("expected_artifacts") or []
name = (d.get("identity", {}) or {}).get("name", d.get("id", ""))
techs = [t.get("technique_id", "") for t in (d.get("mitre_attack", {}) or {}).get("techniques", [])]
techs = [t for t in techs if t]

SYS = ("You are an offensive-security detection engineer for CortexSim (defensive "
       "Cortex POV detection validation). Output ONLY valid JSON, no prose/markdown. "
       "XQL uses Cortex fields: actor_process_image_name, action_process_image_name, "
       "causality_actor_process_image_name, action_file_path, action_remote_ip, dst_ip, "
       "dst_port, agent_hostname, actor_effective_username. Every logic/query MUST start "
       "with exactly 'dataset = xdr_data'.")
USER = f"""TTP: {name}
MITRE: {", ".join(techs)}
This attack already runs the following payload; author the PROCESS/behavioral/malware
detections that a Cortex endpoint agent fires on IT (key every rule to commands/files
below, each a DISTINCT layer, priority BIOC then IOC):

PAYLOAD:
{payload[:1800]}

OBSERVABLE ARTIFACTS:
{json.dumps(arts)[:800]}

Output ONE JSON object:
{{
  "biocs": [{{"name":"<distinct process BIOC>","description":"<what fires>",
    "logic":"dataset = xdr_data\\n| filter event_type = ENUM.PROCESS and event_sub_type = ENUM.PROCESS_START\\n| filter ...",
    "mitre_technique_ids":{json.dumps(techs[:2] or ['T1059'])},"severity":"high","suppression_window_seconds":0}}],
  "iocs": [{{"ioc_type":"filename|file-sha256|domain|ipv4","value":"<concrete artifact from the payload>","context":"<why>"}}],
  "xql_queries": [{{"name":"<hunt>","query":"dataset = xdr_data\\n| filter ...","purpose":"validation","expected_rows_min":1,"dataset":"xdr_data"}}]
}}
1-2 BIOCs, 1-3 IOCs, 1 XQL. Only detections that genuinely apply; JSON only."""

body = json.dumps({"model": MODEL,
                   "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": USER}],
                   "max_tokens": 3000, "temperature": 0.4}).encode()
req = urllib.request.Request("http://localhost:4000/v1/chat/completions", data=body,
                            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=240) as r:
    resp = json.load(r)
m = resp["choices"][0]["message"]
content = m.get("content") or m.get("reasoning_content") or ""
content = re.sub(r"^```(json)?|```$", "", content.strip(), flags=re.M).strip()
mm = re.search(r"\{.*\}", content, re.S)
if not mm:
    sys.exit(f"no JSON from model: {content[:200]}")
frag = json.loads(mm.group(0))

# fill ONLY empty detection arrays (don't clobber anything that exists)
def fix_ds(s): return re.sub(r"^\s*(dataset|preset)\s*=\s*\S+", "dataset = xdr_data", s or "", count=1, flags=re.M)
if not det.get("biocs") and frag.get("biocs"):
    for b in frag["biocs"]:
        b["logic"] = fix_ds(b.get("logic", ""))
    det["biocs"] = frag["biocs"]
if not det.get("iocs") and frag.get("iocs"):
    det["iocs"] = frag["iocs"]
if not det.get("xql_queries") and frag.get("xql_queries"):
    xqs = []
    for q in frag["xql_queries"]:
        xqs.append({"purpose": q.get("purpose", "validation"), "name": q.get("name", "hunt"),
                    "query": fix_ds(q.get("query", "")), "expected_rows_min": q.get("expected_rows_min", 1),
                    "dataset": "xdr_data"})
    det["xql_queries"] = xqs

json.dump(d, open(CARD, "w"), indent=2); open(CARD, "a").write("\n")
print(f"{os.path.basename(CARD)}: biocs={len(det.get('biocs',[]))} iocs={len(det.get('iocs',[]))} xql={len(det.get('xql_queries',[]))} finish={resp['choices'][0].get('finish_reason')}")
