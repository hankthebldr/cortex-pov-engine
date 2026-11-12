#!/usr/bin/env bash
# detect-dev.sh — on-demand detection-development job for cortex-pov-engine.
#
# Drives the local `cyber-detection-author` opencode agent (gateway/cpp-offsec =
# CyberStrike-OffSec-35B) to generate the ATTACK CONTENT and the process /
# behavioral / malware detections that catch it, then regenerates exports and
# validates — leaving a reviewable diff. It NEVER pushes.
#
# What it makes the model produce (in priority order):
#   Agent/ABIOC · IOC · BIOC · WildFire · exploit · malware · process alerts,
#   each keyed to the card's real execution.payload CLI/tool sequence.
#   Correlation rules are plumbing (stitching/grouping) and are DE-prioritised —
#   the job never asks for one.
#
# Two modes:
#   enrich (default, no brief) — take the next endpoint/process card that LACKS a
#     BIOC (or ABIOC/IOC) and author those layers keyed to its existing attack
#     payload, then wire them into the linked scenario's expected_detections.
#   author (a technique id / free-text brief) — author a NEW TTP card AND a
#     runnable scenario YAML (steps[] with command + adapter_ref + identity) with
#     VALID uc_ref/tc_ref index refs.
#
# Usage:
#   scripts/detect-dev.sh                       # enrich next BIOC-less endpoint card
#   scripts/detect-dev.sh TTP-2026-0082         # enrich a specific card
#   scripts/detect-dev.sh T1611                 # author a new TTP+scenario for a technique
#   scripts/detect-dev.sh "ESXi ransomware via ansible"   # author from a brief
#   scripts/detect-dev.sh --list                # the process-detection backlog
#   scripts/detect-dev.sh --dry-run [target]    # print the agent prompt, run nothing
#
# Options:
#   --list            list the process/behavioral coverage backlog and exit
#   --dry-run         resolve target + print the agent prompt; do not invoke the model
#   --branch [NAME]   isolation branch first (clean tree only); default works in place
#   --refs            also run `make check-refs` after (docker; slower; validates scenario refs)
#   --no-verify       skip export + validate after authoring
#   -h | --help       this help
#
# Env: OPENCODE_BIN (default: opencode) · DETECT_DEV_MODEL (override agent model)
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"; cd "$REPO"
OPENCODE_BIN="${OPENCODE_BIN:-opencode}"
AGENT="cyber-detection-author"
TTPS_DIR="detection_scanner/ttps"
EXPORT_SCRIPT="detection_scanner/scripts/export_artifacts.py"
# endpoint/process planes where a BIOC genuinely applies, highest value first
PRIORITY_PLANES="edr cdr itdr koi mp ndr"

c_b=$'\033[1m'; c_dim=$'\033[2m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_red=$'\033[31m'; c_rst=$'\033[0m'
log(){ printf '%s\n' "$*" >&2; }
info(){ printf '%s==>%s %s\n' "$c_b" "$c_rst" "$*" >&2; }
warn(){ printf '%swarn:%s %s\n' "$c_ylw" "$c_rst" "$*" >&2; }
die(){ printf '%serror:%s %s\n' "$c_red" "$c_rst" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# backlog: endpoint/process cards that LACK a BIOC (primary) or ABIOC/IOC.
# TSV: <id> <missing-layers> <plane> <prio> <card>   (prio 0 = priority plane)
# ---------------------------------------------------------------------------
backlog_tsv() {
  python3 - "$TTPS_DIR" "$PRIORITY_PLANES" <<'PY'
import json, glob, os, re, sys
ttps = sys.argv[1]; prio = sys.argv[2].split()
def ne(v): return bool(v) and (len(v) > 0 if isinstance(v, list) else True)
rows = []
for f in sorted(glob.glob(os.path.join(ttps, "TTP-*.json"))):
    b = os.path.basename(f)
    try: det = (json.load(open(f)).get("detections") or {})
    except Exception: continue
    miss = [n for n, k in (("BIOC","biocs"),("ABIOC","abiocs"),("IOC","iocs")) if not ne(det.get(k))]
    if "BIOC" not in miss:        # primary target is a MISSING BIOC
        continue
    m = re.search(r"(TTP-\d{4}-\d{4})", b); tid = m.group(1) if m else b
    pm = re.search(r"sim-([a-z]+)-", b); plane = pm.group(1) if pm else "?"
    rows.append((0 if plane in prio else 1, tid, "+".join(miss), plane, b))
rows.sort(key=lambda r: (r[0], r[3], r[1]))
for p, tid, miss, plane, b in rows:
    print("\t".join([tid, miss, plane, str(p), b]))
PY
}

card_for_id(){ ls "$TTPS_DIR"/"$1"*.json 2>/dev/null | head -1; }
# scenario file for a card: filename suffix sim-<plane>-<nnn> == scenario_id SIM-...
scenario_for_card(){
  local b sid
  b="$(basename "$1")"
  sid="$(printf '%s' "$b" | grep -oE 'sim-[a-z]+-[0-9]+' | head -1)"
  [ -z "$sid" ] && return 0
  sid="$(printf '%s' "$sid" | tr '[:lower:]' '[:upper:]')"
  grep -rl "scenario_id: ${sid}\b" scenarios/ 2>/dev/null | head -1
}

# ---------------------------------------------------------------------------
# args
# ---------------------------------------------------------------------------
DRY=0; DO_LIST=0; WANT_BRANCH=0; BRANCH=""; VERIFY=1; REFS=0; TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --list) DO_LIST=1 ;;
    --dry-run) DRY=1 ;;
    --no-verify) VERIFY=0 ;;
    --refs) REFS=1 ;;
    --branch) WANT_BRANCH=1; case "${2:-}" in ""|--*) : ;; *) BRANCH="$2"; shift ;; esac ;;
    -h|--help) sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --*) die "unknown option: $1 (try --help)" ;;
    *) TARGET="$1" ;;
  esac; shift
done

if [ "$DO_LIST" = 1 ]; then
  info "process/behavioral coverage backlog — endpoint/process cards with NO BIOC (author Agent/IOC/BIOC/WildFire alerts, not correlation):"
  n=0
  while IFS=$'\t' read -r id miss plane prio card; do
    [ -z "$id" ] && continue; n=$((n+1))
    tag=$([ "$prio" = 0 ] && echo "${c_grn}priority${c_rst}" || echo "${c_dim}posture ${c_rst}")
    printf '  %-16s missing[%-16s] %-7s %b  %s%s%s\n' "$id" "$miss" "$plane" "$tag" "$c_dim" "$card" "$c_rst"
  done < <(backlog_tsv)
  [ "$n" = 0 ] && log "  (none — every endpoint card already has a BIOC)"
  log ""; log "enrich one: 'scripts/detect-dev.sh <TTP-ID>'  ·  no arg takes the first priority card"
  exit 0
fi

# ---------------------------------------------------------------------------
# resolve target + mode
# ---------------------------------------------------------------------------
MODE=""; CARD=""; TTP_ID=""; BRIEF=""; SCN=""
if [ -z "$TARGET" ]; then
  line="$(backlog_tsv | head -1 || true)"
  [ -z "$line" ] && die "no BIOC-less endpoint card left. Pass a technique id/brief to author a new TTP."
  TTP_ID="$(printf '%s' "$line" | cut -f1)"; MISSING="$(printf '%s' "$line" | cut -f2)"
  CARD="$(card_for_id "$TTP_ID")"; MODE="enrich"
  info "no target — enriching next priority card: $TTP_ID (missing: $MISSING)"
elif printf '%s' "$TARGET" | grep -Eq '^TTP-[0-9]{4}-[0-9]{4}$'; then
  TTP_ID="$TARGET"; CARD="$(card_for_id "$TTP_ID")"
  [ -z "$CARD" ] && die "no TTP card for $TTP_ID"; MODE="enrich"
else
  BRIEF="$TARGET"; MODE="author"
fi
[ "$MODE" = "enrich" ] && SCN="$(scenario_for_card "$CARD")"

# ---------------------------------------------------------------------------
# assemble prompt context (inventory, attack payload, artifacts, exemplars)
# ---------------------------------------------------------------------------
build_enrich_ctx() {
  python3 - "$CARD" "$TTPS_DIR" "${MISSING:-BIOC}" <<'PY'
import json, glob, os, sys, textwrap
card = sys.argv[1]; ttps = sys.argv[2]; missing = sys.argv[3]
d = json.load(open(card)); det = d.get("detections") or {}; ex = d.get("execution") or {}
def ne(v): return bool(v) and (len(v) > 0 if isinstance(v, list) else True)
inv = {k: (len(det.get(k)) if isinstance(det.get(k), list) else (1 if det.get(k) else 0))
       for k in ("biocs","abiocs","iocs","xql_queries","analytics_modules","correlation_rules")}
print("INVENTORY  " + ", ".join(f"{k}={v}" for k, v in inv.items()))
payload = (((ex.get("payload") or {}).get("code")) or "").strip()
if payload:
    print("\nATTACK PAYLOAD (card.execution.payload.code — key EVERY detection to these commands):")
    for ln in payload.splitlines(): print("  " + ln)
arts = ex.get("expected_artifacts") or []
if arts:
    print("\nOBSERVABLE ARTIFACTS (card.execution.expected_artifacts):")
    for a in arts:
        print(f"  - [{a.get('artifact_type')}] {a.get('description','')}")
        if a.get("expected_value"): print(f"      where: {a['expected_value']}")
# inline exemplars for each missing layer, pulled live from the corpus
want = {"BIOC":"biocs","ABIOC":"abiocs","IOC":"iocs"}
need = [want[m] for m in missing.split("+") if m in want]
print("\nEXEMPLARS (copy the field shape EXACTLY, adapt values to THIS attack):")
for L in need:
    for f in sorted(glob.glob(os.path.join(ttps, "TTP-*.json"))):
        if os.path.abspath(f) == os.path.abspath(card): continue
        v = (json.load(open(f)).get("detections") or {}).get(L)
        if isinstance(v, list) and v:
            s = json.dumps(v[0], ensure_ascii=False)
            s = s if len(s) <= 1100 else s[:1100] + " …(truncated; read the file)"
            print(f"  detections.{L}[0]: {s}")
            break
PY
}

# inspiration for AUTHOR mode: real atomic-red-team commands + (for malware/
# detonation briefs) the TTP-0085 safe-detonation exemplar and curated sources.
build_author_ctx() {
  local b="$BRIEF" techs mal=0
  techs="$(printf '%s' "$b" | grep -oE 'T[0-9]{4}(\.[0-9]{3})?' | sort -u)"
  printf '%s' "$b" | grep -qiE 'malware|detonat|ransomware|sample|payload|backdoor|implant|c2|github|wildfire|eicar|\belf\b' && mal=1
  echo "INSPIRATION — ground every command in a REAL source; do NOT invent CLI:"
  echo "  * atomic-red-team is checked out at sources/atomic-red-team/atomics/ (337 techniques)."
  echo "    For each technique in scope READ sources/atomic-red-team/atomics/<Txxxx>/<Txxxx>.yaml"
  echo "    and lift its real executor.command / input_arguments as your step commands."
  if [ -n "$techs" ]; then
    echo "  * techniques in the brief — read these atomics first:"
    for t in $techs; do
      if [ -d "sources/atomic-red-team/atomics/$t" ]; then
        echo "      sources/atomic-red-team/atomics/$t/$t.yaml   (present)"
      else echo "      $t   (no atomic — design from MITRE + Unit 42 reporting)"; fi
    done
  fi
  echo "  * study 2-3 existing rich cards for depth (multi-step kill chain, causality):"
  echo "      detection_scanner/ttps/TTP-2026-0085-sim-cdr-013.json   (malware-from-github)"
  echo "      detection_scanner/ttps/TTP-2026-0026-sim-cdr-005.json   (WildFire detonation)"
  echo "  * make it ADVANCED: a multi-step kill chain (recon -> retrieval -> staging ->"
  echo "    execution-shape -> impact), each step's causality{parent_step,pivot} forming"
  echo "    ONE connected process spine, >=1 external_tools[].adapter_ref where a pack exists."
  # valid index refs for the plane named in the brief — REUSE one, never invent
  local plane
  plane="$(printf '%s' "$b" | grep -oiE '\b(edr|cdr|itdr|ndr|koi|mp|aispm|aiacc|airs|cspm|asm|tim|email|browser|cloud)\b' | head -1 | tr '[:lower:]' '[:upper:]')"
  if [ -n "$plane" ]; then
    echo "  * VALID index refs for plane ${plane} — bind uc_ref/tc_ref/pov_scenario_id to ONE"
    echo "    whose description fits your scenario (REUSE a real ref; inventing one fails check-refs):"
    python3 - "$plane" <<'PY'
import csv, sys, glob
plane=sys.argv[1]; seen=set(); out=[]
for f in sorted(glob.glob("docs/uc_tc_mapping/_v2.2-source/*.csv")):
    try: rows=list(csv.reader(open(f)))
    except Exception: continue
    for r in rows:
        tc=next((c for c in r if c.startswith(f"TC-{plane}-")), None)
        if not tc or tc in seen: continue
        uc=next((c for c in r if c.startswith("UCS-")), "")
        pov=next((c for c in r if c.startswith("POV-SC-")), "")
        cat=next((c for c in r if c in ("DET","HNT","POS","PLT","AUT")), "")
        desc=r[5] if len(r)>5 else ""
        if cat in ("DET","HNT"): seen.add(tc); out.append((tc,uc,pov,desc[:66]))
for tc,uc,pov,desc in out[:8]:
    print(f"      uc_ref {uc} · tc_ref {tc} · pov {pov}  — {desc}")
if not out: print("      (none parsed — grep docs/uc_tc_mapping/_v2.2-source/*.csv for TC-%s, or reuse a scenarios/*/ ref)" % plane)
PY
  fi
  if [ "$mal" = 1 ]; then
    cat <<'MAL'

  MALWARE-RETRIEVAL / DETONATION — follow the repo SAFETY DOCTRINE exactly
  (exemplar detection_scanner/ttps/TTP-2026-0085-sim-cdr-013.json — READ it first):
    * CortexSim NEVER executes real malware. Reproduce ONLY the detectable telemetry
      SHAPE: wget the sample from the public repo to a SCRATCH path (egress +
      file-write shape), then STUB the chmod/execute (download then an echo stub).
      Say so in the payload comments.
    * Track the REAL SHA-256(s) as detections.iocs[]: ioc_type file-sha256,
      reputation_required: malicious, cortex_severity_override: critical, context
      naming the family + "CortexSim does not execute it" (IOC coverage, no detonation).
    * Detections: BIOC on "minimal/busybox container wget-s an ELF from a public code
      repo and stages it executable" (T1610); XQL on the malware-repo retrieval egress;
      the file-hash IOCs. WildFire/malware verdict = IOC + dropped-binary BIOC.
    * SAFE public sample sources (pin a commit/release; verify the path resolves):
      github.com/timb-machine/linux-malware · github.com/MalwareSamples/Linux-Malware-Samples
      · github.com/ytisf/theZoo (pwd-protected) · EICAR string / WildFire test ELF
      (wildfire.paloaltonetworks.com/publicapi/test/elf) for a HARMLESS detonation.
    * NEVER arm a live sample (no real chmod+exec), C2 to TEST-NET-1 only (192.0.2.0/24),
      everything gated on a lab env + a cleanup block.
MAL
  fi
}

if [ "$MODE" = "enrich" ]; then
  MISSING="${MISSING:-$(backlog_tsv | awk -F'\t' -v id="$TTP_ID" '$1==id{print $2}')}"
  [ -z "$MISSING" ] && MISSING="BIOC"
  PLANE="$(basename "$CARD" | grep -oE 'sim-[a-z]+-' | head -1 | sed 's/sim-//;s/-//')"
  CTX="$(build_enrich_ctx)"
  read -r -d '' PROMPT <<EOF || true
ENRICH the process / behavioral / malware detection coverage of an existing
Cortex POV TTP. Author the alerts that a Cortex ENDPOINT/agent would fire on the
attack this card already runs.

CARD:     ${CARD}  (id ${TTP_ID}, plane ${PLANE})
SCENARIO: ${SCN:-"(none found — enrich the card only)"}

${CTX}

This card is MISSING a usable: ${MISSING//+/, }.

AUTHOR, in THIS priority order — Agent/ABIOC, IOC, BIOC, WildFire, exploit,
malware, process. Do NOT author or modify correlation_rules: correlation is only
stitching/grouping and is out of scope here.
  - BIOC  -> detections.biocs[]: {name, description, logic (XQL keyed on the
            actor_process_* / causality_actor_process_* / action_file_* /
            action_remote_ip fields the PAYLOAD above produces), mitre_technique_ids,
            severity, suppression_window_seconds}. This is the primary deliverable.
            It MUST be DISTINCT from the card's existing abiocs/xql_queries — a
            deterministic PROCESS/FILE rule on the specific process image names,
            file paths and args the PAYLOAD runs (e.g. sh/bash spawning
            curl/nc/nmap/python3, a dropped binary executed from /tmp). Do NOT
            copy or restate an existing ABIOC/XQL — different name, different
            detection layer. If a behavioral rule already covers the network
            deviation, your BIOC covers the PROCESS deviation instead.
  - IOC   -> detections.iocs[]: {ioc_type, value, context} for the concrete
            filenames / hashes / domains / IPs the payload drops or contacts
            (e.g. a dropped binary, a C2 IP, a renamed .enc file).
  - ABIOC -> detections.abiocs[]: behavioral-ML rule with behavioral_profile +
            causality_anchor, anchored to the execution's causality chain — ONLY
            when the attack has a learnable behavioral deviation.
  - WildFire/malware -> if the payload downloads/drops/executes a binary, add the
            IOC for it AND a BIOC on its execution from a writable path.
Author ONLY layers that genuinely apply to THIS technique. If this is a pure
cloud/posture TTP with no endpoint process, add a "_detection_notes" string to
the detections block saying so instead of inventing a BIOC.

WIRE each new detection into the scenario: add an expected_detections row to the
right step in the SCENARIO file with ttp_ref ${TTP_ID}, plane, type, description,
and a detection_id that is  <type>-<EXACT slug of the detection's name>  —
lowercase the name, turn every run of non-alphanumerics into a single hyphen.
Example: a BIOC named "CDR-009 Privileged Container Start on Modeled Field"
=> detection_id  bioc-cdr-009-privileged-container-start-on-modeled-field .
The slug MUST match the card object's name slug or it will not resolve (GAP-4) —
do NOT shorten or paraphrase it.

EDITING DISCIPLINE — as important as the content (a broken bracket fails the gate):
  * Exemplars are ALREADY inline above. Do NOT read, glob, or guess any other
    filenames. Read ONLY these two files, by these exact paths:
      ${CARD}
      ${SCN:-(no linked scenario — edit the card only)}
  * Fill an empty array by replacing ONLY the  []  — KEEP the key and both
    brackets. Exact shape (this is the #1 thing people get wrong):
        before:   "biocs": []
        after:    "biocs": [
          { ...your bioc object... }
        ]
    NEVER drop the  "biocs": [  prefix or the closing  ] . Commas between array
    items, no trailing comma. After every edit the file MUST still be valid JSON.
  * Re-read the exact region immediately BEFORE each edit so the old text matches
    byte-for-byte. If an edit fails exact-match, re-read and retry — never edit
    from memory, never give up.
  * MANDATORY after editing the card, run this command EXACTLY and BARE — no
    2>&1, no "; echo", no pipe or redirection (this run is non-interactive; any
    altered form of an allowed command is AUTO-REJECTED):
        python3 detection_scanner/scripts/validate.py --quiet
    If it reports ANY fail your JSON is broken — re-read, fix brackets/commas, and
    re-run until it prints "0 fail". Do NOT finish on a failing validate.

Deliverable = EDITED, VALIDATING files (card + scenario). Think in <think>, edit,
verify, then LEAVE <think>. Never end inside <think> or on a failing validate.
EOF
else
  # pre-allocate concrete ids/paths
  PLANE="$(printf '%s' "$BRIEF" | grep -oiE '\b(edr|cdr|itdr|ndr|koi|mp|aispm|aiacc|airs|cspm|asm|tim|email|browser|cloud)\b' | head -1 | tr '[:upper:]' '[:lower:]')"
  [ -z "$PLANE" ] && PLANE="edr"
  _n="$(ls "$TTPS_DIR"/TTP-2026-*.json 2>/dev/null | grep -oE 'TTP-2026-[0-9]{4}' | sort -u | tail -1 | sed -E 's/.*-([0-9]{4})/\1/')"
  NEXT_TTP="$(printf 'TTP-2026-%04d' $((10#${_n:-0} + 1)))"
  _s="$(ls scenarios/"$PLANE"/*.yml 2>/dev/null | grep -oE "${PLANE}-[0-9]+" | grep -oE '[0-9]+$' | sort -n | tail -1)"
  SCN_NEXT="$(printf '%03d' $(( 10#${_s:-0} + 1 )))"
  PLANE_U="$(printf '%s' "$PLANE" | tr '[:lower:]' '[:upper:]')"
  SIM_ID="SIM-${PLANE_U}-${SCN_NEXT}"
  CARD_PATH="${TTPS_DIR}/${NEXT_TTP}-sim-${PLANE}-${SCN_NEXT}.json"
  SLUG="$(printf '%s' "$BRIEF" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-40 | sed 's/-*$//')"
  [ -z "$SLUG" ] && SLUG="authored"
  SCN_PATH="scenarios/${PLANE}/${PLANE}-${SCN_NEXT}-${SLUG}.yml"
  # scaffold both files from a plane-matched COMPLETE exemplar so the agent EDITS
  # (its proven strength) rather than writing a full schema from scratch (it
  # under-builds). In --dry-run we only pick/report; we never write.
  SCAF="$(DO_WRITE=$([ "$DRY" = 1 ] && echo 0 || echo 1) python3 - "$PLANE" "$NEXT_TTP" "$SIM_ID" "$CARD_PATH" "$SCN_PATH" "$TTPS_DIR" "$BRIEF" <<'PY'
import json, sys, glob, os, re
plane, next_ttp, sim_id, card_path, scn_path, ttps = sys.argv[1:7]
brief = sys.argv[7] if len(sys.argv) > 7 else ""
do_write = os.environ.get("DO_WRITE") == "1"
ex_card = None
for c in sorted(glob.glob(os.path.join(ttps, f"*sim-{plane}-*.json"))):
    try: d = json.load(open(c))
    except Exception: continue
    req = all(k in d for k in ('schema_version','status','metadata','identity','mitre_attack','execution','detections','panw_mapping','references'))
    det = d.get('detections', {})
    rich = bool(det.get('biocs')) and bool((d.get('execution', {}).get('payload', {}) or {}).get('code'))
    if req and rich: ex_card = c; break
if not ex_card:
    print("SCAFFOLD_ERR=1"); sys.exit(0)
m = re.search(rf"sim-{plane}-(\d+)", os.path.basename(ex_card)); num = m.group(1) if m else ""
ex_scn = next(iter(glob.glob(f"scenarios/{plane}/{plane}-{num}-*.yml")), "")
uc = tc = pov = ""
if ex_scn:
    t = open(ex_scn).read()
    uc = (re.search(r'(?m)^uc_ref:\s*"?([A-Za-z0-9-]+)', t) or [None, ""])[1]
    tc = (re.search(r'(?m)^tc_ref:\s*"?([A-Za-z0-9-]+)', t) or [None, ""])[1]
    pov = (re.search(r'(?m)^pov_scenario_id:\s*"?([A-Za-z0-9-]+)', t) or [None, ""])[1]
TACTIC = {"T1543":("TA0003","Persistence"),"T1505":("TA0003","Persistence"),
          "T1546":("TA0003","Persistence"),"T1059":("TA0002","Execution"),
          "T1071":("TA0011","Command and Control"),"T1095":("TA0011","Command and Control"),
          "T1218":("TA0005","Defense Evasion"),"T1572":("TA0011","Command and Control"),
          "T1003":("TA0006","Credential Access"),"T1574":("TA0005","Defense Evasion"),
          "T1486":("TA0040","Impact"),"T1105":("TA0011","Command and Control"),
          "T1610":("TA0005","Defense Evasion"),"T1611":("TA0004","Privilege Escalation")}
if do_write:
    d = json.load(open(ex_card)); d['id'] = next_ttp
    # BLANK the content so the agent FILLS it (enrich-style edit = its strength),
    # instead of overwriting the whole card (which under-builds the schema).
    techs = re.findall(r'T\d{4}(?:\.\d{3})?', brief)
    if techs:
        tl = []
        for tid in techs:
            base = tid.split(".")[0]; ta, tn = TACTIC.get(base, ("TA0002","Execution"))
            e = {"technique_id": base, "name": tid, "tactic_ids":[ta], "tactic_names":[tn]}
            if "." in tid: e["subtechnique_id"] = tid
            tl.append(e)
        d.setdefault("mitre_attack", {})["techniques"] = tl
    d.setdefault("identity", {})["name"] = "<<FILL: detection title for the brief>>"
    d["execution"].setdefault("payload", {})["code"] = "# <<FILL: the real attack CLI for the brief, safe-detonation doctrine>>"
    d["execution"]["expected_artifacts"] = []
    for k in ("biocs","iocs","xql_queries","abiocs","correlation_rules"):
        if k in d.get("detections", {}): d["detections"][k] = []
    json.dump(d, open(card_path, 'w'), indent=2); open(card_path, 'a').write('\n')
    if ex_scn:
        t2 = re.sub(r'(?m)^scenario_id:.*$', f'scenario_id: {sim_id}', t, count=1)
        open(scn_path, 'w').write(t2)
print(f"EX_CARD={ex_card}"); print(f"EX_SCN={ex_scn}")
print(f"UC={uc}"); print(f"TC={tc}"); print(f"POV={pov}")
PY
)"
  eval "$SCAF"
  [ "${SCAFFOLD_ERR:-0}" = 1 ] && die "no complete exemplar card for plane ${PLANE} to scaffold from."
  AUTHOR_CTX="$(build_author_ctx)"
  read -r -d '' PROMPT <<EOF || true
AUTHOR a NEW, ADVANCED Cortex POV TTP for: ${BRIEF}

I SCAFFOLDED two valid files for you. The schema, the id (${NEXT_TTP}), the
scenario_id (${SIM_ID}), and VALID refs (uc_ref ${UC} · tc_ref ${TC} · pov ${POV})
are ALREADY correct — KEEP them untouched. Your ONLY job is to FILL the BLANKED
fields with content for THIS brief, using the EDIT tool (NOT write/overwrite —
editing empty fields is exactly like the enrich task you do well). Do it NOW; do
not rewrite the whole file, do not plan, do not ask.

  CARD:     ${CARD_PATH}
  SCENARIO: ${SCN_PATH}

${AUTHOR_CTX}

FILL the CARD (${CARD_PATH}) — edit only these fields, leave the rest as-is:
  - identity.name  : replace "<<FILL: detection title for the brief>>"
  - execution.payload.code : replace the "# <<FILL ...>>" placeholder with the
    REAL multi-step attack CLI (lift commands from the atomics above).
  - execution.expected_artifacts : fill the [] with the file/process/network
    observables the payload produces.
  - detections : fill the empty "biocs": [] / "iocs": [] / "xql_queries": []
    arrays (keep the key + brackets, like enrich) with Agent/IOC/BIOC/WildFire/
    process alerts keyed to the payload, each a DISTINCT layer. Leave
    correlation_rules empty.
FILL the SCENARIO (${SCN_PATH}) — edit name, mitre_*, detection_types[], and the
steps[] (command / identity / mitre_technique / causality{parent_step,pivot}),
each step's expected_detections[].detection_id = <type>-<exact slugified card
detection name> so it resolves (GAP-4). KEEP scenario_id and the refs.

After each card edit run BARE (no 2>&1 / ; / pipe — auto-rejected), fix until 0 fail:
  python3 ${EXPORT_SCRIPT}
  python3 detection_scanner/scripts/validate.py --quiet
  make check-refs
Keep the card VALID JSON (fill [] as [ {...} ], keep brackets/commas). Deliverable
= the two EDITED files. Think in <think>, edit, verify, LEAVE <think>. Never finish
on a failing gate or by only planning.
EOF
fi

# ---------------------------------------------------------------------------
# plan / dry-run
# ---------------------------------------------------------------------------
info "detection-development job  [mode: ${MODE}]"
log  "  repo    : $REPO"
log  "  agent   : $AGENT  (${DETECT_DEV_MODEL:-gateway/cpp-offsec})"
[ -n "$TTP_ID" ] && log "  card    : $TTP_ID  ($CARD)"
[ -n "$SCN" ]    && log "  scenario: $SCN"
[ -n "$BRIEF" ]  && log "  brief   : $BRIEF"
log  "  priority: Agent/ABIOC · IOC · BIOC · WildFire · exploit · malware · process   (correlation = out of scope)"
log  "  verify  : export + validate.py$([ "$REFS" = 1 ] && echo ' + make check-refs')"

if [ "$DRY" = 1 ]; then
  log ""; info "DRY RUN — agent prompt that WOULD be sent:"
  printf '%s\n%s\n%s\n' "$c_dim" "$PROMPT" "$c_rst"
  info "no agent invoked, nothing written."; exit 0
fi

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
command -v "$OPENCODE_BIN" >/dev/null 2>&1 || die "opencode not found (set OPENCODE_BIN)."
curl -sf -m 5 http://localhost:4000/v1/models >/dev/null 2>&1 || \
  warn "LiteLLM gateway :4000 not reachable — cpp-offsec routes through it; the run may fail."

if [ "$WANT_BRANCH" = 1 ]; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    warn "tree is dirty — refusing to branch (would carry WIP). Working in place."
  else
    if [ -z "$BRANCH" ]; then
      if [ -n "$TTP_ID" ]; then slug="$(printf '%s' "$TTP_ID" | tr '[:upper:]' '[:lower:]')"
      else slug="$(printf '%s' "$BRIEF" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-32)"; fi
      BRANCH="feature/ttp-${slug}"
    fi
    info "creating isolation branch: $BRANCH (contract: cut from dev)"; git switch -c "$BRANCH"
  fi
fi

# ---------------------------------------------------------------------------
# run the agent
# ---------------------------------------------------------------------------
info "invoking $AGENT …  (local 35B reasoning model; expect minutes)"
set +e
if [ -n "${DETECT_DEV_MODEL:-}" ]; then
  "$OPENCODE_BIN" run "$PROMPT" --agent "$AGENT" --model "$DETECT_DEV_MODEL"
else
  "$OPENCODE_BIN" run "$PROMPT" --agent "$AGENT"
fi
rc=$?; set -e
[ $rc -ne 0 ] && warn "opencode exited $rc — continuing to regenerate/verify for inspection."

# ---------------------------------------------------------------------------
# regenerate exports (FULL — never --ttp: scoped runs truncate exports/README.md) + verify
# ---------------------------------------------------------------------------
if [ "$VERIFY" = 1 ]; then
  info "regenerating export artifacts (full, deterministic) …"
  python3 "$EXPORT_SCRIPT" >/dev/null || warn "export_artifacts.py returned non-zero"
  info "running validate.py …"
  if python3 detection_scanner/scripts/validate.py --quiet; then
    printf '%svalidate.py: PASS%s\n' "$c_grn" "$c_rst" >&2
  else warn "validate.py reported failures — fix before landing."; fi
  if [ "$REFS" = 1 ]; then
    info "running make check-refs (docker) …"; make check-refs || warn "check-refs failed"
  fi
fi

# ---------------------------------------------------------------------------
# review surface — NEVER pushes
# ---------------------------------------------------------------------------
info "done. Review surface (nothing pushed):"
git status --short -- "$TTPS_DIR" detection_scanner/exports scenarios >&2 || true
log ""
[ -n "$TTP_ID" ] && log "  this card:   git diff -- ${TTPS_DIR}/${TTP_ID}* ${SCN}"
log "  detections:  git diff -- $TTPS_DIR scenarios"
log "  ref gate:    make check-refs      (docker; validates scenario uc_ref/tc_ref)"
log "  full gate:   make validate"
log "  land it:     feature/ttp-* branch from dev, stage card + scenario + exports BY NAME, commit, PR (Gate A)."
