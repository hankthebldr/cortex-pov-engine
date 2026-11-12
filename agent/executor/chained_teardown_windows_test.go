//go:build windows

package executor

// Windows twins of chained_teardown_unix_test.go (RG-AGENT-01 / RG-AGENT-02).
// Run only by the windows-native CI job; NOT observed passing on Windows by the
// change that introduced them.
//
// The Windows rule differs from POSIX on purpose:
//   - a chain that finished CLEANLY terminates nothing (the BEACON-04 rule,
//     pinned by TestChainSession_WindowsCloseDoesNotKillAfterTheAnchorExited in
//     executor_windows_test.go);
//   - a chain that was ABANDONED (abort, control loss, timeout, fail-fast)
//     terminates its Job Object, so an earlier step's detached child dies.

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

// detachedChildStep starts a detached, long-lived child, records its pid and
// RETURNS — a successful step that leaves tooling running.
func detachedChildStep(pidFile string) string {
	return fmt.Sprintf("$p = Start-Process -FilePath %s -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 300' -PassThru -WindowStyle Hidden; "+
		"Set-Content -LiteralPath %s -Value $p.Id -Encoding Ascii", psSingleQuote(powerShellExePath()), psSingleQuote(pidFile))
}

// RG-AGENT-02: Abandon reaches the detached child of a step that had already
// SUCCEEDED, through the Job Object, after the anchor itself has exited — and
// nothing outside the job.
func TestChainSession_WindowsAbandonStopsAnEarlierStepsDetachedChild(t *testing.T) {
	bystander := startBystander(t)
	s, err := NewChainSession(context.Background(), "w3wp.exe")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	pidFile := filepath.Join(t.TempDir(), "bg.pid")
	if _, _, rc, err := s.RunStepShell("step-01", "", detachedChildStep(pidFile)); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	bg := waitForPIDFile(t, pidFile, 30*time.Second)
	t.Cleanup(func() { killPID(bg) })

	_ = s.Abandon()
	if !waitDead(bg, 10*time.Second) {
		t.Fatalf("detached child %d of an earlier step survived Abandon() — a run that reads aborted "+
			"would leave its tooling running", bg)
	}
	if got := s.Teardown(); got != teardownJob && got != teardownSnapshot {
		t.Errorf("Teardown() = %q, want the job or snapshot teardown line", got)
	}
	if !pidAlive(uint32(bystander.Process.Pid)) {
		t.Fatalf("Abandon() killed an UNRELATED process (pid %d)", bystander.Process.Pid)
	}
	if err := s.Close(); err != nil {
		t.Errorf("Close() after Abandon() = %v, want a no-op nil", err)
	}
}

// A clean Close reports no teardown on Windows.
func TestChainSession_WindowsCleanCloseReportsNoTeardown(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	if _, _, rc, err := s.RunStepShell("step-01", "", "Write-Output 'ok'"); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	_ = s.Close()
	if got := s.Teardown(); got != "" {
		t.Fatalf("Teardown() after a clean Close = %q, want \"\" (Windows terminates nothing then)", got)
	}
}
