//go:build windows

package main

// BEACON-19 on a real Windows host: with no --proxy, the beacon's startup log
// says outright that the Windows system proxy is not read. Run only by the
// windows-native CI job; NOT observed passing on Windows by the change that
// introduced it.

import (
	"context"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type winLogBuffer struct {
	mu sync.Mutex
	b  strings.Builder
}

func (s *winLogBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *winLogBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

func TestRunBeaconOnWindowsSaysTheSystemProxyIsNotRead(t *testing.T) {
	t.Setenv("CORTEXSIM_STAGE_ROOT", t.TempDir())

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && strings.HasSuffix(r.URL.Path, "/tasks") {
			cancel()
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"task":null}`))
	}))
	defer srv.Close()

	var logged winLogBuffer
	prev := log.Writer()
	log.SetOutput(&logged)
	defer log.SetOutput(prev)

	done := make(chan int, 1)
	go func() {
		done <- runBeacon(ctx, beaconConfig{server: srv.URL, id: "svc-test-win-proxy", interval: 20 * time.Millisecond})
	}()
	select {
	case code := <-done:
		if code != 0 {
			t.Errorf("runBeacon returned %d, want 0", code)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("runBeacon did not return")
	}

	out := logged.String()
	for _, want := range []string{"proxy: SimCore (" + srv.URL + ")", "Windows system proxy", "NOT read", "--proxy"} {
		if !strings.Contains(out, want) {
			t.Errorf("the startup log does not mention %q:\n%s", want, out)
		}
	}
}
