#!/usr/bin/env bash
# Full-list endpoint analytics pass: grind every alert in targets-{windows,linux}-analytics.txt
# off the gateway API. Infers MITRE per alert, per-card validate + auto-revert, resumable
# (skips alerts already OK in coverage-log.md). Review-gated: never pushes. Long-running.
set -u
cd /home/henry/Github/cortex-pov-engine
D=scripts/detdev; GEN="$D/genfrag.py"; PKG="$D/package.py"; LOG="$D/coverage-log.md"
WIN_CARD="$(ls detection_scanner/ttps/TTP-2026-0002-*.json)"; WIN_SCN="$(ls scenarios/edr/edr-002-*.yml)"
LIN_CARD="$(ls detection_scanner/ttps/TTP-2026-0032-*.json)"; LIN_SCN="$(ls scenarios/edr/edr-001-*.yml)"
TMP=$(mktemp -d)
[ -f "$LOG" ] || printf '# Endpoint analytics coverage log\n\n| platform | alert | TTP | scenario | result |\n|---|---|---|---|---|\n' > "$LOG"

nextids() {
  local _n _s
  _n="$(ls detection_scanner/ttps/TTP-2026-*.json | grep -oE 'TTP-2026-[0-9]{4}' | sort -u | tail -1 | sed -E 's/.*-([0-9]{4})/\1/')"
  NEXT_TTP="TTP-2026-$(printf '%04d' $((10#$_n+1)))"
  _s="$(ls scenarios/edr/*.yml | grep -oE 'edr-[0-9]+' | grep -oE '[0-9]+$' | sort -n | tail -1)"
  SCN_NEXT="$(printf '%03d' $((10#$_s+1)))"
}
covered() { grep -Fq "| $1 |" "$LOG" 2>/dev/null; }   # alert already logged (any result)

ok=0; skip=0; n=0; start_ok=$(grep -c '| ok |' "$LOG")
echo "full analytics pass start $(date '+%F %H:%M:%S')"
process_file() {
  local file="$1" plat="$2" exc="$3" exs="$4"
  while IFS=$'\t' read -r alert url; do
    [ -z "${alert:-}" ] && continue
    n=$((n+1))
    if covered "$alert"; then skip=$((skip+1)); continue; fi
    nextids
    local tag frag card scn
    tag="$(printf '%s' "$alert" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-40 | sed 's/-*$//')"
    frag="$TMP/f-$n.json"; card="detection_scanner/ttps/${NEXT_TTP}-sim-edr-${SCN_NEXT}.json"
    scn="scenarios/edr/edr-${SCN_NEXT}-${plat}-${tag}.yml"
    if ! timeout 200 python3 "$GEN" "$frag" edr "${plat} endpoint: reproduce the attacker behavior that the Cortex analytics/ABIOC alert '${alert}' detects; emit the real ${plat} attack commands + a behavioral ABIOC keyed to it" "" cpp-coder >/dev/null 2>&1; then
      printf '| %s | %s | - | - | genfail |\n' "$plat" "$alert" >> "$LOG"; skip=$((skip+1)); continue
    fi
    python3 -c "import json;f='$frag';d=json.load(open(f));d['target_platform']='$plat';d['analytics_modules']=['Cortex XSIAM Analytics (XDR Agent) — ${alert//\'/}; Required Data: XDR Agent. Ref: ${url}'];json.dump(d,open(f,'w'),indent=2)" 2>/dev/null || { skip=$((skip+1)); continue; }
    python3 "$PKG" "$frag" "$exc" "$exs" "$card" "$scn" "$NEXT_TTP" "SIM-EDR-$SCN_NEXT" edr >/dev/null 2>&1
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
            nv=re.sub(r'\|\s*(stats|aggregate|summarize)\b','| comp',v)
            nv=re.sub(r'\|\s*count\(\)\s+by\b','| comp count() as cnt by',nv)
            nv=re.sub(r'\|\s*(count\([^)]*\)|count_distinct\([^)]*\))\s+as\b',r'| comp \1 as',nv)
            nv=re.sub(r'\|\s*(group_by|groupby|group by|by)\s+','| comp count() as cnt by ',nv)
            nv=re.sub(r'\|\s*count\(\)\s*(?!(as|by)\b)','| comp count() as cnt by agent_hostname ',nv)
            nv=re.sub(r'\|\s*count\b(?!\()','| comp count() as cnt by',nv)
            if nv!=v: it[fld]=nv; ch=True
if ch:
    json.dump(d,open(f,"w"),indent=2); open(f,"a").write("\n")
PY
    if [ -f "$card" ] && python3 detection_scanner/scripts/validate.py --quiet 2>&1 | grep -q "0 fail"; then
      ok=$((ok+1)); printf '| %s | %s | %s | SIM-EDR-%s | ok |\n' "$plat" "$alert" "$NEXT_TTP" "$SCN_NEXT" >> "$LOG"
    else
      rm -f "$card" "$scn"; skip=$((skip+1)); printf '| %s | %s | %s | - | reverted |\n' "$plat" "$alert" "$NEXT_TTP" >> "$LOG"
    fi
    if [ $((ok % 15)) -eq 0 ] && [ "$ok" -gt 0 ]; then python3 detection_scanner/scripts/export_artifacts.py >/dev/null 2>&1; echo "  ...checkpoint: $ok built ($(date '+%H:%M:%S'))"; fi
  done < "$file"
}

process_file "$D/targets-windows-analytics.txt" windows "$WIN_CARD" "$WIN_SCN"
process_file "$D/targets-linux-analytics.txt"   linux   "$LIN_CARD" "$LIN_SCN"

python3 detection_scanner/scripts/export_artifacts.py >/dev/null 2>&1
echo "=== full pass: built $ok new (skipped $skip), scanned $n ==="
cur=$(grep -oE 'assert len\(parsed\) == [0-9]+' tests/engine/test_corpus_refs_strict.py | grep -oE '[0-9]+')
new=$((cur+ok))
sed -i "s/assert len(parsed) == $cur/assert len(parsed) == $new/" tests/engine/test_corpus_refs_strict.py
echo "size guard: $cur -> $new"
python3 detection_scanner/scripts/validate.py --quiet 2>&1 | tail -1
make check-refs 2>&1 | tail -2
echo "full analytics pass done $(date '+%F %H:%M:%S')"
rm -rf "$TMP"
