package beacon

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// -----------------------------------------------------------------------------
// recordingServer captures every request the beacon sends so we can assert on
// path, method, and body — the exact contract SimCore relies on.
// -----------------------------------------------------------------------------

type recorded struct {
	Method string
	Path   string
	Body   string
}

func newRecordingServer(t *testing.T, taskOnce *Task) (*httptest.Server, *[]recorded, *sync.Mutex) {
	t.Helper()
	var (
		mu   sync.Mutex
		log  []recorded
	)

	handed := false
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bodyBytes, _ := io.ReadAll(r.Body)
		mu.Lock()
		log = append(log, recorded{Method: r.Method, Path: r.URL.Path, Body: string(bodyBytes)})
		mu.Unlock()

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
		case strings.HasSuffix(r.URL.Path, "/output") && r.Method == http.MethodPost:
			w.WriteHeader(200)
		case strings.HasSuffix(r.URL.Path, "/complete") && r.Method == http.MethodPost:
			w.WriteHeader(200)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	return srv, &log, &mu
}

func TestRegister_PostsExpectedBody(t *testing.T) {
	srv, log, mu := newRecordingServer(t, nil)
	defer srv.Close()

	c := New(srv.URL, "a-1", 100*time.Millisecond)
	if err := c.Register("host-1", "linux", []string{"bash"}); err != nil {
		t.Fatalf("Register: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	if len(*log) != 1 {
		t.Fatalf("expected 1 request, got %d", len(*log))
	}
	r := (*log)[0]
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

func TestPollTasks_404MeansNoTask(t *testing.T) {
	srv, _, _ := newRecordingServer(t, nil)
	defer srv.Close()

	c := New(srv.URL, "a-1", 50*time.Millisecond)
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
		RunID:      "r-1",
		ScenarioID: "SIM-EDR-001",
		Command:    "id",
		Identity:   IdentitySpec{Mode: "runuser", Username: "www-data"},
	}
	srv, _, _ := newRecordingServer(t, want)
	defer srv.Close()

	c := New(srv.URL, "a-1", 50*time.Millisecond)
	got, err := c.PollTasks()
	if err != nil {
		t.Fatalf("PollTasks: %v", err)
	}
	if got == nil {
		t.Fatal("expected task, got nil")
	}
	if got.RunID != want.RunID || got.ScenarioID != want.ScenarioID {
		t.Errorf("decode mismatch: got %+v want %+v", got, want)
	}

	// Second poll → 404 → nil
	got, err = c.PollTasks()
	if err != nil || got != nil {
		t.Errorf("second poll should be nil/nil, got %+v / %v", got, err)
	}
}

func TestSendOutput_PostsRunIdAndOutput(t *testing.T) {
	srv, log, mu := newRecordingServer(t, nil)
	defer srv.Close()

	c := New(srv.URL, "a-1", 0)
	if err := c.SendOutput("r-42", "hello"); err != nil {
		t.Fatalf("SendOutput: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	r := (*log)[0]
	if r.Path != "/api/runs/r-42/output" {
		t.Errorf("wrong path: %s", r.Path)
	}
	if !strings.Contains(r.Body, `"output":"hello"`) {
		t.Errorf("body missing output: %s", r.Body)
	}
}

func TestComplete_PostsExitCodeAndSummary(t *testing.T) {
	srv, log, mu := newRecordingServer(t, nil)
	defer srv.Close()

	c := New(srv.URL, "a-1", 0)
	if err := c.Complete("r-42", 0, "done"); err != nil {
		t.Fatalf("Complete: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	r := (*log)[0]
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
// Wire contract — the FastAPI server wraps the payload as {"task": ...}.
// PollTasks must unwrap the envelope; both null and populated branches
// pass through correctly.
// -----------------------------------------------------------------------------

func TestPollTasks_DecodesWrappedTaskEnvelope(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"task":{"run_id":"r-1","scenario_id":"SIM-EDR-001","command":"id","identity":{"mode":"runuser","username":"www-data"}}}`))
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
	if got.RunID != "r-1" || got.ScenarioID != "SIM-EDR-001" {
		t.Errorf("decode mismatch: %+v", got)
	}
	if got.Identity.Mode != "runuser" || got.Identity.Username != "www-data" {
		t.Errorf("identity decode mismatch: %+v", got.Identity)
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
