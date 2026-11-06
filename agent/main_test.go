//go:build !windows

package main

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"os/signal"
	"runtime"
	"sync"
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

// TestRunAsServiceIsNotHandledOffWindows: on Linux and macOS there is no
// service control manager to connect to, so the dispatcher must decline
// immediately and leave main on the console path it always had. A non-Windows
// beacon that believed it was a service would skip signal handling and could
// not be stopped by systemd or launchd.
func TestRunAsServiceIsNotHandledOffWindows(t *testing.T) {
	called := false
	handled, code, err := runAsService(serviceName, func(context.Context) int {
		called = true
		return 9
	})
	if handled {
		t.Error("runAsService reported handled=true off Windows")
	}
	if code != 0 || err != nil {
		t.Errorf("runAsService returned code=%d err=%v, want 0/nil", code, err)
	}
	if called {
		t.Error("runAsService invoked the beacon off Windows — main would run it twice")
	}
	if underSCM() {
		t.Error("underSCM() is true off Windows")
	}
}

// TestRunBeaconRegistersThenReturnsOnCancel: runBeacon is the whole beacon
// startup the service dispatcher now calls. It must still register with
// SimCore (the move must not have dropped it) and must return promptly once
// its context is cancelled — that return is what lets the SCM see STOPPED.
func TestRunBeaconRegistersThenReturnsOnCancel(t *testing.T) {
	t.Setenv("CORTEXSIM_STAGE_ROOT", t.TempDir())

	var mu sync.Mutex
	var reg map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/api/agents/register" {
			body, _ := io.ReadAll(r.Body)
			mu.Lock()
			_ = json.Unmarshal(body, &reg)
			mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte("{}"))
	}))
	defer srv.Close()

	prev := log.Writer()
	log.SetOutput(io.Discard)
	defer log.SetOutput(prev)

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // already stopping: Run must return without polling

	done := make(chan int, 1)
	go func() {
		done <- runBeacon(ctx, beaconConfig{server: srv.URL, id: "svc-test-01", interval: time.Second})
	}()
	select {
	case code := <-done:
		if code != 0 {
			t.Errorf("runBeacon returned %d, want 0", code)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("runBeacon did not return after its context was cancelled")
	}

	mu.Lock()
	defer mu.Unlock()
	if reg == nil {
		t.Fatal("runBeacon never registered with SimCore")
	}
	if reg["agent_id"] != "svc-test-01" || reg["os"] != runtime.GOOS {
		t.Errorf("registration body = %v, want agent_id=svc-test-01 os=%s", reg, runtime.GOOS)
	}
}
