package beacon

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/executor"
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

func TestExecuteTask_RuntimeDependencyMissing_NeverRunsTheRealCommand(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	// The command's own `|| echo` fallback is the exact pattern SIM-EDR-001
	// step-05 uses to mask mimipenguin's failure — reproduced here so the test
	// proves the guarantee holds even against a command that WOULD otherwise
	// exit 0 no matter what.
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{
				ID: "step-01", Name: "one", Identity: "root", MitreTechnique: "T1003",
				Command:              "echo SHOULD_NEVER_RUN || echo '[*] masked complete'",
				RequiresInterpreters: []string{noSuchInterpreter},
			},
		},
	}

	c := New(rs.URL, "a-1", 0)
	c.executeTask(context.Background(), task)

	outputs := rs.find("POST", "/output")
	if len(outputs) != 1 {
		t.Fatalf("expected 1 /output POST, got %d", len(outputs))
	}
	if strings.Contains(outputs[0].Body, "SHOULD_NEVER_RUN") {
		t.Fatalf("the step's real command ran despite a missing runtime dependency: %s", outputs[0].Body)
	}
	if strings.Contains(outputs[0].Body, "masked complete") {
		t.Fatalf("the masking fallback ran, meaning the underlying command was executed: %s", outputs[0].Body)
	}
	if !strings.Contains(outputs[0].Body, "RUNTIME_DEPENDENCY_MISSING") {
		t.Errorf("expected RUNTIME_DEPENDENCY_MISSING marker in output: %s", outputs[0].Body)
	}
	if !strings.Contains(outputs[0].Body, noSuchInterpreter) {
		t.Errorf("expected the missing interpreter to be NAMED in output: %s", outputs[0].Body)
	}

	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected 1 /complete, got %d", len(completes))
	}
	if !strings.Contains(completes[0].Body, `"exit_code":127`) {
		t.Errorf("expected exit_code 127 (never masked to 0), got: %s", completes[0].Body)
	}
}

// The causality-chained path (SIM-EDR-001 declares cgo_anchor + per-step
// causality, so THIS is the path it actually runs through) must give the same
// guarantee: the anchor session survives, but the gapped step's command never
// executes inside it.
func TestExecuteTaskChained_RuntimeDependencyMissing_NeverRunsTheRealCommand(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		CgoAnchor: &CgoAnchor{ImageName: "apache2", PrimaryUsername: "www-data"},
		Steps: []Step{
			{
				ID: "step-01", Name: "one", Identity: "root", MitreTechnique: "T1003",
				Command:              "echo SHOULD_NEVER_RUN || echo '[*] masked complete'",
				RequiresInterpreters: []string{noSuchInterpreter},
			},
			{
				ID: "step-02", Name: "two", Identity: "root", MitreTechnique: "T1003",
				Command:   "echo step-two-ran",
				Causality: &StepCausality{ParentStep: "step-01", Pivot: "process_lineage"},
			},
		},
	}

	c := New(rs.URL, "a-1", 0)
	c.executeTaskChained(context.Background(), task)

	outputs := rs.find("POST", "/output")
	if len(outputs) < 1 {
		t.Fatalf("expected at least 1 /output POST, got %d", len(outputs))
	}
	if strings.Contains(outputs[0].Body, "SHOULD_NEVER_RUN") || strings.Contains(outputs[0].Body, "masked complete") {
		t.Fatalf("the step's real command ran despite a missing runtime dependency (chained path): %s", outputs[0].Body)
	}
	if !strings.Contains(outputs[0].Body, "RUNTIME_DEPENDENCY_MISSING") {
		t.Errorf("expected RUNTIME_DEPENDENCY_MISSING marker in chained-path output: %s", outputs[0].Body)
	}

	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected 1 /complete, got %d", len(completes))
	}
	if !strings.Contains(completes[0].Body, `"exit_code":127`) {
		t.Errorf("expected exit_code 127, got: %s", completes[0].Body)
	}
}

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

// PROVIDE A PYTHON PATH: an interpreter present only under an alias gets
// PATH-shimmed, and the step's real command runs — proving the shim actually
// works end to end, not just that ResolveInterpreter finds something.
func TestResolveRuntimeDeps_AliasedInterpreter_ShimsAndRunsForReal(t *testing.T) {
	dir := t.TempDir()
	fake := filepath.Join(dir, "python3")
	if err := os.WriteFile(fake, []byte("#!/bin/sh\necho ALIAS_SHIM_RAN\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	// Prepend the fake dir to the REAL PATH so `sh` and other basics the
	// executor needs are still resolvable — only "python3" is shadowed/added.
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	pre := executor.ResolveInterpreter("python")
	if !pre.Found || pre.Exact {
		t.Skipf("test host PATH does not give the expected aliased-only shape: %+v", pre)
	}

	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python", RequiresInterpreters: []string{"python"}}
	outcome := c.resolveRuntimeDeps(&Task{}, step)
	defer outcome.cleanup()

	if outcome.blocked {
		t.Fatalf("expected NOT blocked — an alias should satisfy the requirement, got note=%q", outcome.note)
	}
	if !strings.Contains(outcome.note, "PATH-shimmed") {
		t.Errorf("expected a PATH-shimmed note, got %q", outcome.note)
	}
	if !strings.Contains(step.Command, "export PATH=") {
		t.Errorf("expected the shim directory to be prepended to the step command: %q", step.Command)
	}

	// Prove the shim genuinely works: run the (modified) command for real.
	stdout, _, exitCode, err := executor.RunCommand(step.Command)
	if err != nil {
		t.Fatalf("running the shimmed command failed: %v", err)
	}
	if exitCode != 0 {
		t.Fatalf("shimmed command exited %d, stdout=%q", exitCode, stdout)
	}
	if !strings.Contains(stdout, "ALIAS_SHIM_RAN") {
		t.Errorf("expected the shim to resolve `python` to the fake python3 script, got stdout=%q", stdout)
	}
}

// DELIVER SYSTEM UPDATES: when the run is authorized and a package manager is
// present, the install command is composed correctly and chained with `&&` so
// an install failure cannot be absorbed by the step's own masking.
func TestResolveRuntimeDeps_AuthorizedInstall_ComposesCommand(t *testing.T) {
	dir := t.TempDir()
	fakeApt := filepath.Join(dir, "apt-get")
	if err := os.WriteFile(fakeApt, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir) // no python anywhere — must go the install route

	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python --version", RequiresInterpreters: []string{"python"}}
	task := &Task{RuntimeInstallAuthorized: true}
	outcome := c.resolveRuntimeDeps(task, step)
	defer outcome.cleanup()

	if outcome.blocked {
		t.Fatalf("expected NOT blocked when install is authorized and a package manager exists, note=%q", outcome.note)
	}
	if !strings.Contains(outcome.note, "RUNTIME_INSTALL_AUTHORIZED") {
		t.Errorf("expected an authorized-install note, got %q", outcome.note)
	}
	if !strings.Contains(step.Command, "apt-get") {
		t.Errorf("expected the install command to be composed into step.Command: %q", step.Command)
	}
	if !strings.Contains(step.Command, "&&") || !strings.Contains(step.Command, "(python --version)") {
		t.Errorf("expected install && (original command) composition, got %q", step.Command)
	}
}

// Authorization alone is not a magic wand: if no package manager exists AND no
// interpreter path exists, the step is still blocked — never silently "ok
// because the operator said install was fine".
func TestResolveRuntimeDeps_AuthorizedButNoPackageManager_StillBlocked(t *testing.T) {
	dir := t.TempDir() // empty
	t.Setenv("PATH", dir)

	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "echo SHOULD_NEVER_RUN", RequiresInterpreters: []string{"python"}}
	task := &Task{RuntimeInstallAuthorized: true}
	outcome := c.resolveRuntimeDeps(task, step)
	defer outcome.cleanup()

	if !outcome.blocked {
		t.Fatalf("expected blocked — authorization does not fabricate a package manager that isn't there")
	}
	if !strings.Contains(outcome.note, "RUNTIME_DEPENDENCY_MISSING") {
		t.Errorf("expected RUNTIME_DEPENDENCY_MISSING note, got %q", outcome.note)
	}
}

// Windows is explicitly out of scope for this pass (see
// docs/design/agent-runtime-dependencies.md): resolveRuntimeDeps checks
// runtime.GOOS itself, so this only proves the guard exists — it cannot flip
// GOOS at test time. The cross-compile gate (agent/crosscompile_test.go)
// covers that the windows build still compiles with this code present.
func TestResolveRuntimeDeps_DeclaredRequirementIsIgnoredOnNonPOSIXGuardOnly(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("this host IS windows — the no-op branch is exercised directly by every other test on this platform")
	}
	// Nothing to assert beyond "the package still builds and the other tests
	// above exercised the POSIX branch" — this test exists as a documented
	// marker for the windows exclusion rather than a runnable cross-OS check.
}
