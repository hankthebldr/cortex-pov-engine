package executor

// The Windows executor's riskiest logic is not its Go — it is the PowerShell it
// GENERATES. String assertions prove the shape; only an interpreter proves the
// semantics, and a dialect bug here surfaces on a customer host as "the TTP
// failed" rather than "the agent is broken".
//
// These tests run against whatever PowerShell is on PATH (pwsh on Linux/macOS
// and on GitHub's ubuntu runners, powershell.exe on Windows). They skip when
// none is — unless CORTEXSIM_REQUIRE_POWERSHELL=1, which turns the skip into a
// failure (requirePowerShell), so a CI job that is MEANT to run them cannot go
// green by having no interpreter.
//
// What pwsh 7 proves here, and what it does not (CI-14). pwsh exercises the
// -EncodedCommand encoding, the sentinel framing, the anchor's statement-by-
// statement stdin loop and the exit-wrapper's SHAPE. It is NOT Windows
// PowerShell 5.1 — the product floor — and the two diverge on exactly the
// semantics this file cares about:
//
//   - native stderr and $?: 5.1 wraps a native tool's redirected stderr in
//     NativeCommandError records and clears $? even at exit 0; pwsh 7.2+ does
//     not. Truth-table row (iv) therefore passes on pwsh with or without the fix.
//   - `&&` / `||` pipeline chain operators exist only in pwsh 7; in 5.1 they
//     are parse errors.
//   - encoding: 5.1 writes redirected output in the OEM code page and reads a
//     BOM-less script file as ANSI; pwsh 7 is UTF-8 throughout. The non-ASCII
//     round trip therefore also passes on pwsh with or without the fix.
//
// The 5.1 proof is the windows-native-ci job (windows-latest, powershell.exe
// 5.1) running this package with CORTEXSIM_REQUIRE_POWERSHELL=1. Until that job
// has run green, nothing here is proven on Windows.

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
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

// requirePowerShell returns a PowerShell executable or stops the test: Skip
// normally, Fatal when CORTEXSIM_REQUIRE_POWERSHELL=1 (CI-08).
func requirePowerShell(t *testing.T) string {
	t.Helper()
	if p := findPowerShell(); p != "" {
		return p
	}
	if os.Getenv("CORTEXSIM_REQUIRE_POWERSHELL") == "1" {
		t.Fatal("CORTEXSIM_REQUIRE_POWERSHELL=1 but no PowerShell (powershell.exe or pwsh) is on PATH — " +
			"this job exists to run these tests, so a missing interpreter is a failure, not a skip")
	}
	t.Skip("no PowerShell on PATH")
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

// runEncodedFull is runEncoded that also returns stderr and the raw stdout
// bytes, for the assertions that are about BYTES (encoding, CLIXML framing).
func runEncodedFull(t *testing.T, psExe, command string) (stdout []byte, stderr string, exitCode int) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	args := append(append([]string{}, powerShellArgs...),
		"-EncodedCommand", encodePowerShellCommand(powerShellExitWrapper(command)))
	cmd := exec.CommandContext(ctx, psExe, args...)

	var out, errOut bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errOut
	err := cmd.Run()
	if exitErr, ok := err.(*exec.ExitError); ok {
		return out.Bytes(), errOut.String(), exitErr.ExitCode()
	}
	if err != nil {
		t.Fatalf("could not run %s: %v", psExe, err)
	}
	return out.Bytes(), errOut.String(), 0
}

// THE CANONICAL TRUTH TABLE. The push-bundle harness asserts the same four rows
// for .ps1 bundles, so pull and push agree on what a PowerShell step's exit code
// means. Row (iv) is the Windows PowerShell 5.1 divergence: 5.1 turns a native
// tool's redirected stderr into NativeCommandError records and clears $? even
// at exit 0. pwsh 7.2+ no longer does, so on this host row (iv) passes with or
// without the fix — only the windows-native-ci job (powershell.exe 5.1) proves it.
func TestPowerShellExitWrapper_CanonicalTruthTable(t *testing.T) {
	psExe := requirePowerShell(t)
	native := "& '" + psExe + "' -NoProfile -NonInteractive -Command "
	rows := []struct {
		name, command string
		want          int
	}{
		{"(i) Write-Output is 0", "Write-Output ok", 0},
		{"(ii) last statement a native exiting 3 is 3", native + "'exit 3'", 3},
		{"(iii) last statement a failing cmdlet is 1", "Get-Item 'C:\\cortexsim\\no\\such\\path-xyz'", 1},
		{"(iv) a native writing stderr at exit 0 is 0",
			native + "\"[Console]::Error.WriteLine('warn'); exit 0\" 2>&1", 0},
	}
	for _, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			if _, _, code := runEncodedFull(t, psExe, r.command); code != r.want {
				t.Errorf("exit = %d, want %d", code, r.want)
			}
		})
	}
}

// BEACON-10. TTP output is evidence stored byte-for-byte; a non-ASCII byte must
// reach SimCore as UTF-8. pwsh 7 is UTF-8 by default, so this passes here on
// either side of the fix; the windows-native-ci job is where the OEM code page
// of 5.1 would make it red.
func TestPowerShellExitWrapper_NonASCIIOutputIsUTF8(t *testing.T) {
	psExe := requirePowerShell(t)
	out, _, code := runEncodedFull(t, psExe, "Write-Output 'é'")
	if code != 0 {
		t.Fatalf("exit = %d, want 0", code)
	}
	if !bytes.Contains(out, []byte{0xC3, 0xA9}) {
		t.Fatalf("stdout = % x, want the UTF-8 bytes c3 a9 for 'é'", out)
	}
}

// BEACON-06. An error record must reach the run record as text. Without
// -OutputFormat Text, a powershell whose stderr is a pipe frames it as
// `#< CLIXML <Objs ...>` — observed on pwsh 7.6 and on Windows PowerShell 5.1.
func TestPowerShellExitWrapper_StderrIsTextNotCLIXML(t *testing.T) {
	psExe := requirePowerShell(t)
	_, stderr, _ := runEncodedFull(t, psExe, "Write-Error 'cortexsim-boom'")
	if strings.HasPrefix(strings.TrimSpace(stderr), "#< CLIXML") {
		t.Fatalf("stderr is CLIXML, not text:\n%s", stderr)
	}
	if !strings.Contains(stderr, "cortexsim-boom") {
		t.Fatalf("stderr does not carry the error text: %q", stderr)
	}
}

// The exit-code contract is what every fail-fast, Result-row and push/pull
// parity guarantee in the engine rests on. Left unwrapped,
// `powershell -Command <failing thing>` exits 0 and a broken TTP reports as a
// step that PASSED — the exact false-green this engine exists to eliminate.
func TestPowerShellExitWrapper_RealInterpreterHonoursExitCodes(t *testing.T) {
	psExe := requirePowerShell(t)

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
	psExe := requirePowerShell(t)

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
	// Render through the explicit-interpreter form so the step child is the
	// PowerShell found on this host (on Windows the default render is already
	// the absolute %SystemRoot% path; on POSIX it is a literal placeholder).
	writeStep := func(cmd string) {
		script := powerShellChainStepScriptFor(psExe, marker, "step-x", cmd)
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

	// BEACON-03. Step A succeeds (rc 0), then step B's child cannot be launched
	// at all — the shape an EDR/AppLocker block or a missing image takes. B must
	// NOT inherit A's $LASTEXITCODE of 0: a step that never ran read as a PASS.
	writeStep("Write-Output 'step-a'")
	_, rc = collectOut()
	_ = drainErr()
	if rc != 0 {
		t.Fatalf("step A rc = %d, want 0", rc)
	}
	missing := filepath.Join(t.TempDir(), "nonexistent", "blocked.exe")
	blocked := powerShellChainStepScriptFor(missing, marker, "step-b", "Write-Output 'never'")
	if _, err := io.WriteString(stdin, blocked); err != nil {
		t.Fatalf("write step B: %v", err)
	}
	_, rc = collectOut()
	stderrB := drainErr()
	if rc == 0 {
		t.Errorf("step B's child never launched but its rc is 0 — it inherited step A's $LASTEXITCODE")
	}
	if !strings.Contains(stripANSI(stderrB), "!! STEP CHILD NOT LAUNCHED step=step-b") {
		t.Errorf("step B carries no STEP CHILD NOT LAUNCHED marker; stderr=%q", stderrB)
	}

	_, _ = io.WriteString(stdin, anchorExitScript)
	_ = stdin.Close()
}
