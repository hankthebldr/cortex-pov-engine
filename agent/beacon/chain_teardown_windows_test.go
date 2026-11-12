//go:build windows

package beacon

// Windows twins of chain_teardown_unix_test.go (RG-AGENT-02). Run only by the
// windows-native CI job; NOT observed passing on Windows by the change that
// introduced them.
//
// Windows differs from POSIX in ONE row: a chain that finished cleanly
// terminates nothing (the BEACON-04 rule), so there is no Windows twin of
// TestExecuteTaskChained_ASuccessfulRunLeavesNoHelperBehind — its opposite is
// pinned instead. A chain that did not finish cleanly stops everything it
// started, through the Job Object.

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

const (
	winProcessTerminate        = 0x0001
	winProcessQueryLimitedInfo = 0x1000
	winSynchronize             = 0x00100000
	winPowerShell              = `\System32\WindowsPowerShell\v1.0\powershell.exe`
)

func winPowerShellPath() string {
	root := os.Getenv("SystemRoot")
	if root == "" {
		root = `C:\Windows`
	}
	return root + winPowerShell
}

func winPIDAlive(pid uint32) bool {
	h, err := syscall.OpenProcess(winSynchronize|winProcessQueryLimitedInfo, false, pid)
	if err != nil {
		return false
	}
	defer syscall.CloseHandle(h)
	ev, err := syscall.WaitForSingleObject(h, 0)
	return err == nil && ev == syscall.WAIT_TIMEOUT
}

func winKillPID(pid uint32) {
	if h, err := syscall.OpenProcess(winProcessTerminate, false, pid); err == nil {
		_ = syscall.TerminateProcess(h, 1)
		_ = syscall.CloseHandle(h)
	}
}

func winHelperPID(t *testing.T, pidFile string) uint32 {
	t.Helper()
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		if b, err := os.ReadFile(pidFile); err == nil {
			if v, err := strconv.ParseUint(strings.TrimSpace(strings.TrimPrefix(string(b), "\ufeff")), 10, 32); err == nil && v > 0 {
				pid := uint32(v)
				t.Cleanup(func() { winKillPID(pid) })
				return pid
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("step-01 never wrote its helper's pid to %s", pidFile)
	return 0
}

func winHelperGone(pid uint32, within time.Duration) bool {
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if !winPIDAlive(pid) {
			return true
		}
		time.Sleep(100 * time.Millisecond)
	}
	return !winPIDAlive(pid)
}

// winChainedTaskLeavingAHelper: step-01 starts a detached child, records its
// pid and returns 0; step2 is the second step's command.
func winChainedTaskLeavingAHelper(pidFile, step2 string) *Task {
	ps := "'" + strings.ReplaceAll(winPowerShellPath(), "'", "''") + "'"
	pf := "'" + strings.ReplaceAll(pidFile, "'", "''") + "'"
	step1 := fmt.Sprintf("$p = Start-Process -FilePath %s -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 300' -PassThru -WindowStyle Hidden; "+
		"Set-Content -LiteralPath %s -Value $p.Id -Encoding Ascii; Write-Output 'target-live'", ps, pf)
	return &Task{
		TaskID: "t", RunID: "r", ScenarioID: "SIM-WIN-HELPER",
		CgoAnchor: &CgoAnchor{ImageName: "w3wp.exe"},
		Steps: []Step{
			{ID: "step-01", Command: step1, Identity: "root"},
			{ID: "step-02", Command: step2, Identity: "root", Causality: &StepCausality{ParentStep: "step-01"}},
		},
	}
}

// RG-AGENT-02: an abort caught at step-02's pre-step check stops what step-01
// started.
func TestExecuteTaskChained_WindowsAbortBetweenStepsStopsEarlierHelpers(t *testing.T) {
	srv := newChainTeardownServer(t, 2)
	pidFile := filepath.Join(t.TempDir(), "helper.pid")

	directIdentityClient(srv.URL).executeTask(context.Background(), winChainedTaskLeavingAHelper(pidFile, "Write-Output 'two'"))

	b := srv.completed(t)
	if b.ExitCode == nil || *b.ExitCode != abortExitCode || !strings.Contains(b.Summary, "aborted by operator") {
		t.Fatalf("the run should read aborted by operator: %+v", b)
	}
	if pid := winHelperPID(t, pidFile); !winHelperGone(pid, 15*time.Second) {
		t.Fatalf("run reads %q but step-01's detached helper (pid %d) is still running", b.Summary, pid)
	}
}

// RG-AGENT-02: the same for a chain that fails fast at a later step.
func TestExecuteTaskChained_WindowsFailFastStopsEarlierHelpers(t *testing.T) {
	srv := newChainTeardownServer(t, 0)
	pidFile := filepath.Join(t.TempDir(), "helper.pid")

	directIdentityClient(srv.URL).executeTask(context.Background(), winChainedTaskLeavingAHelper(pidFile, "cmd /c exit 7"))

	b := srv.completed(t)
	if b.ExitCode == nil || *b.ExitCode != 7 {
		t.Fatalf("the run should have failed fast with 7: %+v", b)
	}
	if pid := winHelperPID(t, pidFile); !winHelperGone(pid, 15*time.Second) {
		t.Fatalf("run reads %q but step-01's detached helper (pid %d) is still running", b.Summary, pid)
	}
}

// The row Windows keeps from BEACON-04: a chain that finished CLEANLY leaves
// its steps' detached children running. (POSIX tears them down; whether Windows
// should is CONT-11, Henry's decision.)
func TestExecuteTaskChained_WindowsCleanRunLeavesDetachedChildrenAlone(t *testing.T) {
	srv := newChainTeardownServer(t, 0)
	pidFile := filepath.Join(t.TempDir(), "helper.pid")

	directIdentityClient(srv.URL).executeTask(context.Background(), winChainedTaskLeavingAHelper(pidFile, "Write-Output 'two'"))

	b := srv.completed(t)
	if b.ExitCode == nil || *b.ExitCode != 0 {
		t.Fatalf("the run should have succeeded: %+v", b)
	}
	pid := winHelperPID(t, pidFile)
	time.Sleep(4 * time.Second) // past the executor's kill grace
	if !winPIDAlive(pid) {
		t.Fatalf("a CLEAN chained run terminated step-01's detached child (pid %d) — Windows must not "+
			"kill after a clean chain", pid)
	}
}
