package beacon

import (
	"testing"
)

// noSuchInterpreter mirrors executor's test constant — a logical name no real
// host will ever have installed, so "genuinely absent" is a real, not
// simulated, outcome.
const noSuchInterpreter = "cortexsim-no-such-interpreter-xyz"

// -----------------------------------------------------------------------------
// The crux guarantee: a step whose declared runtime dependency is absent must
// NEVER run its own (possibly-masking) command, and must report a non-zero,
// distinguishable exit rather than a false success.
// -----------------------------------------------------------------------------

// -----------------------------------------------------------------------------
// resolveRuntimeDeps — unit-level coverage of the three honest outcomes.
// -----------------------------------------------------------------------------

// When the exact logical name is already on PATH, nothing is modified.
func TestResolveRuntimeDeps_NoRequirementIsANoOp(t *testing.T) {
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "echo hi"}
	outcome := c.resolveRuntimeDeps(&Task{}, step)
	if outcome.blocked {
		t.Fatalf("a step with no requires_interpreters must never be blocked")
	}
	if step.Command != "echo hi" {
		t.Errorf("command must be unchanged: %q", step.Command)
	}
	if outcome.note != "" {
		t.Errorf("expected no note, got %q", outcome.note)
	}
}

// Windows is explicitly out of scope for this pass (see
// docs/design/agent-runtime-dependencies.md). Previously this test could not
// actually prove the guard fires — it could not flip runtime.GOOS, so it
// asserted nothing beyond "the package builds". resolveRuntimeDeps now reads
// the host OS through the package var runtimeDepsGOOS (default
// runtime.GOOS) specifically so this test can override it and prove the
// no-op branch actually returns a zero-value, untouched outcome even when a
// requirement IS declared — the one case an unguarded caller could get wrong.
func TestResolveRuntimeDeps_WindowsGOOSIsANoOp(t *testing.T) {
	original := runtimeDepsGOOS
	runtimeDepsGOOS = "windows"
	defer func() { runtimeDepsGOOS = original }()

	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "echo SHOULD_BE_UNCHANGED", RequiresInterpreters: []string{"python"}}
	outcome := c.resolveRuntimeDeps(&Task{RuntimeInstallAuthorized: true}, step)
	defer outcome.cleanup()

	if outcome.blocked {
		t.Fatalf("expected the windows no-op guard to short-circuit before ever blocking, got blocked=true note=%q", outcome.note)
	}
	if outcome.note != "" {
		t.Errorf("expected a zero-value outcome (no note) on windows, got note=%q", outcome.note)
	}
	if len(outcome.shims) != 0 {
		t.Errorf("expected no shims to be created on windows, got %d", len(outcome.shims))
	}
	if step.Command != "echo SHOULD_BE_UNCHANGED" {
		t.Errorf("expected step.Command to be left completely untouched on windows, got %q", step.Command)
	}
}
