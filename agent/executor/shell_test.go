package executor

// Dialect-neutral host-shell tests: every command here means the same thing to
// sh -c and to the PowerShell exit wrapper (echo, exit N, sleep N, an unknown
// name), so they run on every host. POSIX-only ones are in shell_unix_test.go,
// their PowerShell twins in shell_windows_test.go (CI-04).

import (
	"context"
	"strings"
	"testing"
)

func TestRunCommand_HappyPath(t *testing.T) {
	stdout, stderr, code, err := RunCommand("echo hello")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if code != 0 {
		t.Errorf("expected exit 0, got %d", code)
	}
	if !strings.Contains(stdout, "hello") {
		t.Errorf("expected stdout to contain hello, got %q", stdout)
	}
	if stderr != "" {
		t.Errorf("expected empty stderr, got %q", stderr)
	}
}

func TestRunCommand_NonZeroExitIsNotAnError(t *testing.T) {
	_, _, code, err := RunCommand("exit 42")
	if err != nil {
		t.Fatalf("non-zero exit should not surface as err, got %v", err)
	}
	if code != 42 {
		t.Errorf("expected exit code 42, got %d", code)
	}
}

func TestRunCommand_BinaryNotFoundIsRealError(t *testing.T) {
	// A missing binary surfaces as a non-zero exit, not a Go error: 127 from
	// `sh -c`, 1 from the PowerShell wrapper (CommandNotFound clears $?).
	_, _, code, err := RunCommand("nonexistent-binary-cortexsim-test")
	if err != nil {
		t.Fatalf("sh-c-mediated 'not found' should surface via exit code, got err=%v", err)
	}
	if code == 0 {
		t.Errorf("expected non-zero exit for missing binary, got 0")
	}
}

func TestRunCommand_PlainExit137IsExitedNotSignaled(t *testing.T) {
	out, _ := RunCommandCtxStreamOutcome(context.Background(), "exit 137", nil)
	if out.ExitCode != 137 || out.Termination != "exited" || out.Signal != 0 {
		t.Fatalf("got exit=%d %q/%d", out.ExitCode, out.Termination, out.Signal)
	}
}

func TestRunCommand_CancelReportsAborted(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	out, err := RunCommandCtxStreamOutcome(ctx, "sleep 30", nil)
	if err != context.Canceled || out.ExitCode != 130 || out.Termination != "aborted" {
		t.Fatalf("got err=%v exit=%d term=%q", err, out.ExitCode, out.Termination)
	}
}
