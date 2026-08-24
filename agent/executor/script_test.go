package executor

import (
	"encoding/base64"
	"strings"
	"testing"
	"unicode/utf16"
)

// decodePowerShellCommand is the inverse of encodePowerShellCommand, so the
// tests below assert on the SCRIPT PowerShell will actually see rather than on
// an opaque blob.
func decodePowerShellCommand(t *testing.T, enc string) string {
	t.Helper()
	raw, err := base64.StdEncoding.DecodeString(enc)
	if err != nil {
		t.Fatalf("not valid base64: %v", err)
	}
	if len(raw)%2 != 0 {
		t.Fatalf("UTF-16LE payload has odd length %d", len(raw))
	}
	units := make([]uint16, 0, len(raw)/2)
	for i := 0; i < len(raw); i += 2 {
		units = append(units, uint16(raw[i])|uint16(raw[i+1])<<8)
	}
	return string(utf16.Decode(units))
}

// -EncodedCommand requires UTF-16LE-then-base64. Getting the byte order wrong
// produces a payload PowerShell decodes as CJK garbage and refuses to run —
// which on a customer host looks like "the TTP failed", not "the agent is
// broken".
func TestEncodePowerShellCommand_IsUTF16LEBase64(t *testing.T) {
	enc := encodePowerShellCommand("AB")
	raw, err := base64.StdEncoding.DecodeString(enc)
	if err != nil {
		t.Fatalf("base64: %v", err)
	}
	want := []byte{'A', 0x00, 'B', 0x00}
	if len(raw) != len(want) {
		t.Fatalf("encoded %q to % x, want % x", "AB", raw, want)
	}
	for i := range want {
		if raw[i] != want[i] {
			t.Fatalf("encoded %q to % x, want % x (byte order)", "AB", raw, want)
		}
	}
}

// TTP commands are attacker-shaped strings. Encoding (rather than quoting) is
// what makes them survive Go's Windows command-line builder, CreateProcess and
// PowerShell's own parser — each of which escapes differently.
func TestEncodePowerShellCommand_RoundTripsHostileStrings(t *testing.T) {
	hostile := "$x = \"a'b`c\"; Write-Output @'\nmulti\nline\n'@ ; & 'C:\\Program Files\\x.exe' --flag=\"v a l\" & echo %PATH%"
	if got := decodePowerShellCommand(t, encodePowerShellCommand(hostile)); got != hostile {
		t.Fatalf("round trip lost data:\n got: %q\nwant: %q", got, hostile)
	}
}

// A PowerShell step must report a UNIX-shaped exit code or every fail-fast,
// Result-row and push/pull-parity guarantee in the engine silently inverts:
// `powershell -Command <failing native tool>` exits 0 by default.
func TestPowerShellExitWrapper_PropagatesFailure(t *testing.T) {
	w := powerShellExitWrapper("whoami")

	if !strings.Contains(w, "whoami") {
		t.Fatalf("wrapper dropped the command: %q", w)
	}
	// $? and $LASTEXITCODE must be snapshotted on the line straight after the
	// command — any intervening statement (including the `if` itself) clobbers $?.
	okIdx := strings.Index(w, "$__cs_ok = $?")
	nativeIdx := strings.Index(w, "$__cs_native = $LASTEXITCODE")
	cmdIdx := strings.Index(w, "whoami")
	if okIdx < 0 || nativeIdx < 0 {
		t.Fatalf("wrapper does not snapshot $? and $LASTEXITCODE: %q", w)
	}
	if !(cmdIdx < okIdx && okIdx < nativeIdx) {
		t.Fatalf("status snapshot is not immediately after the command: %q", w)
	}
	for _, want := range []string{
		"exit $__cs_native", // a failing native tool must fail the step
		"if (-not $__cs_ok) { exit 1 }",
		"exit 0",
	} {
		if !strings.Contains(w, want) {
			t.Fatalf("wrapper missing %q:\n%s", want, w)
		}
	}
	// Stop would abandon later commands in the same multi-command step, unlike sh -c.
	if strings.Contains(w, "$ErrorActionPreference = 'Stop'") {
		t.Fatalf("wrapper uses ErrorActionPreference Stop — diverges from sh -c semantics")
	}
}

// The chained executor's whole purpose is a connected parent→child process tree.
// An in-process script block (`& { ... }`) produces NO process-create event, so
// the causality chain would collapse to a single process on Windows while every
// test still passed. The step must be dispatched as a nested powershell.exe.
func TestPowerShellChainStepScript_RunsStepAsAChildProcess(t *testing.T) {
	s := powerShellChainStepScript("__MARK__", "whoami")

	if !strings.Contains(s, powerShellExe) {
		t.Fatalf("step is not dispatched as a child %s process:\n%s", powerShellExe, s)
	}
	if !strings.Contains(s, "-EncodedCommand ") {
		t.Fatalf("step is not passed via -EncodedCommand:\n%s", s)
	}
	// And the encoded payload really is this step, exit-wrapped.
	for _, line := range strings.Split(s, "\n") {
		if i := strings.Index(line, "-EncodedCommand "); i >= 0 {
			enc := strings.TrimSpace(line[i+len("-EncodedCommand "):])
			payload := decodePowerShellCommand(t, enc)
			if !strings.Contains(payload, "whoami") {
				t.Fatalf("encoded payload does not carry the step: %q", payload)
			}
			if !strings.Contains(payload, "exit $__cs_native") {
				t.Fatalf("encoded payload is not exit-wrapped: %q", payload)
			}
		}
	}
}

// The anchor reads stdin in `-Command -` mode and executes statement by
// statement. A construct spanning lines leaves it waiting for more input and the
// step hangs forever — so every emitted line must stand alone.
func TestPowerShellChainStepScript_EveryLineIsAStandaloneStatement(t *testing.T) {
	s := powerShellChainStepScript("__MARK__", "Get-Process")
	for _, line := range strings.Split(strings.TrimRight(s, "\n"), "\n") {
		if strings.Count(line, "{") != strings.Count(line, "}") {
			t.Fatalf("line has unbalanced braces (anchor would block waiting for more input): %q", line)
		}
		if strings.HasSuffix(strings.TrimSpace(line), "|") ||
			strings.HasSuffix(strings.TrimSpace(line), "`") {
			t.Fatalf("line continues onto the next (anchor would block): %q", line)
		}
	}
}

// Both dialects must emit the sentinels the beacon parses, in the shape
// chained.go matches: "<marker> <rc>" on stdout and a bare "<marker>" on stderr,
// each preceded by a newline so a step with no trailing newline cannot glue its
// last output line onto the marker.
func TestChainStepScripts_EmitParsableSentinels(t *testing.T) {
	const marker = "__CORTEXSIM_STEP_1__"

	posix := posixChainStepScript(marker, "echo hi")
	if !strings.Contains(posix, `printf '\n`+marker+` %d\n'`) {
		t.Fatalf("posix stdout sentinel missing/changed:\n%s", posix)
	}
	if !strings.Contains(posix, `printf '\n`+marker+`\n' 1>&2`) {
		t.Fatalf("posix stderr sentinel missing/changed:\n%s", posix)
	}

	ps := powerShellChainStepScript(marker, "echo hi")
	if !strings.Contains(ps, "[Console]::Out.WriteLine('"+marker+" ' + $__cs_rc)") {
		t.Fatalf("powershell stdout sentinel missing/changed:\n%s", ps)
	}
	if !strings.Contains(ps, "[Console]::Error.WriteLine('"+marker+"')") {
		t.Fatalf("powershell stderr sentinel missing/changed:\n%s", ps)
	}
	// The blank line before each sentinel is what guarantees it starts a line.
	if !strings.Contains(ps, "[Console]::Out.WriteLine('');") ||
		!strings.Contains(ps, "[Console]::Error.WriteLine('');") {
		t.Fatalf("powershell sentinels are not newline-anchored:\n%s", ps)
	}
	// Write-Error would emit a multi-line ErrorRecord the scanner cannot match.
	if strings.Contains(ps, "Write-Error") || strings.Contains(ps, "Write-Host") {
		t.Fatalf("sentinels must bypass the PowerShell formatter:\n%s", ps)
	}
}

// A null $LASTEXITCODE (the child never launched) must not produce a marker line
// with an unparsable rc — the beacon would report a sentinel-parse error instead
// of a failed step.
func TestPowerShellChainStepScript_GuardsNullExitCode(t *testing.T) {
	s := powerShellChainStepScript("__MARK__", "whoami")
	if !strings.Contains(s, "if ($null -eq $__cs_rc)") {
		t.Fatalf("no null guard on $LASTEXITCODE:\n%s", s)
	}
	if !strings.Contains(s, "$__cs_rc = 1 }") {
		t.Fatalf("null $LASTEXITCODE must fail the step, not pass it:\n%s", s)
	}
}

// PowerShell terminates lines with CRLF and bufio.Scanner strips only the \n.
// Without trimming the \r, the stderr sentinel (an exact string comparison) can
// never match and every chained step on Windows blocks until teardown.
func TestTrimEOL_StripsCarriageReturn(t *testing.T) {
	if got := trimEOL("__MARK__\r"); got != "__MARK__" {
		t.Fatalf("trimEOL(%q) = %q, want %q", "__MARK__\r", got, "__MARK__")
	}
	if got := trimEOL("plain"); got != "plain" {
		t.Fatalf("trimEOL must not alter LF-terminated lines, got %q", got)
	}
	// Only the line terminator is stripped — a \r inside the payload is data.
	if got := trimEOL("a\rb"); got != "a\rb" {
		t.Fatalf("trimEOL corrupted interior data: %q", got)
	}
}

// A PowerShell anchor's host prompt loop emits terminal control sequences around
// every statement it reads from stdin, and they get glued onto whichever output
// line follows — potentially the sentinel. An unmatched sentinel does not fail
// the step, it HANGS it: RunStep blocks until the session is torn down, with no
// diagnostic. Verified empirically against PowerShell 7, which emits
// "\x1b[?1h\x1b[?1l" per statement.
func TestStripANSI_UnhidesADecoratedSentinel(t *testing.T) {
	const marker = "__CORTEXSIM_STEP_1__"

	decorated := "\x1b[?1h\x1b[?1l" + marker + " 0"
	if got := stripANSI(decorated); got != marker+" 0" {
		t.Fatalf("stripANSI(%q) = %q — a decorated sentinel would never match and the step would hang",
			decorated, got)
	}
	// The bare stderr sentinel is matched by EQUALITY, so a trailing sequence is
	// just as fatal as a leading one.
	if got := stripANSI(marker + "\x1b[0m"); got != marker {
		t.Fatalf("stripANSI did not strip a trailing sequence: %q", got)
	}
	// Colour codes from a TTP tool must not defeat matching either.
	if got := stripANSI("\x1b[31m" + marker + " 7\x1b[0m"); got != marker+" 7" {
		t.Fatalf("stripANSI left SGR codes behind: %q", got)
	}
}

func TestStripANSI_LeavesOrdinaryTextUntouched(t *testing.T) {
	for _, s := range []string{
		"",
		"plain output",
		`C:\Windows\System32 [brackets] 100% $var`,
		"tab\tseparated",
	} {
		if got := stripANSI(s); got != s {
			t.Errorf("stripANSI(%q) = %q — must be identity for text with no escapes", s, got)
		}
	}
	// A lone ESC at end-of-line must not panic or loop.
	if got := stripANSI("abc\x1b"); got != "abc" {
		t.Errorf("stripANSI(%q) = %q", "abc\x1b", got)
	}
	if got := stripANSI("abc\x1b["); got != "abc" {
		t.Errorf("stripANSI on a truncated CSI = %q", got)
	}
}
