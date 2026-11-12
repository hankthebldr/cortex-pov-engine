#!/usr/bin/env bash
# Long-running dispatch: author Windows + Linux endpoint ANALYTICS/ABIOC detections
# off the gateway API (cpp-coder). Per-card validate (reverts a card that fails),
# XQL-verb sweep, coverage log. Review-gated: never pushes.
set -u
cd /home/henry/Github/cortex-pov-engine
D=scripts/detdev
GEN="$D/genfrag.py"; PKG="$D/package.py"
WIN_CARD="$(ls detection_scanner/ttps/TTP-2026-0002-*.json)"; WIN_SCN="$(ls scenarios/edr/edr-002-*.yml)"
LIN_CARD="$(ls detection_scanner/ttps/TTP-2026-0032-*.json)"; LIN_SCN="$(ls scenarios/edr/edr-001-*.yml)"
LOG="$D/coverage-log.md"; TMP=$(mktemp -d)
[ -f "$LOG" ] || printf '# Endpoint analytics coverage log\n\n| platform | alert | TTP | scenario | result |\n|---|---|---|---|---|\n' > "$LOG"

# curated high-value endpoint analytics detectors: platform|techs|alert|brief
JOBS=$(cat <<'LIST'
windows|T1036.005|A process is masquerading as a common Microsoft product|Malware drops svchost.exe/lsass.exe into a non-system path and runs it, masquerading as a signed Microsoft product
windows|T1574.001|A rare DLL signed by an uncommon vendor was hijacked into a Microsoft process|A rarely-seen DLL from an uncommon signer is side-loaded into a Microsoft-signed process via search-order hijack
windows|T1021.001|Abnormal RDP connections to multiple hosts from a rarely seen host|A rarely-active host opens outbound RDP to many internal hosts in a short window (lateral sweep)
windows|T1053.005,T1055|An unsigned process created a scheduled task and performed an injection|An unsigned process creates a scheduled task via schtasks and then injects into a remote process
windows|T1021.002,T1569.002|Attempt to execute a command on a remote host using PsExec|PsExec.exe creates a remote service and runs a command on another host (lateral movement)
windows|T1047|Command execution via wmiexec|wmiexec-style remote command execution spawns cmd.exe under WmiPrvSE with ADMIN$ output redirection
windows|T1003.001|Copy a process memory file|A tool copies an LSASS process-memory dump file from a writable path for offline credential extraction
windows|T1059.001,T1027|PowerShell runs suspicious base64-encoded commands|powershell.exe runs a long -EncodedCommand base64 blob that decodes to a download-and-run
windows|T1003.003|Suspicious dump of ntds.dit using Shadow Copy with ntdsutil or vssadmin|ntdsutil/vssadmin creates a shadow copy and copies ntds.dit + SYSTEM for domain credential theft
windows|T1543.003|An uncommon service was started|sc.exe creates and starts a new Windows service with a rarely-seen binary path for persistence
windows|T1059.001|64-bit PowerShell spawning an uncommon 32-bit PowerShell|A 64-bit powershell.exe spawns the uncommon SysWOW64 32-bit powershell to evade controls
windows|T1562.001|New addition to Windows Defender exclusion list|Add-MpPreference adds a path/extension to the Defender exclusion list to stage a payload
windows|T1564.001|Hidden Attribute was added to a file using attrib|attrib +h +s hides a dropped payload under a system directory
windows|T1078.003|A rare local administrator login|An interactive logon with a local administrator account rarely seen on this host
windows|T1053.005,T1218|An uncommon lolbin execution by scheduled task|A scheduled task launches an uncommon LOLBin (mshta/regsvr32/rundll32) with a remote argument
linux|T1110.001,T1548.003|Possible brute force on sudo user|Repeated failed sudo authentications for a user in a short window, then a success
linux|T1036.004|Masquerading as the Linux crond process|A malicious process renames itself to crond and runs from a non-standard path
linux|T1548.001|Setuid and Setgid file bit manipulation|chmod u+s on a shell/binary in a writable path to stage a setuid privilege-escalation
linux|T1552.003|Reading bash command history file|A non-interactive process reads ~/.bash_history to harvest credentials and commands
linux|T1027.001|Possible binary padding using dd|dd appends null bytes to an ELF to inflate its size and evade hash/size detection
linux|T1048,T1560|A compressed file was exfiltrated over SSH|A tar/gzip archive is piped over an outbound ssh/scp session to exfiltrate staged data
linux|T1222.002|Adding execution privileges|chmod +x on a freshly downloaded file in /tmp before executing it
linux|T1611|Kubernetes nsenter container escape|A privileged container uses nsenter --target 1 --mount to break into the host namespaces
linux|T1610,T1611|Suspicious process execution in a privileged container|An interactive shell spawns host-recon tooling inside a privileged Kubernetes container
linux|T1071.001|Suspicious curl user agent|curl beacons outbound with an unusual/empty User-Agent to a rare host (C2 shape)
LIST
)

nextids() {
  local pl="$1" _n _s
  _n="$(ls detection_scanner/ttps/TTP-2026-*.json | grep -oE 'TTP-2026-[0-9]{4}' | sort -u | tail -1 | sed -E 's/.*-([0-9]{4})/\1/')"
  NEXT_TTP="TTP-2026-$(printf '%04d' $((10#$_n+1)))"
  _s="$(ls scenarios/edr/*.yml | grep -oE 'edr-[0-9]+' | grep -oE '[0-9]+$' | sort -n | tail -1)"
  SCN_NEXT="$(printf '%03d' $((10#$_s+1)))"
}

ok=0; skip=0; n=0
echo "analytics pass start $(date '+%F %H:%M:%S')"
while IFS='|' read -r plat techs alert brief; do
  [ -z "${plat:-}" ] && continue
  n=$((n+1)); nextids "$plat"
  tag="$(printf '%s' "$alert" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-36 | sed 's/-*$//')"
  frag="$TMP/f-$n.json"; card="detection_scanner/ttps/${NEXT_TTP}-sim-edr-${SCN_NEXT}.json"
  scn="scenarios/edr/edr-${SCN_NEXT}-${plat}-${tag}.yml"
  exc="$WIN_CARD"; exs="$WIN_SCN"; [ "$plat" = linux ] && { exc="$LIN_CARD"; exs="$LIN_SCN"; }
  if ! timeout 200 python3 "$GEN" "$frag" edr "${plat} endpoint: ${brief}; should trigger the Cortex analytics/ABIOC '${alert}'" "$techs" cpp-coder >/dev/null 2>&1; then
    echo "  [$n] GENFAIL: $alert"; printf '| %s | %s | - | - | genfail |\n' "$plat" "$alert" >> "$LOG"; skip=$((skip+1)); continue
  fi
  python3 -c "import json;f='$frag';d=json.load(open(f));d['target_platform']='$plat';d['analytics_modules']=['Cortex XSIAM Analytics (XDR Agent) — ${alert//\'/}; Required Data: XDR Agent; ATT&CK ${techs}. Ref: cortex-docs.paloaltonetworks.com/analytics-alerts.'];json.dump(d,open(f,'w'),indent=2)"
  python3 "$PKG" "$frag" "$exc" "$exs" "$card" "$scn" "$NEXT_TTP" "SIM-EDR-$SCN_NEXT" edr >/dev/null 2>&1
  # sweep non-Cortex XQL verbs on the new card
  python3 - "$card" <<'PY'
import json,sys,re
f=sys.argv[1]; d=json.load(open(f)); ch=False
for coll in ("biocs","xql_queries","abiocs"):
    for it in d.get("detections",{}).get(coll,[]):
        for fld in ("logic","query"):
            v=it.get(fld)
            if not v: continue
            nv=re.sub(r'\|\s*(stats|aggregate|summarize)\b','| comp',v)
            nv=re.sub(r'\|\s*count\(\)\s+by\b','| comp count() as cnt by',nv)
            nv=re.sub(r'\|\s*(count\([^)]*\)|count_distinct\([^)]*\))\s+as\b',r'| comp \1 as',nv)
            nv=re.sub(r'\|\s*(group_by|groupby|group by|by)\s+','| comp count() as cnt by ',nv)
            nv=re.sub(r'\|\s*count\b(?!\()','| comp count() as cnt by',nv)
            if nv!=v: it[fld]=nv; ch=True
if ch: json.dump(d,open(f,"w"),indent=2); open(f,"a").write("\n")
PY
  # validate this card in isolation-ish: whole-corpus validate; revert if it introduces a fail
  if python3 detection_scanner/scripts/validate.py --quiet 2>&1 | grep -q "0 fail"; then
    ok=$((ok+1)); echo "  [$n] OK  $NEXT_TTP  $alert"
    printf '| %s | %s | %s | SIM-EDR-%s | ok |\n' "$plat" "$alert" "$NEXT_TTP" "$SCN_NEXT" >> "$LOG"
  else
    rm -f "$card" "$scn"; skip=$((skip+1)); echo "  [$n] REVERTED (validate) $alert"
    printf '| %s | %s | %s | - | reverted |\n' "$plat" "$alert" "$NEXT_TTP" >> "$LOG"
  fi
done <<< "$JOBS"

python3 detection_scanner/scripts/export_artifacts.py >/dev/null 2>&1
echo "=== built $ok, skipped $skip of $n ==="
# bump size guard by the number of scenarios actually added
cur=$(grep -oE 'assert len\(parsed\) == [0-9]+' tests/engine/test_corpus_refs_strict.py | grep -oE '[0-9]+')
new=$((cur+ok))
sed -i "s/assert len(parsed) == $cur/assert len(parsed) == $new/" tests/engine/test_corpus_refs_strict.py
echo "size guard: $cur -> $new"
echo "=== validate + check-refs ==="
python3 detection_scanner/scripts/validate.py --quiet 2>&1 | tail -1
make check-refs 2>&1 | tail -2
echo "analytics pass done $(date '+%F %H:%M:%S')"
rm -rf "$TMP"
