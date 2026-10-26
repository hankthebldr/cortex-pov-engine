#!/usr/bin/env bash
# ===========================================================================
# inject-kill.sh — Tier-D host-termination injector (runs INSIDE the target)
# ---------------------------------------------------------------------------
# Plays the part of the Cortex XDR agent in a lab that has none: it kills one
# process of the running beacon's tree at a chosen step, so the engine's three
# host witnesses can be exercised and classified end to end.
#
#   subshell  SIGKILL the step's subshell (the anchor shell's newest child)
#             -> the step reports  signaled
#   anchor    SIGKILL the anchor shell (the beacon's `sh` child)
#             -> the step reports  anchor_terminated
#   beacon    SIGKILL the beacon itself
#             -> no /complete ever arrives; the heartbeat sweep must call the
#                run beacon_lost
#
# HOW IT FINDS THE PIDS (process ancestry from the beacon, never by name guess)
#   beacon    --beacon-pid, else the process whose command line is
#             `cortexsim-agent ... --id <agent-id>` (the id is unique per install)
#   anchor    a DIRECT child of the beacon whose executable is a shell
#             (comm sh|bash|dash|ash). The anchor's argv[0] is relabelled to the
#             scenario's cgo_anchor image, but comm is the exec'd file, so this
#             still sees a shell. Needs a scenario that declares cgo_anchor or a
#             step `causality` -- without one the beacon runs each step as its
#             own `sh -c` and there is NO anchor; this then times out, loudly.
#   subshell  the NEWEST direct child of the anchor
#
# WHEN IT FIRES
#   A step's /output is only POSTed AFTER the step ends, so "when /output for
#   the step arrives" is too late to kill that step. The injector therefore
#   waits until the PREVIOUS step's block is in the run output (--after-step,
#   empty = fire as soon as a victim exists), then kills the first victim it
#   sees. If the target step's own block appears first, the step finished
#   before it could be hit: status MISSED, not a silent pass.
#
# STATUS (written to --status-file; the first word is the verdict)
#   KILLED <mode> pid=<n> comm=<c>     the kill was delivered
#   TIMEOUT <mode>                     nothing killable appeared in --timeout s
#   MISSED <mode>                      the target step finished first
# Exit: 0 KILLED, 3 TIMEOUT, 4 MISSED, 2 usage.
# ===========================================================================
set -u

SIMCORE="" RUN_ID="" MODE="" AT_STEP="" AFTER_STEP="" AGENT_ID="" BEACON_PID=""
TIMEOUT=120
STATUS_FILE="/tmp/tier-d-inject.status"

while [ $# -gt 0 ]; do
  case "$1" in
    --simcore)     SIMCORE="$2"; shift 2 ;;
    --run-id)      RUN_ID="$2"; shift 2 ;;
    --mode)        MODE="$2"; shift 2 ;;
    --at-step)     AT_STEP="$2"; shift 2 ;;
    --after-step)  AFTER_STEP="$2"; shift 2 ;;
    --agent-id)    AGENT_ID="$2"; shift 2 ;;
    --beacon-pid)  BEACON_PID="$2"; shift 2 ;;
    --timeout)     TIMEOUT="$2"; shift 2 ;;
    --status-file) STATUS_FILE="$2"; shift 2 ;;
    *) echo "inject-kill: unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in subshell|anchor|beacon) ;; *) echo "inject-kill: --mode must be subshell|anchor|beacon" >&2; exit 2 ;; esac
[ -n "$SIMCORE" ] && [ -n "$RUN_ID" ] && [ -n "$AT_STEP" ] || { echo "inject-kill: --simcore, --run-id, --at-step are required" >&2; exit 2; }
[ -n "$BEACON_PID" ] || [ -n "$AGENT_ID" ] || { echo "inject-kill: need --beacon-pid or --agent-id" >&2; exit 2; }

: > "$STATUS_FILE"
finish() { printf '%s\n' "$1" > "$STATUS_FILE"; echo "[inject-kill] $1"; exit "$2"; }

# step_in_output <step-id>: has this step's `=== STEP n/N · <id> ·` block landed?
# The separator is a multibyte dot (or its \u escape), hence the loose gap.
step_in_output() {
  curl -fsS --max-time 5 "${SIMCORE}/api/runs/${RUN_ID}" 2>/dev/null \
    | grep -Eq "=== STEP [0-9]+/[0-9]+ .{1,8}$1 "
}

find_beacon() {
  if [ -n "$BEACON_PID" ]; then
    kill -0 "$BEACON_PID" 2>/dev/null && echo "$BEACON_PID"
    return
  fi
  pgrep -f -- "cortexsim-agent.* --id ${AGENT_ID}( |\$)" | head -n1
}

# children_of <pid>: "pid comm" lines, oldest first.
children_of() {
  ps -eo pid=,ppid=,comm= | awk -v p="$1" '$2==p {print $1, $3}' | sort -n
}

is_shell() { case "${1##*/}" in sh|bash|dash|ash) return 0 ;; *) return 1 ;; esac; }

pick_victim() { # echoes "pid comm" or nothing
  local beacon anchor line pid comm
  beacon="$(find_beacon)"
  [ -n "$beacon" ] || return 1
  if [ "$MODE" = beacon ]; then
    echo "$beacon $(ps -o comm= -p "$beacon" | tr -d ' ')"; return 0
  fi
  anchor=""
  while read -r pid comm; do
    [ -n "$pid" ] || continue
    if is_shell "$comm"; then anchor="$pid $comm"; break; fi
  done < <(children_of "$beacon")
  [ -n "$anchor" ] || return 1
  if [ "$MODE" = anchor ]; then echo "$anchor"; return 0; fi
  line="$(children_of "${anchor%% *}" | tail -n1)"
  [ -n "$line" ] || return 1
  echo "$line"
}

echo "[inject-kill] mode=$MODE at=$AT_STEP after=${AFTER_STEP:-<none>} run=$RUN_ID timeout=${TIMEOUT}s"
deadline=$(( $(date +%s) + TIMEOUT ))

# Gate: the previous step has finished, so the target step is the next to run.
if [ -n "$AFTER_STEP" ]; then
  until step_in_output "$AFTER_STEP"; do
    [ "$(date +%s)" -lt "$deadline" ] || finish "TIMEOUT $MODE (step ${AFTER_STEP} never reported)" 3
    sleep 0.3
  done
fi

# Spin on the process tree: the victim can live only milliseconds.
i=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if victim="$(pick_victim)" && [ -n "$victim" ]; then
    pid="${victim%% *}"; comm="${victim#* }"
    if kill -KILL "$pid" 2>/dev/null; then
      finish "KILLED $MODE pid=$pid comm=$comm" 0
    fi
  fi
  i=$((i + 1))
  if [ $((i % 25)) -eq 0 ] && step_in_output "$AT_STEP"; then
    finish "MISSED $MODE (step ${AT_STEP} finished before it could be hit)" 4
  fi
  sleep 0.02
done
finish "TIMEOUT $MODE (no killable process within ${TIMEOUT}s)" 3
