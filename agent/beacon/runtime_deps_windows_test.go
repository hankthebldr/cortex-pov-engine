//go:build windows

package beacon

// Windows twin of runtime_deps_unix_test.go (CI-04). On a Windows host the
// runtime-dependency and tool-presence preflights are NO-OPS today
// (resolveRuntimeDeps / checkRequiredBinaries return early; the feature is
// scoped to POSIX — docs/design/agent-runtime-dependencies.md). That means a
// missing interpreter or tool on a Windows host is NOT caught before the step
// runs. This test pins that stated gap so it cannot change silently in either
// direction; it is not a claim that the behaviour is right.

import "testing"

func TestResolveRuntimeDeps_WindowsHostIsANoOp(t *testing.T) {
	if runtimeDepsGOOS != "windows" {
		t.Fatalf("runtimeDepsGOOS = %q on a Windows build", runtimeDepsGOOS)
	}
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "Write-Output x", RequiresInterpreters: []string{noSuchInterpreter},
		RequiredBinaries: []string{"cortexsim-no-such-tool"}}
	if out := c.resolveRuntimeDeps(&Task{}, step); out.blocked || out.note != "" || step.Command != "Write-Output x" {
		t.Fatalf("resolveRuntimeDeps acted on Windows: %+v cmd=%q", out, step.Command)
	}
	if missing := checkRequiredBinaries(step); missing != nil {
		t.Fatalf("checkRequiredBinaries acted on Windows: %v", missing)
	}
}
