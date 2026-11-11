//go:build !windows

package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestShutdownSignalsIncludesSIGHUP proves the beacon survives a hangup as a
// GRACEFUL shutdown rather than a kill.
//
// This is not a set-membership assertion dressed up as a test: it registers the
// production signal set and actually delivers SIGHUP to this process. If SIGHUP
// were missing from shutdownSignals, the default disposition would terminate the
// test binary outright — the run fails loudly rather than silently passing.
// That matters because the installer's nohup fallback (and any DC running the
// beacon by hand over ssh) gets SIGHUP the moment the controlling terminal goes
// away.
func TestShutdownSignalsIncludesSIGHUP(t *testing.T) {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, shutdownSignals...)
	defer signal.Stop(ch)

	if err := syscall.Kill(syscall.Getpid(), syscall.SIGHUP); err != nil {
		t.Fatalf("could not deliver SIGHUP to self: %v", err)
	}

	select {
	case got := <-ch:
		if got != syscall.SIGHUP {
			t.Fatalf("expected SIGHUP, got %v", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("SIGHUP was not delivered to the notified channel — it is not in shutdownSignals")
	}
}

// TestShutdownSignalsCoversInterruptAndTerm guards the pre-existing contract so
// adding SIGHUP cannot quietly drop the signals systemd and Ctrl-C rely on.
func TestShutdownSignalsCoversInterruptAndTerm(t *testing.T) {
	want := map[os.Signal]bool{syscall.SIGINT: false, syscall.SIGTERM: false, syscall.SIGHUP: false}
	for _, s := range shutdownSignals {
		if _, ok := want[s]; ok {
			want[s] = true
		}
	}
	for sig, seen := range want {
		if !seen {
			t.Errorf("shutdownSignals is missing %v", sig)
		}
	}
}

// ---- enrollment failure mapping (the thin os.Exit wrapper is not under test) ----

func enrollFixture(t *testing.T, handler http.HandlerFunc) (srvURL, tokenFile, stateDir string) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	dir := t.TempDir()
	tokenFile = filepath.Join(dir, "token")
	os.WriteFile(tokenFile, []byte("cxs_token\n"), 0o600)
	return srv.URL, tokenFile, filepath.Join(dir, "state")
}

func TestEnrollAgentFailureCodes(t *testing.T) {
	old := enrollSleep
	enrollSleep = func(time.Duration) {}
	defer func() { enrollSleep = old }()

	denied := func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
		w.Write([]byte(`{"detail":{"code":"ENROLL_DENIED"}}`))
	}
	ok := func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"agent_id":"pod-1"}`)) }

	t.Run("denied", func(t *testing.T) {
		u, tf, sd := enrollFixture(t, denied)
		_, code, err := enrollAgent(u, tf, sd, "h", nil, nil, nil)
		if err == nil || code != "ENROLL_DENIED" {
			t.Errorf("code=%q err=%v", code, err)
		}
	})
	t.Run("unreachable", func(t *testing.T) {
		u, tf, sd := enrollFixture(t, ok)
		_, code, err := enrollAgent("http://127.0.0.1:1", tf, sd, "h", nil, nil, nil)
		_ = u
		if err == nil || code != "SERVER_UNREACHABLE" {
			t.Errorf("code=%q err=%v", code, err)
		}
	})
	t.Run("redirect", func(t *testing.T) {
		u, tf, sd := enrollFixture(t, func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, "http://127.0.0.1:1/x", 307)
		})
		_, code, err := enrollAgent(u, tf, sd, "h", nil, nil, nil)
		if err == nil || code != "ENROLL_REDIRECTED" {
			t.Errorf("code=%q err=%v", code, err)
		}
	})
	t.Run("empty token", func(t *testing.T) {
		u, tf, sd := enrollFixture(t, ok)
		os.WriteFile(tf, []byte(" \n"), 0o600)
		_, code, err := enrollAgent(u, tf, sd, "h", nil, nil, nil)
		if err == nil || code != "ENROLL_TOKEN_EMPTY" {
			t.Errorf("code=%q err=%v", code, err)
		}
	})
	t.Run("state dir unwritable, token never redeemed", func(t *testing.T) {
		if os.Geteuid() == 0 {
			t.Skip("root ignores directory permissions")
		}
		hits := 0
		u, tf, sd := enrollFixture(t, func(w http.ResponseWriter, r *http.Request) { hits++; ok(w, r) })
		os.MkdirAll(sd, 0o500)
		defer os.Chmod(sd, 0o700)
		_, code, err := enrollAgent(u, tf, sd, "h", nil, nil, nil)
		if err == nil || code != "STATE_DIR_UNWRITABLE" || hits != 0 {
			t.Errorf("code=%q err=%v server hits=%d (want 0)", code, err, hits)
		}
	})
	t.Run("stored id reused without reading the token file", func(t *testing.T) {
		hits := 0
		u, tf, sd := enrollFixture(t, func(w http.ResponseWriter, r *http.Request) { hits++; ok(w, r) })
		os.MkdirAll(sd, 0o700)
		os.WriteFile(filepath.Join(sd, "agent-id"), []byte("pod-keep\n"), 0o600)
		os.Remove(tf) // the Secret was rotated away
		id, code, err := enrollAgent(u, tf, sd, "h", nil, nil, nil)
		if err != nil || id != "pod-keep" || code != "" || hits != 0 {
			t.Errorf("id=%q code=%q err=%v hits=%d", id, code, err, hits)
		}
	})
	t.Run("success stores the id", func(t *testing.T) {
		u, tf, sd := enrollFixture(t, ok)
		id, _, err := enrollAgent(u, tf, sd, "h", nil, nil, nil)
		if err != nil || id != "pod-1" {
			t.Fatalf("id=%q err=%v", id, err)
		}
		if b, _ := os.ReadFile(filepath.Join(sd, "agent-id")); strings.TrimSpace(string(b)) != "pod-1" {
			t.Errorf("stored = %q", b)
		}
	})
}
