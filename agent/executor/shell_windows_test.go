//go:build windows

package executor

// PowerShell-dialect twins of shell_unix_test.go (CI-04). Run only by the
// windows-native CI job; not observed passing on Windows by the change that
// introduced them.

import (
	"context"
	"strings"
	"testing"
)

func TestRunCommand_WindowsCapturesStderrSeparately(t *testing.T) {
	stdout, stderr, code, err := RunCommand("[Console]::Error.WriteLine('err'); Write-Output 'out'; exit 0")
	if err != nil || code != 0 {
		t.Fatalf("code=%d err=%v", code, err)
	}
	if !strings.Contains(stdout, "out") || strings.Contains(stdout, "err") {
		t.Errorf("stdout = %q", stdout)
	}
	if !strings.Contains(stderr, "err") || strings.Contains(stderr, "out") {
		t.Errorf("stderr = %q", stderr)
	}
}

// Windows has no signals: a step that terminates its own process is an EXITED
// process with a non-zero code — never "signaled" (BEACON-09's executor half).
func TestRunCommand_WindowsSelfTerminationIsExitedNotSignaled(t *testing.T) {
	out, err := RunCommandCtxStreamOutcome(context.Background(), "Stop-Process -Id $PID -Force", nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	if out.Termination != TerminationExited || out.Signal != 0 || out.ExitCode == 0 {
		t.Fatalf("got exit=%d term=%q sig=%d, want non-zero/exited/0", out.ExitCode, out.Termination, out.Signal)
	}
}
