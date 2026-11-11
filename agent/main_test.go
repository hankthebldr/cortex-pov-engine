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
	"strings"
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

	// The stop arrives once the beacon is polling, i.e. after registration has
	// completed. (A context cancelled BEFORE runBeacon would now cancel the
	// registration itself — RG-AGENT-05, see the test below.)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var mu sync.Mutex
	var reg map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && r.URL.Path == "/api/agents/register" {
			body, _ := io.ReadAll(r.Body)
			mu.Lock()
			_ = json.Unmarshal(body, &reg)
			mu.Unlock()
		}
		if r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/tasks") {
			cancel()
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte("{}"))
	}))
	defer srv.Close()

	prev := log.Writer()
	log.SetOutput(io.Discard)
	defer log.SetOutput(prev)

	done := make(chan int, 1)
	go func() {
		done <- runBeacon(ctx, beaconConfig{server: srv.URL, id: "svc-test-01", interval: 20 * time.Millisecond})
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

// syncBuffer is a goroutine-safe log sink for the tests below.
type syncBuffer struct {
	mu sync.Mutex
	b  strings.Builder
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

// TestRunBeaconLogsItsRouteToSimCore (BEACON-19): the proxy decision is in the
// startup log — the first place a DC looks when an agent never appears — and
// --proxy is applied before the first request.
func TestRunBeaconLogsItsRouteToSimCore(t *testing.T) {
	t.Setenv("CORTEXSIM_STAGE_ROOT", t.TempDir())

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	// "SimCore" here is reachable only THROUGH this server acting as a proxy:
	// the beacon's server URL does not resolve.
	var viaProxy sync.Map
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		viaProxy.Store(r.Method+" "+r.URL.String(), true)
		if r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/tasks") {
			cancel()
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"task":null}`))
	}))
	defer proxy.Close()

	var logged syncBuffer
	prev := log.Writer()
	log.SetOutput(&logged)
	defer log.SetOutput(prev)

	done := make(chan int, 1)
	go func() {
		done <- runBeacon(ctx, beaconConfig{server: "http://simcore.lab.invalid:8888", id: "svc-test-03",
			interval: 20 * time.Millisecond, proxy: proxy.URL})
	}()
	select {
	case code := <-done:
		if code != 0 {
			t.Errorf("runBeacon returned %d, want 0", code)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("runBeacon did not return")
	}

	if _, ok := viaProxy.Load("POST http://simcore.lab.invalid:8888/api/agents/register"); !ok {
		t.Error("the registration did not go through --proxy")
	}
	out := logged.String()
	if !strings.Contains(out, "proxy: SimCore (http://simcore.lab.invalid:8888) is reached through "+proxy.URL) {
		t.Errorf("the startup log does not state the route to SimCore:\n%s", out)
	}
	if regAt, proxyAt := strings.Index(out, "registering —"), strings.Index(out, "proxy: SimCore"); regAt < 0 || proxyAt < 0 || proxyAt > regAt {
		t.Errorf("the route must be logged before the first request (proxy line at %d, registering at %d)", proxyAt, regAt)
	}
}

// TestRunBeacon_AStopDuringABlockedRegistrationReturnsAtOnce (RG-AGENT-05).
// SimCore accepts the connection and never answers — an address a firewall
// silently drops looks the same from here. The stop (SIGTERM on the console
// path, an SCM STOP under the service: both only cancel ctx) arrives while
// Register is blocked. runBeacon must return at once; at head it waited out the
// 30 s HTTP timeout, past launchd's 20 s kill timer.
func TestRunBeacon_AStopDuringABlockedRegistrationReturnsAtOnce(t *testing.T) {
	t.Setenv("CORTEXSIM_STAGE_ROOT", t.TempDir())

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	release := make(chan struct{})
	var once sync.Once
	stopped := make(chan time.Time, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		once.Do(func() {
			stopped <- time.Now()
			cancel()
		})
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer srv.Close()
	defer close(release)

	prev := log.Writer()
	log.SetOutput(io.Discard)
	defer log.SetOutput(prev)

	done := make(chan int, 1)
	go func() {
		done <- runBeacon(ctx, beaconConfig{server: srv.URL, id: "svc-test-02", interval: time.Second})
	}()
	select {
	case code := <-done:
		if code != 0 {
			t.Errorf("runBeacon returned %d, want 0", code)
		}
		if took := time.Since(<-stopped); took > 2*time.Second {
			t.Errorf("runBeacon returned %s after the stop", took.Round(10*time.Millisecond))
		}
	case <-time.After(5 * time.Second):
		t.Fatal("runBeacon had not returned 5 s after a stop that arrived during a blocked registration " +
			"(it is waiting out the 30 s HTTP timeout)")
	}
}
