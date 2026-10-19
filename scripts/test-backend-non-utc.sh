#!/usr/bin/env bash
# ==============================================================================
# test-backend-non-utc.sh — the backend suite again, with the host in a non-UTC zone
#
# WHAT WENT WRONG (observed 2026-10-19)
#   The `_alert` fixtures in tests/connectors/test_reconcile_scope.py and
#   test_measurement_service.py minted detection epochs with
#   `datetime.utcnow().timestamp()`. Python reads a naive datetime as HOST-LOCAL
#   time, so on a UTC-5 laptop every alert landed five hours outside the run
#   window and nothing matched: 7 failures in CDT, 0 in CI. CI and the image run
#   UTC, where that mistake costs nothing, so no gate could see it.
#
#   The same class had already shipped once in production code: the reconcile
#   request window and the observation timestamp coercer read naive UTC as local,
#   so a DC's UTC offset shifted every observation and the matcher matched
#   nothing (guarded since by tests/connectors/test_request_window_utc.py and
#   connectors/base.py::coerce_utc). Each fix guarded its own module. Nothing
#   guarded the next one.
#
# WHAT THIS DOES
#   Runs pytest inside the same prod image the backend job just built, with TZ
#   set to a zone behind UTC. Code or a fixture that reads naive UTC as host-local
#   time now fails in CI instead of on a DC's laptop.
#
# WHY IT PROVES THE ZONE BEFORE PYTEST
#   An unknown TZ name is not an error: glibc falls back to UTC, offset 0
#   (measured in the image: TZ=Bogus/Zone -> "+0000"). A base-image bump that
#   stops shipping tzdata does the same. Either one turns this pass into a second
#   UTC run that cannot fail and still reports green, so the container refuses to
#   start pytest unless the resolved offset is non-zero.
#
# WHY THE WHOLE TREE, IN ITS DEFAULT ORDER
#   The bug class is not confined to connectors/: tests that handle datetimes
#   live in api/, engine/, eal_simulator/ and assertions/ as well. A subset also
#   reorders the suite. `pytest tests/connectors tests/integration tests/engine`
#   runs integration/xsiam before engine, and its db_session fixture leaves a
#   re-imported `config` behind that changes every generate_bash hash, so
#   test_push_generator_invariant fails under UTC too. That failure has nothing
#   to do with zones. The default collection order is the one the UTC pass
#   already proves, so a red here means the zone changed the result.
#
# Exit code:
#   0  — the suite passed with the process in $NON_UTC_TZ
#   1  — no image given, or the zone resolved to UTC (fail closed)
#   *  — pytest's own exit code
#
# Usage:
#   make test-backend-non-utc                          # repo default image
#   IMAGE=cortexsim:ci scripts/test-backend-non-utc.sh
#   NON_UTC_TZ=Asia/Kolkata scripts/test-backend-non-utc.sh <image>
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# No default image, for the reason check-ui-shelf.sh gives: the Makefile derives
# IMAGE from VERSION, and a literal here would drift from it silently.
IMAGE="${1:-${IMAGE:-}}"
[[ -n "$IMAGE" ]] || {
    echo "usage: $0 <image>   (or: make test-backend-non-utc, which passes the repo default)" >&2
    exit 1
}

# Behind UTC with DST, so a naive-UTC-as-local read lands 7-8 h in the future.
# tests/connectors/conftest.py::non_utc_host also covers ahead-of-UTC per module.
NON_UTC_TZ="${NON_UTC_TZ:-America/Los_Angeles}"

docker run --rm \
    -v "${REPO_ROOT}:/repo" \
    -w /repo \
    -e CORTEXSIM_BASE_DIR=/repo \
    -e CORTEXSIM_ENV=development \
    -e CORTEXSIM_SECRET="${CORTEXSIM_SECRET:-$(openssl rand -hex 32)}" \
    -e PYTHONPATH=/repo/core \
    -e TZ="${NON_UTC_TZ}" \
    "$IMAGE" \
    sh -c "python -c 'import os, sys, time
z = os.environ.get(\"TZ\")
if time.timezone == 0 and time.altzone == 0:
    sys.exit(\"non-utc pass: TZ=%s resolved to UTC (unknown zone, or no tzdata in the image); this run could not fail\" % z)
print(\"non-utc pass: TZ=%s -> %s\" % (z, time.strftime(\"%Z %z\")))' && \
           pip install --no-cache-dir pytest pytest-asyncio httpx && \
           pytest tests/ -q --tb=short --ignore=tests/smoke"
