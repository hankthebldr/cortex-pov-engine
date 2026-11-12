//go:build !windows

package executor

import (
	"context"
	"runtime"
	"strings"
	"testing"
)

// On POSIX a step SimCore resolved to cmd or PowerShell is refused, never
// handed to sh: running cmd batch or PowerShell text under sh executes garbage
// that may still exit 0. Windows twin: TestRunShell_WindowsRefusesAnUnknownShell.
func TestRunShell_POSIXRefusesAForeignShell(t *testing.T) {
	for _, shell := range []string{ShellCmd, ShellPowerShell, "bash"} {
		o, err := RunShellCtxStreamOutcome(context.Background(), shell, "echo should-not-run", nil)
		if err == nil {
			t.Errorf("shell %q ran on %s: %+v", shell, runtime.GOOS, o)
		}
		if strings.Contains(o.Stdout, "should-not-run") {
			t.Errorf("shell %q text reached sh", shell)
		}
	}
	// The chained anchor refuses the same way.
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	if _, _, rc, err := s.RunStepShell("step-01", ShellCmd, "echo should-not-run"); err == nil || rc == 0 {
		t.Errorf("chained POSIX anchor ran a cmd step: rc=%d err=%v", rc, err)
	}
}

func TestRunCommand_CapturesStderrSeparately(t *testing.T) {
	stdout, stderr, code, err := RunCommand(">&2 echo err && echo out && exit 0")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if code != 0 {
		t.Errorf("expected 0, got %d", code)
	}
	if !strings.Contains(stdout, "out") {
		t.Errorf("stdout = %q", stdout)
	}
	if !strings.Contains(stderr, "err") {
		t.Errorf("stderr = %q", stderr)
	}
}

func TestRunCommand_SignaledChildReportsTermination(t *testing.T) {
	out, err := RunCommandCtxStreamOutcome(context.Background(), "kill -9 $$", nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	if out.ExitCode != 137 || out.Termination != "signaled" || out.Signal != 9 {
		t.Fatalf("got exit=%d term=%q sig=%d, want 137/signaled/9", out.ExitCode, out.Termination, out.Signal)
	}
}
