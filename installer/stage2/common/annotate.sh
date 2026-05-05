#!/usr/bin/env bash
# annotate.sh — structured ATT&CK-annotated install event emitter.
# Source this file; call `annotate <technique_or_dash> <action> [extra_json]`.
#
# Pass "-" as the technique to mark an event as infrastructure-setup
# (technique and tactic both null). Otherwise the technique must be one of
# the IDs the installer is expected to emit; unknown IDs cause an error
# so we never silently mis-tag events.
#
# Output is one NDJSON line per call to:
#   - $ANNOTATE_LOG_PATH (default /var/log/cortexsim-install.ndjson)
#   - logger -t cortexsim-install (best effort, journald)
#
# Quiet by default — emits nothing unless CORTEXSIM_DEMO_MODE=1.

set -u

: "${CORTEXSIM_DEMO_MODE:=0}"
: "${CORTEXSIM_INSTALLER_RUN_ID:=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen 2>/dev/null || echo "run-$$-$(date +%s)")}"
: "${ANNOTATE_LOG_PATH:=/var/log/cortexsim-install.ndjson}"
: "${ANNOTATE_STAGE:=stage2-linux}"

# Technique → tactic lookup. Covers only techniques the installer emits.
__annotate_tactic() {
    case "$1" in
        T1059.001|T1059.004)  echo "execution" ;;
        T1105)                echo "command-and-control" ;;
        T1027)                echo "defense-evasion" ;;
        T1548.002|T1548.003)  echo "privilege-escalation" ;;
        T1543.002|T1543.003)  echo "persistence" ;;
        T1569.002)            echo "execution" ;;
        T1053.005)            echo "persistence" ;;
        *)                    return 1 ;;
    esac
}

__annotate_escape_json() {
    # Minimal JSON string escape (backslash, quote, newline).
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e ':a;N;$!ba;s/\n/\\n/g'
}

annotate() {
    local technique="$1"
    local action="$2"
    local extra="${3:-}"

    [ "$CORTEXSIM_DEMO_MODE" = "1" ] || return 0

    local tactic technique_json tactic_json
    if [ "$technique" = "-" ]; then
        technique_json="null"
        tactic_json="null"
    else
        if ! tactic="$(__annotate_tactic "$technique")"; then
            echo "annotate: unknown technique '$technique'" >&2
            return 2
        fi
        technique_json="\"$technique\""
        tactic_json="\"$tactic\""
    fi

    local ts host user
    ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)"
    host="$(hostname 2>/dev/null || echo unknown)"
    user="$(id -un 2>/dev/null || echo unknown)"

    local action_esc
    action_esc="$(__annotate_escape_json "$action")"

    local line
    line="{\"ts\":\"${ts}\",\"installer_run_id\":\"${CORTEXSIM_INSTALLER_RUN_ID}\",\"stage\":\"${ANNOTATE_STAGE}\",\"technique\":${technique_json},\"tactic\":${tactic_json},\"action\":\"${action_esc}\",\"host\":\"${host}\",\"user\":\"${user}\""

    if [ -n "$extra" ]; then
        # strip wrapping braces from extra and append
        local inner="${extra#\{}"
        inner="${inner%\}}"
        line="${line},${inner}"
    fi

    line="${line}}"

    mkdir -p "$(dirname "$ANNOTATE_LOG_PATH")" 2>/dev/null || true
    printf '%s\n' "$line" >> "$ANNOTATE_LOG_PATH"
    if command -v logger >/dev/null 2>&1; then
        logger -t cortexsim-install "$line" || true
    fi
}
