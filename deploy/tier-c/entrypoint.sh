#!/usr/bin/env bash
# ===========================================================================
# CortexSim Tier-C runner entrypoint
# ---------------------------------------------------------------------------
# Runs inside the isolated runner container. Lifecycle:
#
#   1. Start auditd + load the Tier-C ruleset (degrade gracefully if the
#      kernel audit subsystem is unavailable in this container).
#   2. Locate the mounted push bundle ($CORTEXSIM_BUNDLE_DIR, default /bundle).
#   3. Execute the bundle. The bundle is self-contained CortexSim bash that
#      already wraps each step in its identity harness (run_as), so we just
#      run it — the harness produces the setuid/execve transitions we audit.
#   4. Dump the raw audit log + a structured observed-signals JSON summary to
#      $CORTEXSIM_RESULTS_DIR (default /results) for diffing against a
#      scenario's expected_detections.
#
# No network egress: the compose profile pins this container to an internal
# bridge and points /etc/resolv.conf at the sinkhole. Nothing here phones home.
# ===========================================================================
set -uo pipefail

BUNDLE_DIR="${CORTEXSIM_BUNDLE_DIR:-/bundle}"
RESULTS_DIR="${CORTEXSIM_RESULTS_DIR:-/results}"
AUDIT_LOG="/var/log/audit/audit.log"
RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$RESULTS_DIR"

log() { echo "[tier-c $(date -u +%H:%M:%S)] $*" >&2; }

# ---------------------------------------------------------------------------
# 1. Audit subsystem
# ---------------------------------------------------------------------------
AUDIT_MODE="auditd"
start_audit() {
    # auditd needs CAP_AUDIT_CONTROL/WRITE and a kernel audit netlink socket.
    # In some CI / rootless runners that's unavailable; detect and degrade.
    if ! command -v auditctl >/dev/null 2>&1; then
        log "auditctl not present — running in degraded (proc-accounting) mode"
        AUDIT_MODE="degraded"
        return 0
    fi
    mkdir -p /var/log/audit
    : > "$AUDIT_LOG" 2>/dev/null || true

    # Start the daemon (best-effort; auditctl rule load is what actually matters).
    if command -v auditd >/dev/null 2>&1; then
        auditd -n >/dev/null 2>&1 &
        sleep 1
    fi

    # Load our ruleset. If the kernel rejects it (no CAP_AUDIT_CONTROL / no
    # audit support), fall back to degraded mode rather than aborting the run.
    if auditctl -R /etc/audit/rules.d/cortexsim-tier-c.rules >/dev/null 2>&1; then
        log "auditd rules loaded ($(auditctl -l 2>/dev/null | wc -l) rules active)"
    else
        log "kernel rejected audit rules (no CAP_AUDIT_CONTROL?) — degraded mode"
        AUDIT_MODE="degraded"
    fi
}

start_audit

# ---------------------------------------------------------------------------
# 2. Locate the push bundle
# ---------------------------------------------------------------------------
# Operator may mount a single bundle file, or a directory containing one (or
# more) *.sh bundles. CORTEXSIM_BUNDLE_FILE pins an exact file when set.
BUNDLE=""
if [ -n "${CORTEXSIM_BUNDLE_FILE:-}" ] && [ -f "${CORTEXSIM_BUNDLE_FILE}" ]; then
    BUNDLE="${CORTEXSIM_BUNDLE_FILE}"
elif [ -f "$BUNDLE_DIR" ]; then
    BUNDLE="$BUNDLE_DIR"            # operator bind-mounted a file directly
elif [ -d "$BUNDLE_DIR" ]; then
    # Pick the first *.sh; deterministic via sort.
    BUNDLE="$(find "$BUNDLE_DIR" -maxdepth 1 -type f -name '*.sh' | sort | head -n1)"
fi

if [ -z "$BUNDLE" ] || [ ! -f "$BUNDLE" ]; then
    log "ERROR: no push bundle found under $BUNDLE_DIR (mount one at /bundle)"
    cat > "$RESULTS_DIR/observed-signals.json" <<EOF
{"status":"error","reason":"no_bundle","bundle_dir":"$BUNDLE_DIR","run_ts":"$RUN_TS"}
EOF
    exit 2
fi
log "executing push bundle: $BUNDLE"

# ---------------------------------------------------------------------------
# 3. Execute the bundle
# ---------------------------------------------------------------------------
BUNDLE_STDOUT="$RESULTS_DIR/bundle-stdout.log"
BUNDLE_START_EPOCH="$(date +%s)"
# Run under bash explicitly; the bundle's own `set -euo pipefail` + identity
# harness drive execution. We DO NOT let a bundle failure kill the entrypoint —
# a non-zero exit is itself a signal we want to capture and report.
bash "$BUNDLE" >"$BUNDLE_STDOUT" 2>&1
BUNDLE_EXIT=$?
BUNDLE_END_EPOCH="$(date +%s)"
log "bundle exited code=$BUNDLE_EXIT in $((BUNDLE_END_EPOCH - BUNDLE_START_EPOCH))s"

# Give auditd a moment to flush its backlog to disk.
sleep 2

# ---------------------------------------------------------------------------
# 4a. Dump the raw audit log
# ---------------------------------------------------------------------------
RAW_AUDIT_OUT="$RESULTS_DIR/audit.log"
if [ "$AUDIT_MODE" = "auditd" ] && [ -f "$AUDIT_LOG" ]; then
    cp "$AUDIT_LOG" "$RAW_AUDIT_OUT" 2>/dev/null || : > "$RAW_AUDIT_OUT"
else
    : > "$RAW_AUDIT_OUT"
fi

# ---------------------------------------------------------------------------
# 4b. Structured observed-signals summary (JSON)
# ---------------------------------------------------------------------------
# We summarise by audit key so the DC (or a Tier-C reference test in increment
# 2) can diff against expected_detections. We use ausearch when available;
# otherwise we emit empty arrays with mode=degraded so the consumer knows the
# kernel audit data was not captured here (and should rely on bundle-stdout +
# tier-D real telemetry instead).
summary_count() {
    # $1 = audit key. Counts matching records; 0 in degraded mode.
    local key="$1"
    if [ "$AUDIT_MODE" != "auditd" ] || ! command -v ausearch >/dev/null 2>&1; then
        echo 0; return
    fi
    ausearch -i -k "$key" 2>/dev/null | grep -c "type=SYSCALL" 2>/dev/null || echo 0
}

# Distinct executables seen (process tree spine). Best-effort parse of the raw
# log for exe= / comm= tokens under the exec key.
EXEC_LIST="[]"
SETID_LIST="[]"
NET_COUNT=0
CRED_COUNT=0
EXEC_COUNT=0
SETID_COUNT=0

if [ "$AUDIT_MODE" = "auditd" ] && command -v ausearch >/dev/null 2>&1; then
    EXEC_COUNT="$(summary_count cortexsim_exec)"
    SETID_COUNT="$(summary_count cortexsim_setid)"
    NET_COUNT="$(summary_count cortexsim_net)"
    CRED_COUNT="$(summary_count cortexsim_creds)"

    # Distinct exe paths under the exec key (top 50, deterministic sort).
    EXEC_LIST="$(ausearch -i -k cortexsim_exec 2>/dev/null \
        | grep -oE 'exe=[^ ]+' | sort -u | sed 's/^exe=//' \
        | head -n 50 \
        | jq -R . | jq -s . 2>/dev/null || echo '[]')"

    # Distinct (auid->uid) identity transitions under the setid key.
    SETID_LIST="$(ausearch -i -k cortexsim_setid 2>/dev/null \
        | grep -oE 'uid=[^ ]+' | sort -u | sed 's/^uid=//' \
        | head -n 50 \
        | jq -R . | jq -s . 2>/dev/null || echo '[]')"
fi

# Bundle stdout often names the identity per step (the harness logs
# "STEP ... identity=..."). Surface that as a cheap, always-available signal
# even in degraded mode.
HARNESS_IDENTITIES="$(grep -oiE 'identity=[a-z0-9_-]+' "$BUNDLE_STDOUT" 2>/dev/null \
    | sort -u | sed 's/^identity=//I' \
    | jq -R . | jq -s . 2>/dev/null || echo '[]')"

cat > "$RESULTS_DIR/observed-signals.json" <<EOF
{
  "status": "ok",
  "run_ts": "$RUN_TS",
  "audit_mode": "$AUDIT_MODE",
  "bundle": "$(basename "$BUNDLE")",
  "bundle_exit_code": $BUNDLE_EXIT,
  "duration_seconds": $((BUNDLE_END_EPOCH - BUNDLE_START_EPOCH)),
  "signals": {
    "exec": { "count": ${EXEC_COUNT:-0}, "executables": $EXEC_LIST },
    "setid": { "count": ${SETID_COUNT:-0}, "uids": $SETID_LIST },
    "net": { "count": ${NET_COUNT:-0} },
    "creds": { "count": ${CRED_COUNT:-0} }
  },
  "harness_identities_from_stdout": $HARNESS_IDENTITIES
}
EOF

log "wrote $RESULTS_DIR/observed-signals.json (audit_mode=$AUDIT_MODE)"
log "wrote $RAW_AUDIT_OUT and $BUNDLE_STDOUT"

# Exit code mirrors the bundle so `docker compose ... up --exit-code-from` and
# run-tier-c.sh can surface a bundle failure to the operator.
exit "$BUNDLE_EXIT"
