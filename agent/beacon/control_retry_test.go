package beacon

// BEACON-11 / OPS-04. Two ways a run record used to lie about what stopped it
// or lose the result entirely:
//
//  1. Control() fails CLOSED — a transport error, a non-200 or an undecodable
//     body all read as abort=true. That stays (Henry's decision is pending), but
//     the run record said "aborted by operator" for an abort nobody issued. A DC
//     reads that as a person stopping the run.
//  2. post() made ONE attempt. A SimCore restart or a proxy blip at the moment a
//     step finished lost that step's /output (the evidence) or the run's
//     /complete (the run then sat "running" until the beacon-lost sweep).

import (
	"context"
	"encoding/json"
	"net"
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

func shortPostBackoff(t *testing.T) {
	t.Helper()
	old := postRetryBackoff
	postRetryBackoff = []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond, time.Millisecond, time.Millisecond}
	t.Cleanup(func() { postRetryBackoff = old })
}

// controlFailingServer answers /control with controlStatus and records /output
// and /complete bodies.
type controlFailingServer struct {
	*httptest.Server
	mu            sync.Mutex
	outputs       []string
	completes     []string
	controlStatus atomic.Int64 // 0 = healthy 200 abort:false
}

func newControlFailingServer(t *testing.T) *controlFailingServer {
	t.Helper()
	s := &controlFailingServer{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body strings.Builder
		buf := make([]byte, 4096)
		for {
			n, err := r.Body.Read(buf)
			body.Write(buf[:n])
			if err != nil {
				break
			}
		}
		switch {
		case strings.HasSuffix(r.URL.Path, "/control"):
			if code := s.controlStatus.Load(); code != 0 {
				w.WriteHeader(int(code))
				return
			}
			_ = json.NewEncoder(w).Encode(ControlState{Abort: false, Status: "running"})
		case strings.HasSuffix(r.URL.Path, "/output"):
			s.mu.Lock()
			s.outputs = append(s.outputs, body.String())
			s.mu.Unlock()
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.mu.Lock()
			s.completes = append(s.completes, body.String())
			s.mu.Unlock()
		}
	}))
	t.Cleanup(s.Close)
	return s
}

func (s *controlFailingServer) summary(t *testing.T) string {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.completes) != 1 {
		t.Fatalf("want exactly one /complete, got %d", len(s.completes))
	}
	var b struct {
		Summary string `json:"summary"`
	}
	if err := json.Unmarshal([]byte(s.completes[0]), &b); err != nil {
		t.Fatal(err)
	}
	return b.Summary
}

// A control channel that breaks while a step runs still stops the step (fail
// closed), but the run record must say WHY — never "aborted by operator".
func TestExecuteTask_ControlChannelLostMidStepIsNotAnOperatorAbort(t *testing.T) {
	srv := newControlFailingServer(t)

	blocking := func(ctx context.Context, id identity.ExecutionIdentity, _ executor.OutputChunkFunc) (identity.ExecResult, error) {
		// The control channel breaks once the step is in flight.
		srv.controlStatus.Store(http.StatusBadGateway)
		<-ctx.Done()
		return identity.ExecResult{ExitCode: 130, Termination: TermAborted}, context.Canceled
	}
	c := New(srv.URL, "a-1", 0)
	c.execStep = blocking
	c.executeTask(context.Background(), &Task{
		TaskID: "t", RunID: "r", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", Command: "long", Identity: "root"}},
	})

	sum := srv.summary(t)
	if strings.Contains(sum, "aborted by operator") {
		t.Fatalf("a lost control channel was reported as an operator abort: %q", sum)
	}
	for _, want := range []string{"control channel lost", "NOT an operator abort", "CONTROL_CHANNEL_LOST"} {
		if !strings.Contains(sum, want) {
			t.Errorf("summary missing %q: %q", want, sum)
		}
	}
}

// The same for the pre-step check: a control channel that is already down
// before step 1 stops the run, and says the channel — not a person — did it.
func TestExecuteTask_ControlChannelDownBeforeAStepIsNotAnOperatorAbort(t *testing.T) {
	srv := newControlFailingServer(t)
	srv.controlStatus.Store(http.StatusInternalServerError)

	rec := &execRecorder{}
	c := New(srv.URL, "a-1", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), &Task{
		TaskID: "t", RunID: "r", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", Command: "echo x", Identity: "root"}},
	})

	if len(rec.commands()) != 0 {
		t.Fatalf("a step ran although the control channel was down (fail-closed is kept)")
	}
	sum := srv.summary(t)
	if strings.Contains(sum, "aborted by operator") || !strings.Contains(sum, "CONTROL_CHANNEL_LOST") {
		t.Fatalf("pre-step control loss reported as %q", sum)
	}
}

// Only an explicit abort:true body is an operator abort.
func TestExecuteTask_ExplicitAbortBodyIsStillAnOperatorAbort(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	rs.abortFlag.Store(true)
	c := New(rs.URL, "a-1", 0)
	c.execStep = (&execRecorder{}).exec
	c.executeTask(context.Background(), &Task{
		TaskID: "t", RunID: "r", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", Command: "echo x", Identity: "root"}},
	})
	if sum := decodeComplete(t, rs).Summary; !strings.Contains(sum, "aborted by operator") ||
		strings.Contains(sum, "CONTROL_CHANNEL_LOST") {
		t.Fatalf("an explicit abort must read as an operator abort: %q", sum)
	}
}

// statusSequenceServer answers /complete and /output with the given statuses
// in order (the last one repeats) and counts attempts.
func statusSequenceServer(t *testing.T, statuses ...int) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var hits atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := int(hits.Add(1)) - 1
		if n >= len(statuses) {
			n = len(statuses) - 1
		}
		w.WriteHeader(statuses[n])
	}))
	t.Cleanup(srv.Close)
	return srv, &hits
}

func TestComplete_RetriesA5xxThenSucceeds(t *testing.T) {
	shortPostBackoff(t)
	srv, hits := statusSequenceServer(t, 503, 502, 200)
	if err := New(srv.URL, "a", 0).Complete("r", 0, "ok", nil); err != nil {
		t.Fatalf("Complete gave up on a transient 5xx: %v", err)
	}
	if got := hits.Load(); got != 3 {
		t.Fatalf("attempts = %d, want 3 (two 5xx then 200)", got)
	}
}

func TestComplete_GivesUpAfterTheBoundedRetries(t *testing.T) {
	shortPostBackoff(t)
	srv, hits := statusSequenceServer(t, 503)
	if err := New(srv.URL, "a", 0).Complete("r", 0, "ok", nil); err == nil {
		t.Fatal("Complete reported success against a server that only ever said 503")
	}
	if got, want := hits.Load(), int64(len(postRetryBackoff)+1); got != want {
		t.Fatalf("attempts = %d, want %d (1 + %d retries)", got, want, len(postRetryBackoff))
	}
}

// 409: the run is already terminal on SimCore. Retrying cannot change that and
// reporting an error would log a phantom failure, so it is terminal success.
func TestComplete_409IsTerminalSuccess(t *testing.T) {
	shortPostBackoff(t)
	srv, hits := statusSequenceServer(t, 409)
	if err := New(srv.URL, "a", 0).Complete("r", 0, "ok", nil); err != nil {
		t.Fatalf("409 (run already terminal) must not be an error: %v", err)
	}
	if hits.Load() != 1 {
		t.Fatalf("409 was retried (%d attempts)", hits.Load())
	}
}

// 404 (unknown run) and any other 4xx are not transient: no retry.
func TestComplete_4xxIsNotRetried(t *testing.T) {
	shortPostBackoff(t)
	for _, code := range []int{404, 400, 422} {
		srv, hits := statusSequenceServer(t, code)
		if err := New(srv.URL, "a", 0).Complete("r", 0, "ok", nil); err == nil {
			t.Errorf("%d reported as success", code)
		}
		if hits.Load() != 1 {
			t.Errorf("%d was retried (%d attempts)", code, hits.Load())
		}
	}
}

// A transport error (the connection dropped before any response) is retried.
func TestSendOutput_RetriesATransportError(t *testing.T) {
	shortPostBackoff(t)
	var hits atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if hits.Add(1) == 1 {
			hj, ok := w.(http.Hijacker)
			if !ok {
				t.Fatal("no hijacker")
			}
			conn, _, _ := hj.Hijack()
			_ = conn.(*net.TCPConn).SetLinger(0)
			_ = conn.Close()
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()
	if err := New(srv.URL, "a", 0).SendOutput("r", "step-01", "evidence"); err != nil {
		t.Fatalf("SendOutput lost the step's evidence on one dropped connection: %v", err)
	}
	if hits.Load() != 2 {
		t.Fatalf("attempts = %d, want 2", hits.Load())
	}
}
