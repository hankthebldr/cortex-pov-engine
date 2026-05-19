package executor

import (
	"runtime"
	"strings"
	"testing"
)

func TestRunCommand_HappyPath(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("sh -c is POSIX-only")
	}
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

func TestRunCommand_CapturesStderrSeparately(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("sh -c is POSIX-only")
	}
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

func TestRunCommand_NonZeroExitIsNotAnError(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("sh -c is POSIX-only")
	}
	_, _, code, err := RunCommand("exit 42")
	if err != nil {
		t.Fatalf("non-zero exit should not surface as err, got %v", err)
	}
	if code != 42 {
		t.Errorf("expected exit code 42, got %d", code)
	}
}

func TestRunCommand_BinaryNotFoundIsRealError(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("sh -c is POSIX-only")
	}
	// `sh -c "bogus-binary-xyz"` exits with 127 from sh, which IS captured
	// as an exit code — not a Go error.  This documents that contract.
	_, _, code, err := RunCommand("nonexistent-binary-cortexsim-test")
	if err != nil {
		t.Fatalf("sh-c-mediated 'not found' should surface via exit code, got err=%v", err)
	}
	if code == 0 {
		t.Errorf("expected non-zero exit for missing binary, got 0")
	}
}
