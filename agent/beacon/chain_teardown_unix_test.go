//go:build !windows

package beacon

// RG-AGENT-01 / RG-AGENT-02, at the level a DC observes them: a chained pull
// run that has reported /complete must not leave its steps' background
// processes behind, however it ended. Pull mode runs no cleanup.commands, so
// nothing else will ever stop them. Windows twins:
// chain_teardown_windows_test.go.

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// chainedTaskLeavingAHelper is a two-step chained task whose step-01 leaves a
// background process running (the shape of SIM-AIRS-001..005 step-01) and
// writes its pid to pidFile. step2 is the second step's command.
func chainedTaskLeavingAHelper(pidFile, step2 string) *Task {
	return &Task{
		TaskID: "t", RunID: "r", ScenarioID: "SIM-AIRS-LIKE",
		CgoAnchor: &CgoAnchor{ImageName: "python3"},
		Steps: []Step{
			{ID: "step-01", Command: "sleep 300 >/dev/null 2>&1 & echo $! > " + pidFile + "; echo target-live", Identity: "root"},
			{ID: "step-02", Command: step2, Identity: "root", Causality: &StepCausality{ParentStep: "step-01"}},
		},
	}
}

func helperPID(t *testing.T, pidFile string) int {
	t.Helper()
	b, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatalf("step-01 never wrote its helper's pid: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		t.Fatalf("bad helper pid %q", b)
	}
	t.Cleanup(func() { _ = syscall.Kill(pid, syscall.SIGKILL) })
	return pid
}

func helperGone(pid int) bool {
	deadline := time.Now().Add(2 * time.Second)
	for {
		if err := syscall.Kill(pid, 0); err == syscall.ESRCH {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// RG-AGENT-01: the run SUCCEEDED, and its helper is gone when executeTask
// returns — so the next run on this beacon cannot be served by it.
func TestExecuteTaskChained_ASuccessfulRunLeavesNoHelperBehind(t *testing.T) {
	srv := newChainTeardownServer(t, 0)
	pidFile := filepath.Join(t.TempDir(), "helper.pid")

	directIdentityClient(srv.URL).executeTask(context.Background(), chainedTaskLeavingAHelper(pidFile, "echo two"))

	b := srv.completed(t)
	if b.ExitCode == nil || *b.ExitCode != 0 {
		t.Fatalf("the run should have succeeded: %+v", b)
	}
	if pid := helperPID(t, pidFile); !helperGone(pid) {
		t.Fatalf("run reported %q but step-01's background helper (pid %d) is still running — "+
			"it would answer the next run's health check", b.Summary, pid)
	}
}

// RG-AGENT-02: an operator abort caught at the pre-step check of step-02 stops
// what step-01 started, not just the steps that had not run yet.
func TestExecuteTaskChained_AnAbortBetweenStepsStopsEarlierHelpers(t *testing.T) {
	// Poll 1 is step-01's pre-step check, poll 2 is step-02's.
	srv := newChainTeardownServer(t, 2)
	pidFile := filepath.Join(t.TempDir(), "helper.pid")

	directIdentityClient(srv.URL).executeTask(context.Background(), chainedTaskLeavingAHelper(pidFile, "echo two"))

	b := srv.completed(t)
	if b.ExitCode == nil || *b.ExitCode != abortExitCode || !strings.Contains(b.Summary, "aborted by operator") {
		t.Fatalf("the run should read aborted by operator: %+v", b)
	}
	if pid := helperPID(t, pidFile); !helperGone(pid) {
		t.Fatalf("run reads %q but step-01's background helper (pid %d) is still running", b.Summary, pid)
	}
}

// RG-AGENT-02: the same for a chain that fails fast at a later step.
func TestExecuteTaskChained_AFailFastStopStopsEarlierHelpers(t *testing.T) {
	srv := newChainTeardownServer(t, 0)
	pidFile := filepath.Join(t.TempDir(), "helper.pid")

	directIdentityClient(srv.URL).executeTask(context.Background(), chainedTaskLeavingAHelper(pidFile, "echo failing; exit 7"))

	b := srv.completed(t)
	if b.ExitCode == nil || *b.ExitCode != 7 {
		t.Fatalf("the run should have failed fast with 7: %+v", b)
	}
	if pid := helperPID(t, pidFile); !helperGone(pid) {
		t.Fatalf("run reads %q but step-01's background helper (pid %d) is still running", b.Summary, pid)
	}
}
