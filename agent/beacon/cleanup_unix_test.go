//go:build !windows

package beacon

// Scenario cleanup in pull mode (#324), as a POSIX host runs it.
//
// Pull mode never ran a scenario's cleanup.commands, so a run left its files,
// units, cron lines and accounts on the target. The task now carries the
// commands for this OS and the beacon runs them when the task ends.
//
// Every task here is decoded from the JSON SimCore sends, and every assertion
// reads what the beacon did on the host or sent back: the marker file a step
// left behind, the /output frames, the /complete body.

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// taskStep is one step of a cleanup-test task, in the wire shape.
func taskStep(id, command string) map[string]any {
	return map[string]any{
		"id": id, "name": id, "command": command,
		"identity": "root", "mitre_technique": "T1053.003",
	}
}

// taskWithCleanup builds the task SimCore sends for steps plus cleanup
// commands, and decodes it the way PollTasks does.
func taskWithCleanup(t *testing.T, steps []map[string]any, cleanup ...string) *Task {
	t.Helper()
	return taskWithCleanupTimeout(t, 120, steps, cleanup...)
}

func taskWithCleanupTimeout(t *testing.T, timeoutSeconds int, steps []map[string]any, cleanup ...string) *Task {
	t.Helper()
	body := map[string]any{
		"task_id": "t-1", "run_id": "r-1", "scenario_id": "SIM-EDR-003", "steps": steps,
		// Every task SimCore sends also asks for live output (liveout.go).
		"live_output": map[string]any{"flush_ms": 1000, "max_chunk_bytes": 16384, "max_step_bytes": 262144},
	}
	if len(cleanup) > 0 {
		cmds := make([]map[string]any, 0, len(cleanup))
		for _, c := range cleanup {
			cmds = append(cmds, map[string]any{"command": c, "shell": ""})
		}
		body["cleanup"] = map[string]any{"commands": cmds, "timeout_seconds": timeoutSeconds}
	}
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal task: %v", err)
	}
	return decodeTask(t, string(raw))
}

// outputFrame is one /output body. Kind is set on a live frame (a step's start
// or a chunk of its output while it runs) and empty on a frame SimCore stores.
type outputFrame struct {
	Output  string `json:"output"`
	StepID  string `json:"step_id"`
	Kind    string `json:"kind"`
	Command string `json:"command"`
}

// isStoredFrame reports whether a recorded request is an /output frame SimCore
// stores: a step's final frame or the cleanup section, not a live frame.
func isStoredFrame(rec recorded) bool {
	return strings.HasSuffix(rec.Path, "/output") && !strings.Contains(rec.Body, `"kind"`)
}

// cleanupFrames returns the /output frames that carry a cleanup section.
func cleanupFrames(t *testing.T, rs *recordingServer) []outputFrame {
	t.Helper()
	var out []outputFrame
	for _, rec := range rs.find("POST", "/output") {
		var f outputFrame
		if err := json.Unmarshal([]byte(rec.Body), &f); err != nil {
			t.Fatalf("decode /output body %q: %v", rec.Body, err)
		}
		if strings.Contains(f.Output, "=== CLEANUP") {
			out = append(out, f)
		}
	}
	return out
}

// completeWithCleanup is the /complete body, cleanup object included.
type completeWithCleanup struct {
	ExitCode *int          `json:"exit_code"`
	Summary  string        `json:"summary"`
	Steps    []StepOutcome `json:"steps"`
	Cleanup  *struct {
		Status string  `json:"status"`
		Ran    int     `json:"ran"`
		Failed int     `json:"failed"`
		Reason *string `json:"reason"`
	} `json:"cleanup"`
}

func decodeCompleteWithCleanup(t *testing.T, rs *recordingServer) completeWithCleanup {
	t.Helper()
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected exactly 1 /complete, got %d", len(completes))
	}
	var b completeWithCleanup
	if err := json.Unmarshal([]byte(completes[0].Body), &b); err != nil {
		t.Fatalf("decode /complete body %q: %v", completes[0].Body, err)
	}
	return b
}

// assertCleanup checks the cleanup object of a /complete body.
func assertCleanup(t *testing.T, b completeWithCleanup, status string, ran, failed int, reason string) {
	t.Helper()
	if b.Cleanup == nil {
		t.Fatalf("/complete carries no cleanup outcome")
	}
	got := ""
	if b.Cleanup.Reason != nil {
		got = *b.Cleanup.Reason
	}
	if b.Cleanup.Status != status || b.Cleanup.Ran != ran || b.Cleanup.Failed != failed || got != reason {
		t.Fatalf("cleanup = {status:%s ran:%d failed:%d reason:%q}, want {status:%s ran:%d failed:%d reason:%q}",
			b.Cleanup.Status, b.Cleanup.Ran, b.Cleanup.Failed, got, status, ran, failed, reason)
	}
}

func assertGone(t *testing.T, path string) {
	t.Helper()
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("%s is still on the host after the task ended (stat err = %v)", path, err)
	}
}

func TestCleanupRunsAfterFailedStep(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	marker := filepath.Join(t.TempDir(), "cortexsim_marker")

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "touch "+marker),
		taskStep("step-02", "exit 7"),
		taskStep("step-03", "echo should-not-run"),
	}, "rm -f "+marker)
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	assertGone(t, marker)
	b := decodeCompleteWithCleanup(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 7 {
		t.Fatalf("exit code = %v, want the failed step's 7", b.ExitCode)
	}
	assertCleanup(t, b, "clean", 1, 0, "")

	frames := cleanupFrames(t, rs)
	if len(frames) != 1 {
		t.Fatalf("expected one cleanup section in the run output, got %d", len(frames))
	}
	section := frames[0].Output
	if !strings.HasPrefix(section, "=== CLEANUP · 1 commands · identity=direct ===\n") {
		t.Errorf("cleanup section header: %q", section)
	}
	if !strings.Contains(section, "--- cleanup 1/1 exit_code=0 duration=") {
		t.Errorf("cleanup section has no footer for its command: %q", section)
	}
	// SimCore reads a step id on /output as "that step started", and a `kind`
	// as a live frame it publishes and never stores. The section is neither.
	if frames[0].StepID != "" || frames[0].Kind != "" {
		t.Errorf("cleanup frame is attributed to step %q with kind %q", frames[0].StepID, frames[0].Kind)
	}
	// Cleanup is not a step: no live frame announces or streams its commands.
	for _, rec := range rs.find("POST", "/output") {
		var f outputFrame
		_ = json.Unmarshal([]byte(rec.Body), &f)
		if f.Kind != "" && (strings.Contains(f.Command, "rm -f") || strings.Contains(f.Output, "CLEANUP")) {
			t.Errorf("a live %q frame carries cleanup: %s", f.Kind, rec.Body)
		}
	}
	// The section lands after the failed step's frame and before /complete.
	failedAt, cleanupAt, completeAt := -1, -1, -1
	for i, rec := range rs.records() {
		switch {
		case isStoredFrame(rec) && strings.Contains(rec.Body, `"step_id":"step-02"`):
			failedAt = i
		case isStoredFrame(rec) && strings.Contains(rec.Body, "=== CLEANUP"):
			cleanupAt = i
		case strings.HasSuffix(rec.Path, "/complete"):
			completeAt = i
		}
	}
	if !(failedAt >= 0 && failedAt < cleanupAt && cleanupAt < completeAt) {
		t.Errorf("order: failed step frame at %d, cleanup at %d, /complete at %d", failedAt, cleanupAt, completeAt)
	}
}

func TestCleanupRunsAfterAbort(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	marker := filepath.Join(t.TempDir(), "cortexsim_marker")

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "touch "+marker+"; sleep 30"),
		taskStep("step-02", "echo should-not-run"),
	}, "rm -f "+marker)
	go func() {
		time.Sleep(300 * time.Millisecond)
		rs.abortFlag.Store(true)
	}()
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	assertGone(t, marker)
	b := decodeCompleteWithCleanup(t, rs)
	if b.ExitCode == nil || *b.ExitCode != abortExitCode {
		t.Fatalf("exit code = %v, want %d (aborted)", b.ExitCode, abortExitCode)
	}
	if len(b.Steps) != 2 || b.Steps[0].Termination != TermAborted {
		t.Fatalf("step outcomes changed: %+v", b.Steps)
	}
	assertCleanup(t, b, "clean", 1, 0, "")
	if n := len(cleanupFrames(t, rs)); n != 1 {
		t.Fatalf("expected one cleanup section, got %d", n)
	}
}

func TestCleanupSkippedWhenNoStepStarted(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	rs.abortFlag.Store(true) // stopped before step 1
	// Not this run's: nothing ran, so nothing here may be removed.
	bystander := filepath.Join(t.TempDir(), "not_this_runs")
	if err := os.WriteFile(bystander, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "echo never"),
	}, "rm -f "+bystander)
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	if _, err := os.Stat(bystander); err != nil {
		t.Fatalf("a cleanup command ran although no step started: %v", err)
	}
	if n := len(cleanupFrames(t, rs)); n != 0 {
		t.Fatalf("expected no cleanup section, got %d", n)
	}
	assertCleanup(t, decodeCompleteWithCleanup(t, rs), "not_run", 0, 0, "NO_STEP_STARTED")
}

func TestCleanupFailureDoesNotChangeExitCode(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	marker := filepath.Join(t.TempDir(), "cortexsim_marker")

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "touch "+marker),
		taskStep("step-02", "echo fine"),
	}, "echo cannot-remove-unit >&2; exit 3", "rm -f "+marker)
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	b := decodeCompleteWithCleanup(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 0 {
		t.Fatalf("exit code = %v, want 0: a failed cleanup is not a failed run", b.ExitCode)
	}
	if !strings.HasPrefix(b.Summary, "SUCCESS | scenario=SIM-EDR-003 steps=2") {
		t.Errorf("summary changed: %s", b.Summary)
	}
	for _, s := range b.Steps {
		if s.Termination != TermExited || s.ExitCode == nil || *s.ExitCode != 0 {
			t.Errorf("step outcome changed by cleanup: %+v", s)
		}
	}
	assertCleanup(t, b, "failed", 2, 1, "COMMAND_FAILED")

	frames := cleanupFrames(t, rs)
	if len(frames) != 1 {
		t.Fatalf("expected one cleanup section, got %d", len(frames))
	}
	for _, want := range []string{
		"=== CLEANUP · 2 commands · identity=direct ===",
		"cannot-remove-unit",
		"--- cleanup 1/2 exit_code=3 duration=",
		"--- cleanup 2/2 exit_code=0 duration=",
	} {
		if !strings.Contains(frames[0].Output, want) {
			t.Errorf("cleanup section lacks %q:\n%s", want, frames[0].Output)
		}
	}
	// The command after the failed one still ran.
	assertGone(t, marker)
}

func TestCompleteCarriesCleanupOutcome(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	dir := t.TempDir()

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "touch "+filepath.Join(dir, "a")+" "+filepath.Join(dir, "b")),
	}, "rm -f "+filepath.Join(dir, "a"), "rm -f "+filepath.Join(dir, "b"))
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	body := rs.find("POST", "/complete")[0].Body
	for _, want := range []string{
		`"cleanup":{"status":"clean","ran":2,"failed":0,"reason":null}`,
		`"task_id":"t-1"`,
		`"agent_id":"a-1"`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("/complete body lacks %s: %s", want, body)
		}
	}

	// An older SimCore sends no cleanup key. The beacon then runs nothing
	// extra and its /complete is the body it has always sent.
	old := newRecordingServer(t, nil)
	defer old.Close()
	New(old.URL, "a-1", 0).executeTask(context.Background(),
		taskWithCleanup(t, []map[string]any{taskStep("step-01", "echo one")}))
	if body := old.find("POST", "/complete")[0].Body; strings.Contains(body, `"cleanup"`) {
		t.Errorf("/complete for a task without cleanup carries one: %s", body)
	}
	if n := len(cleanupFrames(t, old)); n != 0 {
		t.Errorf("a task without cleanup wrote %d cleanup section(s)", n)
	}
}

// Every way a task can end with a step started: cleanup runs, once.
func TestCleanupRunsOnceOnEveryExitPath(t *testing.T) {
	cases := []struct {
		name     string
		chained  bool
		last     string // the second step
		timeout  int    // its timeout_seconds, 0 = default
		wantExit int
	}{
		{name: "after the last step", last: "echo done", wantExit: 0},
		{name: "after the last step of a chained run", chained: true, last: "echo done", wantExit: 0},
		{name: "after a timeout", last: "sleep 20", timeout: 1, wantExit: timeoutExitCode},
		{name: "after a timeout in a chained run", chained: true, last: "sleep 20", timeout: 1, wantExit: timeoutExitCode},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rs := newRecordingServer(t, nil)
			defer rs.Close()
			marker := filepath.Join(t.TempDir(), "cortexsim_marker")
			second := taskStep("step-02", tc.last)
			if tc.timeout > 0 {
				second["timeout_seconds"] = tc.timeout
			}
			task := taskWithCleanup(t, []map[string]any{
				taskStep("step-01", "touch "+marker), second,
			}, "rm -f "+marker)
			if tc.chained {
				task.CgoAnchor = &CgoAnchor{ImageName: "apache2"}
			}
			New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

			assertGone(t, marker)
			b := decodeCompleteWithCleanup(t, rs)
			if b.ExitCode == nil || *b.ExitCode != tc.wantExit {
				t.Fatalf("exit code = %v, want %d", b.ExitCode, tc.wantExit)
			}
			assertCleanup(t, b, "clean", 1, 0, "")
			if n := len(cleanupFrames(t, rs)); n != 1 {
				t.Fatalf("expected one cleanup section, got %d", n)
			}
		})
	}
}

// A cleanup command that hangs is stopped at the task's timeout, and the
// commands after it still run.
func TestCleanupCommandIsStoppedAtItsTimeout(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	marker := filepath.Join(t.TempDir(), "cortexsim_marker")

	task := taskWithCleanupTimeout(t, 1, []map[string]any{
		taskStep("step-01", "touch "+marker),
	}, "sleep 20", "rm -f "+marker)
	start := time.Now()
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)
	if took := time.Since(start); took > 12*time.Second {
		t.Fatalf("a hung cleanup command held the task for %s", took)
	}

	assertGone(t, marker)
	b := decodeCompleteWithCleanup(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 0 {
		t.Fatalf("exit code = %v, want 0", b.ExitCode)
	}
	assertCleanup(t, b, "failed", 2, 1, "COMMAND_TIMEOUT")
	frames := cleanupFrames(t, rs)
	if len(frames) != 1 || !strings.Contains(frames[0].Output, "--- cleanup 1/2 exit_code=124 duration=") {
		t.Fatalf("no timeout footer for the hung command: %+v", frames)
	}
}

// While cleanup runs the beacon keeps polling the run's control channel, as it
// does during a step. That poll is what tells SimCore the beacon is alive; a
// cleanup longer than SimCore's beacon-lost window would otherwise read as a
// lost beacon.
func TestCleanupKeepsTheControlChannelAlive(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "echo one"),
	}, "sleep 5")
	New(rs.URL, "a-1", 0).executeTask(context.Background(), task)

	stepAt, cleanupAt := -1, -1
	recs := rs.records()
	for i, rec := range recs {
		if !isStoredFrame(rec) {
			continue
		}
		if strings.Contains(rec.Body, `"step_id":"step-01"`) {
			stepAt = i
		}
		if strings.Contains(rec.Body, "=== CLEANUP") {
			cleanupAt = i
		}
	}
	if stepAt < 0 || cleanupAt < stepAt {
		t.Fatalf("step frame at %d, cleanup frame at %d", stepAt, cleanupAt)
	}
	polls := 0
	for _, rec := range recs[stepAt:cleanupAt] {
		if rec.Method == "GET" && strings.HasSuffix(rec.Path, "/control") {
			polls++
		}
	}
	if polls == 0 {
		t.Fatalf("no /control poll during a %s cleanup", 5*time.Second)
	}
}
