#!/usr/bin/env bash
# test_run_tier_d_status.sh — plain-bash unit test for the shared
# is_non_terminal_status() helper (deploy/tier-d/lib/poll_status.sh) that
# run-tier-d.sh's poll loop AND its post-loop timeout gate both call.
#
# No test-framework dependency (bats is not wired into this repo's CI) so
# this runs anywhere bash runs:
#   bash tests/deploy_tier_d/test_run_tier_d_status.sh
#
# Pre-fix, deploy/tier-d/lib/poll_status.sh did not exist and run-tier-d.sh's
# poll loop had no post-loop gate at all — a run stuck at "running" for the
# whole poll budget fell out of the loop and was reported as if it were a
# real terminal status. This test sources the extracted helper directly and
# fails loudly (source: No such file) against that pre-fix state.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." >/dev/null 2>&1 && pwd)"
LIB="${REPO_ROOT}/deploy/tier-d/lib/poll_status.sh"

if [ ! -f "$LIB" ]; then
  echo "FAIL: ${LIB} does not exist — the poll-status helper has not been extracted yet" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$LIB"

if ! command -v is_non_terminal_status >/dev/null 2>&1; then
  echo "FAIL: is_non_terminal_status is not defined after sourcing ${LIB}" >&2
  exit 1
fi

fail=0

assert_non_terminal() {
  if is_non_terminal_status "$1"; then
    echo "ok - '$1' correctly classified NON-terminal (keep polling)"
  else
    echo "FAIL: expected '$1' to be NON-terminal (keep polling), got terminal" >&2
    fail=1
  fi
}

assert_terminal() {
  if is_non_terminal_status "$1"; then
    echo "FAIL: expected '$1' to be TERMINAL (stop polling), got non-terminal" >&2
    fail=1
  else
    echo "ok - '$1' correctly classified TERMINAL (stop polling)"
  fi
}

# The exact set the poll loop must keep waiting through.
for s in running pending queued ""; do
  assert_non_terminal "$s"
done

# The exact set that must stop the loop and (if reached only via timeout,
# i.e. STATUS never actually changed) trip the C1 exit-3 gate in
# run-tier-d.sh. complete/failed/aborted/staged all come from
# core/api/runs.py's real terminal-status vocabulary.
for s in complete failed aborted staged; do
  assert_terminal "$s"
done

# Regression guard for the exact bug: a run.json's status is read via
# `json.load(...).get("status","")` — if SimCore ever returns a bare `null`
# for status, Python's json module hands back None, and the curl pipeline in
# run-tier-d.sh coerces that (via the `|| echo ""` fallback and any parse
# failure) toward an empty string, not the literal text "None". Confirm the
# helper treats an empty string as non-terminal so that path is covered too.
assert_non_terminal ""

if [ "$fail" -ne 0 ]; then
  echo "FAILED" >&2
  exit 1
fi
echo "ALL PASSED"
