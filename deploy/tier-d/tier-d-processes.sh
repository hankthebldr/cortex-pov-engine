#!/usr/bin/env bash
# ===========================================================================
# tier-d-processes.sh — keep the target alive with a few live processes.
#
# Scenario steps that enumerate processes or read /proc/<pid>/environ need
# something to find. An empty process table produces an empty result, and an
# empty result reads in a POV report exactly like a detection that did not
# fire — the manufactured-false-negative failure this whole harness exists to
# separate from real ones.
#
# These are plain `sleep` processes started under real service accounts. They
# are deliberately NOT renamed to look like sshd/apache/nginx: a process
# masquerading as a daemon would make the target lie to the sensor, which is
# the same dishonesty as a scenario step that `echo`s instead of executing.
# What they DO provide is genuine, non-root, long-lived processes owned by the
# service accounts the corpus impersonates — which is what the /proc steps
# actually need.
# ===========================================================================
set -euo pipefail

for u in www-data postgres svc-account svc-backup node; do
    if getent passwd "$u" >/dev/null 2>&1; then
        # Each holder carries an env var so /proc/<pid>/environ scraping has
        # deterministic, inert content to surface.
        runuser -l "$u" -c \
            'CORTEXSIM_TIER_D_FIXTURE=inert-process-holder exec sleep infinity' \
            >/dev/null 2>&1 &
    fi
done

echo "[tier-d] target ready — $(getent passwd | grep -c '/bin/bash') accounts with a login shell"
echo "[tier-d] process holders: $(jobs -p | wc -l)"

# PID 1 must not exit.
exec sleep infinity
