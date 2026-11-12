package beacon

// RG-AGENT-04, residual. The retry ladder and the idle poll were bound to the
// shutdown context in round 2, but Control() still used c.http.Get: a 30 s GET
// that nothing cancels. The abort check runs before every step and every
// controlPollInterval while one runs, so a stop (SIGTERM, launchctl stop, an
// SCM STOP) that lands while /control is blocked on an unresponsive SimCore
// waited out that whole timeout — past launchd's 20 s stop window, after which
// SIGKILL skips the staged-artifact cleanup and the "agent shutdown" record.
//
// And when the GET did give up, the failed request read as a lost control
// channel, so the run record said CONTROL_CHANNEL_LOST for a stop the beacon
// was told to make.

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

// controlBlackHoleServer answers /output and /complete at once and recording
// their bodies; /control answers 200 abort:false until hang is set, then
// accepts the request and never answers it.
type controlBlackHoleServer struct {
	*httptest.Server
	hang      atomic.Bool
	hung      atomic.Int64 // /control requests accepted and left hanging
	mu        sync.Mutex
	outputs   []string
	completes []string
	release   chan struct{}
}

func newControlBlackHoleServer(t *testing.T) *controlBlackHoleServer {
	t.Helper()
	s := &controlBlackHoleServer{release: make(chan struct{})}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/control"):
			if s.hang.Load() {
				s.hung.Add(1)
				select {
				case <-s.release:
				case <-r.Context().Done():
				}
				return
			}
			_ = json.NewEncoder(w).Encode(ControlState{Abort: false, Status: "running"})
		case strings.HasSuffix(r.URL.Path, "/output"):
			s.mu.Lock()
			s.outputs = append(s.outputs, string(body))
			s.mu.Unlock()
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.mu.Lock()
			s.completes = append(s.completes, string(body))
			s.mu.Unlock()
		}
	}))
	t.Cleanup(func() {
		close(s.release)
		s.Close()
	})
	return s
}

func (s *controlBlackHoleServer) record(t *testing.T) (summary string, steps []StepOutcome, outputs string) {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.completes) != 1 {
		t.Fatalf("want exactly one /complete, got %d", len(s.completes))
	}
	var b struct {
		Summary string        `json:"summary"`
		Steps   []StepOutcome `json:"steps"`
	}
	if err := json.Unmarshal([]byte(s.completes[0]), &b); err != nil {
		t.Fatal(err)
	}
	return b.Summary, b.Steps, strings.Join(s.outputs, "\n")
}

// runUntilStopped runs a two-step task, cancels the beacon's context once a
// /control request is hanging, and returns how long executeTask took to return
// after the stop.
func runUntilStopped(t *testing.T, srv *controlBlackHoleServer, c *BeaconClient) time.Duration {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var stopAt atomic.Int64
	go func() {
		for srv.hung.Load() == 0 {
			time.Sleep(5 * time.Millisecond)
		}
		stopAt.Store(time.Now().UnixNano())
		cancel() // SIGTERM / launchctl stop / SCM STOP
	}()
	done := make(chan struct{})
	go func() {
		defer close(done)
		c.executeTask(ctx, &Task{TaskID: "t", RunID: "r", ScenarioID: "S", Steps: []Step{
			{ID: "step-01", Command: "long", Identity: "root"},
			{ID: "step-02", Command: "next", Identity: "root"},
		}})
	}()
	select {
	case <-done:
	case <-time.After(15 * time.Second):
		t.Fatal("executeTask had not returned 15 s after the stop — the /control GET " +
			"(30 s client timeout) is not cancelled by the shutdown")
	}
	return time.Since(time.Unix(0, stopAt.Load()))
}

func assertShutdownRecord(t *testing.T, srv *controlBlackHoleServer) []StepOutcome {
	t.Helper()
	sum, steps, outputs := srv.record(t)
	if !strings.Contains(sum, "agent shutdown") {
		t.Errorf("/complete summary does not say the beacon was stopped: %q", sum)
	}
	for _, s := range []string{sum, outputs} {
		if strings.Contains(s, ReasonControlChannelLost) || strings.Contains(s, "control channel lost") {
			t.Errorf("a shutdown was recorded as a lost control channel: %q", s)
		}
		if strings.Contains(s, "aborted by operator") || strings.Contains(s, "ABORTED (operator)") {
			t.Errorf("a shutdown was recorded as an operator abort: %q", s)
		}
	}
	return steps
}

// A stop while the in-step /control poll is blocked ends the run at once, and
// the run record says the beacon was stopped.
func TestExecuteTask_ShutdownCancelsABlockedInStepControlPoll(t *testing.T) {
	srv := newControlBlackHoleServer(t)
	c := New(srv.URL, "a-1", 0)
	c.execStep = func(ctx context.Context, _ identity.ExecutionIdentity, _ executor.OutputChunkFunc) (identity.ExecResult, error) {
		srv.hang.Store(true) // the next /control poll (in ~2 s) hangs
		<-ctx.Done()
		return identity.ExecResult{ExitCode: 130, Termination: TermAborted}, context.Canceled
	}

	if took := runUntilStopped(t, srv, c); took > time.Second {
		t.Errorf("executeTask returned %s after the stop", took.Round(10*time.Millisecond))
	}
	steps := assertShutdownRecord(t, srv)
	if len(steps) != 2 || steps[0].Termination != TermAborted || steps[1].Termination != TermNotRun {
		t.Errorf("step outcomes = %+v, want step-01 aborted, step-02 not_run", steps)
	}
}

// A stop while the pre-step /control check is blocked ends the run before the
// step starts: the step is not run, and is not reported as aborted.
func TestExecuteTask_ShutdownCancelsABlockedPreStepControlCheck(t *testing.T) {
	srv := newControlBlackHoleServer(t)
	srv.hang.Store(true)
	ran := atomic.Bool{}
	c := New(srv.URL, "a-1", 0)
	c.execStep = func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error) {
		ran.Store(true)
		return identity.ExecResult{ExitCode: 0, Termination: TermExited}, nil
	}

	if took := runUntilStopped(t, srv, c); took > time.Second {
		t.Errorf("executeTask returned %s after the stop", took.Round(10*time.Millisecond))
	}
	if ran.Load() {
		t.Error("a step was started after the beacon was told to stop")
	}
	steps := assertShutdownRecord(t, srv)
	for _, s := range steps {
		if s.Termination != TermNotRun {
			t.Errorf("step %s = %q, want not_run: it never started", s.StepID, s.Termination)
		}
	}
}

// Control alone: bound to the shutdown context, and a cancelled request is
// the beacon stopping, not the channel failing.
func TestControl_ShutdownCancelsABlockedPollAndSaysSo(t *testing.T) {
	srv, hits := hangingServer(t)
	c := New(srv.URL, "a-1", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c.BindShutdown(ctx)
	since := cancelOnceHit(hits, cancel)

	state, err := c.Control("r")
	if d := since(); d > time.Second {
		t.Errorf("Control returned %s after the stop", d.Round(10*time.Millisecond))
	}
	if err == nil {
		t.Error("Control reported success against a server that never answered")
	}
	if !state.Abort || state.Lost || !state.Shutdown {
		t.Errorf("state = %+v, want Abort, Shutdown, not Lost", state)
	}
}

// Without a stop, a broken channel is still CONTROL_CHANNEL_LOST (fail closed).
func TestControl_ATransportFailureWithoutAStopIsStillLost(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close() // connection refused
	c := New(url, "a-1", 0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c.BindShutdown(ctx) // bound, never cancelled

	state, err := c.Control("r")
	if err == nil || !state.Abort || !state.Lost || state.Shutdown {
		t.Errorf("state = %+v err = %v, want a lost channel (Abort, Lost, not Shutdown)", state, err)
	}
}
