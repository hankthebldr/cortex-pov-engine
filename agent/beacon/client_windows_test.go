//go:build windows

package beacon

// PowerShell-dialect twins of client_unix_test.go (CI-04): streaming, a step
// that terminates itself, anchor death, fail-fast and abort — the beacon
// contracts as a Windows host actually runs them. Run only by the
// windows-native CI job; not observed passing on Windows by the change that
// introduced them.

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// direct resolver: these tests are about execution, not the token they run on.
func directResolver(string) identity.Resolution {
	return identity.Resolution{Mode: "direct", Honoured: true}
}

func TestStreamOutput_WindowsLiveChunksSent(t *testing.T) {
	task := &Task{TaskID: "t", RunID: "r", ScenarioID: "SIM-TEST-STREAM",
		Steps: []Step{{ID: "step-stream-1", Command: "Write-Output 'line1'; Start-Sleep -Milliseconds 300; Write-Output 'line2'",
			Identity: "root", MitreTechnique: "T1059"}}}
	rs := newRecordingServer(t, task)
	defer rs.Close()
	c := New(rs.URL, "agent-test", time.Millisecond)
	c.StreamOutput = true
	c.resolveIdentity = directResolver
	c.executeTask(context.Background(), task)
	if n := len(rs.find("POST", "/output")); n <= 1 {
		t.Errorf("expected >1 /output POSTs with StreamOutput=true, got %d", n)
	}
}

// Twin of TestExecuteTask_SignaledStepIsRecordedAndSeversChain. A Windows
// process has no signals: a step that terminates itself EXITED non-zero, so the
// run fails fast (UPSTREAM_FAILED) — it is never "signaled" or "severed".
func TestExecuteTask_WindowsSelfTerminatedStepIsExitedNotSevered(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", 0)
	c.resolveIdentity = directResolver
	c.executeTask(context.Background(), &Task{TaskID: "t", RunID: "r", ScenarioID: "S", Steps: []Step{
		{ID: "step-01", Command: "Stop-Process -Id $PID -Force", Identity: "root"},
		{ID: "step-02", Command: "Write-Output never", Identity: "root"},
	}})
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode == 0 {
		t.Fatalf("exit = %v, want non-zero", b.ExitCode)
	}
	if len(b.Steps) != 2 || b.Steps[0].Termination != TermExited || b.Steps[0].Signal != 0 {
		t.Fatalf("step 0 should be exited/no-signal: %+v", b.Steps)
	}
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamFailed)
	if strings.Contains(b.Summary, "SEVERED") {
		t.Errorf("a Windows self-termination must not read as a severance: %s", b.Summary)
	}
}

// Twin of TestExecuteTaskChained_AnchorDeathIsAnchorTerminatedNotExecutionError:
// the step kills its own anchor (its parent process).
func TestExecuteTaskChained_WindowsAnchorDeathIsAnchorTerminated(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", 0)
	c.resolveIdentity = directResolver
	c.executeTask(context.Background(), &Task{TaskID: "t", RunID: "r", ScenarioID: "S",
		CgoAnchor: &CgoAnchor{ImageName: "w3wp.exe"},
		Steps: []Step{
			{ID: "step-01", Identity: "root",
				Command: `Stop-Process -Force -Id (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId; Start-Sleep -Seconds 30`},
			{ID: "step-02", Command: "Write-Output never", Identity: "root"},
		}})
	b := decodeComplete(t, rs)
	if b.ExitCode == nil || *b.ExitCode != 137 {
		t.Fatalf("expected exit 137, got %v", b.ExitCode)
	}
	if b.Steps[0].Termination != TermAnchorTerminated || b.Steps[0].ExitCode != nil {
		t.Errorf("step 0 should be anchor_terminated with nil exit: %+v", b.Steps[0])
	}
	assertNotRun(t, b.Steps[1], "step-02", ReasonUpstreamSevered)
}

// Fail-fast with PowerShell and native exit codes, both execution paths.
func TestExecuteTask_WindowsFailFastStopsAtFirstNonZero(t *testing.T) {
	for _, chained := range []bool{false, true} {
		rs := newRecordingServer(t, nil)
		task := &Task{TaskID: "t", RunID: "r", ScenarioID: "S", Steps: []Step{
			{ID: "step-01", Command: "Write-Output 'first'", Identity: "root"},
			{ID: "step-02", Command: "cmd /c exit 3", Identity: "root"},
			{ID: "step-03", Command: "Write-Output 'should-not-run'", Identity: "root"},
		}}
		if chained {
			task.CgoAnchor = &CgoAnchor{ImageName: "w3wp.exe"}
		}
		c := New(rs.URL, "a-1", 0)
		c.resolveIdentity = directResolver
		c.executeTask(context.Background(), task)
		b := decodeComplete(t, rs)
		if b.ExitCode == nil || *b.ExitCode != 3 {
			t.Errorf("chained=%v: exit = %v, want the native 3", chained, b.ExitCode)
		}
		for _, o := range rs.find("POST", "/output") {
			if strings.Contains(o.Body, "should-not-run") && strings.Contains(o.Body, "STEP 3/3") {
				t.Errorf("chained=%v: step-03 ran after a failure", chained)
			}
		}
		assertNotRun(t, b.Steps[2], "step-03", ReasonUpstreamFailed)
		rs.Close()
	}
}

// Abort mid-step via Start-Sleep, both execution paths: the step stops well
// before the sleep would end and the run completes 130.
func TestExecuteTask_WindowsAbortMidLongRunningStep(t *testing.T) {
	for _, chained := range []bool{false, true} {
		rs := newRecordingServer(t, nil)
		task := &Task{TaskID: "t", RunID: "r", ScenarioID: "S", Steps: []Step{
			{ID: "step-01", Command: "Start-Sleep -Seconds 60", Identity: "root"},
			{ID: "step-02", Command: "Write-Output 'should-not-run'", Identity: "root"},
		}}
		if chained {
			task.CgoAnchor = &CgoAnchor{ImageName: "w3wp.exe"}
		}
		c := New(rs.URL, "a-1", 0)
		c.resolveIdentity = directResolver
		go func() {
			time.Sleep(3 * time.Second)
			rs.abortFlag.Store(true)
		}()
		start := time.Now()
		c.executeTask(context.Background(), task)
		if el := time.Since(start); el > 30*time.Second {
			t.Errorf("chained=%v: abort took %s (job kill not reaching the step?)", chained, el)
		}
		b := decodeComplete(t, rs)
		if b.ExitCode == nil || *b.ExitCode != 130 || !strings.Contains(b.Summary, "aborted by operator") {
			t.Errorf("chained=%v: exit=%v summary=%q, want 130 / aborted by operator", chained, b.ExitCode, b.Summary)
		}
		rs.Close()
	}
}
