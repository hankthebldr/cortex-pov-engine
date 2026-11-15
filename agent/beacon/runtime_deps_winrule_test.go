package beacon

// BEACON-13. On a Windows host the tool-presence check (TOOL_ABSENT) and the
// interpreter preflight were both skipped, silently: a step whose tool or
// interpreter was missing RAN, did nothing, and its absent detections read as
// the customer's miss. These tests drive the Windows RULE from any host through
// the runtimeDepsGOOS seam; what only a real Windows host can show (PATHEXT,
// the %LOCALAPPDATA%\Microsoft\WindowsApps aliases) is in
// runtime_deps_windows_test.go.
//
// The rule on Windows:
//   - required_binaries: checked exactly as on POSIX — a miss is not_run /
//     TOOL_ABSENT.
//   - requires_interpreters, resolvable under its own name: the step runs
//     unmodified.
//   - requires_interpreters, absent: the step is BLOCKED (exit 127,
//     RUNTIME_DEPENDENCY_MISSING).
//   - requires_interpreters, present only under another name (python3, not
//     python): BLOCKED too. The PATH shim that bridges the two names is POSIX
//     only, and a step that calls `python` would not find it — under PowerShell
//     a command that is not found does not even fail the step.
//   - an authorised runtime install is never composed: the install pipeline is
//     POSIX sh text.

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func asWindowsHost(t *testing.T) {
	t.Helper()
	old := runtimeDepsGOOS
	runtimeDepsGOOS = "windows"
	t.Cleanup(func() { runtimeDepsGOOS = old })
}

// fakeTool puts an executable called name on a PATH of its own and returns the
// directory. (A .cmd shim on Windows, which LookPath finds through PATHEXT.)
func fakeTool(t *testing.T, name string) string {
	t.Helper()
	dir := t.TempDir()
	path, body := filepath.Join(dir, name), "#!/bin/sh\nexit 0\n"
	if runtime.GOOS == "windows" {
		path, body = path+".cmd", "@exit /b 0\r\n"
	}
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestCheckRequiredBinaries_WindowsRuleReportsAMissingTool(t *testing.T) {
	asWindowsHost(t)
	t.Setenv("PATH", fakeTool(t, "cortexsim-present-tool"))

	step := &Step{ID: "s", RequiredBinaries: []string{"cortexsim-present-tool", "cortexsim-no-such-tool"}}
	missing := checkRequiredBinaries(step)
	if len(missing) != 1 || missing[0] != "cortexsim-no-such-tool" {
		t.Fatalf("missing = %v, want [cortexsim-no-such-tool]: on Windows a missing tool must be reported, "+
			"and a present one must not", missing)
	}
}

// End to end: the step with a missing tool never reaches the executor, and the
// run says TOOL_ABSENT.
func TestExecuteTask_WindowsRuleMissingToolIsNotRunToolAbsent(t *testing.T) {
	asWindowsHost(t)
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	rec := &execRecorder{}
	c := New(rs.URL, "a-1", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), &Task{TaskID: "t", RunID: "r", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", Command: "Rubeus.exe kerberoast", Identity: "root",
			RequiredBinaries: []string{`C:\tools\Rubeus\cortexsim-no-such-Rubeus.exe`}}}})

	if got := rec.commands(); len(got) != 0 {
		t.Fatalf("the step ran although its required binary is absent: %v", got)
	}
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 127 || !strings.Contains(b.Summary, "TOOL_ABSENT") {
		t.Fatalf("complete = %+v, want exit 127 and TOOL_ABSENT in the summary", b)
	}
	assertNotRun(t, b.Steps[0], "step-01", ReasonToolAbsent)
}

func TestResolveRuntimeDeps_WindowsRuleBlocksAnAbsentInterpreter(t *testing.T) {
	asWindowsHost(t)
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python tool.py; Write-Output done", RequiresInterpreters: []string{noSuchInterpreter}}
	out := c.resolveRuntimeDeps(&Task{}, step)
	defer out.cleanup()

	if !out.blocked {
		t.Fatalf("a step whose interpreter is absent was NOT blocked on Windows (note=%q) — it would run, "+
			"do nothing, and under PowerShell still exit 0", out.note)
	}
	if out.synthetic.ExitCode != 127 || !strings.Contains(out.note, runtimeDepMissingMarker) ||
		!strings.Contains(out.note, noSuchInterpreter) {
		t.Errorf("blocked outcome = exit %d note %q, want 127 and the marker naming the interpreter",
			out.synthetic.ExitCode, out.note)
	}
	if step.Command != "python tool.py; Write-Output done" {
		t.Errorf("command was rewritten: %q", step.Command)
	}
}

func TestResolveRuntimeDeps_WindowsRuleRunsAnExactInterpreterUnmodified(t *testing.T) {
	asWindowsHost(t)
	t.Setenv("PATH", fakeTool(t, "python"))
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python tool.py", RequiresInterpreters: []string{"python"}}
	out := c.resolveRuntimeDeps(&Task{}, step)
	defer out.cleanup()

	if out.blocked || out.note != "" || len(out.shims) != 0 || step.Command != "python tool.py" {
		t.Fatalf("an interpreter resolvable under its own name must run unmodified: blocked=%v note=%q shims=%d cmd=%q",
			out.blocked, out.note, len(out.shims), step.Command)
	}
}

func TestResolveRuntimeDeps_WindowsRuleBlocksAnAliasOnlyInterpreter(t *testing.T) {
	asWindowsHost(t)
	t.Setenv("PATH", fakeTool(t, "python3")) // python3 exists, python does not
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python tool.py; Write-Output done", RequiresInterpreters: []string{"python"}}
	out := c.resolveRuntimeDeps(&Task{}, step)
	defer out.cleanup()

	if !out.blocked {
		t.Fatalf("python resolves only as python3 and Windows has no PATH shim, yet the step was not blocked "+
			"(note=%q cmd=%q)", out.note, step.Command)
	}
	if len(out.shims) != 0 || strings.Contains(step.Command, "export PATH") {
		t.Errorf("a POSIX PATH shim was built on Windows: shims=%d cmd=%q", len(out.shims), step.Command)
	}
	for _, want := range []string{runtimeDepMissingMarker, "python3", "POSIX"} {
		if !strings.Contains(out.note, want) {
			t.Errorf("note does not explain the alias-only refusal (missing %q): %q", want, out.note)
		}
	}
}

func TestResolveRuntimeDeps_WindowsRuleNeverComposesAnInstall(t *testing.T) {
	asWindowsHost(t)
	// A package manager IS on PATH (Cygwin, Git-for-Windows) and the operator
	// authorised runtime installs: the POSIX install pipeline must still not be
	// handed to PowerShell.
	t.Setenv("PATH", fakeTool(t, "apt-get"))
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "s", Command: "python tool.py", RequiresInterpreters: []string{"python"}}
	out := c.resolveRuntimeDeps(&Task{RuntimeInstallAuthorized: true}, step)
	defer out.cleanup()

	if !out.blocked {
		t.Fatalf("absent interpreter with an authorised install was not blocked on Windows: note=%q cmd=%q",
			out.note, step.Command)
	}
	if strings.Contains(step.Command, "apt-get") || strings.Contains(step.Command, "command -v") {
		t.Fatalf("a POSIX install pipeline was composed for a Windows step: %q", step.Command)
	}
	if !strings.Contains(out.note, "not supported on Windows") {
		t.Errorf("note does not say the authorised install was not attempted: %q", out.note)
	}
}
