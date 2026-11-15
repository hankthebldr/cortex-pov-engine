package beacon

// INST-16, beacon half. The installer enrolls the agent row with ITS roster of
// capabilities; the beacon's own Register replaces it with the truth
// (artifact-fetch included). When that boot-time Register failed (SimCore
// briefly unreachable at service start), the beacon re-registered only on a
// 404 from /tasks. An ENROLLED row never answers 404, so the installer's
// roster stayed for the life of the process and the artifact-staging gate
// refused every artifact task with AGENT_CANNOT_STAGE_ARTIFACTS.

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestRunLoop_RetriesAFailedBootRegistrationOnTheNextPoll(t *testing.T) {
	var mu sync.Mutex
	var paths []string
	registers := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		paths = append(paths, r.URL.Path)
		mu.Unlock()
		switch {
		case r.URL.Path == "/api/agents/register":
			mu.Lock()
			registers++
			n := registers
			mu.Unlock()
			if n == 1 {
				w.WriteHeader(http.StatusServiceUnavailable) // SimCore still starting
				return
			}
			_, _ = w.Write([]byte(`{"status":"registered"}`))
		case strings.HasSuffix(r.URL.Path, "/tasks"):
			// Enrolled row: an idle 200, never the 404 that triggered re-register.
			_, _ = w.Write([]byte(`{"task":null}`))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer srv.Close()

	c := New(srv.URL, "enrolled-1", 20*time.Millisecond)
	if err := c.Register("h", "linux", []string{"shell", "identity-harness", "artifact-fetch"}, nil); err == nil {
		t.Fatalf("the first Register should fail (503) in this test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	c.Run(ctx)

	mu.Lock()
	defer mu.Unlock()
	if registers < 2 {
		t.Fatalf("a failed boot Register was never retried while /tasks answered 200 (requests: %v)", paths)
	}
	// Once it succeeded it stops: no Register on every poll.
	if registers > 2 {
		t.Errorf("Register kept being re-sent after it succeeded: %d calls (%v)", registers, paths)
	}
}
