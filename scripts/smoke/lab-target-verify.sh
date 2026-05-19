#!/usr/bin/env bash
# ==============================================================================
# CortexSim — lab-target-verify.sh
#
# Runs ON a lab attack target (the box that will execute scenario steps),
# not on the jumpbox.  Confirms the local environment is ready to host a
# cortexsim-agent beacon and that the identity-harness prerequisites hold.
#
# Usage (on the lab target):
#   curl -fsSL https://<jumpbox>/scripts/smoke/lab-target-verify.sh | bash -s -- \
#       --server=https://<jumpbox>:8888
#
# Or locally if the script is rsync'd:
#   bash scripts/smoke/lab-target-verify.sh --server=https://jumpbox.lab:8888
#
# Exit codes:
#   0   target is lab-ready
#   1   missing prerequisite
#   2   cannot reach SimCore
#   3   identity harness check failed
# ==============================================================================
set -uo pipefail

SERVER=""
AGENT_ID="${HOSTNAME:-$(hostname)}-verify"

for arg in "$@"; do
    case "$arg" in
        --server=*)   SERVER="${arg#*=}" ;;
        --agent-id=*) AGENT_ID="${arg#*=}" ;;
        --help|-h)
            head -25 "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
done

if [[ -z "$SERVER" ]]; then
    echo "ERROR: --server=https://<jumpbox>:8888 is required" >&2
    exit 1
fi

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

echo "[lab-target-verify] server=$SERVER  agent_id=$AGENT_ID"
echo

# ----------------------------------------------------------------------
# 1. OS + arch sanity
# ----------------------------------------------------------------------
echo "[1/5] OS / arch"
if [[ "$(uname -s)" == "Linux" ]]; then
    ok "Linux $(uname -r) on $(uname -m)"
else
    warn "Non-Linux target ($(uname -s)) — supported but identity harness may differ"
fi

# ----------------------------------------------------------------------
# 2. Prerequisites for the identity harness
# ----------------------------------------------------------------------
echo "[2/5] Identity harness prerequisites"
HARNESS_OK=1
for cmd in runuser sudo su bash; do
    if command -v "$cmd" >/dev/null 2>&1; then
        ok "$cmd present"
    else
        bad "$cmd missing"
        HARNESS_OK=0
    fi
done

# Service accounts the scenarios reference must be resolvable.  Missing
# users is not always fatal — some plane scenarios skip identities — but
# we surface them so the DC knows what won't fire cleanly.
for user in www-data postgres nobody nginx; do
    if id "$user" >/dev/null 2>&1; then
        ok "user '$user' exists"
    else
        warn "user '$user' missing — scenarios using this identity will degrade to root"
    fi
done

[[ $HARNESS_OK -eq 0 ]] && { bad "identity harness incomplete"; exit 3; }

# ----------------------------------------------------------------------
# 3. SimCore reachable
# ----------------------------------------------------------------------
echo "[3/5] SimCore reachability"
if ! curl -fsS "$SERVER/api/health" -o /tmp/_simcore_health 2>/dev/null; then
    bad "cannot reach $SERVER/api/health"
    exit 2
fi
ok "$(cat /tmp/_simcore_health)"

# ----------------------------------------------------------------------
# 4. Can we register as a beacon?  (proves credential/network is fine)
# ----------------------------------------------------------------------
echo "[4/5] Beacon registration round-trip"
REGISTER_PAYLOAD=$(cat <<EOF
{"agent_id":"$AGENT_ID","hostname":"$(hostname)","os":"linux","capabilities":["smoke-probe"]}
EOF
)
if ! curl -fsS -X POST "$SERVER/api/agents/register" \
        -H 'Content-Type: application/json' \
        -d "$REGISTER_PAYLOAD" -o /tmp/_simcore_register; then
    bad "register POST failed"
    exit 2
fi
ok "registered: $(cat /tmp/_simcore_register)"

# Poll once — must return {"task": null} on a freshly-registered agent
POLL=$(curl -fsS "$SERVER/api/agents/$AGENT_ID/tasks") || { bad "poll failed"; exit 2; }
case "$POLL" in
    *'"task":null'*|*'"task": null'*)
        ok "idle poll returns null task as expected" ;;
    *)
        warn "unexpected poll response: $POLL" ;;
esac

# ----------------------------------------------------------------------
# 5. Optional: cortexsim-agent binary local-execute self-test
# ----------------------------------------------------------------------
echo "[5/5] cortexsim-agent binary"
AGENT_BIN="${AGENT_BIN:-/usr/local/bin/cortexsim-agent}"
if [[ -x "$AGENT_BIN" ]]; then
    if "$AGENT_BIN" --help 2>&1 | grep -q -- '--server'; then
        ok "$AGENT_BIN --help looks correct"
    else
        warn "$AGENT_BIN exists but --help output unexpected"
    fi
else
    warn "no $AGENT_BIN on PATH — install with: scp jumpbox:/opt/cortexsim/bin/cortexsim-agent $AGENT_BIN"
fi

echo
echo "[lab-target-verify] ✓ target is lab-ready"
