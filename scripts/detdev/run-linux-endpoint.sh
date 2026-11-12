#!/usr/bin/env bash
# Linux endpoint attack scenarios (TTP cards + scenarios, process BIOC + behavioral ABIOC)
# off the gateway API. Per-card validate + auto-revert + XQL sweep. Review-gated: never pushes.
set -u
cd /home/henry/Github/cortex-pov-engine
D=scripts/detdev; GEN="$D/genfrag.py"; PKG="$D/package.py"; LOG="$D/coverage-log.md"
EXC="$(ls detection_scanner/ttps/TTP-2026-0032-*.json)"; EXS="$(ls scenarios/edr/edr-001-*.yml)"
TMP=$(mktemp -d)

JOBS=$(cat <<'LIST'
T1053.003|Cron persistence|Attacker adds a malicious crontab entry that periodically curls a payload and pipes it to bash, re-establishing a reverse shell
T1543.002|systemd service persistence|Attacker drops a malicious .service unit into /etc/systemd/system and runs systemctl enable --now for root persistence
T1574.006|ld.so.preload rootkit hook|Attacker writes a malicious shared object path into /etc/ld.so.preload to hook libc and hide processes/files
T1098.004|SSH authorized_keys backdoor|Attacker appends an attacker-controlled public key to ~/.ssh/authorized_keys for persistent remote access
T1548.001|SUID GTFOBins privilege escalation|Attacker abuses a SUID binary (find -exec /bin/sh, or env) to spawn a root shell
T1068|Polkit pkexec pwnkit privilege escalation|Attacker exploits CVE-2021-4034 pkexec (pwnkit) with a crafted environment to escalate to root
T1003.007|Credential dumping from process memory|Attacker reads /proc/PID/maps and dumps process memory via gcore/dd to harvest secrets from memory
T1059.004,T1071.001|Interactive reverse shell|Attacker opens a reverse shell via bash -i to /dev/tcp or a python pty to an external C2 host
T1070.002,T1070.003|Log and shell-history tampering|Attacker truncates /var/log/auth.log, unsets HISTFILE and shreds ~/.bash_history to cover tracks
T1486|Linux file-encryption ransomware impact|Attacker finds documents and encrypts them with openssl, drops a ransom note and removes originals
T1620|Fileless execution via memfd_create|Attacker downloads an ELF and executes it from an anonymous memfd file descriptor, leaving no disk artifact
T1552.001|Cloud/credential file theft|Attacker reads ~/.aws/credentials, ~/.kube/config and ~/.ssh private keys to steal cloud and cluster credentials
LIST
)

nextids() {
  local _n _s
  _n="$(ls detection_scanner/ttps/TTP-2026-*.json | grep -oE 'TTP-2026-[0-9]{4}' | sort -u | tail -1 | sed -E 's/.*-([0-9]{4})/\1/')"
  NEXT_TTP="TTP-2026-$(printf '%04d' $((10#$_n+1)))"
  _s="$(ls scenarios/edr/*.yml | grep -oE 'edr-[0-9]+' | grep -oE '[0-9]+$' | sort -n | tail -1)"
  SCN_NEXT="$(printf '%03d' $((10#$_s+1)))"
}

ok=0; skip=0; n=0
echo "linux endpoint batch start $(date '+%F %H:%M:%S')"
while IFS='|' read -r techs name brief; do
  [ -z "${techs:-}" ] && continue
  n=$((n+1)); nextids
  tag="$(printf '%s' "$name" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-36 | sed 's/-*$//')"
  frag="$TMP/f-$n.json"; card="detection_scanner/ttps/${NEXT_TTP}-sim-edr-${SCN_NEXT}.json"
  scn="scenarios/edr/edr-${SCN_NEXT}-linux-${tag}.yml"
  if ! timeout 200 python3 "$GEN" "$frag" edr "Linux endpoint: ${brief}. Emit the real Linux attack commands + a process BIOC + a behavioral ABIOC keyed to them (actor_process_image_name, action_file_path, action_process_command_line)" "$techs" cpp-coder >/dev/null 2>&1; then
    echo "  [$n] GENFAIL: $name"; printf '| linux | %s | - | - | genfail |\n' "$name" >> "$LOG"; skip=$((skip+1)); continue
  fi
  python3 -c "import json;f='$frag';d=json.load(open(f));d['target_platform']='linux';json.dump(d,open(f,'w'),indent=2)"
  python3 "$PKG" "$frag" "$EXC" "$EXS" "$card" "$scn" "$NEXT_TTP" "SIM-EDR-$SCN_NEXT" linux >/dev/null 2>&1
  python3 - "$card" <<'PY' 2>/dev/null
import json,sys,re
f=sys.argv[1]
try: d=json.load(open(f))
except Exception: sys.exit(0)
ch=False
for coll in ("biocs","xql_queries","abiocs"):
    for it in d.get("detections",{}).get(coll,[]):
        for fld in ("logic","query"):
            v=it.get(fld)
            if not v: continue
            nv=v
            nv=re.sub(r'\|\s*having\b','| filter',nv); nv=re.sub(r'\|\s*where\b','| filter',nv)
            nv=re.sub(r'\|\s*(stats|aggregate|summarize|window|timeslice|analyze|eval|rename)\b','| comp',nv)
            nv=re.sub(r'\|\s*(select|project)\b','| fields',nv)
            nv=re.sub(r'\|\s*count\(\)\s+by\b','| comp count() as cnt by',nv)
            nv=re.sub(r'\|\s*(count\([^)]*\)|count_distinct\([^)]*\))\s+as\b',r'| comp \1 as',nv)
            nv=re.sub(r'\|\s*(group_by|groupby|group by)\s+','| comp count() as cnt by ',nv)
            nv=re.sub(r'\|\s*count\(\)\s*(?!(as|by)\b)','| comp count() as cnt by agent_hostname ',nv)
            nv=re.sub(r'\|\s*count\b(?!\()','| comp count() as cnt by',nv); nv=nv.replace('count(*)','count()')
            if nv!=v: it[fld]=nv; ch=True
if ch: json.dump(d,open(f,"w"),indent=2); open(f,"a").write("\n")
PY
  if [ -f "$card" ] && python3 detection_scanner/scripts/validate.py --quiet 2>&1 | grep -q "0 fail"; then
    ok=$((ok+1)); echo "  [$n] OK  $NEXT_TTP  $name"
    printf '| linux | %s | %s | SIM-EDR-%s | ok |\n' "$name" "$NEXT_TTP" "$SCN_NEXT" >> "$LOG"
  else
    rm -f "$card" "$scn"; skip=$((skip+1)); echo "  [$n] REVERTED $name"
    printf '| linux | %s | %s | - | reverted |\n' "$name" "$NEXT_TTP" >> "$LOG"
  fi
done <<< "$JOBS"

python3 detection_scanner/scripts/export_artifacts.py >/dev/null 2>&1
echo "=== linux batch: built $ok, skipped $skip of $n ==="
cur=$(grep -oE 'assert len\(parsed\) == [0-9]+' tests/engine/test_corpus_refs_strict.py | grep -oE '[0-9]+')
new=$((cur+ok)); sed -i "s/assert len(parsed) == $cur/assert len(parsed) == $new/" tests/engine/test_corpus_refs_strict.py
echo "size guard: $cur -> $new"
python3 detection_scanner/scripts/validate.py --quiet 2>&1 | tail -1
make check-refs 2>&1 | tail -2
echo "linux endpoint batch done $(date '+%F %H:%M:%S')"
rm -rf "$TMP"
