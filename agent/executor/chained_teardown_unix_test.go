//go:build !windows

package executor

// RG-AGENT-01 / RG-AGENT-02 (POSIX). Pull mode never runs a scenario's
// cleanup.commands, so the process-group signal a chained run fires when it
// ends is the ONLY teardown its background helpers get. c1cc90c removed it
// (Close stopped signalling once the anchor had exited), and a helper left by
// run N then served run N+1: SIM-AIRS-001..005 each background
// `cortex-vulnerable-llm serve --port 8089 --vuln <one> &` and only probe
// /healthz, so the next AIRS run attacked the previous run's server and still
// completed SUCCESS.
//
// The Windows twins are in chained_teardown_windows_test.go.

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// readPID reads a pid a step wrote with `echo $! > file`.
func readPID(t *testing.T, path string) int {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read pid file: %v", err)
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || pid <= 0 {
		t.Fatalf("bad pid %q in %s", b, path)
	}
	return pid
}

// goneWithin reports whether pid stops existing within d. A zombie still
// answers kill(pid, 0), so the pids these tests watch are children of the
// (exited) anchor: once dead they are reparented and reaped by init.
func goneWithin(pid int, d time.Duration) bool {
	deadline := time.Now().Add(d)
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

// A finished chain leaves nothing running: the background child of a step that
// SUCCEEDED is gone once Close returns.
func TestChainSession_CloseTearsDownBackgroundChildrenOfAFinishedChain(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	pidFile := filepath.Join(t.TempDir(), "bg.pid")
	if _, _, rc, err := s.RunStep("sleep 300 >/dev/null 2>&1 & echo $! > " + pidFile); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	bg := readPID(t, pidFile)
	t.Cleanup(func() { _ = syscall.Kill(bg, syscall.SIGKILL) })

	if err := s.Close(); err != nil {
		t.Fatalf("Close() = %v, want nil: the anchor must still exit cleanly", err)
	}
	if !goneWithin(bg, 2*time.Second) {
		t.Fatalf("background child %d of a finished chain is still running after Close() — "+
			"pull mode runs no cleanup, so this helper would serve the NEXT run", bg)
	}
}

// A helper that ignores SIGTERM is SIGKILLed once killGrace has passed, and
// Close does not return before it is gone.
func TestChainSession_CloseKillsASurvivorThatIgnoresSIGTERM(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	pidFile := filepath.Join(t.TempDir(), "bg.pid")
	// An ignored SIGTERM is inherited across exec, so `sleep` ignores it too.
	step := "(trap '' TERM; exec sleep 300) >/dev/null 2>&1 & echo $! > " + pidFile
	if _, _, rc, err := s.RunStep(step); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	bg := readPID(t, pidFile)
	t.Cleanup(func() { _ = syscall.Kill(bg, syscall.SIGKILL) })

	start := time.Now()
	_ = s.Close()
	if !goneWithin(bg, time.Second) {
		t.Fatalf("SIGTERM-ignoring child %d survived Close() (returned after %s) — the SIGKILL leg did not run",
			bg, time.Since(start).Round(time.Millisecond))
	}
	if took := time.Since(start); took < killGrace {
		t.Fatalf("child died after %s, before killGrace (%s): it was not given the grace period", took, killGrace)
	}
}

// recordKills swaps the kill seam for one that records every (pid, signal) and
// still delivers it.
func recordKills(t *testing.T) func() [][2]int {
	t.Helper()
	var mu sync.Mutex
	var seen [][2]int
	old := sysKill
	sysKill = func(pid int, sig syscall.Signal) error {
		mu.Lock()
		seen = append(seen, [2]int{pid, int(sig)})
		mu.Unlock()
		return old(pid, sig)
	}
	t.Cleanup(func() { sysKill = old })
	return func() [][2]int {
		mu.Lock()
		defer mu.Unlock()
		return append([][2]int(nil), seen...)
	}
}

// The pid-reuse guard. A chain that left NOTHING running has an empty process
// group once its anchor is reaped, and an empty group's number is free for the
// kernel to hand to an unrelated process. Close must only PROBE it (signal 0):
// base sent SIGTERM and, three seconds later, SIGKILL to that number
// unconditionally.
func TestChainSession_CloseNeverSignalsAnEmptyGroup(t *testing.T) {
	kills := recordKills(t)
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	if _, _, rc, err := s.RunStep("echo nothing-left-behind"); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	start := time.Now()
	_ = s.Close()
	if took := time.Since(start); took > time.Second {
		t.Errorf("Close() of a chain with no survivors took %s — it must not wait out the grace period", took)
	}
	if got := s.Teardown(); got != "" {
		t.Errorf("Teardown() = %q for a chain that left nothing running, want \"\"", got)
	}
	// The teardown is synchronous: everything it will ever send has been sent.
	probes := 0
	for _, k := range kills() {
		if k[1] != 0 {
			t.Fatalf("Close() sent signal %d to %d although the group was empty — that number may already "+
				"belong to an unrelated process", k[1], k[0])
		}
		probes++
	}
	if probes == 0 {
		t.Fatal("Close() never probed the group: the teardown did not run at all")
	}
}

// Teardown says what was done, so the beacon can log it.
func TestChainSession_TeardownReportsWhatWasStopped(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	if got := s.Teardown(); got != "" {
		t.Fatalf("Teardown() on an open session = %q, want \"\"", got)
	}
	if _, _, rc, err := s.RunStep("sleep 300 >/dev/null 2>&1 &"); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	_ = s.Close()
	if got := s.Teardown(); got != teardownTERM {
		t.Fatalf("Teardown() = %q, want %q", got, teardownTERM)
	}
}

// Abandon (a chain that did not finish cleanly) tears the group down as well,
// and a second Close after it is a no-op.
func TestChainSession_AbandonTearsDownBackgroundChildren(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	pidFile := filepath.Join(t.TempDir(), "bg.pid")
	if _, _, rc, err := s.RunStep("sleep 300 >/dev/null 2>&1 & echo $! > " + pidFile); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	bg := readPID(t, pidFile)
	t.Cleanup(func() { _ = syscall.Kill(bg, syscall.SIGKILL) })

	_ = s.Abandon()
	if !goneWithin(bg, 2*time.Second) {
		t.Fatalf("background child %d survived Abandon()", bg)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close() after Abandon() = %v, want a no-op nil", err)
	}
}

// The kill is the anchor's process GROUP and nothing else: a process the test
// owns, in another group, is untouched.
func TestChainSession_CloseSparesAProcessOutsideTheGroup(t *testing.T) {
	bystander := newShellCmd("sleep 300")
	if err := bystander.Start(); err != nil {
		t.Fatalf("start bystander: %v", err)
	}
	t.Cleanup(func() { _ = syscall.Kill(-bystander.Process.Pid, syscall.SIGKILL); _ = bystander.Wait() })

	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	if _, _, rc, err := s.RunStep("sleep 300 >/dev/null 2>&1 &"); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	_ = s.Close()
	if err := syscall.Kill(bystander.Process.Pid, 0); err != nil {
		t.Fatalf("Close() reached a process outside the anchor's group: %v", err)
	}
}
