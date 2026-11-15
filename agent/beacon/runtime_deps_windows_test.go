//go:build windows

package beacon

// BEACON-13 on a real Windows host: what runtime_deps_winrule_test.go cannot
// show from a POSIX runner — that exec.LookPath resolves a required binary
// through PATH + PATHEXT and by full path, and that the interpreter rule holds
// against real .cmd shims. Run only by the windows-native CI job; NOT observed
// passing on Windows by the change that introduced it.
//
// (Until BEACON-13 this file pinned the opposite: both preflights were no-ops on
// Windows, so a missing tool or interpreter was not caught before the step ran.)

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCheckRequiredBinaries_WindowsHostResolvesThroughPATHEXTAndByFullPath(t *testing.T) {
	if runtimeDepsGOOS != "windows" {
		t.Fatalf("runtimeDepsGOOS = %q on a Windows build", runtimeDepsGOOS)
	}
	root := os.Getenv("SystemRoot")
	if root == "" {
		root = `C:\Windows`
	}
	step := &Step{ID: "s", RequiredBinaries: []string{
		"cmd", // bare name, found as cmd.exe through PATHEXT
		filepath.Join(root, "System32", "cmd.exe"), // full path, the shape tools/packs/rubeus.yml uses
		"cortexsim-no-such-tool",
		`C:\tools\cortexsim-no-such-dir\Rubeus.exe`,
	}}
	missing := checkRequiredBinaries(step)
	if len(missing) != 2 || missing[0] != "cortexsim-no-such-tool" || missing[1] != `C:\tools\cortexsim-no-such-dir\Rubeus.exe` {
		t.Fatalf("missing = %v, want exactly the two absent entries", missing)
	}
}

// The step with a missing tool never runs on Windows either, in both execution
// paths, and the run record says TOOL_ABSENT.
func TestExecuteTask_WindowsHostMissingToolIsNotRunToolAbsent(t *testing.T) {
	for _, chained := range []bool{false, true} {
		rs := newRecordingServer(t, nil)
		task := &Task{TaskID: "t", RunID: "r", ScenarioID: "S", Steps: []Step{
			{ID: "step-01", Command: "Write-Output 'SHOULD_NEVER_RUN'", Identity: "root",
				RequiredBinaries: []string{"cortexsim-no-such-tool"}},
			{ID: "step-02", Command: "Write-Output 'never'", Identity: "root"},
		}}
		if chained {
			task.CgoAnchor = &CgoAnchor{ImageName: "w3wp.exe"}
		}
		c := New(rs.URL, "a-1", 0)
		c.resolveIdentity = directResolver
		c.executeTask(context.Background(), task)

		for _, o := range rs.find("POST", "/output") {
			if strings.Contains(o.Body, "SHOULD_NEVER_RUN") {
				t.Errorf("chained=%v: the step ran although its required binary is absent", chained)
			}
		}
		b := decodeComplete(t, rs)
		if b.ExitCode == nil || *b.ExitCode != 127 || !strings.Contains(b.Summary, "TOOL_ABSENT") {
			t.Errorf("chained=%v: complete = %+v, want 127 / TOOL_ABSENT", chained, b)
		}
		assertNotRun(t, b.Steps[0], "step-01", ReasonToolAbsent)
		rs.Close()
	}
}

func writeCmdShim(t *testing.T, dir, name string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name+".cmd"), []byte("@echo "+name+"-ran\r\n"), 0o755); err != nil {
		t.Fatal(err)
	}
}

func TestResolveRuntimeDeps_WindowsHostRule(t *testing.T) {
	c := New("http://unused.invalid", "a-1", 0)

	// Absent.
	step := &Step{ID: "s", Command: "python tool.py", RequiresInterpreters: []string{noSuchInterpreter}}
	if out := c.resolveRuntimeDeps(&Task{}, step); !out.blocked || out.synthetic.ExitCode != 127 ||
		!strings.Contains(out.note, runtimeDepMissingMarker) || step.Command != "python tool.py" {
		t.Fatalf("absent interpreter: %+v cmd=%q, want blocked/127/marker and an untouched command", out, step.Command)
	}

	// Exact: python.cmd on PATH satisfies `python` through PATHEXT.
	exact := t.TempDir()
	writeCmdShim(t, exact, "python")
	t.Setenv("PATH", exact)
	step = &Step{ID: "s", Command: "python tool.py", RequiresInterpreters: []string{"python"}}
	if out := c.resolveRuntimeDeps(&Task{}, step); out.blocked || out.note != "" || step.Command != "python tool.py" {
		t.Fatalf("exact interpreter: %+v cmd=%q, want an unmodified, unblocked step", out, step.Command)
	}

	// Alias only: python3.cmd, no python — blocked, no shim.
	alias := t.TempDir()
	writeCmdShim(t, alias, "python3")
	t.Setenv("PATH", alias)
	step = &Step{ID: "s", Command: "python tool.py", RequiresInterpreters: []string{"python"}}
	out := c.resolveRuntimeDeps(&Task{RuntimeInstallAuthorized: true}, step)
	if !out.blocked || len(out.shims) != 0 || step.Command != "python tool.py" ||
		!strings.Contains(out.note, "python3") || !strings.Contains(out.note, "not supported on Windows") {
		t.Fatalf("alias-only interpreter: %+v cmd=%q, want blocked, no shim, a note naming python3 and the "+
			"unsupported install", out, step.Command)
	}
}

// The Store stub: an executable that LookPath finds, in a
// ...\Microsoft\WindowsApps\ directory on PATH, is not an interpreter.
func TestResolveRuntimeDeps_WindowsHostDoesNotCountAnAppExecutionAlias(t *testing.T) {
	apps := filepath.Join(t.TempDir(), "AppData", "Local", "Microsoft", "WindowsApps")
	if err := os.MkdirAll(apps, 0o755); err != nil {
		t.Fatal(err)
	}
	writeCmdShim(t, apps, "python")
	writeCmdShim(t, apps, "python3")
	t.Setenv("PATH", apps)

	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python tool.py", RequiresInterpreters: []string{"python"}}
	out := c.resolveRuntimeDeps(&Task{}, step)
	if !out.blocked {
		t.Fatalf("an App Execution Alias was accepted as python: %+v", out)
	}
	if !strings.Contains(out.note, "App Execution Alias") || !strings.Contains(out.note, "WindowsApps") {
		t.Errorf("note does not say the only match was an App Execution Alias: %q", out.note)
	}
}
