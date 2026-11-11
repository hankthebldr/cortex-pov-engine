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
//
// The POSIX outcomes (PATH shim, authorised install) are exercised against the
// real host in runtime_deps_unix_test.go; the Windows rule — the guarantee
// without the two repairs — is in runtime_deps_winrule_test.go (any host, via
// the runtimeDepsGOOS seam) and runtime_deps_windows_test.go (a real Windows
// host).
// -----------------------------------------------------------------------------

// When nothing is declared, nothing is checked and nothing is modified.
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

// The same on the Windows rule: declaring nothing is still a no-op there.
func TestResolveRuntimeDeps_NoRequirementIsANoOpOnTheWindowsRuleToo(t *testing.T) {
	asWindowsHost(t)
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "Write-Output hi"}
	if out := c.resolveRuntimeDeps(&Task{RuntimeInstallAuthorized: true}, step); out.blocked || out.note != "" ||
		step.Command != "Write-Output hi" {
		t.Fatalf("a step that declares no interpreter was touched: %+v cmd=%q", out, step.Command)
	}
	if missing := checkRequiredBinaries(step); missing != nil {
		t.Fatalf("a step that declares no required binary reported %v", missing)
	}
}
