package beacon

import (
	"context"
	"encoding/json"
	"errors"
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

// -----------------------------------------------------------------------------
// recordingServer captures every request the beacon sends so we can assert on
// path, method, and body — the exact contract SimCore relies on. It also serves
// the /control endpoint (default abort=false; flip abortFlag to true to drive an
// abort mid-run).
// -----------------------------------------------------------------------------

type recorded struct {
	Method string
	Path   string
	Query  string
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
		rs.log = append(rs.log, recorded{Method: r.Method, Path: r.URL.Path, Query: r.URL.RawQuery, Body: string(bodyBytes)})
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
			// Real API contract: a REGISTERED agent with no work gets
			// 200 {"task": null}; a 404 is reserved for an UNKNOWN agent
			// (GAP-AGENT-003). The recording server models the registered-idle
			// case here.
			_ = json.NewEncoder(w).Encode(map[string]any{"task": nil})
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
	if err := c.Register("host-1", "linux", []string{"bash"}, []string{"python"}); err != nil {
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
	if !strings.Contains(r.Body, `"interpreters":["python"]`) {
		t.Errorf("body missing interpreters: %s", r.Body)
	}
}

// -----------------------------------------------------------------------------
// PollTasks
// -----------------------------------------------------------------------------

func TestPollTasks_IdleMeansNoTask(t *testing.T) {
	// A registered-but-idle agent gets 200 {"task": null} → (nil, nil).
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 50*time.Millisecond)
	task, err := c.PollTasks()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if task != nil {
		t.Errorf("expected nil task when idle, got %+v", task)
	}
}

func TestPollTasks_404MeansUnknownAgent(t *testing.T) {
	// GAP-AGENT-003 — a 404 is now distinct from "no task": it signals the
	// agent is unknown to SimCore and must re-register.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
		_, _ = w.Write([]byte(`{"error":"Agent not found","code":"AGENT_NOT_FOUND"}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "ghost", 0)
	task, err := c.PollTasks()
	if task != nil {
		t.Errorf("expected nil task on 404, got %+v", task)
	}
	if !errors.Is(err, ErrUnknownAgent) {
		t.Fatalf("expected ErrUnknownAgent, got %v", err)
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

// TestRunLoop_ReRegistersOnUnknownAgent verifies the GAP-AGENT-003 recovery
// path: when the server first reports the agent unknown (404) the run loop
// re-registers, and once registered an idle poll (200 {"task":null}) is a
// clean no-op. We drive the loop directly and cancel after the recovery.
func TestRunLoop_ReRegistersOnUnknownAgent(t *testing.T) {
	var mu sync.Mutex
	registered := false
	var registerCalls, pollCalls int

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/agents/register" && r.Method == http.MethodPost:
			mu.Lock()
			registered = true
			registerCalls++
			mu.Unlock()
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"status":"registered"}`))
		case strings.HasSuffix(r.URL.Path, "/tasks") && r.Method == http.MethodGet:
			mu.Lock()
			pollCalls++
			isReg := registered
			mu.Unlock()
			if !isReg {
				// Unknown agent until it re-registers.
				w.WriteHeader(404)
				_, _ = w.Write([]byte(`{"code":"AGENT_NOT_FOUND"}`))
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"task": nil})
		default:
			w.WriteHeader(200)
		}
	}))
	defer srv.Close()

	// Fast tick so the loop polls a few times quickly.
	c := New(srv.URL, "ghost", 10*time.Millisecond)

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	c.Run(ctx)

	mu.Lock()
	defer mu.Unlock()
	if registerCalls == 0 {
		t.Fatalf("expected the run loop to re-register after a 404, got %d register calls", registerCalls)
	}
	if pollCalls < 2 {
		t.Fatalf("expected multiple polls (recovery), got %d", pollCalls)
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
	if err := c.Complete("r-42", 0, "done", []StepOutcome{}); err != nil {
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

// -----------------------------------------------------------------------------
// Step Execution Timeouts
// -----------------------------------------------------------------------------

func TestStepTimeout_Termination(t *testing.T) {
	task := &Task{
		TaskID:     "task-timeout-1",
		RunID:      "run-timeout-1",
		ScenarioID: "SIM-TEST-TIMEOUT",
		Steps: []Step{
			{
				ID:             "step-hang",
				Name:           "Hanging step",
				Command:        "sleep 10",
				Identity:       "root",
				MitreTechnique: "T1059",
				TimeoutSeconds: 1,
			},
		},
	}

	rs := newRecordingServer(t, task)
	defer rs.Close()

	c := New(rs.URL, "agent-test", time.Millisecond)
	c.resolveIdentity = func(string) identity.Resolution {
		return identity.Resolution{Mode: "direct", Username: "root"}
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	start := time.Now()
	c.executeTask(ctx, task)
	duration := time.Since(start)

	if duration > 5*time.Second {
		t.Errorf("step execution took %s, should have timed out after ~1s", duration)
	}

	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected 1 complete call, got %d", len(completes))
	}
	if !strings.Contains(completes[0].Body, `"exit_code":124`) {
		t.Errorf("expected exit_code 124 on timeout, got body: %s", completes[0].Body)
	}

	outputs := rs.find("POST", "/output")
	if len(outputs) == 0 {
		t.Fatalf("expected output frames, got none")
	}
	foundTimeoutMarker := false
	for _, out := range outputs {
		if strings.Contains(out.Body, stepTimeoutMarker) {
			foundTimeoutMarker = true
			break
		}
	}
	if !foundTimeoutMarker {
		t.Errorf("expected output to contain %q, outputs: %+v", stepTimeoutMarker, outputs)
	}
}

func TestPollTasks_LongPolling(t *testing.T) {
	task := &Task{
		TaskID:     "task-lp-1",
		RunID:      "run-lp-1",
		ScenarioID: "SIM-TEST-LP",
	}
	rs := newRecordingServer(t, task)
	defer rs.Close()

	c := New(rs.URL, "agent-test", time.Millisecond)

	gotTask, err := c.PollTasksWait(15)
	if err != nil {
		t.Fatalf("PollTasksWait error: %v", err)
	}
	if gotTask == nil || gotTask.TaskID != "task-lp-1" {
		t.Fatalf("expected task-lp-1, got %+v", gotTask)
	}

	reqs := rs.records()
	foundQuery := false
	for _, r := range reqs {
		if strings.Contains(r.Query, "wait=15") {
			foundQuery = true
			break
		}
	}
	if !foundQuery {
		t.Errorf("expected GET /tasks with ?wait=15, got requests: %+v", reqs)
	}
}

func TestStreamOutput_LiveChunksSent(t *testing.T) {
	task := &Task{
		TaskID:     "task-stream-1",
		RunID:      "run-stream-1",
		ScenarioID: "SIM-TEST-STREAM",
		Steps: []Step{
			{
				ID:             "step-stream-1",
				Name:           "Streaming Step",
				Command:        "echo line1 && echo line2",
				Identity:       "root",
				MitreTechnique: "T1059",
			},
		},
	}
	rs := newRecordingServer(t, task)
	defer rs.Close()

	c := New(rs.URL, "agent-test", time.Millisecond)
	c.StreamOutput = true
	c.resolveIdentity = func(string) identity.Resolution {
		return identity.Resolution{Mode: "direct", Username: "root"}
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	c.executeTask(ctx, task)

	outputs := rs.find("POST", "/output")
	if len(outputs) <= 1 {
		t.Errorf("expected >1 /output POSTs with StreamOutput=true, got %d", len(outputs))
	}
}

// -----------------------------------------------------------------------------
// Per-step outcomes on /complete
// -----------------------------------------------------------------------------

type completeBody struct {
	ExitCode *int          `json:"exit_code"`
	Summary  string        `json:"summary"`
	Steps    []StepOutcome `json:"steps"`
}

func decodeComplete(t *testing.T, rs *recordingServer) completeBody {
	t.Helper()
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected exactly 1 /complete, got %d", len(completes))
	}
	var b completeBody
	if err := json.Unmarshal([]byte(completes[0].Body), &b); err != nil {
		t.Fatalf("decode /complete body %q: %v", completes[0].Body, err)
	}
	return b
}

func TestComplete_PostsStepOutcomes(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", 0)

	code := 3
	in := []StepOutcome{
		{StepID: "s1", ExitCode: &code, Termination: TermExited},
		{StepID: "s2", Termination: TermNotRun, Reason: ReasonUpstreamSevered},
	}
	if err := c.Complete("r-1", 3, "x", in); err != nil {
		t.Fatalf("Complete: %v", err)
	}
	if err := c.Complete("r-2", 0, "y", []StepOutcome{}); err != nil {
		t.Fatalf("Complete: %v", err)
	}
	recs := rs.find("POST", "/complete")
	if len(recs) != 2 {
		t.Fatalf("expected 2 /complete, got %d", len(recs))
	}
	var b completeBody
	if err := json.Unmarshal([]byte(recs[0].Body), &b); err != nil {
		t.Fatal(err)
	}
	if len(b.Steps) != 2 || b.Steps[0].ExitCode == nil || *b.Steps[0].ExitCode != 3 ||
		b.Steps[1].Termination != TermNotRun || b.Steps[1].Reason != ReasonUpstreamSevered ||
		b.Steps[1].ExitCode != nil {
		t.Errorf("steps did not round-trip: %+v", b.Steps)
	}
	if !strings.Contains(recs[0].Body, `"exit_code":null`) {
		t.Errorf("never-ran step must encode exit_code null: %s", recs[0].Body)
	}
	if !strings.Contains(recs[1].Body, `"steps":[]`) {
		t.Errorf("empty steps must encode as [] not null: %s", recs[1].Body)
	}
	// nil must also encode as [] — never null.
	rs2 := newRecordingServer(t, nil)
	defer rs2.Close()
	if err := New(rs2.URL, "a-1", 0).Complete("r-3", 0, "z", nil); err != nil {
		t.Fatal(err)
	}
	if body := rs2.find("POST", "/complete")[0].Body; !strings.Contains(body, `"steps":[]`) {
		t.Errorf("nil steps must encode as []: %s", body)
	}
}

func TestExecuteTask_SignaledStepIsRecordedAndSeversChain(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "kill -9 $$", Identity: "root"},
			{ID: "step-02", Command: "echo never", Identity: "root"},
		},
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 137 {
		t.Fatalf("expected exit 137, got %v", b.ExitCode)
	}
	if len(b.Steps) != 2 {
		t.Fatalf("expected 2 step outcomes, got %+v", b.Steps)
	}
	if b.Steps[0].Termination != TermSignaled || b.Steps[0].Signal != 9 {
		t.Errorf("step 0 should be signaled/9: %+v", b.Steps[0])
	}
	if b.Steps[0].ExitCode == nil || *b.Steps[0].ExitCode != 137 {
		t.Errorf("signaled entry should carry exit_code 137: %+v", b.Steps[0])
	}
	if b.Steps[1].Termination != TermNotRun || b.Steps[1].Reason != ReasonUpstreamSevered || b.Steps[1].ExitCode != nil {
		t.Errorf("step 1 should be not_run/UPSTREAM_SEVERED: %+v", b.Steps[1])
	}
	if !strings.Contains(b.Summary, "SEVERED at step-01 (signaled)") {
		t.Errorf("summary missing SEVERED suffix: %s", b.Summary)
	}
	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "echo never") && strings.Contains(o.Body, "STEP 2/2") {
			t.Errorf("step-02 must not run")
		}
	}
}

func TestExecuteTaskChained_AnchorDeathIsAnchorTerminatedNotExecutionError(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		CgoAnchor: &CgoAnchor{ImageName: "apache2"},
		Steps: []Step{
			{ID: "step-01", Command: "kill -9 $$", Identity: "root"},
			{ID: "step-02", Command: "echo never", Identity: "root"},
		},
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 137 {
		t.Fatalf("expected exit 137, got %v", b.ExitCode)
	}
	if len(b.Steps) != 2 {
		t.Fatalf("expected 2 step outcomes, got %+v", b.Steps)
	}
	if b.Steps[0].Termination != TermAnchorTerminated || b.Steps[0].ExitCode != nil {
		t.Errorf("step 0 should be anchor_terminated with nil exit: %+v", b.Steps[0])
	}
	if b.Steps[1].Termination != TermNotRun || b.Steps[1].Reason != ReasonUpstreamSevered {
		t.Errorf("step 1 should be not_run/UPSTREAM_SEVERED: %+v", b.Steps[1])
	}
	if !strings.Contains(b.Summary, "SEVERED at") {
		t.Errorf("summary missing SEVERED: %s", b.Summary)
	}
	if strings.Contains(b.Summary, "execution error") {
		t.Errorf("anchor death must not be reported as an execution error: %s", b.Summary)
	}
}

func TestExecuteTask_MissingRequiredBinaryIsNotRunToolAbsent(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "echo should-not-run", Identity: "root",
				RequiredBinaries: []string{"sh", "cortexsim-no-such-tool"}},
			{ID: "step-02", Command: "echo also-not", Identity: "root"},
		},
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 127 {
		t.Fatalf("expected exit 127, got %v", b.ExitCode)
	}
	if len(b.Steps) != 2 {
		t.Fatalf("expected 2 outcomes, got %+v", b.Steps)
	}
	if b.Steps[0].Termination != TermNotRun || b.Steps[0].Reason != ReasonToolAbsent {
		t.Errorf("step 0 should be not_run/TOOL_ABSENT: %+v", b.Steps[0])
	}
	if b.Steps[1].Termination != TermNotRun || b.Steps[1].Reason != ReasonUpstreamNotExecuted {
		t.Errorf("step 1 should be not_run/UPSTREAM_NOT_EXECUTED: %+v", b.Steps[1])
	}
	found := false
	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "!! TOOL_ABSENT: cortexsim-no-such-tool") {
			found = true
		}
		if strings.Contains(o.Body, "should-not-run") {
			t.Errorf("step command must not have run: %s", o.Body)
		}
	}
	if !found {
		t.Errorf("missing !! TOOL_ABSENT output")
	}
}

func TestExecuteTask_OldShapeTaskStillCompletes(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	// Decode from raw JSON with no required_binaries key: the old wire shape.
	var task Task
	raw := `{"task_id":"t-1","run_id":"r-1","scenario_id":"SIM-EDR-001","steps":[
	  {"id":"step-01","name":"one","command":"echo step-one","identity":"root","mitre_technique":"T1001"},
	  {"id":"step-02","name":"two","command":"echo step-two","identity":"root","mitre_technique":"T1002"}]}`
	if err := json.Unmarshal([]byte(raw), &task); err != nil {
		t.Fatal(err)
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), &task)

	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 0 {
		t.Fatalf("expected exit 0, got %v", b.ExitCode)
	}
	if !strings.HasPrefix(b.Summary, "SUCCESS | scenario=SIM-EDR-001 steps=2") {
		t.Errorf("summary prefix changed: %s", b.Summary)
	}
	if len(b.Steps) != 2 {
		t.Fatalf("expected 2 outcomes, got %+v", b.Steps)
	}
	for _, s := range b.Steps {
		if s.Termination != TermExited || s.ExitCode == nil || *s.ExitCode != 0 || s.Reason != "" {
			t.Errorf("expected clean exited/0: %+v", s)
		}
	}
}

func assertNotRun(t *testing.T, st StepOutcome, id, reason string) {
	t.Helper()
	if st.StepID != id || st.Termination != TermNotRun || st.Reason != reason || st.ExitCode != nil {
		t.Errorf("want %s not_run/%s, got %+v", id, reason, st)
	}
}

func TestExecuteTask_FailFastMarksRemainingUpstreamFailed(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "exit 7", Identity: "root"},
			{ID: "step-02", Command: "echo no", Identity: "root"},
			{ID: "step-03", Command: "echo no", Identity: "root"},
		},
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)
	b := decodeComplete(t, rs)
	if len(b.Steps) != 3 || b.Steps[0].Termination != TermExited || *b.Steps[0].ExitCode != 7 {
		t.Fatalf("unexpected steps: %+v", b.Steps)
	}
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamFailed)
	assertNotRun(t, b.Steps[2], "step-03", ReasonUpstreamFailed)
}

func TestExecuteTask_TimeoutRecordsExactlyOneOutcome(t *testing.T) {
	for _, chained := range []bool{false, true} {
		rs := newRecordingServer(t, nil)
		task := &Task{
			TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
			Steps: []Step{
				{ID: "step-01", Command: "sleep 10", Identity: "root", TimeoutSeconds: 1},
				{ID: "step-02", Command: "echo no", Identity: "root"},
			},
		}
		if chained {
			task.CgoAnchor = &CgoAnchor{ImageName: "apache2"}
		}
		New(rs.URL, "a-1", 0).executeTask(context.Background(), task)
		b := decodeComplete(t, rs)
		rs.Close()
		if b.ExitCode == nil || *b.ExitCode != 124 {
			t.Fatalf("chained=%v expected 124, got %v", chained, b.ExitCode)
		}
		if len(b.Steps) != 2 {
			t.Fatalf("chained=%v expected exactly 2 entries (timeout + not_run), got %+v", chained, b.Steps)
		}
		if b.Steps[0].StepID != "step-01" || b.Steps[0].Termination != TermTimeout ||
			b.Steps[0].ExitCode == nil || *b.Steps[0].ExitCode != 124 {
			t.Errorf("chained=%v step 0: %+v", chained, b.Steps[0])
		}
		assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamFailed)
	}
}

func TestExecuteTask_OperatorAbortMidStepRecordsAborted(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "sleep 30", Identity: "root"},
			{ID: "step-02", Command: "echo no", Identity: "root"},
		},
	}
	go func() {
		time.Sleep(300 * time.Millisecond)
		rs.abortFlag.Store(true)
	}()
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 130 {
		t.Fatalf("expected 130, got %v", b.ExitCode)
	}
	if len(b.Steps) != 2 || b.Steps[0].Termination != TermAborted || *b.Steps[0].ExitCode != 130 {
		t.Fatalf("unexpected steps: %+v", b.Steps)
	}
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamFailed)
}

func TestExecuteTask_AgentShutdownMidStepRecordsAborted(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "sleep 30", Identity: "root"},
			{ID: "step-02", Command: "echo no", Identity: "root"},
		},
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(300 * time.Millisecond)
		cancel()
	}()
	New(rs.URL, "a-1", 0).executeTask(ctx, task)
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 130 || !strings.Contains(b.Summary, "agent shutdown") {
		t.Fatalf("expected shutdown 130, got %v %q", b.ExitCode, b.Summary)
	}
	if len(b.Steps) != 2 || b.Steps[0].Termination != TermAborted {
		t.Fatalf("unexpected steps: %+v", b.Steps)
	}
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamFailed)
}

func TestExecuteTaskChained_MissingRequiredBinaryIsNotRunToolAbsent(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		CgoAnchor: &CgoAnchor{ImageName: "apache2"},
		Steps: []Step{
			{ID: "step-01", Command: "echo no", Identity: "root", RequiredBinaries: []string{"cortexsim-no-such-tool"}},
			{ID: "step-02", Command: "echo no", Identity: "root"},
		},
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 127 || len(b.Steps) != 2 {
		t.Fatalf("unexpected: %v %+v", b.ExitCode, b.Steps)
	}
	assertNotRun(t, b.Steps[0], "step-01", ReasonToolAbsent)
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamNotExecuted)
}

func TestExecuteTask_RuntimeDepBlockedStepIsNotRunToolAbsent(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", Command: "echo no", Identity: "root", RequiresInterpreters: []string{noSuchInterpreter}},
			{ID: "step-02", Command: "echo no", Identity: "root"},
		},
	}
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 127 || len(b.Steps) != 2 {
		t.Fatalf("unexpected: %v %+v", b.ExitCode, b.Steps)
	}
	assertNotRun(t, b.Steps[0], "step-01", ReasonToolAbsent)
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamNotExecuted)
}

func TestExecuteTaskPerStepFallback_RecordsOutcomes(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		CgoAnchor: &CgoAnchor{ImageName: "apache2"},
		Steps: []Step{
			{ID: "step-01", Command: "exit 4", Identity: "root"},
			{ID: "step-02", Command: "echo no", Identity: "root"},
		},
	}
	// Make the anchor shell unstartable so executeTaskChained degrades to
	// executeTaskPerStep.
	old := newChainSession
	newChainSession = func(context.Context, string) (*executor.ChainSession, error) {
		return nil, errors.New("forced anchor start failure")
	}
	defer func() { newChainSession = old }()
	c := New(rs.URL, "a-1", 0)
	c.executeTaskChained(context.Background(), task)
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 4 || len(b.Steps) != 2 {
		t.Fatalf("fallback did not complete as expected: %d %+v %s", *b.ExitCode, b.Steps, b.Summary)
	}
	if b.Steps[0].Termination != TermExited || *b.Steps[0].ExitCode != 4 {
		t.Errorf("step 0: %+v", b.Steps[0])
	}
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamFailed)
}

func TestAfterStep_UnknownTerminationIsFlagged(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{RunID: "r-1", ScenarioID: "S", Steps: []Step{{ID: "step-01"}}}
	led := newOutcomeLedger(task)
	c := New(rs.URL, "a-1", 0)
	severed := c.afterStep(task, 0, led, identity.ExecResult{ExitCode: 0, Termination: ""}, false)
	if severed {
		t.Error("unknown termination must not sever")
	}
	if len(led.steps) != 1 || led.steps[0].Termination != TermExited {
		t.Fatalf("expected one exited entry, got %+v", led.steps)
	}
	outs := rs.find("POST", "/output")
	if len(outs) != 1 || !strings.Contains(outs[0].Body, "!! TERMINATION_UNKNOWN:") ||
		!strings.Contains(outs[0].Body, "signal=0") || !strings.Contains(outs[0].Body, `"step_id":"step-01"`) {
		t.Errorf("marker not written: %+v", outs)
	}
	if sum := buildSummary(task, 0, 0, led.steps); !strings.Contains(sum, " | TERMINATION_UNKNOWN: step-01") {
		t.Errorf("summary not suffixed: %s", sum)
	}
	// signaled with no signal number is equally unrecognised.
	led2 := newOutcomeLedger(task)
	c.afterStep(task, 0, led2, identity.ExecResult{ExitCode: 137, Termination: "signaled"}, false)
	if !led2.steps[0].terminationUnknown || led2.steps[0].Termination != TermExited {
		t.Errorf("signaled/0 must be flagged: %+v", led2.steps[0])
	}
}
