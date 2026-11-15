//go:build windows

package identity

// Windows twin of harness_unix_test.go (CI-04). Run only by the windows-native
// CI job; not observed passing on Windows by the change that introduced it.

import (
	"context"
	"testing"
)

// Windows has no signals: a step that terminates its own process surfaces as an
// EXITED process with a non-zero code, and the harness carries that through.
func TestExecuteCtxStream_WindowsSelfTerminationIsExitedNotSignaled(t *testing.T) {
	r, err := ExecuteCtxStream(context.Background(),
		ExecutionIdentity{Mode: "direct", Command: "Stop-Process -Id $PID -Force"}, nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	if r.ExitCode == 0 || r.Termination != "exited" || r.Signal != 0 {
		t.Fatalf("got exit=%d term=%q sig=%d, want non-zero/exited/0", r.ExitCode, r.Termination, r.Signal)
	}
}

// A cmd step goes through the harness unchanged (direct mode, Shell=cmd).
func TestExecuteCtxStream_WindowsCmdShellReachesCmd(t *testing.T) {
	r, err := ExecuteCtxStream(context.Background(),
		ExecutionIdentity{Mode: "direct", Shell: "cmd", Command: "echo %COMSPEC%\nexit /b 5"}, nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	if r.ExitCode != 5 {
		t.Fatalf("exit = %d, want cmd's 5 (stdout=%q)", r.ExitCode, r.Stdout)
	}
}
