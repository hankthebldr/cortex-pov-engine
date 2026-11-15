//go:build windows

package executor

// Windows-native executor behaviour. These compile everywhere the Windows
// build does but RUN only on a Windows host — in this repo, the windows-native
// CI job. Nothing in this file has been observed passing on Windows by the
// author of the change that introduced it; it is the proof that job owes.

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// pidAlive reports whether pid names a process that has not exited.
func pidAlive(pid uint32) bool {
	h, err := syscall.OpenProcess(processSynchronize|processQueryLimitedInformation, false, pid)
	if err != nil {
		return false
	}
	defer syscall.CloseHandle(h)
	ev, err := syscall.WaitForSingleObject(h, 0)
	return err == nil && ev == syscall.WAIT_TIMEOUT
}

func waitForPIDFile(t *testing.T, path string, within time.Duration) uint32 {
	t.Helper()
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if b, err := os.ReadFile(path); err == nil {
			if v, err := strconv.ParseUint(strings.TrimSpace(strings.TrimPrefix(string(b), "\ufeff")), 10, 32); err == nil && v > 0 {
				return uint32(v)
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("no pid written to %s within %s", path, within)
	return 0
}

// startBystander starts a long-lived process the TEST owns — no step started
// it, so no abort may touch it.
func startBystander(t *testing.T) *exec.Cmd {
	t.Helper()
	c := exec.Command(powerShellExePath(), "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 300")
	if err := c.Start(); err != nil {
		t.Fatalf("start bystander: %v", err)
	}
	t.Cleanup(func() { _ = c.Process.Kill(); _ = c.Wait() })
	return c
}

// spawnGrandchildStep is a step that launches a detached grandchild, records
// its pid, then sleeps — the shape of a TTP whose tool outlives its shell.
func spawnGrandchildStep(pidFile string) string {
	return fmt.Sprintf("$p = Start-Process -FilePath %s -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 300' -PassThru -WindowStyle Hidden; "+
		"Set-Content -LiteralPath %s -Value $p.Id -Encoding Ascii; Start-Sleep -Seconds 300",
		psSingleQuote(powerShellExePath()), psSingleQuote(pidFile))
}

func waitDead(pid uint32, within time.Duration) bool {
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if !pidAlive(pid) {
			return true
		}
		time.Sleep(100 * time.Millisecond)
	}
	return !pidAlive(pid)
}

// BEACON-04: an abort reaches the step's WHOLE tree through its Job Object —
// including a grandchild the step's shell never waited on — and nothing else.
func TestRunShell_WindowsAbortKillsTheStepTreeAndSparesABystander(t *testing.T) {
	bystander := startBystander(t)
	pidFile := filepath.Join(t.TempDir(), "grandchild.pid")

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan Outcome, 1)
	go func() {
		o, _ := RunShellCtxStreamOutcome(ctx, "", spawnGrandchildStep(pidFile), nil)
		done <- o
	}()
	gc := waitForPIDFile(t, pidFile, 60*time.Second)
	t.Cleanup(func() { killPID(gc) })
	cancel()

	select {
	case o := <-done:
		if o.Termination != TerminationAborted || o.ExitCode != 130 {
			t.Errorf("aborted step reported %q/%d, want aborted/130", o.Termination, o.ExitCode)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("abort did not stop the step within 30s")
	}
	if !waitDead(gc, 10*time.Second) {
		t.Fatalf("grandchild %d survived the abort — the Job Object did not reach it", gc)
	}
	if !pidAlive(uint32(bystander.Process.Pid)) {
		t.Fatalf("the abort killed an UNRELATED process (pid %d)", bystander.Process.Pid)
	}
}

// The chained anchor's abort reaches a step's grandchild the same way.
func TestChainSession_WindowsAbortKillsTheStepTreeAndSparesABystander(t *testing.T) {
	bystander := startBystander(t)
	pidFile := filepath.Join(t.TempDir(), "grandchild.pid")

	ctx, cancel := context.WithCancel(context.Background())
	s, err := NewChainSession(ctx, "w3wp.exe")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	type res struct {
		rc  int
		err error
	}
	done := make(chan res, 1)
	go func() {
		_, _, rc, err := s.RunStepShell("step-01", "", spawnGrandchildStep(pidFile))
		done <- res{rc, err}
	}()
	gc := waitForPIDFile(t, pidFile, 60*time.Second)
	t.Cleanup(func() { killPID(gc) })
	cancel()

	select {
	case r := <-done:
		if r.rc != 130 || !errors.Is(r.err, context.Canceled) {
			t.Errorf("aborted chained step: rc=%d err=%v, want 130/context.Canceled", r.rc, r.err)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("abort did not stop the chained step within 30s")
	}
	if !waitDead(gc, 10*time.Second) {
		t.Fatalf("grandchild %d survived the chained abort", gc)
	}
	if !pidAlive(uint32(bystander.Process.Pid)) {
		t.Fatalf("the chained abort killed an UNRELATED process (pid %d)", bystander.Process.Pid)
	}
}

// BEACON-04, the Windows no-kill rule: a successful step's detached child
// survives a clean Close(), as it does in the per-step path (KILL_ON_JOB_CLOSE
// is deliberately not set). POSIX no longer shares this row — there a finished
// chain's process group is torn down (RG-AGENT-01, chained_teardown_unix_test.go)
// — and a chain that did NOT finish cleanly is Abandon()ed, which does terminate
// the job (chained_teardown_windows_test.go).
func TestChainSession_WindowsCloseDoesNotKillAfterTheAnchorExited(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	pidFile := filepath.Join(t.TempDir(), "bg.pid")
	step := fmt.Sprintf("$p = Start-Process -FilePath %s -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 300' -PassThru -WindowStyle Hidden; "+
		"Set-Content -LiteralPath %s -Value $p.Id -Encoding Ascii", psSingleQuote(powerShellExePath()), psSingleQuote(pidFile))
	if _, _, rc, err := s.RunStepShell("step-01", "", step); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	bg := waitForPIDFile(t, pidFile, 30*time.Second)
	t.Cleanup(func() { killPID(bg) })
	_ = s.Close()
	time.Sleep(killGrace + time.Second)
	if !pidAlive(bg) {
		t.Fatalf("Close() killed the detached child %d of a SUCCESSFUL step", bg)
	}
}

// PUSH-18: a cmd step runs under cmd.exe from a file, keeps cmd's exit code,
// sees the author's text verbatim, and leaves no temp file behind.
func TestRunShell_WindowsCmdStepRunsUnderCmd(t *testing.T) {
	// A private temp dir: other packages' tests run concurrently and also
	// write cortexsim-step-*.cmd files into the shared %TEMP%.
	tmp := t.TempDir()
	t.Setenv("TMP", tmp)
	t.Setenv("TEMP", tmp)
	before, _ := filepath.Glob(filepath.Join(os.TempDir(), "cortexsim-step-*.cmd"))
	o, err := RunShellCtxStreamOutcome(context.Background(), ShellCmd,
		"echo \"quoted & piped\"\necho %COMSPEC%\nexit /b 3", nil)
	if err != nil {
		t.Fatalf("cmd step: %v", err)
	}
	if o.ExitCode != 3 {
		t.Errorf("exit = %d, want cmd's 3", o.ExitCode)
	}
	if !strings.Contains(o.Stdout, `"quoted & piped"`) {
		t.Errorf("author text was not run verbatim under cmd: %q", o.Stdout)
	}
	if !strings.Contains(strings.ToLower(o.Stdout), `cmd.exe`) {
		t.Errorf("%%COMSPEC%% did not expand — not cmd.exe? stdout=%q", o.Stdout)
	}
	after, _ := filepath.Glob(filepath.Join(os.TempDir(), "cortexsim-step-*.cmd"))
	if len(after) > len(before) {
		t.Errorf("cmd step left a temp script behind: %v", after)
	}
}

// Twin of TestRunShell_POSIXRefusesAForeignShell.
func TestRunShell_WindowsRefusesAnUnknownShell(t *testing.T) {
	for _, shell := range []string{"bash", "sh", "zsh"} {
		if _, err := RunShellCtxStreamOutcome(context.Background(), shell, "echo x", nil); err == nil {
			t.Errorf("shell %q was accepted on windows", shell)
		}
	}
}

// A cmd step in the chained anchor is a child of the anchor and keeps cmd's rc.
func TestChainSession_WindowsCmdStepKeepsItsExitCode(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	out, _, rc, err := s.RunStepShell("step-01", ShellCmd, "echo cmd-step-ran\nexit /b 4")
	if err != nil || rc != 4 || !strings.Contains(out, "cmd-step-ran") {
		t.Fatalf("chained cmd step: out=%q rc=%d err=%v, want cmd-step-ran/4", out, rc, err)
	}
	// And a PowerShell step after it still runs in the same anchor.
	out, _, rc, err = s.RunStepShell("step-02", "", "Write-Output 'after-cmd'")
	if err != nil || rc != 0 || strings.TrimSpace(stripANSI(out)) != "after-cmd" {
		t.Fatalf("anchor did not survive a cmd step: out=%q rc=%d err=%v", out, rc, err)
	}
}

// longPowerShellStep is n characters of valid PowerShell that prints `done`:
// comment padding, then one statement.
func longPowerShellStep(n int) string {
	tail := "\nWrite-Output 'done'"
	return "#" + strings.Repeat("x", n-len(tail)-1) + tail
}

// BEACON-18: a step too long for CreateProcess is refused BEFORE any process is
// created, on both execution paths, with the cause named.
func TestRunShell_WindowsRefusesAStepTooLongForCreateProcess(t *testing.T) {
	long := longPowerShellStep(20000)

	o, err := RunShellCtxStreamOutcome(context.Background(), "", long, nil)
	if !errors.Is(err, ErrCommandLineTooLong) || o.ExitCode != -1 {
		t.Fatalf("per-step: outcome=%+v err=%v, want ErrCommandLineTooLong / -1", o, err)
	}

	s, serr := NewChainSession(context.Background(), "")
	if serr != nil {
		t.Fatalf("NewChainSession: %v", serr)
	}
	defer s.Close()
	_, _, rc, err := s.RunStepShell("step-01", "", long)
	if !errors.Is(err, ErrCommandLineTooLong) || rc != -1 {
		t.Fatalf("chained: rc=%d err=%v, want ErrCommandLineTooLong / -1", rc, err)
	}
	// The anchor was never handed the step, so it is still usable.
	out, _, rc, err := s.RunStepShell("step-02", "", "Write-Output 'after'")
	if err != nil || rc != 0 || strings.TrimSpace(stripANSI(out)) != "after" {
		t.Fatalf("anchor after a refused step: out=%q rc=%d err=%v", out, rc, err)
	}
}

// And the guard is not set absurdly low: a step of 10,000 characters — larger
// than anything in the corpus — really runs, on both paths. (If Windows itself
// rejects this line, the guard's arithmetic is wrong.)
func TestRunShell_WindowsRunsAStepJustUnderTheLimit(t *testing.T) {
	long := longPowerShellStep(10000)
	if err := checkPowerShellCommandLine(powerShellExePath(), long); err != nil {
		t.Fatalf("a 10,000-character step is refused by the guard: %v", err)
	}
	o, err := RunShellCtxStreamOutcome(context.Background(), "", long, nil)
	if err != nil || o.ExitCode != 0 || !strings.Contains(o.Stdout, "done") {
		t.Fatalf("per-step: outcome=%+v err=%v, want exit 0 and `done`", o, err)
	}
	s, serr := NewChainSession(context.Background(), "")
	if serr != nil {
		t.Fatalf("NewChainSession: %v", serr)
	}
	defer s.Close()
	out, _, rc, err := s.RunStepShell("step-01", "", long)
	if err != nil || rc != 0 || !strings.Contains(out, "done") {
		t.Fatalf("chained: out=%q rc=%d err=%v, want 0 and `done`", out, rc, err)
	}
}

// BEACON-12: the interpreter really is the %SystemRoot% image.
func TestPowerShellExePath_IsTheSystemRootImage(t *testing.T) {
	p := powerShellExePath()
	if !filepath.IsAbs(p) {
		t.Fatalf("powerShellExePath() = %q is not absolute", p)
	}
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("powerShellExePath() = %q does not exist: %v", p, err)
	}
	if _, err := os.Stat(cmdExePath()); err != nil {
		t.Fatalf("cmdExePath() = %q does not exist: %v", cmdExePath(), err)
	}
}

func killPID(pid uint32) {
	if h, err := syscall.OpenProcess(processTerminate, false, pid); err == nil {
		_ = syscall.TerminateProcess(h, 1)
		_ = syscall.CloseHandle(h)
	}
}
