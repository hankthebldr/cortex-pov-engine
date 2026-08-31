#!/usr/bin/env bash
# poll_status.sh — shared status-classification helper for run-tier-d.sh's
# run-status poll loop and its post-loop terminal-state gate.
#
# WHY THIS IS ITS OWN FILE
#
# Before this fix, run-tier-d.sh's poll loop had a `case` statement deciding
# "keep polling" vs "stop", but nothing after the loop checked WHY it stopped.
# A run stuck at running/pending/queued/"" for the entire poll budget (120 x
# 3s) fell out of the loop exactly the same way a run that reached a real
# terminal status did, and the script printed "terminal status: running" as
# if that were a legitimate result — then handed a partial run.json to
# classify.py. A hung run was a green harness.
#
# Pulling the classification into one named, independently testable function
# (rather than a second copy-pasted `case` after the loop) is what makes it
# possible to prove this with a test that does not require mocking curl,
# docker, or a live SimCore — see tests/deploy_tier_d/test_run_tier_d_status.sh.
#
# Sourced, not executed — defines is_non_terminal_status() only.

# is_non_terminal_status STATUS
# Returns 0 (true, "still going") for running/pending/queued/empty — the
# states run-tier-d.sh's poll loop is supposed to keep waiting through.
# Returns 1 (false, "stop — something concluded") for everything else,
# including a status this script has never heard of: an unrecognized status
# is treated as terminal-SHAPED so a typo'd/renamed status can't spin the
# poll loop forever. classify.py independently gates on the run_status value
# it actually receives (see CLEAN_TERMINAL_STATUSES there) — this function
# only answers "should run-tier-d.sh keep polling", not "is this run's
# outcome trustworthy".
is_non_terminal_status() {
  case "${1:-}" in
    running | pending | queued | "") return 0 ;;
    *) return 1 ;;
  esac
}
