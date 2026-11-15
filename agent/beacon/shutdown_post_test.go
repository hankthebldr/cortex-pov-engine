package beacon

// RG-AGENT-04 / RG-AGENT-05 — what the retry ladder of f72da6e cost a beacon
// that is told to stop. (The frame key a retry needs is frame_id_test.go.)
//
//   - RG-AGENT-04: postRetry slept with time.Sleep and posted with no context,
//     so a beacon told to stop (SIGTERM, an SCM STOP) while SimCore was down sat
//     out the whole ladder for /output and again for /complete: 62 s measured,
//     past launchd's 20 s kill timer, and a SIGKILL skips the staged-artifact
//     cleanup.
//   - RG-AGENT-05: Register runs after the signal handler is installed and had
//     no context either, so a stop during a blocked registration waited out the
//     30 s HTTP timeout.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// evidenceRefusingServer keeps /control healthy and answers every /output and
// /complete with status (0 = hang until the test ends), counting attempts.
type evidenceRefusingServer struct {
	*httptest.Server
	status    int
	outputs   atomic.Int64
	completes atomic.Int64
	mu        sync.Mutex
	bodies    []string // every /output body, in arrival order
	release   chan struct{}
}

func newEvidenceRefusingServer(t *testing.T, status int) *evidenceRefusingServer {
	t.Helper()
	s := &evidenceRefusingServer{status: status, release: make(chan struct{})}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/control"):
			_ = json.NewEncoder(w).Encode(ControlState{Abort: false, Status: "running"})
			return
		case strings.HasSuffix(r.URL.Path, "/output"):
			s.outputs.Add(1)
			s.mu.Lock()
			s.bodies = append(s.bodies, string(body))
			s.mu.Unlock()
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.completes.Add(1)
		}
		if s.status == 0 {
			select {
			case <-s.release:
			case <-r.Context().Done():
			}
			return
		}
		w.WriteHeader(s.status)
	}))
	t.Cleanup(func() {
		close(s.release)
		s.Close()
	})
	return s
}

// blockUntilCancelled is a step that runs until its context is cancelled — an
// in-flight TTP at the moment the beacon is told to stop.
func blockUntilCancelled(started chan<- struct{}) func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error) {
	return func(ctx context.Context, _ identity.ExecutionIdentity, _ executor.OutputChunkFunc) (identity.ExecResult, error) {
		close(started)
		<-ctx.Done()
		return identity.ExecResult{ExitCode: 130, Termination: TermAborted}, context.Canceled
	}
}

// RG-AGENT-04. SimCore answers 503 to every /output and /complete and the
// beacon is told to stop mid-step. The run must end at once: one attempt each
// for the step's output and the "agent shutdown" completion, no ladder.
func TestExecuteTask_ShutdownIsNotHeldByTheRetryLadder(t *testing.T) {
	old := postRetryBackoff
	postRetryBackoff = []time.Duration{400 * time.Millisecond, 400 * time.Millisecond, 400 * time.Millisecond, 400 * time.Millisecond, 400 * time.Millisecond}
	t.Cleanup(func() { postRetryBackoff = old })

	srv := newEvidenceRefusingServer(t, http.StatusServiceUnavailable)
	started := make(chan struct{})
	c := New(srv.URL, "a-1", 0)
	c.execStep = blockUntilCancelled(started)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		c.executeTask(ctx, &Task{TaskID: "t", RunID: "r", ScenarioID: "S",
			Steps: []Step{{ID: "step-01", Command: "long", Identity: "root"}}})
	}()
	<-started
	stopAt := time.Now()
	cancel() // SIGTERM / SCM STOP

	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("executeTask had not returned 10 s after shutdown")
	}
	if took := time.Since(stopAt); took > time.Second {
		t.Errorf("shutdown took %s with SimCore answering 503 — the retry ladder (2 x %s here, 2 x 31 s in "+
			"production) was waited out instead of being cut short", took.Round(10*time.Millisecond), 5*400*time.Millisecond)
	}
	if o, k := srv.outputs.Load(), srv.completes.Load(); o != 1 || k != 1 {
		t.Errorf("attempts after shutdown: /output=%d /complete=%d, want exactly one each "+
			"(the evidence still gets its one chance; nothing is retried)", o, k)
	}
}

// RG-AGENT-04. A stop that lands during a backoff wait ends the wait and buys
// exactly one more attempt — the evidence gets a last chance, the ladder does
// not get its remaining 30 s.
func TestPostRetry_ShutdownDuringTheWaitBuysOneFinalAttempt(t *testing.T) {
	old := postRetryBackoff
	postRetryBackoff = []time.Duration{5 * time.Second, 5 * time.Second, 5 * time.Second, 5 * time.Second, 5 * time.Second}
	t.Cleanup(func() { postRetryBackoff = old })

	srv := newEvidenceRefusingServer(t, http.StatusServiceUnavailable)
	c := New(srv.URL, "a-1", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c.BindShutdown(ctx)

	go func() {
		// Wait for the first attempt to have failed, then stop mid-wait.
		for srv.completes.Load() == 0 {
			time.Sleep(5 * time.Millisecond)
		}
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()
	start := time.Now()
	err := c.Complete("r", 0, "ok", nil)
	if err == nil {
		t.Fatal("Complete reported success against a server that only said 503")
	}
	if took := time.Since(start); took > 2*time.Second {
		t.Errorf("Complete took %s — the 5 s backoff wait was not interrupted by the shutdown", took.Round(10*time.Millisecond))
	}
	if got := srv.completes.Load(); got != 2 {
		t.Errorf("attempts = %d, want 2 (the one that failed, then ONE final attempt after the stop)", got)
	}
	if !strings.Contains(err.Error(), "shutting down") {
		t.Errorf("the error does not say why the ladder stopped: %v", err)
	}
}

// RG-AGENT-04, the black-hole case. An attempt that is in flight when the stop
// arrives (SimCore accepts the connection and never answers) is cut after
// shutdownPostGrace, not after the client's 30 s timeout, and is not retried.
func TestPostRetry_AnAttemptInFlightAtShutdownIsCutAfterTheGrace(t *testing.T) {
	shortPostBackoff(t)
	oldGrace := shutdownPostGrace
	shutdownPostGrace = 200 * time.Millisecond
	t.Cleanup(func() { shutdownPostGrace = oldGrace })

	srv := newEvidenceRefusingServer(t, 0) // hangs
	c := New(srv.URL, "a-1", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c.BindShutdown(ctx)

	since := cancelOnceHit(&srv.completes, cancel)
	err := c.Complete("r", 0, "ok", nil)
	after := since()
	if err == nil {
		t.Fatal("Complete reported success against a server that never answered")
	}
	if after > 2*time.Second {
		t.Errorf("Complete returned %s after the stop — the in-flight attempt was not cut", after.Round(10*time.Millisecond))
	}
	if after < shutdownPostGrace/2 {
		t.Errorf("Complete returned %s after the stop — the in-flight attempt was not given its %s grace",
			after.Round(time.Millisecond), shutdownPostGrace)
	}
	if got := srv.completes.Load(); got != 1 {
		t.Errorf("attempts = %d, want 1: an attempt that fails after the stop is not retried", got)
	}
}

// Without a stop nothing changes: the ladder is still climbed in full.
func TestPostRetry_WithoutAShutdownTheLadderIsUnchanged(t *testing.T) {
	shortPostBackoff(t)
	srv := newEvidenceRefusingServer(t, http.StatusServiceUnavailable)
	c := New(srv.URL, "a-1", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c.BindShutdown(ctx) // bound, never cancelled
	if err := c.Complete("r", 0, "ok", nil); err == nil {
		t.Fatal("Complete reported success against a server that only said 503")
	}
	if got, want := srv.completes.Load(), int64(len(postRetryBackoff)+1); got != want {
		t.Errorf("attempts = %d, want %d", got, want)
	}
}

// hangingServer accepts every request and never answers it.
func hangingServer(t *testing.T) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var hits atomic.Int64
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	t.Cleanup(func() {
		close(release)
		srv.Close()
	})
	return srv, &hits
}

// cancelOnceHit cancels once the server has seen a request, and returns a func
// reporting how long ago that was.
func cancelOnceHit(hits *atomic.Int64, cancel context.CancelFunc) func() time.Duration {
	var mu sync.Mutex
	var at time.Time
	go func() {
		for hits.Load() == 0 {
			time.Sleep(5 * time.Millisecond)
		}
		mu.Lock()
		at = time.Now()
		mu.Unlock()
		cancel()
	}()
	return func() time.Duration {
		mu.Lock()
		defer mu.Unlock()
		return time.Since(at)
	}
}

// RG-AGENT-05. A stop during a blocked registration cancels it at once. At head
// it waited out the 30 s HTTP timeout (28 s measured with the built beacon).
func TestRegister_ShutdownCancelsABlockedRegistration(t *testing.T) {
	srv, hits := hangingServer(t)
	c := New(srv.URL, "a-1", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c.BindShutdown(ctx)
	since := cancelOnceHit(hits, cancel)

	err := c.Register("host", "linux", []string{"shell"}, nil)
	if err == nil {
		t.Fatal("Register reported success against a server that never answered")
	}
	if d := since(); d > time.Second {
		t.Errorf("Register returned %s after the stop — a blocked registration must be cancelled at once", d.Round(10*time.Millisecond))
	}
}

// The same for an idle task poll, including a long poll (wait + 10 s timeout).
func TestPollTasks_ShutdownCancelsABlockedPoll(t *testing.T) {
	for _, wait := range []int{0, 60} {
		srv, hits := hangingServer(t)
		c := New(srv.URL, "a-1", 0)
		ctx, cancel := context.WithCancel(context.Background())
		c.BindShutdown(ctx)
		since := cancelOnceHit(hits, cancel)

		if _, err := c.PollTasksWait(wait); err == nil {
			t.Errorf("wait=%d: PollTasksWait reported success against a server that never answered", wait)
		}
		if d := since(); d > time.Second {
			t.Errorf("wait=%d: the poll returned %s after the stop", wait, d.Round(10*time.Millisecond))
		}
		cancel()
	}
}

// Run binds its own ctx: a stop while the poll loop is blocked on SimCore ends
// the loop, with no main-side wiring.
func TestRun_ShutdownDuringABlockedPollReturns(t *testing.T) {
	srv, hits := hangingServer(t)
	c := New(srv.URL, "a-1", 10*time.Millisecond)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	since := cancelOnceHit(hits, cancel)

	done := make(chan struct{})
	go func() { c.Run(ctx); close(done) }()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Run had not returned 10 s after shutdown")
	}
	if d := since(); d > time.Second {
		t.Errorf("Run returned %s after the stop", d.Round(10*time.Millisecond))
	}
}
