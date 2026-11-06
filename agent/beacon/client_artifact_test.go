package beacon

// End-to-end task execution WITH staged artifacts: the beacon fetches, verifies,
// runs the step against the staged file, and tears the tooling down again.
//
// These use their own server (rather than newRecordingServer) because they must
// also serve /api/shelf/payload/{name}.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// artifactRecordingServer is newRecordingServer plus a payload shelf.
type artifactRecordingServer struct {
	*httptest.Server
	mu        sync.Mutex
	log       []recorded
	abortFlag atomic.Bool
	shelf     map[string][]byte
	shelfHits atomic.Int64
}

func newArtifactRecordingServer(t *testing.T, taskOnce *Task, shelf map[string][]byte) *artifactRecordingServer {
	t.Helper()
	rs := &artifactRecordingServer{shelf: shelf}
	handed := false
	rs.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		bodyBytes, _ := io.ReadAll(r.Body)
		rs.mu.Lock()
		rs.log = append(rs.log, recorded{Method: r.Method, Path: r.URL.Path, Body: string(bodyBytes)})
		rs.mu.Unlock()

		switch {
		case strings.HasPrefix(r.URL.Path, "/api/shelf/payload/") && r.Method == http.MethodGet:
			rs.shelfHits.Add(1)
			name := strings.TrimPrefix(r.URL.Path, "/api/shelf/payload/")
			body, ok := rs.shelf[name]
			if !ok {
				w.WriteHeader(404)
				return
			}
			w.Header().Set("Cache-Control", "no-store")
			_, _ = w.Write(body)
		case strings.HasPrefix(r.URL.Path, "/api/agents/") && strings.HasSuffix(r.URL.Path, "/tasks") && r.Method == http.MethodGet:
			if taskOnce != nil && !handed {
				handed = true
				_ = json.NewEncoder(w).Encode(map[string]any{"task": taskOnce})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"task": nil})
		case strings.HasSuffix(r.URL.Path, "/control") && r.Method == http.MethodGet:
			runID := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/api/runs/"), "/control")
			abort := rs.abortFlag.Load()
			status := "running"
			if abort {
				status = "aborted"
			}
			_ = json.NewEncoder(w).Encode(ControlState{Abort: abort, RunID: runID, Status: status})
		case strings.HasSuffix(r.URL.Path, "/output") && r.Method == http.MethodPost,
			strings.HasSuffix(r.URL.Path, "/complete") && r.Method == http.MethodPost,
			r.URL.Path == "/api/agents/register" && r.Method == http.MethodPost:
			w.WriteHeader(200)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(500)
		}
	}))
	t.Cleanup(rs.Close)
	return rs
}

func (rs *artifactRecordingServer) records() []recorded {
	rs.mu.Lock()
	defer rs.mu.Unlock()
	out := make([]recorded, len(rs.log))
	copy(out, rs.log)
	return out
}

func (rs *artifactRecordingServer) find(method, pathSuffix string) []recorded {
	var out []recorded
	for _, r := range rs.records() {
		if r.Method == method && strings.HasSuffix(r.Path, pathSuffix) {
			out = append(out, r)
		}
	}
	return out
}

func (rs *artifactRecordingServer) completeBody(t *testing.T) (int, string) {
	t.Helper()
	comps := rs.find("POST", "/complete")
	if len(comps) != 1 {
		t.Fatalf("want exactly 1 /complete, got %d", len(comps))
	}
	var body struct {
		ExitCode int    `json:"exit_code"`
		Summary  string `json:"summary"`
	}
	if err := json.Unmarshal([]byte(comps[0].Body), &body); err != nil {
		t.Fatal(err)
	}
	return body.ExitCode, body.Summary
}

func artifactClientFor(t *testing.T, rs *artifactRecordingServer, stageRoot string) *BeaconClient {
	t.Helper()
	c := New(rs.URL, "a-1", time.Millisecond)
	c.artifactGetenv = func(k string) string {
		if k == "CORTEXSIM_STAGE_ROOT" {
			return stageRoot
		}
		return ""
	}
	c.artifactProbeExec = func(string) error { return nil }
	cacheDir := t.TempDir()
	c.artifactCacheDirFunc = func() string { return cacheDir }
	return c
}

// -----------------------------------------------------------------------------
// The happy path: fetch -> verify -> the step actually runs the staged file
// -----------------------------------------------------------------------------

func bodiesOf(rs []recorded) []string {
	out := make([]string, len(rs))
	for i, r := range rs {
		out[i] = r.Body
	}
	return out
}

// -----------------------------------------------------------------------------
// The refusal: a corrupted shelf copy must stop the run dead
// -----------------------------------------------------------------------------

func TestExecuteTask_CorruptedArtifactRunsNoStepsAndSaysSo(t *testing.T) {
	shortRetries(t)
	root := t.TempDir()
	dest := filepath.Join(root, "run-1", "linpeas.sh")
	canary := filepath.Join(root, "canary")

	task := &Task{
		TaskID: "t-1", RunID: "run-1", ScenarioID: "SIM-EDR-999",
		Steps: []Step{{
			ID: "step-01", Name: "enumerate", MitreTechnique: "T1082", Identity: "root",
			// If this ever runs, the canary exists and the test fails loudly.
			Command: "touch " + canary,
		}},
		Artifacts: []Artifact{{
			Name: "linpeas.sh", SHA256: digestOf([]byte("THE ORIGINAL BYTES")),
			Path: "/api/shelf/payload/linpeas.sh", Dest: dest, Mode: "0755", Steps: []string{"step-01"},
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"linpeas.sh": []byte("TAMPERED BYTES")})
	c := artifactClientFor(t, rs, root)

	c.executeTask(context.Background(), task)

	if _, err := os.Stat(canary); err == nil {
		t.Fatalf("a step RAN without its tool — this is the manufactured false negative the feature exists to prevent")
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("an unverified file is on disk at %s", dest)
	}

	code, summary := rs.completeBody(t)
	if code != artifactStagingExitCode {
		t.Fatalf("exit_code = %d, want %d (EX_CONFIG — the pod's 'this never ran' code)", code, artifactStagingExitCode)
	}
	for _, want := range []string{artifactStagingFailedMarker, codeSHA256Mismatch, "NO STEPS RAN", "proves NOTHING"} {
		if !strings.Contains(summary, want) {
			t.Errorf("summary missing %q: %s", want, summary)
		}
	}
	joined := strings.Join(bodiesOf(rs.find("POST", "/output")), "\n")
	if !strings.Contains(joined, "THIS STEP DID NOT RUN") {
		t.Fatalf("no frame told the reader the step did not run:\n%s", joined)
	}
}

// A missing artifact must be a loud refusal too — not a step that runs anyway.
func TestExecuteTask_MissingArtifactRefusesTheRun(t *testing.T) {
	shortRetries(t)
	root := t.TempDir()
	task := &Task{
		TaskID: "t-1", RunID: "run-1", ScenarioID: "SIM-EDR-999",
		Steps: []Step{{ID: "step-01", MitreTechnique: "T1082", Identity: "root", Command: "true"}},
		Artifacts: []Artifact{{
			Name: "nope.sh", SHA256: digestOf([]byte("x")),
			Path: "/api/shelf/payload/nope.sh", Dest: filepath.Join(root, "nope.sh"), Steps: []string{"step-01"},
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{})
	c := artifactClientFor(t, rs, root)
	c.executeTask(context.Background(), task)

	code, summary := rs.completeBody(t)
	if code != artifactStagingExitCode {
		t.Fatalf("exit_code = %d, want %d", code, artifactStagingExitCode)
	}
	if !strings.Contains(summary, codeUnavailable) {
		t.Errorf("summary must name ARTIFACT_UNAVAILABLE: %s", summary)
	}
}

// -----------------------------------------------------------------------------
// Teardown on every exit path
// -----------------------------------------------------------------------------

func TestExecuteTask_CleansUpAfterFailFast(t *testing.T) {
	tool := []byte("#!/bin/sh\nexit 0\n")
	root := t.TempDir()
	dest := filepath.Join(root, "run-1", "x.sh")
	task := &Task{
		TaskID: "t", RunID: "run-1", ScenarioID: "S",
		Steps: []Step{
			{ID: "step-01", MitreTechnique: "T1059", Identity: "root", Command: "exit 3"},
			{ID: "step-02", MitreTechnique: "T1059", Identity: "root", Command: "true"},
		},
		Artifacts: []Artifact{{
			Name: "x.sh", SHA256: digestOf(tool), Path: "/api/shelf/payload/x.sh",
			Dest: dest, Mode: "0755",
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"x.sh": tool})
	c := artifactClientFor(t, rs, root)
	c.executeTask(context.Background(), task)

	if code, _ := rs.completeBody(t); code != 3 {
		t.Fatalf("exit_code = %d, want 3 (fail-fast semantics unchanged)", code)
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived a fail-fast run at %s", dest)
	}
}

func TestExecuteTask_CleansUpAfterOperatorAbortMidStep(t *testing.T) {
	tool := []byte("#!/bin/sh\nexit 0\n")
	root := t.TempDir()
	dest := filepath.Join(root, "run-1", "x.sh")
	task := &Task{
		TaskID: "t", RunID: "run-1", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", MitreTechnique: "T1059", Identity: "root", Command: "sleep 30"}},
		Artifacts: []Artifact{{
			Name: "x.sh", SHA256: digestOf(tool), Path: "/api/shelf/payload/x.sh",
			Dest: dest, Mode: "0755",
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"x.sh": tool})
	c := artifactClientFor(t, rs, root)

	go func() {
		time.Sleep(150 * time.Millisecond)
		rs.abortFlag.Store(true)
	}()
	c.executeTask(context.Background(), task)

	if code, _ := rs.completeBody(t); code != abortExitCode {
		t.Fatalf("exit_code = %d, want %d", code, abortExitCode)
	}
	// The one case an operator most needs teardown for: they killed the run
	// BECAUSE something went wrong, and the tooling is still on the host.
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived an ABORTED run at %s", dest)
	}
}

func TestExecuteTask_CleansUpAfterAgentShutdown(t *testing.T) {
	tool := []byte("#!/bin/sh\nexit 0\n")
	root := t.TempDir()
	dest := filepath.Join(root, "run-1", "x.sh")
	task := &Task{
		TaskID: "t", RunID: "run-1", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", MitreTechnique: "T1059", Identity: "root", Command: "sleep 30"}},
		Artifacts: []Artifact{{
			Name: "x.sh", SHA256: digestOf(tool), Path: "/api/shelf/payload/x.sh",
			Dest: dest, Mode: "0755",
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"x.sh": tool})
	c := artifactClientFor(t, rs, root)

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(150 * time.Millisecond)
		cancel()
	}()
	c.executeTask(ctx, task)

	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived an agent shutdown at %s", dest)
	}
}

// An abort signalled BEFORE staging must not put tooling on the host at all.
func TestExecuteTask_AbortBeforeStagingFetchesNothing(t *testing.T) {
	tool := []byte("#!/bin/sh\nexit 0\n")
	root := t.TempDir()
	task := &Task{
		TaskID: "t", RunID: "run-1", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", MitreTechnique: "T1059", Identity: "root", Command: "true"}},
		Artifacts: []Artifact{{
			Name: "x.sh", SHA256: digestOf(tool), Path: "/api/shelf/payload/x.sh",
			Dest: filepath.Join(root, "run-1", "x.sh"), Mode: "0755",
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"x.sh": tool})
	rs.abortFlag.Store(true)
	c := artifactClientFor(t, rs, root)
	c.executeTask(context.Background(), task)

	if n := rs.shelfHits.Load(); n != 0 {
		t.Fatalf("the beacon staged %d artifact(s) for a run the operator had already aborted", n)
	}
	if code, _ := rs.completeBody(t); code != abortExitCode {
		t.Fatalf("exit_code = %d, want %d", code, abortExitCode)
	}
}

// -----------------------------------------------------------------------------
// Back-compat: a task with no artifacts must behave EXACTLY as before
// -----------------------------------------------------------------------------

func TestExecuteTask_NoArtifactsTouchesTheShelfNotAtAll(t *testing.T) {
	root := t.TempDir()
	task := &Task{
		TaskID: "t", RunID: "run-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{
			{ID: "step-01", MitreTechnique: "T1059", Identity: "root", Command: "echo one"},
			{ID: "step-02", MitreTechnique: "T1059", Identity: "root", Command: "echo two"},
		},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{})
	c := artifactClientFor(t, rs, root)
	c.executeTask(context.Background(), task)

	if n := rs.shelfHits.Load(); n != 0 {
		t.Fatalf("a task with no artifacts made %d shelf request(s)", n)
	}
	outs := rs.find("POST", "/output")
	if len(outs) != 2 {
		t.Fatalf("want exactly 2 per-step frames and no staging frame, got %d", len(outs))
	}
	for _, o := range outs {
		if strings.Contains(o.Body, "ARTIFACT STAGING") {
			t.Fatalf("a staging frame was emitted for an artifact-free task: %s", o.Body)
		}
	}
	if code, _ := rs.completeBody(t); code != 0 {
		t.Fatalf("exit_code = %d, want 0", code)
	}
}

// -----------------------------------------------------------------------------
// The permission trap — gated, because it needs euid 0 AND a real www-data.
// Runs inside the prod image, skips on a laptop.
// -----------------------------------------------------------------------------
