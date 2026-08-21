package executor

// The Windows executor's riskiest logic is not its Go — it is the PowerShell it
// GENERATES. String assertions prove the shape; only an interpreter proves the
// semantics, and a dialect bug here surfaces on a customer host as "the TTP
// failed" rather than "the agent is broken".
//
// These tests run against whatever PowerShell is on PATH (pwsh on Linux/macOS
// and on GitHub's ubuntu runners, powershell.exe on Windows) and skip when none
// is. PowerShell 7 and Windows PowerShell 5.1 agree on every construct used
// here — $LASTEXITCODE, $?, exit, [Console]::Out/Error — so exercising pwsh is
// a real check of the 5.1 path, not a proxy for it.

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

// findPowerShell returns a usable PowerShell executable, or "" if none exists.
func findPowerShell() string {
	for _, candidate := range []string{powerShellExe, "pwsh"} {
		if p, err := exec.LookPath(candidate); err == nil {
			return p
		}
	}
	return ""
}

// runEncoded executes a command through the exact -EncodedCommand path
// newShellCmd uses on Windows, and returns its stdout and process exit code.
func runEncoded(t *testing.T, psExe, command string) (stdout string, exitCode int) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	args := append(append([]string{}, powerShellArgs...),
		"-EncodedCommand", encodePowerShellCommand(powerShellExitWrapper(command)))
	cmd := exec.CommandContext(ctx, psExe, args...)

	var out strings.Builder
	cmd.Stdout = &out
	err := cmd.Run()
	if exitErr, ok := err.(*exec.ExitError); ok {
		return out.String(), exitErr.ExitCode()
	}
	if err != nil {
		t.Fatalf("could not run %s: %v", psExe, err)
	}
	return out.String(), 0
}

// The exit-code contract is what every fail-fast, Result-row and push/pull
// parity guarantee in the engine rests on. Left unwrapped,
// `powershell -Command <failing thing>` exits 0 and a broken TTP reports as a
// step that PASSED — the exact false-green this engine exists to eliminate.
func TestPowerShellExitWrapper_RealInterpreterHonoursExitCodes(t *testing.T) {
	psExe := findPowerShell()
	if psExe == "" {
		t.Skip("no PowerShell on PATH")
	}

	t.Run("success is 0 and stdout is captured", func(t *testing.T) {
		out, code := runEncoded(t, psExe, "Write-Output 'cortexsim-ok'")
		if code != 0 {
			t.Errorf("exit = %d, want 0", code)
		}
		if !strings.Contains(out, "cortexsim-ok") {
			t.Errorf("stdout = %q, want it to contain cortexsim-ok", out)
		}
	})

	t.Run("explicit exit code propagates", func(t *testing.T) {
		if _, code := runEncoded(t, psExe, "cmd_that_sets_code\nexit 42"); code != 42 {
			t.Errorf("exit = %d, want 42", code)
		}
	})

	t.Run("a failing native process fails the step", func(t *testing.T) {
		// A native process exiting non-zero sets $LASTEXITCODE but does NOT change
		// the interpreter's own exit code. Without the wrapper this returns 0.
		_, code := runEncoded(t, psExe,
			"& "+psExe+" -NoProfile -NonInteractive -Command 'exit 17'")
		if code != 17 {
			t.Errorf("exit = %d, want 17 — a failing child process was reported as success", code)
		}
	})

	t.Run("an unresolvable command fails the step", func(t *testing.T) {
		// A missing binary is a non-terminating error under
		// ErrorActionPreference=Continue: it only clears $?, which is why the
		// wrapper snapshots it.
		if _, code := runEncoded(t, psExe, "cortexsim-no-such-binary-xyz"); code == 0 {
			t.Errorf("a missing command reported exit 0 — a no-op TTP would read as a pass")
		}
	})

	t.Run("a mid-step failure does not abandon later commands", func(t *testing.T) {
		// sh -c semantics: the step keeps going. ErrorActionPreference=Stop would
		// silently truncate multi-command steps.
		out, _ := runEncoded(t, psExe, "cortexsim-no-such-binary-xyz\nWrite-Output 'still-running'")
		if !strings.Contains(out, "still-running") {
			t.Errorf("later commands were abandoned after a non-terminating error: %q", out)
		}
	})

	t.Run("hostile quoting survives encoding", func(t *testing.T) {
		// A PowerShell SINGLE-quoted literal, so what reaches the interpreter is
		// asserted rather than PowerShell's own escaping rules (inside a
		// double-quoted string a backtick IS the escape character, so `c would
		// legitimately render as "c"). The payload carries the characters that
		// break every quoting layer between here and CreateProcess.
		out, code := runEncoded(t, psExe, "Write-Output 'a\"b`c $notavar & | > %PATH%'")
		if code != 0 {
			t.Errorf("exit = %d, want 0", code)
		}
		if !strings.Contains(out, "a\"b`c $notavar & | > %PATH%") {
			t.Errorf("quoting was mangled: %q", out)
		}
	})
}

// The full anchor path: PowerShell reading statements from stdin, dispatching
// each step as a CHILD process, and emitting the sentinels the beacon parses.
// This is what ChainSession drives on Windows, reconstructed here because
// NewChainSession compiles to the POSIX anchor on this host.
func TestPowerShellChainStepScript_RealInterpreterRoundTrip(t *testing.T) {
	psExe := findPowerShell()
	if psExe == "" {
		t.Skip("no PowerShell on PATH")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	anchorArgs := append(append([]string{}, powerShellArgs...), "-Command", "-")
	anchor := exec.CommandContext(ctx, psExe, anchorArgs...)
	// Mirror newAnchorCmd's env: TERM=dumb keeps the host's prompt loop from
	// injecting terminal control sequences into the evidence stream.
	anchor.Env = append(os.Environ(), "TERM=dumb")
	stdin, err := anchor.StdinPipe()
	if err != nil {
		t.Fatalf("stdin: %v", err)
	}
	stdoutPipe, err := anchor.StdoutPipe()
	if err != nil {
		t.Fatalf("stdout: %v", err)
	}
	stderrPipe, err := anchor.StderrPipe()
	if err != nil {
		t.Fatalf("stderr: %v", err)
	}
	if err := anchor.Start(); err != nil {
		t.Fatalf("start anchor: %v", err)
	}
	defer func() { _ = anchor.Wait() }()

	// Scan exactly as chained.go does, INCLUDING trimEOL — that is the assertion
	// that PowerShell's CRLF line endings do not break sentinel matching.
	lines := func(r io.Reader) <-chan string {
		ch := make(chan string, 256)
		go func() {
			defer close(ch)
			sc := bufio.NewScanner(r)
			for sc.Scan() {
				ch <- trimEOL(sc.Text())
			}
		}()
		return ch
	}
	outCh, errCh := lines(stdoutPipe), lines(stderrPipe)

	const marker = "__CORTEXSIM_STEP_TEST__"
	// powerShellExe is a Windows literal; on this host point it at the real binary.
	writeStep := func(cmd string) {
		script := strings.ReplaceAll(powerShellChainStepScript(marker, cmd), powerShellExe, psExe)
		if _, err := io.WriteString(stdin, script); err != nil {
			t.Fatalf("write step: %v", err)
		}
	}

	// collect reads until the sentinel, mirroring ChainSession.RunStep.
	collectOut := func() (string, int) {
		var body []string
		for {
			select {
			case line, ok := <-outCh:
				if !ok {
					t.Fatalf("stdout closed before the sentinel; got %q", strings.Join(body, "\n"))
				}
				// stripANSI is the PRODUCTION matcher (chained.go). Using it here is
				// the point: it proves a host that decorates the stream with terminal
				// control sequences cannot hide a sentinel and hang the step.
				if clean := stripANSI(line); strings.HasPrefix(clean, marker+" ") {
					rc := 0
					_, _ = fmt.Sscan(strings.TrimSpace(strings.TrimPrefix(clean, marker+" ")), &rc)
					return strings.Join(body, "\n"), rc
				}
				body = append(body, line)
			case <-time.After(60 * time.Second):
				t.Fatalf("timed out waiting for the stdout sentinel; got %q", strings.Join(body, "\n"))
			}
		}
	}
	drainErr := func() string {
		var body []string
		for {
			select {
			case line, ok := <-errCh:
				if !ok {
					t.Fatal("stderr closed before the sentinel")
				}
				if stripANSI(line) == marker {
					return strings.Join(body, "\n")
				}
				body = append(body, line)
			case <-time.After(60 * time.Second):
				t.Fatalf("timed out waiting for the stderr sentinel (CRLF not trimmed?); got %q",
					strings.Join(body, "\n"))
			}
		}
	}

	// Step 1: stdout + stderr + exit 0, correctly delimited.
	writeStep("Write-Output 'hello'; [Console]::Error.WriteLine('oops')")
	out, rc := collectOut()
	stderrBody := drainErr()
	if rc != 0 {
		t.Errorf("step1 rc = %d, want 0", rc)
	}
	if strings.TrimSpace(stripANSI(out)) != "hello" {
		t.Errorf("step1 stdout = %q, want hello", out)
	}
	if strings.TrimSpace(stripANSI(stderrBody)) != "oops" {
		t.Errorf("step1 stderr = %q, want oops", stderrBody)
	}

	// Step 2: a non-zero exit is captured as an rc, not an error, and the anchor
	// SURVIVES it — a step calling `exit` must not tear the session down, or every
	// subsequent step in the scenario is silently lost.
	writeStep("exit 7")
	_, rc = collectOut()
	_ = drainErr()
	if rc != 7 {
		t.Errorf("step2 rc = %d, want 7", rc)
	}

	writeStep("Write-Output 'survived'")
	out, rc = collectOut()
	_ = drainErr()
	if rc != 0 || strings.TrimSpace(stripANSI(out)) != "survived" {
		t.Errorf("anchor did not survive a step's exit: out=%q rc=%d", out, rc)
	}

	// Step 4: no cross-step state leak — parity with the POSIX subshell path.
	writeStep("$env:CORTEXSIM_LEAK = 'should-not-persist'")
	_, _ = collectOut()
	_ = drainErr()
	writeStep("if ($env:CORTEXSIM_LEAK) { Write-Output $env:CORTEXSIM_LEAK } else { Write-Output 'unset' }")
	out, _ = collectOut()
	_ = drainErr()
	if strings.TrimSpace(stripANSI(out)) != "unset" {
		t.Errorf("cross-step state leaked: %q", out)
	}

	_, _ = io.WriteString(stdin, anchorExitScript)
	_ = stdin.Close()
}
