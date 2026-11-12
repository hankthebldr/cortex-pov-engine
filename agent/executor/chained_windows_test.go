//go:build windows

package executor

// PowerShell-dialect twins of chained_unix_test.go (CI-04). The contracts are
// the same — per-step delimiting, exit codes, the anchor surviving a step's
// `exit`, no cross-step state, one parent for every step, abort mid-step,
// multi-line steps, anchor death mid-step — but the text is what a Windows
// anchor actually runs. Run only by the windows-native CI job; none of these
// has been observed passing on Windows by the change that introduced it.

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"testing"
	"time"
)

func psClean(s string) string { return strings.TrimSpace(stripANSI(s)) }

func TestChainSession_WindowsPerStepCapture(t *testing.T) {
	s, err := NewChainSession(context.Background(), "nginx")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	out, errOut, rc, err := s.RunStep("Write-Output 'hello'; [Console]::Error.WriteLine('oops')")
	if err != nil || rc != 0 || psClean(out) != "hello" || psClean(errOut) != "oops" {
		t.Fatalf("step1: out=%q err-out=%q rc=%d err=%v, want hello/oops/0", out, errOut, rc, err)
	}

	if _, _, rc, err = s.RunStep("exit 7"); err != nil || rc != 7 {
		t.Fatalf("step2 (exit 7): rc=%d err=%v", rc, err)
	}
	// A native exit code is the step's exit code too.
	if _, _, rc, err = s.RunStep("cmd /c exit 3"); err != nil || rc != 3 {
		t.Fatalf("step3 (cmd /c exit 3): rc=%d err=%v", rc, err)
	}

	out, _, rc, err = s.RunStep("Write-Output 'survived'")
	if err != nil || rc != 0 || psClean(out) != "survived" {
		t.Fatalf("anchor did not survive a step's exit: out=%q rc=%d err=%v", out, rc, err)
	}

	_, _, _, _ = s.RunStep("$env:CORTEXSIM_LEAK = 'should-not-persist'; $global:leak = 'x'")
	out, _, _, err = s.RunStep("if ($env:CORTEXSIM_LEAK -or $global:leak) { Write-Output 'leaked' } else { Write-Output 'unset' }")
	if err != nil || psClean(out) != "unset" {
		t.Fatalf("cross-step state leaked: %q err=%v", out, err)
	}
}

// Every step is a child of the SAME anchor: each step's parent pid is the
// anchor's pid, stable across steps.
func TestChainSession_WindowsStepsShareOneParent(t *testing.T) {
	s, err := NewChainSession(context.Background(), "w3wp.exe")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	parent := `(Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId`
	p1, _, _, err := s.RunStep("Write-Output " + parent)
	if err != nil {
		t.Fatalf("step1: %v", err)
	}
	p2, _, _, err := s.RunStep("Write-Output " + parent)
	if err != nil {
		t.Fatalf("step2: %v", err)
	}
	if psClean(p1) == "" || psClean(p1) != psClean(p2) {
		t.Fatalf("steps do not share one parent: %q vs %q", psClean(p1), psClean(p2))
	}
	if want := s.cmd.Process.Pid; psClean(p1) != strconv.Itoa(want) {
		t.Fatalf("step parent = %s, want the anchor pid %d", psClean(p1), want)
	}
	self1, _, _, _ := s.RunStep("Write-Output $PID")
	self2, _, _, _ := s.RunStep("Write-Output $PID")
	if psClean(self1) == psClean(self2) {
		t.Fatalf("two steps ran in the SAME process (%s) — a step must be its own child process", psClean(self1))
	}
}

func TestChainSession_WindowsAbortMidStep(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	s, err := NewChainSession(ctx, "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	go func() {
		time.Sleep(2 * time.Second) // let the nested powershell start
		cancel()
	}()
	start := time.Now()
	_, _, rc, err := s.RunStep("Start-Sleep -Seconds 60")
	if time.Since(start) > 20*time.Second {
		t.Fatalf("abort did not interrupt the step in time")
	}
	if rc != 130 || !errors.Is(err, context.Canceled) {
		t.Fatalf("abort: rc=%d err=%v, want 130/context.Canceled", rc, err)
	}
}

func TestChainSession_WindowsMultiLineCommand(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	out, _, rc, err := s.RunStep("$a = 1\n$b = 2\nWrite-Output ($a + $b)\nif ($true) {\n  Write-Output 'block'\n}")
	if err != nil || rc != 0 || !strings.Contains(out, "3") || !strings.Contains(out, "block") {
		t.Fatalf("multi-line: out=%q rc=%d err=%v", out, rc, err)
	}
}

// The step kills its own anchor (its parent process). The sentinel never
// arrives; RunStep must report ErrAnchorTerminated / -1, never exit 0.
func TestChainSession_WindowsAnchorDiesMidStep(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	_, _, rc, err := s.RunStep(`Stop-Process -Force -Id (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId; Start-Sleep -Seconds 30`)
	if rc != -1 || !errors.Is(err, ErrAnchorTerminated) {
		t.Fatalf("anchor died mid-step: rc=%d err=%v, want -1/ErrAnchorTerminated", rc, err)
	}
}

// The chained seam on the real interpreter: a failing cmdlet inside a chain
// step reports its error as text. Windows PowerShell 5.1 hands the anchor's
// pipe CLIXML whatever -OutputFormat says; RunStep decodes it (clixml.go).
func TestChainSession_WindowsCmdletErrorIsTextNotCLIXML(t *testing.T) {
	s, err := NewChainSession(context.Background(), "nginx")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	_, errOut, rc, err := s.RunStep("Write-Error 'cortexsim-boom'")
	if err != nil || rc != 1 {
		t.Fatalf("rc=%d err=%v, want 1/nil (a failing cmdlet as the last statement)", rc, err)
	}
	if strings.Contains(errOut, "#< CLIXML") || strings.Contains(errOut, "<Objs ") {
		t.Fatalf("stderr is CLIXML, not text:\n%s", errOut)
	}
	if !strings.Contains(errOut, "cortexsim-boom") {
		t.Fatalf("stderr does not carry the error text: %q", errOut)
	}

	// The next step starts clean: nothing of the previous step's envelope leaks.
	_, errOut, rc, err = s.RunStep("Write-Output 'after'")
	if err != nil || rc != 0 || psClean(errOut) != "" {
		t.Fatalf("step after the error: err-out=%q rc=%d err=%v, want empty/0", errOut, rc, err)
	}
}
