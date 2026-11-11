package main

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestParseExpiresAt(t *testing.T) {
	if got, err := parseExpiresAt(""); err != nil || !got.IsZero() {
		t.Fatalf("empty: got %v err=%v, want zero time (never expires)", got, err)
	}
	got, err := parseExpiresAt("2026-11-10T12:00:00Z")
	if err != nil || !got.Equal(time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)) {
		t.Fatalf("RFC3339: got %v err=%v", got, err)
	}
	for _, bad := range []string{"tomorrow", "2026-11-10", "1731240000", "2026-11-10 12:00:00"} {
		if _, err := parseExpiresAt(bad); err == nil {
			t.Errorf("parseExpiresAt(%q) accepted a non-RFC3339 value; it must be fatal, never 'no expiry'", bad)
		}
	}
}

// TestMainRejectsBadExpiresAt drives the real main() through the re-exec helper
// in enroll_flags_test.go.
func TestMainRejectsBadExpiresAt(t *testing.T) {
	cmd := exec.Command(os.Args[0], "-test.run=^TestMainFlagValidationExits$")
	cmd.Env = append(os.Environ(), "CORTEXSIM_TEST_MAIN_ARGS=--id x --expires-at tomorrow")
	out, err := cmd.CombinedOutput()
	ee, ok := err.(*exec.ExitError)
	if !ok || ee.ExitCode() == 0 {
		t.Fatalf("want non-zero exit, got err=%v\n%s", err, out)
	}
	if !strings.Contains(string(out), "BAD_EXPIRES_AT") {
		t.Fatalf("output does not carry BAD_EXPIRES_AT:\n%s", out)
	}
}

// TestRunUntilShutdown_BoundsAStuckLoop: a poll loop that ignores cancellation
// (a long-poll request in flight takes no ctx) must not hold the process past
// the grace period after a signal.
func TestRunUntilShutdown_BoundsAStuckLoop(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sigCh := make(chan os.Signal, 1)
	block := make(chan struct{})
	defer close(block)
	stuck := func(context.Context) { <-block }

	sigCh <- syscall.SIGTERM
	start := time.Now()
	timedOut := runUntilShutdown(ctx, cancel, stuck, sigCh, 100*time.Millisecond)
	if !timedOut {
		t.Fatal("runUntilShutdown reported a clean return for a loop that never returned")
	}
	if el := time.Since(start); el > 2*time.Second {
		t.Fatalf("took %s to give up, want ~100ms", el)
	}
	if ctx.Err() == nil {
		t.Fatal("the signal did not cancel the context")
	}
}

func TestRunUntilShutdown_CleanReturnOnSignal(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sigCh := make(chan os.Signal, 1)
	sigCh <- syscall.SIGTERM
	if runUntilShutdown(ctx, cancel, func(c context.Context) { <-c.Done() }, sigCh, 5*time.Second) {
		t.Fatal("a loop that honoured cancel was reported as timed out")
	}
}

func TestRunUntilShutdown_ReturnsWhenLoopEndsOnItsOwn(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if runUntilShutdown(ctx, cancel, func(context.Context) {}, make(chan os.Signal), time.Second) {
		t.Fatal("lifetime expiry (loop returns, no signal) was reported as a timeout")
	}
}
