package beacon

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
)

// -----------------------------------------------------------------------------
// recordingServer captures every request the beacon sends so we can assert on
// path, method, and body — the exact contract SimCore relies on. It also serves
// the /control endpoint (default abort=false; flip abortFlag to true to drive an
// abort mid-run).
// -----------------------------------------------------------------------------

type recorded struct {
	Method string
	Path   string
	Body   string
}

type recordingServer struct {
	*httptest.Server
	mu        sync.Mutex
	log       []recorded
	abortFlag atomic.Bool // when true, /control returns {"abort":true}
}

func newRecordingServer(t *testing.T, taskOnce *Task) *recordingServer {
	t.Helper()
	rs := &recordingServer{}

	handed := false
	rs.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bodyBytes, _ := io.ReadAll(r.Body)
		rs.mu.Lock()
		rs.log = append(rs.log, recorded{Method: r.Method, Path: r.URL.Path, Body: string(bodyBytes)})
		rs.mu.Unlock()

		switch {
		case r.URL.Path == "/api/agents/register" && r.Method == http.MethodPost:
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"status":"registered"}`))
		case strings.HasPrefix(r.URL.Path, "/api/agents/") && strings.HasSuffix(r.URL.Path, "/tasks") && r.Method == http.MethodGet:
			if taskOnce != nil && !handed {
				handed = true
				// Server contract: {"task": {...}} (busy) or {"task": null} (idle).
				_ = json.NewEncoder(w).Encode(map[string]any{"task": taskOnce})
				return
			}
			w.WriteHeader(404)
		case strings.HasSuffix(r.URL.Path, "/control") && r.Method == http.MethodGet:
			runID := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/runs/"), "/control")
			abort := rs.abortFlag.Load()
			status := "running"
			if abort {
				status = "aborted"
			}
			_ = json.NewEncoder(w).Encode(ControlState{Abort: abort, RunID: runID, Status: status})
		case strings.HasSuffix(r.URL.Path, "/output") && r.Method == http.MethodPost:
			w.WriteHeader(200)
		case strings.HasSuffix(r.URL.Path, "/complete") && r.Method == http.MethodPost:
			w.WriteHeader(200)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	return rs
}

func (rs *recordingServer) records() []recorded {
	rs.mu.Lock()
	defer rs.mu.Unlock()
	out := make([]recorded, len(rs.log))
	copy(out, rs.log)
	return out
}

func (rs *recordingServer) find(method, pathSuffix string) []recorded {
	var out []recorded
	for _, r := range rs.records() {
		if r.Method == method && strings.HasSuffix(r.Path, pathSuffix) {
			out = append(out, r)
		}
	}
	return out
}

// -----------------------------------------------------------------------------
// Registration
// -----------------------------------------------------------------------------

func TestRegister_PostsExpectedBody(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 100*time.Millisecond)
	if err := c.Register("host-1", "linux", []string{"bash"}); err != nil {
		t.Fatalf("Register: %v", err)
	}

	recs := rs.records()
	if len(recs) != 1 {
		t.Fatalf("expected 1 request, got %d", len(recs))
	}
	r := recs[0]
	if r.Method != "POST" || r.Path != "/api/agents/register" {
		t.Errorf("wrong route: %s %s", r.Method, r.Path)
	}
	if !strings.Contains(r.Body, `"agent_id":"a-1"`) {
		t.Errorf("body missing agent_id: %s", r.Body)
	}
	if !strings.Contains(r.Body, `"hostname":"host-1"`) {
		t.Errorf("body missing hostname: %s", r.Body)
	}
}

// -----------------------------------------------------------------------------
// PollTasks
// -----------------------------------------------------------------------------

func TestPollTasks_404MeansNoTask(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 50*time.Millisecond)
	task, err := c.PollTasks()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if task != nil {
		t.Errorf("expected nil task on 404, got %+v", task)
	}
}

func TestPollTasks_ReturnsTaskOnce(t *testing.T) {
	want := &Task{
		TaskID:          "t-1",
		RunID:           "r-1",
		ScenarioID:      "SIM-EDR-001",
		IdentityContext: "www-data",
		IdentityDefault: "www-data",
		Steps: []Step{
			{ID: "step-01", Name: "whoami", Command: "id", Identity: "www-data", MitreTechnique: "T1003"},
		},
	}
	rs := newRecordingServer(t, want)
	defer rs.Close()

	c := New(rs.URL, "a-1", 50*time.Millisecond)
	got, err := c.PollTasks()
	if err != nil {
		t.Fatalf("PollTasks: %v", err)
	}
	if got == nil {
		t.Fatal("expected task, got nil")
	}
	if got.RunID != want.RunID || got.ScenarioID != want.ScenarioID || got.TaskID != want.TaskID {
		t.Errorf("decode mismatch: got %+v want %+v", got, want)
	}
	if len(got.Steps) != 1 {
		t.Fatalf("expected 1 step, got %d", len(got.Steps))
	}
	if got.Steps[0].Command != "id" || got.Steps[0].Identity != "www-data" {
		t.Errorf("step decode mismatch: %+v", got.Steps[0])
	}

	// Second poll → 404 → nil
	got, err = c.PollTasks()
	if err != nil || got != nil {
		t.Errorf("second poll should be nil/nil, got %+v / %v", got, err)
	}
}

// Wire contract — the FastAPI server wraps the payload as {"task": ...}.
// PollTasks must unwrap the envelope and decode the steps[] shape.
func TestPollTasks_DecodesWrappedTaskEnvelope(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"task":{"task_id":"t-1","run_id":"r-1","scenario_id":"SIM-EDR-001","steps":[{"id":"step-01","name":"whoami","command":"id","identity":"www-data","mitre_technique":"T1003"}],"identity_context":null,"identity_default":"www-data","created_at":"2026-06-07T12:00:00.000000"}}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "a-1", 0)
	got, err := c.PollTasks()
	if err != nil {
		t.Fatalf("decode failed: %v", err)
	}
	if got == nil {
		t.Fatal("expected task, got nil")
	}
	if got.TaskID != "t-1" || got.RunID != "r-1" || got.ScenarioID != "SIM-EDR-001" {
		t.Errorf("decode mismatch: %+v", got)
	}
	// identity_context was null → empty string; identity_default carried through.
	if got.IdentityContext != "" {
		t.Errorf("expected empty identity_context for null, got %q", got.IdentityContext)
	}
	if got.IdentityDefault != "www-data" {
		t.Errorf("identity_default mismatch: %q", got.IdentityDefault)
	}
	if len(got.Steps) != 1 || got.Steps[0].Identity != "www-data" || got.Steps[0].Command != "id" {
		t.Errorf("step decode mismatch: %+v", got.Steps)
	}
}

func TestPollTasks_NullTaskIsIdle(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"task": null}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "a-1", 0)
	got, err := c.PollTasks()
	if err != nil {
		t.Fatalf("decode failed: %v", err)
	}
	if got != nil {
		t.Errorf("expected nil task for null envelope, got %+v", got)
	}
}

// -----------------------------------------------------------------------------
// SendOutput / Complete contract
// -----------------------------------------------------------------------------

func TestSendOutput_PostsRunIdStepIdAndOutput(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 0)
	if err := c.SendOutput("r-42", "step-01", "hello"); err != nil {
		t.Fatalf("SendOutput: %v", err)
	}

	recs := rs.records()
	r := recs[0]
	if r.Path != "/api/runs/r-42/output" {
		t.Errorf("wrong path: %s", r.Path)
	}
	if !strings.Contains(r.Body, `"output":"hello"`) {
		t.Errorf("body missing output: %s", r.Body)
	}
	if !strings.Contains(r.Body, `"step_id":"step-01"`) {
		t.Errorf("body missing step_id: %s", r.Body)
	}
}

func TestSendOutput_OmitsEmptyStepId(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 0)
	if err := c.SendOutput("r-42", "", "hello"); err != nil {
		t.Fatalf("SendOutput: %v", err)
	}
	r := rs.records()[0]
	if strings.Contains(r.Body, "step_id") {
		t.Errorf("expected no step_id key for empty stepID, got: %s", r.Body)
	}
}

func TestComplete_PostsExitCodeAndSummary(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 0)
	if err := c.Complete("r-42", 0, "done"); err != nil {
		t.Fatalf("Complete: %v", err)
	}

	r := rs.records()[0]
	if r.Path != "/api/runs/r-42/complete" {
		t.Errorf("wrong path: %s", r.Path)
	}
	if !strings.Contains(r.Body, `"exit_code":0`) {
		t.Errorf("body missing exit_code: %s", r.Body)
	}
	if !strings.Contains(r.Body, `"summary":"done"`) {
		t.Errorf("body missing summary: %s", r.Body)
	}
}

// -----------------------------------------------------------------------------
// Control channel
// -----------------------------------------------------------------------------

func TestControl_DefaultNoAbort(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 0)
	state, err := c.Control("r-1")
	if err != nil {
		t.Fatalf("Control: %v", err)
	}
	if state.Abort {
		t.Errorf("expected abort=false by default, got %+v", state)
	}
	if state.RunID != "r-1" {
		t.Errorf("run_id mismatch: %+v", state)
	}
}

func TestControl_AbortFlagReturnsAbort(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	rs.abortFlag.Store(true)

	c := New(rs.URL, "a-1", 0)
	state, err := c.Control("r-1")
	if err != nil {
		t.Fatalf("Control: %v", err)
	}
	if !state.Abort {
		t.Errorf("expected abort=true, got %+v", state)
	}
}

func TestControl_404MeansAbort(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
	}))
	defer srv.Close()

	c := New(srv.URL, "a-1", 0)
	state, err := c.Control("gone")
	if err != nil {
		t.Fatalf("404 should be a clean abort signal, got err=%v", err)
	}
	if !state.Abort {
		t.Errorf("expected abort=true for vanished run, got %+v", state)
	}
}

// -----------------------------------------------------------------------------
// executeTask — multi-step ordering, per-step output, fail-fast, abort
// -----------------------------------------------------------------------------

func TestExecuteTask_RunsAllStepsInOrderAndCompletes(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := &Task{
		TaskID:     "t-1",
		RunID:      "r-1",
		ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Name: "one", Command: "echo step-one", Identity: "root", MitreTechnique: "T1001"},
			{ID: "step-02", Name: "two", Command: "echo step-two", Identity: "root", MitreTechnique: "T1002"},
		},
	}

	c := New(rs.URL, "a-1", 0)
	c.executeTask(context.Background(), task)

	outputs := rs.find("POST", "/output")
	if len(outputs) != 2 {
		t.Fatalf("expected one /output POST per step (2), got %d", len(outputs))
	}
	if !strings.Contains(outputs[0].Body, `"step_id":"step-01"`) ||
		!strings.Contains(outputs[0].Body, "step-one") {
		t.Errorf("first output not step-01/step-one: %s", outputs[0].Body)
	}
	if !strings.Contains(outputs[1].Body, `"step_id":"step-02"`) ||
		!strings.Contains(outputs[1].Body, "step-two") {
		t.Errorf("second output not step-02/step-two: %s", outputs[1].Body)
	}

	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected 1 /complete, got %d", len(completes))
	}
	if !strings.Contains(completes[0].Body, `"exit_code":0`) {
		t.Errorf("expected exit_code 0, got: %s", completes[0].Body)
	}
}

func TestExecuteTask_FailFastStopsAtFirstNonZero(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := &Task{
		TaskID:     "t-1",
		RunID:      "r-1",
		ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "exit 7", Identity: "root"},
			{ID: "step-02", Command: "echo should-not-run", Identity: "root"},
		},
	}

	c := New(rs.URL, "a-1", 0)
	c.executeTask(context.Background(), task)

	outputs := rs.find("POST", "/output")
	if len(outputs) != 1 {
		t.Fatalf("expected exactly 1 step to run before fail-fast, got %d", len(outputs))
	}
	for _, o := range outputs {
		if strings.Contains(o.Body, "should-not-run") {
			t.Errorf("step-02 should not have executed: %s", o.Body)
		}
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, `"exit_code":7`) {
		t.Errorf("expected single complete with exit_code 7, got: %+v", completes)
	}
}

func TestExecuteTask_AbortBeforeFirstStep(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	rs.abortFlag.Store(true) // abort already signalled

	task := &Task{
		TaskID:     "t-1",
		RunID:      "r-1",
		ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "echo never", Identity: "root"},
		},
	}

	c := New(rs.URL, "a-1", 0)
	c.executeTask(context.Background(), task)

	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "never") {
			t.Errorf("command should not have executed after pre-step abort: %s", o.Body)
		}
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, `"exit_code":130`) {
		t.Errorf("expected complete with exit_code 130 (aborted), got: %+v", completes)
	}
}

func TestExecuteTask_AbortMidLongRunningStep(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := &Task{
		TaskID:     "t-1",
		RunID:      "r-1",
		ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			// Sleeps well past the 2s control-poll interval so the abort lands mid-step.
			{ID: "step-01", Command: "sleep 30", Identity: "root"},
			{ID: "step-02", Command: "echo should-not-run", Identity: "root"},
		},
	}

	c := New(rs.URL, "a-1", 0)

	// Flip the abort flag shortly after execution starts so the in-flight step is
	// terminated by the control-poll path.
	go func() {
		time.Sleep(500 * time.Millisecond)
		rs.abortFlag.Store(true)
	}()

	start := time.Now()
	c.executeTask(context.Background(), task)
	elapsed := time.Since(start)

	// Must stop well before the 30s sleep would finish (2s poll + 3s kill grace ≈ 5s).
	if elapsed > 12*time.Second {
		t.Fatalf("abort took too long: %s (process group kill not working?)", elapsed)
	}

	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "should-not-run") {
			t.Errorf("step-02 must not run after mid-step abort: %s", o.Body)
		}
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, `"exit_code":130`) {
		t.Errorf("expected single complete with exit_code 130, got: %+v", completes)
	}
}

func TestExecuteTask_EmptyStepsCompletesNoOp(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := &Task{TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001", Steps: nil}
	c := New(rs.URL, "a-1", 0)
	c.executeTask(context.Background(), task)

	if len(rs.find("POST", "/output")) != 0 {
		t.Errorf("no output expected for empty task")
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, `"exit_code":0`) {
		t.Errorf("expected single complete exit_code 0, got: %+v", completes)
	}
}

// -----------------------------------------------------------------------------
// Identity precedence
// -----------------------------------------------------------------------------

func TestResolveStepUsername_Precedence(t *testing.T) {
	c := New("http://x", "a-1", 0)

	cases := []struct {
		name string
		task *Task
		step *Step
		want string
	}{
		{"step identity wins", &Task{IdentityContext: "ctx", IdentityDefault: "def"}, &Step{Identity: "www-data"}, "www-data"},
		{"falls back to context", &Task{IdentityContext: "ctx", IdentityDefault: "def"}, &Step{Identity: ""}, "ctx"},
		{"falls back to default", &Task{IdentityContext: "", IdentityDefault: "def"}, &Step{Identity: ""}, "def"},
		{"falls back to root", &Task{IdentityContext: "", IdentityDefault: ""}, &Step{Identity: ""}, "root"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := c.resolveStepUsername(tc.task, tc.step); got != tc.want {
				t.Errorf("got %q want %q", got, tc.want)
			}
		})
	}
}
