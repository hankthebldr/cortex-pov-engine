package beacon

// Live runtime log, beacon half (liveout.go). When the task carries
// `live_output` the beacon posts a "start" frame with the command before a
// step runs and "chunk" frames while it runs. The final per-step frame is
// unchanged, and a slow or failing SimCore never holds a step up.
//
// These tests drive executeTask through the execStep seam, so they run the
// same on every platform. The chained path needs a real shell:
// liveout_unix_test.go.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// liveFrame is one /output body as SimCore would read it.
type liveFrame struct {
	Kind     string `json:"kind"`
	Seq      int    `json:"seq"`
	StepID   string `json:"step_id"`
	Output   string `json:"output"`
	Command  string `json:"command"`
	Identity string `json:"identity"`
	Index    int    `json:"index"`
	Total    int    `json:"total"`
	FrameID  string `json:"frame_id"`
	raw      string
}

// liveServer records every /output and /complete body in arrival order.
type liveServer struct {
	*httptest.Server
	mu        sync.Mutex
	frames    []liveFrame
	completes []string
	// hold, when set, blocks every LIVE frame (one with a kind) until it is
	// closed. Final frames and /complete are never held.
	hold chan struct{}
	// liveStatus, when non-zero, is the status every live frame is answered with.
	liveStatus int
}

func newLiveServer(t *testing.T) *liveServer {
	t.Helper()
	s := &liveServer{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/control"):
			_ = json.NewEncoder(w).Encode(ControlState{Status: "running"})
		case strings.HasSuffix(r.URL.Path, "/output"):
			f := liveFrame{raw: string(body)}
			_ = json.Unmarshal(body, &f)
			s.mu.Lock()
			s.frames = append(s.frames, f)
			hold, status := s.hold, s.liveStatus
			s.mu.Unlock()
			if f.Kind != "" {
				if hold != nil {
					select {
					case <-hold:
					case <-r.Context().Done():
					}
				}
				if status != 0 {
					w.WriteHeader(status)
					return
				}
			}
			w.WriteHeader(http.StatusOK)
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.mu.Lock()
			s.completes = append(s.completes, string(body))
			s.mu.Unlock()
			w.WriteHeader(http.StatusOK)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusInternalServerError)
		}
	}))
	t.Cleanup(s.Close)
	return s
}

func (s *liveServer) all() []liveFrame {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]liveFrame(nil), s.frames...)
}

// kind returns the recorded frames of one kind ("" = final per-step frames).
func (s *liveServer) kind(kind string) []liveFrame {
	var out []liveFrame
	for _, f := range s.all() {
		if f.Kind == kind {
			out = append(out, f)
		}
	}
	return out
}

func (s *liveServer) completeBodies() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.completes...)
}

// waitFor polls cond for up to 5 s and reports a timeout with Errorf, never
// Fatalf: it is called from inside a step, which runs on its own goroutine.
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Errorf("timed out waiting for %s", what)
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// streams reports whether the step was handed a live output callback.
func streams(t *testing.T, on executor.OutputChunkFunc) bool {
	t.Helper()
	if on == nil {
		t.Errorf("the step was given no live output callback although the task carries live_output")
	}
	return on != nil
}

func setLiveStartWait(t *testing.T, d time.Duration) {
	t.Helper()
	old := liveStartWait
	liveStartWait = d
	t.Cleanup(func() { liveStartWait = old })
}

func liveClient(url string) *BeaconClient {
	c := New(url, "agent-live", time.Millisecond)
	c.resolveIdentity = func(string) identity.Resolution {
		return identity.Resolution{Mode: "direct", Username: "root", Honoured: true}
	}
	return c
}

type stepExec = func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error)

func liveTask(live *LiveOutput) *Task {
	return &Task{
		TaskID: "task-live", RunID: "run-live", ScenarioID: "SIM-TEST-LIVE", LiveOutput: live,
		Steps: []Step{
			{ID: "step-01", Name: "first", Command: "echo one", Identity: "www-data", MitreTechnique: "T1059"},
			{ID: "step-02", Name: "second", Command: "echo two", Identity: "postgres", MitreTechnique: "T1082"},
		},
	}
}

// fixedExec is a step that prints two stdout lines and one stderr line and
// reports a fixed duration, so its final frame is the same on every run.
func fixedExec(_ context.Context, _ identity.ExecutionIdentity, onChunk executor.OutputChunkFunc) (identity.ExecResult, error) {
	if onChunk != nil {
		onChunk("tick 1\n", false)
		onChunk("warn\n", true)
		onChunk("tick 2\n", false)
	}
	return identity.ExecResult{Stdout: "tick 1\ntick 2\n", Stderr: "warn\n",
		Duration: 1500 * time.Millisecond, Termination: TermExited}, nil
}

func TestStartFramePostedBeforeStepRuns(t *testing.T) {
	setLiveStartWait(t, 5*time.Second) // a healthy server answers long before this
	srv := newLiveServer(t)
	c := liveClient(srv.URL)

	// What SimCore had been told about each step at the moment it executed.
	startsAtExec := map[string][]liveFrame{}
	c.execStep = func(ctx context.Context, id identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
		startsAtExec[id.Command] = srv.kind("start")
		return fixedExec(ctx, id, on)
	}
	c.executeTask(context.Background(), liveTask(&LiveOutput{FlushMs: 1000, MaxChunkBytes: 16384, MaxStepBytes: 262144}))

	for i, want := range []liveFrame{
		{StepID: "step-01", Command: "echo one", Identity: "www-data", Index: 1, Total: 2},
		{StepID: "step-02", Command: "echo two", Identity: "postgres", Index: 2, Total: 2},
	} {
		seen := startsAtExec[want.Command]
		if len(seen) != i+1 {
			t.Fatalf("when %s executed SimCore had %d start frame(s), want %d: the start frame "+
				"must arrive before the step runs", want.StepID, len(seen), i+1)
		}
		got := seen[i]
		if got.StepID != want.StepID || got.Command != want.Command || got.Identity != want.Identity ||
			got.Index != want.Index || got.Total != want.Total {
			t.Errorf("start frame for %s = %+v, want step/command/identity/index/total of %+v", want.StepID, got, want)
		}
		if got.Output != "" || got.FrameID != "" {
			t.Errorf("start frame for %s carries output %q / frame_id %q, want neither", want.StepID, got.Output, got.FrameID)
		}
	}
	// And in the stream as a whole each step reads start, then its final frame.
	var order []string
	for _, f := range srv.all() {
		if f.Kind != "chunk" {
			order = append(order, f.Kind+":"+f.StepID)
		}
	}
	if got, want := strings.Join(order, " "), "start:step-01 :step-01 start:step-02 :step-02"; got != want {
		t.Errorf("frame order = %q, want %q", got, want)
	}
}

func TestChunksFlushOnIntervalAndSize(t *testing.T) {
	chunkText := func(fs []liveFrame) string {
		var b strings.Builder
		for _, f := range fs {
			b.WriteString(f.Output)
		}
		return b.String()
	}

	t.Run("interval", func(t *testing.T) {
		srv := newLiveServer(t)
		c := liveClient(srv.URL)
		c.execStep = func(_ context.Context, _ identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
			if !streams(t, on) {
				return identity.ExecResult{Termination: TermExited}, nil
			}
			// The step is still running each time a chunk is seen to arrive.
			on("tick 1\n", false)
			waitFor(t, "the first chunk while the step runs", func() bool { return len(srv.kind("chunk")) >= 1 })
			on("tick 2\n", true)
			waitFor(t, "the second chunk while the step runs", func() bool { return len(srv.kind("chunk")) >= 2 })
			return identity.ExecResult{Stdout: "tick 1\n", Stderr: "tick 2\n", Termination: TermExited}, nil
		}
		task := liveTask(&LiveOutput{FlushMs: 20, MaxChunkBytes: 16384, MaxStepBytes: 262144})
		task.Steps = task.Steps[:1]
		c.executeTask(context.Background(), task)

		chunks := srv.kind("chunk")
		if got := chunkText(chunks); got != "tick 1\ntick 2\n" {
			t.Fatalf("streamed text = %q, want both lines in order", got)
		}
		for i, f := range chunks {
			if f.Seq != i+1 || f.StepID != "step-01" || f.FrameID != "" {
				t.Errorf("chunk %d = seq %d step %q frame_id %q, want seq %d, step-01, no frame_id", i, f.Seq, f.StepID, f.FrameID, i+1)
			}
		}
	})

	t.Run("size", func(t *testing.T) {
		srv := newLiveServer(t)
		c := liveClient(srv.URL)
		data := strings.Repeat("abcdefgh", 25) // 200 bytes
		c.execStep = func(_ context.Context, _ identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
			if !streams(t, on) {
				return identity.ExecResult{Termination: TermExited}, nil
			}
			on(data, false)
			// The interval is a minute: only the size limit can send these.
			waitFor(t, "three full chunks", func() bool { return len(srv.kind("chunk")) >= 3 })
			return identity.ExecResult{Stdout: data, Termination: TermExited}, nil
		}
		task := liveTask(&LiveOutput{FlushMs: 60000, MaxChunkBytes: 64, MaxStepBytes: 262144})
		task.Steps = task.Steps[:1]
		c.executeTask(context.Background(), task)

		chunks := srv.kind("chunk")
		if len(chunks) != 3 {
			t.Fatalf("got %d chunks, want 3 full ones (the 8-byte remainder waits for the interval)", len(chunks))
		}
		for i, f := range chunks {
			if len(f.Output) != 64 || f.Seq != i+1 {
				t.Errorf("chunk %d: %d bytes seq %d, want 64 bytes seq %d", i, len(f.Output), f.Seq, i+1)
			}
		}
		if got := chunkText(chunks); got != data[:192] {
			t.Errorf("streamed text is not the first 192 bytes of the output: %q", got)
		}
	})
}

func TestLiveChunksStopAtMaxStepBytes(t *testing.T) {
	srv := newLiveServer(t)
	c := liveClient(srv.URL)
	data := strings.Repeat("0123456789", 50) // 500 bytes
	c.execStep = func(_ context.Context, _ identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
		if !streams(t, on) {
			return identity.ExecResult{Termination: TermExited}, nil
		}
		for i := 0; i < len(data); i += 50 {
			on(data[i:i+50], false)
		}
		waitFor(t, "the limit notice", func() bool {
			fs := srv.kind("chunk")
			return len(fs) > 0 && strings.Contains(fs[len(fs)-1].Output, liveCapNote)
		})
		return identity.ExecResult{Stdout: data, Termination: TermExited}, nil
	}
	task := liveTask(&LiveOutput{FlushMs: 10, MaxChunkBytes: 4096, MaxStepBytes: 120})
	task.Steps = task.Steps[:1]
	c.executeTask(context.Background(), task)

	var live strings.Builder
	for _, f := range srv.kind("chunk") {
		live.WriteString(f.Output)
	}
	if got, want := live.String(), data[:120]+liveCapNote; got != want {
		t.Errorf("streamed text = %q, want the first 120 bytes then the limit notice", got)
	}
	// The stored frame is not capped by the live limit.
	final := srv.kind("")
	if len(final) != 1 || !strings.Contains(final[0].Output, data) {
		t.Errorf("final frame does not carry the step's full output: %+v", final)
	}
}

func TestSlowServerDoesNotBlockStep(t *testing.T) {
	for name, arrange := range map[string]func(*liveServer){
		// Every live frame hangs until the test ends.
		"slow": func(s *liveServer) { s.hold = make(chan struct{}); t.Cleanup(func() { close(s.hold) }) },
		// Every live frame is answered 500.
		"failing": func(s *liveServer) { s.liveStatus = http.StatusInternalServerError },
	} {
		t.Run(name, func(t *testing.T) {
			setLiveStartWait(t, 50*time.Millisecond)
			srv := newLiveServer(t)
			arrange(srv)
			c := liveClient(srv.URL)
			ran := 0
			c.execStep = func(ctx context.Context, id identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
				ran++
				return fixedExec(ctx, id, on)
			}

			began := time.Now()
			c.executeTask(context.Background(), liveTask(&LiveOutput{FlushMs: 10, MaxChunkBytes: 4, MaxStepBytes: 262144}))
			took := time.Since(began)

			if took > 2*time.Second {
				t.Fatalf("executeTask took %s: a slow or failing live POST held the task up", took)
			}
			if ran != 2 {
				t.Fatalf("%d step(s) ran, want 2", ran)
			}
			final := srv.kind("")
			if len(final) != 2 || !strings.Contains(final[0].Output, "tick 1\ntick 2\n") ||
				!strings.Contains(final[1].Output, "--- exit_code=0 duration=1.5s ---") {
				t.Fatalf("final frames = %+v, want both steps' full frames", final)
			}
			done := srv.completeBodies()
			if len(done) != 1 || !strings.Contains(done[0], `"exit_code":0`) {
				t.Fatalf("/complete bodies = %v, want one with exit_code 0", done)
			}
		})
	}
}

func TestNoLiveFramesWithoutTaskField(t *testing.T) {
	srv := newLiveServer(t)
	c := liveClient(srv.URL)
	streamed := false
	c.execStep = func(ctx context.Context, id identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
		if on != nil {
			streamed = true
		}
		return fixedExec(ctx, id, on)
	}
	// An older SimCore: the task has no live_output.
	var task Task
	if err := json.Unmarshal([]byte(`{"task_id":"task-live","run_id":"run-live","scenario_id":"SIM-TEST-LIVE",
		"steps":[{"id":"step-01","command":"echo one","identity":"www-data"},
		         {"id":"step-02","command":"echo two","identity":"postgres"}]}`), &task); err != nil {
		t.Fatal(err)
	}
	c.executeTask(context.Background(), &task)

	if streamed {
		t.Error("the step was given a live output callback although the task carries no live_output")
	}
	frames := srv.all()
	if len(frames) != 2 {
		t.Fatalf("%d /output POSTs, want exactly the 2 final per-step frames: %+v", len(frames), frames)
	}
	for _, f := range frames {
		if f.Kind != "" || strings.Contains(f.raw, `"kind"`) || strings.Contains(f.raw, `"seq"`) ||
			strings.Contains(f.raw, `"command"`) {
			t.Errorf("an older SimCore was sent a live field: %s", f.raw)
		}
	}
}

func TestFinalFrameUnchanged(t *testing.T) {
	// The same task run three ways; the stored record must not depend on which.
	run := func(live *LiveOutput, liveStatus int) (finals []string, completes []string, liveFrames int) {
		setLiveStartWait(t, 50*time.Millisecond)
		srv := newLiveServer(t)
		srv.liveStatus = liveStatus
		c := liveClient(srv.URL)
		c.execStep = fixedExec
		c.executeTask(context.Background(), liveTask(live))
		for _, f := range srv.all() {
			if f.Kind == "" {
				finals = append(finals, f.raw)
			} else {
				liveFrames++
			}
		}
		return finals, srv.completeBodies(), liveFrames
	}
	cfg := &LiveOutput{FlushMs: 5, MaxChunkBytes: 4, MaxStepBytes: 262144}

	plainFinals, plainDone, plainLive := run(nil, 0)
	liveFinals, liveDone, delivered := run(cfg, 0)
	lostFinals, lostDone, _ := run(cfg, http.StatusInternalServerError)

	if plainLive != 0 {
		t.Fatalf("the run without live_output sent %d live frame(s)", plainLive)
	}
	if delivered == 0 {
		t.Fatal("the live run sent no live frame: this test would prove nothing")
	}
	if len(plainFinals) != 2 {
		t.Fatalf("%d final frames, want 2: %v", len(plainFinals), plainFinals)
	}
	for name, got := range map[string][]string{"live frames delivered": liveFinals, "live frames lost": lostFinals} {
		if strings.Join(got, "\n") != strings.Join(plainFinals, "\n") {
			t.Errorf("final frames differ with %s:\n got  %v\n want %v", name, got, plainFinals)
		}
	}
	for name, got := range map[string][]string{"live frames delivered": liveDone, "live frames lost": lostDone} {
		if strings.Join(got, "\n") != strings.Join(plainDone, "\n") {
			t.Errorf("/complete differs with %s:\n got  %v\n want %v", name, got, plainDone)
		}
	}
	// And the frame is what it has always been: header, both streams, footer, a frame id.
	want := `{"frame_id":"task-live:1","output":"=== STEP 1/2 · step-01 · T1059 · identity=www-data ===\n`
	if beaconGOOS != "windows" && !strings.HasPrefix(plainFinals[0], want) {
		t.Errorf("final frame = %s, want prefix %s", plainFinals[0], want)
	}
	if !strings.Contains(plainFinals[0], `tick 1\ntick 2\n`) || !strings.Contains(plainFinals[0], `--- exit_code=0 duration=1.5s ---\n","run_id":"run-live","step_id":"step-01"}`) {
		t.Errorf("final frame lost its body or footer: %s", plainFinals[0])
	}
}
