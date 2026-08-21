package executor

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"
)

// A chained session runs each step as a child of ONE persistent anchor shell,
// with per-step stdout/stderr and exit codes correctly delimited.
func TestChainSession_PerStepCapture(t *testing.T) {
	s, err := NewChainSession(context.Background(), "nginx")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	// Step 1: stdout + stderr + exit 0.
	out, errOut, rc, err := s.RunStep("echo hello; echo oops 1>&2")
	if err != nil {
		t.Fatalf("step1 err: %v", err)
	}
	if rc != 0 {
		t.Fatalf("step1 rc = %d, want 0", rc)
	}
	if strings.TrimSpace(out) != "hello" {
		t.Fatalf("step1 stdout = %q, want hello", out)
	}
	if strings.TrimSpace(errOut) != "oops" {
		t.Fatalf("step1 stderr = %q, want oops", errOut)
	}

	// Step 2: non-zero exit is captured, not treated as an error.
	_, _, rc, err = s.RunStep("exit 7")
	if err != nil {
		t.Fatalf("step2 err: %v", err)
	}
	if rc != 7 {
		t.Fatalf("step2 rc = %d, want 7", rc)
	}

	// Step 3: a step that calls `exit` must NOT tear down the anchor — it runs
	// in a subshell, so the session survives and later steps still execute.
	_, _, rc, err = s.RunStep("echo before-exit; exit 3")
	if err != nil {
		t.Fatalf("step3 err: %v", err)
	}
	if rc != 3 {
		t.Fatalf("step3 rc = %d, want 3 (subshell exit)", rc)
	}
	out, _, rc, err = s.RunStep("echo survived")
	if err != nil {
		t.Fatalf("step4 err: %v (anchor died after a step called exit)", err)
	}
	if rc != 0 || strings.TrimSpace(out) != "survived" {
		t.Fatalf("anchor did not survive a step's exit: out=%q rc=%d", out, rc)
	}

	// And there is NO cross-step state leak (parity with independent `sh -c`).
	_, _, _, _ = s.RunStep("LEAK=should-not-persist")
	out, _, _, err = s.RunStep("echo \"[${LEAK:-unset}]\"")
	if err != nil {
		t.Fatalf("step6 err: %v", err)
	}
	if strings.TrimSpace(out) != "[unset]" {
		t.Fatalf("cross-step state leaked: %q, want [unset]", out)
	}
}

// Every step is a descendant of the SAME anchor process — a connected tree, not
// siblings. We prove it by comparing each step's parent PID: they must match,
// and match the anchor, across steps.
func TestChainSession_StepsShareOneParent(t *testing.T) {
	s, err := NewChainSession(context.Background(), "apache2")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	ppid1, _, _, err := s.RunStep("echo $PPID")
	if err != nil {
		t.Fatalf("step1: %v", err)
	}
	ppid2, _, _, err := s.RunStep("echo $PPID")
	if err != nil {
		t.Fatalf("step2: %v", err)
	}
	// $PPID inside the anchor is the anchor's parent; the point is it is STABLE
	// across steps (same shell), whereas independent `sh -c` invocations would
	// each have a different shell. Also assert the anchor pid itself is stable.
	p1, p2 := strings.TrimSpace(ppid1), strings.TrimSpace(ppid2)
	if p1 != p2 {
		t.Fatalf("PPID changed across steps (%q vs %q) — not one anchor", p1, p2)
	}
	self1, _, _, _ := s.RunStep("echo $$")
	self2, _, _, _ := s.RunStep("echo $$")
	if strings.TrimSpace(self1) != strings.TrimSpace(self2) {
		t.Fatalf("anchor $$ changed (%q vs %q) — steps are not children of one shell",
			strings.TrimSpace(self1), strings.TrimSpace(self2))
	}
	// And the anchor is a distinct shell from this test process.
	if strings.TrimSpace(self1) == "" || strings.TrimSpace(self1) == string(rune(os.Getpid())) {
		t.Fatalf("anchor pid looks wrong: %q", self1)
	}
}

// A cancelled context tears the whole anchor group down; an in-flight step
// returns the abort convention (130, context.Canceled).
func TestChainSession_AbortMidStep(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	s, err := NewChainSession(ctx, "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	go func() {
		time.Sleep(150 * time.Millisecond)
		cancel()
	}()

	// A long sleep that must be interrupted by the cancel.
	start := time.Now()
	_, _, rc, err := s.RunStep("sleep 10")
	if time.Since(start) > 5*time.Second {
		t.Fatalf("abort did not interrupt the step in time")
	}
	if rc != 130 || err != context.Canceled {
		t.Fatalf("abort: rc=%d err=%v, want 130/context.Canceled", rc, err)
	}
}

// Multi-line commands (YAML block scalars in real scenarios) execute correctly
// and their exit code is the last command's.
func TestChainSession_MultiLineCommand(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	cmd := "a=1\nb=2\necho $((a+b))\ntrue"
	out, _, rc, err := s.RunStep(cmd)
	if err != nil {
		t.Fatalf("err: %v", err)
	}
	if rc != 0 {
		t.Fatalf("rc = %d, want 0", rc)
	}
	if strings.TrimSpace(out) != "3" {
		t.Fatalf("stdout = %q, want 3", out)
	}
}
