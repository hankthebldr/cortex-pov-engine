package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
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

func TestExpiredAtStartup(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	if expiredAtStartup(time.Time{}, now) {
		t.Fatal("zero expiry (never) reported as expired")
	}
	if expiredAtStartup(now.Add(time.Minute), now) {
		t.Fatal("future expiry reported as expired")
	}
	if !expiredAtStartup(now, now) || !expiredAtStartup(now.Add(-time.Hour), now) {
		t.Fatal("past expiry not reported as expired")
	}
}

// A pod whose lifetime ran out before the beacon started must not redeem its
// enrollment token or register: it logs LIFETIME_EXPIRED and exits 0, and the
// supervisor idles. The enroll server must receive ZERO requests.
func TestMainExpiredAtStartupNeverEnrolls(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	dir := t.TempDir()
	tok := filepath.Join(dir, "token")
	if err := os.WriteFile(tok, []byte("cxs_test_token\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	past := time.Now().Add(-time.Hour).UTC().Format(time.RFC3339)
	cmd := exec.Command(os.Args[0], "-test.run=^TestMainFlagValidationExits$")
	cmd.Env = append(os.Environ(), "CORTEXSIM_TEST_MAIN_ARGS=--server "+srv.URL+
		" --enroll-token-file "+tok+" --state-dir "+filepath.Join(dir, "state")+
		" --expires-at "+past)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("want exit 0, got err=%v\n%s", err, out)
	}
	if !strings.Contains(string(out), "LIFETIME_EXPIRED") {
		t.Fatalf("output does not carry LIFETIME_EXPIRED:\n%s", out)
	}
	if n := atomic.LoadInt32(&hits); n != 0 {
		t.Fatalf("enroll server received %d request(s), want 0\n%s", n, out)
	}
}
