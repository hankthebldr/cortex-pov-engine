//go:build !windows

package main

import (
	"context"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
)

// hammerReaper runs the production reaper loop AND a goroutine calling
// reapOnce back-to-back, so a blanket Wait4(-1) gets every chance to collect a
// child os/exec is about to Wait on. The returned stop waits for both to exit so
// no reaper outlives the test (later tests in this package re-exec the binary
// with untracked os/exec children).
func hammerReaper(t *testing.T) (stop func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	var wg sync.WaitGroup
	wg.Add(2)
	go func() { defer wg.Done(); childReaperLoop(ctx) }()
	go func() {
		defer wg.Done()
		for ctx.Err() == nil {
			reapOnce()
			runtime.Gosched()
		}
	}()
	return func() { cancel(); wg.Wait() }
}

// TestExitCodeSurvivesReaper_Shell is the race the task exists to close: a step
// run on the default per-step path while the reaper is live must still report
// its real exit status, not "wait: no child processes".
func TestExitCodeSurvivesReaper_Shell(t *testing.T) {
	stop := hammerReaper(t)
	defer stop()
	for i := 0; i < 50; i++ {
		_, _, rc, err := executor.RunCommandCtx(context.Background(), "exit 7")
		if err != nil || rc != 7 {
			t.Fatalf("iteration %d: rc=%d err=%v, want rc=7 err=nil (the reaper stole the step's exit status)", i, rc, err)
		}
	}
}

// TestExitCodeSurvivesReaper_Chained covers the causality-chained path, whose
// anchor shell is a long-lived direct child of the beacon that spans every step
// of a run. Close returns the anchor's own Wait result, so a reaper that
// collected the anchor surfaces here as a non-nil error.
func TestExitCodeSurvivesReaper_Chained(t *testing.T) {
	stop := hammerReaper(t)
	defer stop()
	for i := 0; i < 50; i++ {
		s, err := executor.NewChainSession(context.Background(), "")
		if err != nil {
			t.Fatalf("iteration %d: NewChainSession: %v", i, err)
		}
		_, _, rc, err := s.RunStep("exit 7")
		if err != nil || rc != 7 {
			_ = s.Close()
			t.Fatalf("iteration %d: chained rc=%d err=%v, want rc=7 err=nil", i, rc, err)
		}
		if cerr := s.Close(); cerr != nil {
			t.Fatalf("iteration %d: anchor Wait = %v, want nil (the reaper stole the anchor's exit status)", i, cerr)
		}
	}
}

// TestReapOnce_SkipsWhileChildActive: while the beacon holds a child it will
// Wait on (here a chain anchor, which lives across steps), reapOnce must not
// call Wait4 at all.
func TestReapOnce_SkipsWhileChildActive(t *testing.T) {
	calls := 0
	orig := wait4
	wait4 = func(pid int, ws *syscall.WaitStatus, opts int, ru *syscall.Rusage) (int, error) {
		calls++
		return 0, nil
	}
	defer func() { wait4 = orig }()

	s, err := executor.NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	if got := executor.ActiveChildren(); got < 1 {
		_ = s.Close()
		t.Fatalf("ActiveChildren() = %d with a live chain anchor, want >= 1", got)
	}
	if n := reapOnce(); n != 0 || calls != 0 {
		_ = s.Close()
		t.Fatalf("reapOnce with an active child = %d after %d Wait4 calls, want 0 and 0", n, calls)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := executor.ActiveChildren(); got != 0 {
		t.Fatalf("ActiveChildren() = %d after Close, want 0", got)
	}
	reapOnce()
	if calls == 0 {
		t.Fatal("reapOnce called Wait4 0 times once idle, want >= 1")
	}
}

// TestReapOnce_CollectsUntrackedZombie: a child nobody will Wait on (an orphan,
// from the gate's point of view) is collected and counted.
func TestReapOnce_CollectsUntrackedZombie(t *testing.T) {
	pid, err := syscall.ForkExec("/bin/sh", []string{"sh", "-c", "exit 0"}, &syscall.ProcAttr{})
	if err != nil {
		t.Fatalf("ForkExec: %v", err)
	}
	deadline := time.Now().Add(3 * time.Second)
	total := 0
	for total == 0 && time.Now().Before(deadline) {
		total += reapOnce()
		time.Sleep(10 * time.Millisecond)
	}
	if total != 1 {
		t.Fatalf("reapOnce collected %d, want 1 (pid %d)", total, pid)
	}
}

const reaperHelperEnv = "CORTEXSIM_TEST_REAPER_HELPER"

// TestReapOnce_CollectsReparentedOrphan runs in a re-exec'd helper that makes
// itself a child subreaper, runs a step that backgrounds a grandchild and exits,
// and reaps the grandchild once it reparents. linux only: PR_SET_CHILD_SUBREAPER
// does not exist elsewhere.
func TestReapOnce_CollectsReparentedOrphan(t *testing.T) {
	if os.Getenv(reaperHelperEnv) == "1" {
		if err := setChildSubreaper(); err != nil {
			os.Stdout.WriteString("SUBREAPER_ERR=" + err.Error() + "\n")
			os.Exit(3)
		}
		_, _, rc, err := executor.RunCommandCtx(context.Background(), "sleep 0.3 >/dev/null 2>&1 & exit 0")
		if err != nil || rc != 0 {
			os.Stdout.WriteString("STEP_ERR\n")
			os.Exit(4)
		}
		total := 0
		deadline := time.Now().Add(3 * time.Second)
		for total == 0 && time.Now().Before(deadline) {
			total += reapOnce()
			time.Sleep(20 * time.Millisecond)
		}
		if total == 1 {
			os.Stdout.WriteString("REAPED=1\n")
			os.Exit(0)
		}
		os.Stdout.WriteString("REAPED_WRONG\n")
		os.Exit(5)
	}
	if runtime.GOOS != "linux" {
		t.Skip("PR_SET_CHILD_SUBREAPER is linux-only; orphan reparenting cannot be exercised on " + runtime.GOOS)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestReapOnce_CollectsReparentedOrphan$")
	cmd.Env = append(os.Environ(), reaperHelperEnv+"=1")
	out, err := cmd.CombinedOutput()
	if err != nil || !strings.Contains(string(out), "REAPED=1") {
		t.Fatalf("helper: err=%v output=%s", err, out)
	}
}
