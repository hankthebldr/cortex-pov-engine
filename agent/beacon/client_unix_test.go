//go:build !windows

package beacon

// POSIX-dialect beacon tests (CI-04): `&&`, `kill -9 $$`, and the tool-presence
// / runtime-dependency preflights as a POSIX host runs them. (Both preflights
// run on Windows too since BEACON-13 — runtime_deps_winrule_test.go,
// runtime_deps_windows_test.go.) PowerShell-dialect twins: client_windows_test.go.

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

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
