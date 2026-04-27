#!/usr/bin/env bats

setup() {
  export CORTEXSIM_DEMO_MODE=1
  export CORTEXSIM_INSTALLER_RUN_ID="test-run-$$"
  export ANNOTATE_LOG_PATH="$(mktemp)"
  # shellcheck disable=SC1091
  source "${BATS_TEST_DIRNAME}/../../../installer/stage2/common/annotate.sh"
}

teardown() {
  rm -f "$ANNOTATE_LOG_PATH"
}

@test "annotate emits NDJSON with technique when given T-id" {
  annotate "T1105" "fetched_stage2" '{"src":"ghcr.io/foo:bar"}'
  line="$(cat "$ANNOTATE_LOG_PATH")"
  echo "$line" | grep -q '"technique":"T1105"'
  echo "$line" | grep -q '"tactic":"command-and-control"'
  echo "$line" | grep -q '"action":"fetched_stage2"'
  echo "$line" | grep -q '"src":"ghcr.io/foo:bar"'
  echo "$line" | grep -q '"installer_run_id":"test-run-'
}

@test "annotate with dash marks event as infra-setup (technique null)" {
  annotate "-" "installed_docker_ce"
  line="$(cat "$ANNOTATE_LOG_PATH")"
  echo "$line" | grep -q '"technique":null'
  echo "$line" | grep -q '"tactic":null'
  echo "$line" | grep -q '"action":"installed_docker_ce"'
}

@test "annotate is a no-op when CORTEXSIM_DEMO_MODE=0" {
  export CORTEXSIM_DEMO_MODE=0
  annotate "T1105" "should_not_fire"
  [ ! -s "$ANNOTATE_LOG_PATH" ]
}

@test "annotate rejects unknown technique IDs" {
  run annotate "T9999" "bogus"
  [ "$status" -ne 0 ]
  echo "$output" | grep -q "unknown technique"
}
