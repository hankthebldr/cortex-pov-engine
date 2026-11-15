//go:build !windows

package beacon

// Scenario cleanup when the beacon itself is told to stop (#324).
//
// A stop (SIGTERM, a service stop) cancels the task's context and kills the
// step in flight. Cleanup does not run under that context: it is given
// cleanupShutdownBudget from the stop, then cut.

import (
	"context"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestCleanupRunsAfterAgentShutdown(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	marker := filepath.Join(t.TempDir(), "cortexsim_marker")

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "touch "+marker+"; sleep 30"),
	}, "rm -f "+marker)
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(300 * time.Millisecond)
		cancel()
	}()
	New(rs.URL, "a-1", 0).executeTask(ctx, task)

	assertGone(t, marker)
	b := decodeCompleteWithCleanup(t, rs)
	if b.ExitCode == nil || *b.ExitCode != abortExitCode || !strings.Contains(b.Summary, "agent shutdown") {
		t.Fatalf("expected the shutdown completion, got %v %q", b.ExitCode, b.Summary)
	}
	assertCleanup(t, b, "clean", 1, 0, "")
}

func TestCleanupOnShutdownIsCutAtItsBudget(t *testing.T) {
	old := cleanupShutdownBudget
	cleanupShutdownBudget = 500 * time.Millisecond
	t.Cleanup(func() { cleanupShutdownBudget = old })

	rs := newRecordingServer(t, nil)
	defer rs.Close()
	marker := filepath.Join(t.TempDir(), "cortexsim_marker")

	task := taskWithCleanup(t, []map[string]any{
		taskStep("step-01", "touch "+marker+"; sleep 30"),
	}, "sleep 30", "rm -f "+marker)
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(300 * time.Millisecond)
		cancel()
	}()
	start := time.Now()
	New(rs.URL, "a-1", 0).executeTask(ctx, task)
	if took := time.Since(start); took > 12*time.Second {
		t.Fatalf("cleanup held a stopping beacon for %s", took)
	}

	b := decodeCompleteWithCleanup(t, rs)
	if b.ExitCode == nil || *b.ExitCode != abortExitCode {
		t.Fatalf("exit code = %v, want %d", b.ExitCode, abortExitCode)
	}
	// One command was started and killed; the second never ran.
	assertCleanup(t, b, "failed", 1, 1, "SHUTDOWN_BUDGET_SPENT")
	frames := cleanupFrames(t, rs)
	if len(frames) != 1 {
		t.Fatalf("expected one cleanup section, got %d", len(frames))
	}
	for _, want := range []string{
		"--- cleanup 1/2 exit_code=130 duration=",
		"--- cleanup 2/2 exit_code=none duration=0s ---",
	} {
		if !strings.Contains(frames[0].Output, want) {
			t.Errorf("cleanup section lacks %q:\n%s", want, frames[0].Output)
		}
	}
}
