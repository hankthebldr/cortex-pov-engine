package executor

// Shell-script construction for both host families, kept in ONE platform-neutral
// file so the two dialects sit side by side and can be diffed — and so both are
// unit-testable from a Linux CI runner, which is the only place this repo's Go
// tests actually execute.
//
// The Windows dialect is not a translation of the POSIX one; it is a different
// shape chosen to preserve the SAME two properties the POSIX path relies on:
//
//  1. per-step output delimiting via a sentinel, so the beacon keeps one
//     /output POST per step; and
//  2. each step running in its own CHILD PROCESS of the anchor, so the endpoint
//     sensor records a connected causality chain rather than a star.
//
// (2) is why a Windows step is dispatched as a nested `powershell.exe
// -EncodedCommand`, not as an in-process `& { ... }` script block. A script
// block is only a child SCOPE — it produces no process-create event, so the
// causality chain the whole ChainSession exists to build would silently
// collapse back to a single process on Windows.

import (
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"unicode/utf16"
)

// powerShellExe is the interpreter the Windows executor drives. Windows
// PowerShell 5.1 ships in-box on every supported Windows Server / client SKU
// including Server Core; `pwsh` (PowerShell 7) does not, and a beacon that
// requires an install step is a beacon that does not run on a customer's
// golden image. On Windows it is always launched by the absolute path
// powerShellExePath() returns (BEACON-12), never resolved through PATH.
const powerShellExe = "powershell.exe"

// powerShellArgs are the flags applied to every PowerShell invocation.
//
//	-NoProfile        a customer profile script must not alter TTP behaviour
//	-NonInteractive   never block a headless beacon on a prompt
//	-ExecutionPolicy Bypass   -EncodedCommand is not a file, but a machine
//	                  policy of AllSigned still blocks it
//	-OutputFormat Text  a PowerShell whose stderr is a pipe otherwise frames
//	                  every error record as `#< CLIXML <Objs ...>` (observed on
//	                  pwsh 7.6), so the run record carried XML instead of the
//	                  error a DC must read. The nested chained child inherits
//	                  these args verbatim. PowerShell 7 honours the flag.
//	                  Windows PowerShell 5.1 does NOT for an encoded command
//	                  (observed on the windows-2022 CI job): it still writes
//	                  CLIXML, so the beacon decodes it after capture
//	                  (clixml.go, normalizeStepStderr).
var powerShellArgs = []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-OutputFormat", "Text"}

// windowsPowerShellPath is the absolute path of Windows PowerShell 5.1 under
// systemRoot (C:\Windows when systemRoot is empty).
//
// BEACON-12: the beacon runs as LocalSystem. A bare `powershell.exe` is found
// through PATH, so a directory on PATH that a non-admin can write would let
// them plant powershell.exe and have SYSTEM run it on the next step. The image
// is therefore named by an absolute path rooted at %SystemRoot%. Built with
// explicit backslashes, not filepath.Join, so the Windows result is
// unit-testable from a POSIX runner.
func windowsPowerShellPath(systemRoot string) string {
	return windowsSystem32(systemRoot) + `\WindowsPowerShell\v1.0\powershell.exe`
}

// windowsCmdPath is the absolute path of cmd.exe under systemRoot, for the
// same reason windowsPowerShellPath exists.
func windowsCmdPath(systemRoot string) string {
	return windowsSystem32(systemRoot) + `\cmd.exe`
}

func windowsSystem32(systemRoot string) string {
	root := strings.TrimRight(strings.TrimSpace(systemRoot), `\/`)
	if root == "" {
		root = `C:\Windows`
	}
	return root + `\System32`
}

// psSingleQuote renders s as a PowerShell single-quoted literal. Inside one the
// only metacharacter is the quote itself, written twice.
func psSingleQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}

// posixChainStepScript builds the anchor-shell input for one step on POSIX.
//
// The step runs in a SUBSHELL `( ... )` — a real forked child of the anchor (so
// the step is a descendant of the one CGO) that ALSO isolates `exit`/`set -e`
// from the anchor and leaks no cross-step state, exactly matching the default
// per-step `sh -c` semantics. The sentinel printfs run in the ANCHOR (outside
// the parens) so they always fire even if the subshell called `exit`. $? is
// captured immediately after the subshell so it reflects the step, not printf.
func posixChainStepScript(marker, wrappedCmd string) string {
	return "(\n" + wrappedCmd + "\n)\n__cs_rc=$?" +
		fmt.Sprintf("; printf '\\n%s %%d\\n' \"$__cs_rc\"", marker) + // stdout: marker + rc
		fmt.Sprintf("; printf '\\n%s\\n' 1>&2", marker) + // stderr: bare marker
		"\n"
}

// powerShellChainStepScript builds the anchor-shell input for one step on
// Windows: powerShellChainStepScriptFor with this host's interpreter path and
// no step id. Its name and signature are kept stable because other code (the
// powershell-gate lane's AST dump test) renders it directly.
func powerShellChainStepScript(marker, wrappedCmd string) string {
	return powerShellChainStepScriptFor(powerShellExePath(), marker, "", wrappedCmd)
}

// powerShellChainStepScriptFor builds the anchor-shell input for one step on
// Windows, launching the step child through psExe.
//
// The anchor is `powershell.exe -Command -`, which reads and executes stdin
// statement by statement. Every line emitted here is therefore a COMPLETE
// single-line statement: a construct spanning lines would leave the anchor
// waiting for more input and the step would hang forever.
//
// The step itself is dispatched as a nested powershell.exe (a real child
// process — see the package comment) carrying the command base64/UTF-16LE
// encoded, which sidesteps quoting entirely: a TTP command full of quotes,
// backticks, `$` and newlines survives verbatim.
func powerShellChainStepScriptFor(psExe, marker, stepID, wrappedCmd string) string {
	// The same argv the per-step path launches, and the one the command-line
	// length guard measures (powerShellArgv).
	argv := powerShellArgv(psExe, wrappedCmd)
	launch := "& " + psSingleQuote(argv[0]) + " " + strings.Join(argv[1:], " ")
	return powerShellChainLaunch(launch, marker, stepID)
}

// powerShellChainCmdStepScript is the anchor-shell input for a cmd.exe step
// (PUSH-18). The author's batch text was written to cmdFile and cmd.exe runs it
// as a direct child of the anchor, so the causality chain stays connected. The
// step's exit code is cmd's.
func powerShellChainCmdStepScript(cmdExe, marker, stepID, cmdFile string) string {
	launch := fmt.Sprintf("& %s /d /c %s", psSingleQuote(cmdExe), psSingleQuote(cmdFile))
	return powerShellChainLaunch(launch, marker, stepID)
}

// childNotLaunchedMarker is the stable literal a DC (and any parser of the run
// record) greps for: the step's child process was never created.
const childNotLaunchedMarker = "!! STEP CHILD NOT LAUNCHED"

// childNotLaunchedExitCode is POSIX's "found but could not execute".
const childNotLaunchedExitCode = 126

// powerShellChainLaunch wraps one child-launch statement with the exit-code
// capture and the sentinels the beacon parses.
//
// BEACON-03: $LASTEXITCODE is a session-global the anchor never resets, and it
// is only written when a native process actually RAN. When a step's child
// cannot be launched at all (an EDR or AppLocker block, a missing image) `&`
// writes nothing and the step read the PREVIOUS step's exit code — usually 0 —
// so a step that never ran reported PASS. The reset to $null is therefore its
// own statement BEFORE the launch, the launch is caught, and "caught" or "still
// $null" both read as not-launched: a greppable marker and rc 126, never a pass.
func powerShellChainLaunch(launch, marker, stepID string) string {
	var b strings.Builder
	b.WriteString("$global:LASTEXITCODE = $null\n")
	b.WriteString("$__cs_nl = $false\n")
	fmt.Fprintf(&b, "try { %s } catch { $__cs_nl = $true; [Console]::Error.WriteLine('launch failed: ' + $_) }\n", launch)
	fmt.Fprintf(&b, "$__cs_rc = $LASTEXITCODE; if ($__cs_nl -or $null -eq $__cs_rc) "+
		"{ [Console]::Error.WriteLine(%s); $__cs_rc = %d }\n",
		psSingleQuote(childNotLaunchedMarker+" step="+stepID), childNotLaunchedExitCode)
	// Sentinels are written through [Console] rather than Write-Output /
	// Write-Error: Write-Error would emit a multi-line ErrorRecord (and set the
	// anchor's error state), and PowerShell's output formatter can wrap or pad
	// pipeline objects. [Console] writes the exact bytes the scanner matches.
	// The leading blank line guarantees the marker starts a line even when the
	// step's own output had no trailing newline.
	fmt.Fprintf(&b, "[Console]::Out.WriteLine(''); [Console]::Out.WriteLine('%s ' + $__cs_rc)\n", marker)
	fmt.Fprintf(&b, "[Console]::Error.WriteLine(''); [Console]::Error.WriteLine('%s')\n", marker)
	return b.String()
}

// powerShellExitWrapper makes a PowerShell command report a UNIX-shaped exit
// code, which is what the whole engine (fail-fast, Result rows, the push/pull
// parity contract) is built on.
//
// Left alone, `powershell -Command <x>` exits 0 for almost everything: a failed
// native tool sets $LASTEXITCODE but does not change the interpreter's exit
// code, and a non-terminating cmdlet error only clears $?. Both would be
// reported to SimCore as a step that PASSED — a scenario that silently "worked"
// while its TTP did nothing is exactly the false-green this engine exists to
// eliminate.
//
// $? and $LASTEXITCODE are snapshotted into locals on the line immediately
// after the command, because any statement in between (including the `if`
// itself) overwrites $?.
//
// BEACON-10: Windows PowerShell 5.1 writes redirected output in the OEM code
// page, so non-ASCII TTP output reached SimCore mangled. The prelude sets
// BOM-less UTF-8; the [Console] setter is wrapped in try because a process with
// no console handle (a service) can throw on it.
//
// BEACON-05 (the default; Henry's decision is pending): Windows PowerShell 5.1
// turns a native tool's redirected stderr into NativeCommandError records and
// clears $? even when the tool exited 0, so every chatty-but-successful native
// tool read as a failed step. When the native exit code is 0 and the most recent
// error record is a NativeCommandError, the native exit code is the truth. A
// failing cmdlet still exits 1 — its error id is not NativeCommandError.
//
// THE CANONICAL TRUTH TABLE (the push-bundle harness asserts the same rows for
// .ps1 bundles): `Write-Output ok` -> 0; last statement a native exiting 3 -> 3;
// last statement a failing cmdlet (`Get-Item <missing>`) -> 1; a native that
// writes to stderr and exits 0 -> 0.
//
// WHERE THIS IS NOT `sh -c` (BEACON-17). The engine reads a step's exit code
// the way sh defines it — the status of the LAST command — and this wrapper
// differs in two places. Both are stated rules, each a row in
// TestPowerShellExitWrapper_ShParityRows:
//
//  1. A bare `exit` ends the step with 0, whatever ran before it. The author's
//     `exit` leaves the process before the epilogue below can look at $? or
//     $LASTEXITCODE, and PowerShell's `exit` with no argument is 0, where sh's
//     is "the last command's status" (`false; exit` is 1). Nothing here can
//     see that it happened; the beacon notes a step whose text ENDS in a bare
//     exit (EndsWithBareExit) and content should write `exit $LASTEXITCODE`.
//  2. A native command that failed EARLIER in the step fails the step even if
//     later statements succeed: $LASTEXITCODE is only ever written by the next
//     native command, so `tool.exe; Write-Output done` exits with tool.exe's
//     code where sh's `false; echo ok` is 0. This is deliberately the STRICTER
//     reading. The looser one ("$? is true, so 0") would report a PASS for
//     `tool.exe | Out-String` when tool.exe failed, which is the false-green
//     this wrapper exists to remove. A step that expects a failure says so with
//     `$global:LASTEXITCODE = 0` after it. When the wrapper fails a step this
//     way it writes one line to stderr (earlierNativeFailureNote) so the run
//     record explains an exit code the last statement did not produce.
func powerShellExitWrapper(cmd string) string {
	var b strings.Builder
	// First, and before any cmdlet: Windows PowerShell 5.1 raises a "Preparing
	// modules for first use" progress record on the first cmdlet lookup and, with
	// stderr redirected, serialises it there as CLIXML ("#< CLIXML" + <Objs>),
	// -OutputFormat Text notwithstanding. Observed on the first windows-2022 CI
	// run: every step's stderr carried the envelope. The .ps1 bundle and the
	// installers already set this; the beacon wrapper did not.
	b.WriteString("$ProgressPreference = 'SilentlyContinue'\n")
	b.WriteString("try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}\n")
	b.WriteString("$OutputEncoding = New-Object System.Text.UTF8Encoding $false\n")
	// Continue (not Stop) so a step behaves like `sh -c`: a non-fatal error does
	// not abandon the remaining commands in the same step.
	b.WriteString("$ErrorActionPreference = 'Continue'\n")
	b.WriteString("$global:LASTEXITCODE = 0\n")
	b.WriteString(cmd)
	b.WriteString("\n$__cs_ok = $?\n")
	b.WriteString("$__cs_native = $LASTEXITCODE\n")
	// $__cs_ok is true here only when the last statement itself succeeded, i.e.
	// the non-zero code was left by an earlier native command (or by a native
	// inside the last pipeline): rule 2 above, said out loud.
	b.WriteString("if ($null -ne $__cs_native -and $__cs_native -ne 0) { if ($__cs_ok) { " +
		"[Console]::Error.WriteLine(" + psSingleQuote(earlierNativeFailureNote) + " -f $__cs_native) }; " +
		"exit $__cs_native }\n")
	b.WriteString("if (-not $__cs_ok -and $__cs_native -eq 0 -and $Error.Count -gt 0 -and " +
		"$Error[0].FullyQualifiedErrorId -like 'NativeCommandError*') { exit 0 }\n")
	b.WriteString("if (-not $__cs_ok) { exit 1 }\n")
	b.WriteString("exit 0\n")
	return b.String()
}

// windowsMaxCommandLine is the largest command line CreateProcess accepts, in
// UTF-16 code units INCLUDING the terminating NUL (lpCommandLine: "The maximum
// length of this string is 32,767 characters, including the Unicode terminating
// null character").
const windowsMaxCommandLine = 32767

// ErrCommandLineTooLong is returned (wrapped) when a Windows step is refused
// because launching it would exceed windowsMaxCommandLine. Match with errors.Is.
var ErrCommandLineTooLong = errors.New("COMMAND_LINE_TOO_LONG")

// powerShellArgv is the argv a PowerShell step is launched with: the
// interpreter, the shared flags, and the step inside the exit wrapper as an
// -EncodedCommand payload. newShellCmd builds its process from it and the
// length guard measures it, so the two cannot drift.
func powerShellArgv(psExe, cmd string) []string {
	argv := append([]string{psExe}, powerShellArgs...)
	return append(argv, "-EncodedCommand", encodePowerShellCommand(powerShellExitWrapper(cmd)))
}

// windowsCommandLineLen is an upper bound, in UTF-16 code units, on the command
// line Windows receives for argv: every argument is counted with a pair of
// quotes and a separator. (Go's argv escaper and PowerShell's `&` both quote
// only when they must; none of the arguments measured here contains a quote or
// a backslash run that would be escaped longer.)
func windowsCommandLineLen(argv []string) int {
	n := 0
	for _, a := range argv {
		n += len(utf16.Encode([]rune(a))) + 3
	}
	return n
}

// checkPowerShellCommandLine refuses a PowerShell step whose command line would
// not fit (BEACON-18). The -EncodedCommand payload is base64 of UTF-16LE, about
// 2.67 characters per character of step text, so roughly 11,000 characters of
// text is the ceiling. Past it CreateProcess fails with error 206 — reported on
// the per-step path as a bare "filename or extension is too long" and on the
// chained path as a child that could not be launched. Checked here, before any
// process exists, the run record names the real cause and the sizes.
func checkPowerShellCommandLine(psExe, cmd string) error {
	n := windowsCommandLineLen(powerShellArgv(psExe, cmd))
	if n < windowsMaxCommandLine {
		return nil
	}
	return fmt.Errorf("%w: the step's text is %d characters; inside the exit wrapper and encoded for "+
		"-EncodedCommand its command line would be %d characters, and Windows CreateProcess accepts %d. "+
		"The step was NOT launched. Shorten it, or stage its payload as an artifact and have the step read the file",
		ErrCommandLineTooLong, len([]rune(cmd)), n, windowsMaxCommandLine)
}

// earlierNativeFailureNote is the stderr line the wrapper writes when it fails
// a step whose LAST statement succeeded (BEACON-17 rule 2). {0} is the exit
// code. ASCII only, and a .NET format string: braces other than {0} would have
// to be doubled.
const earlierNativeFailureNote = "[cortexsim] step exit code {0}: a native command in this step exited {0} and " +
	"nothing after it reset $LASTEXITCODE, although the last statement itself succeeded. sh would report the " +
	"last command only; this wrapper does not. If that failure is expected, put $global:LASTEXITCODE = 0 after it."

// bareExitCaveat is the run-record note for a PowerShell step whose text ends
// in a bare `exit` (BEACON-17 rule 1).
const bareExitCaveat = "[cortexsim] exit-code caveat: this step ends with a bare `exit`. PowerShell exits 0 there " +
	"whatever the command before it returned (sh would return that command's status), so an exit_code=0 below " +
	"does not show that the step's last tool succeeded. Write `exit $LASTEXITCODE` instead."

// BareExitCaveat returns the note to put in a PowerShell step's output when its
// text ends in a bare `exit`, or "".
func BareExitCaveat(stepText string) string {
	if EndsWithBareExit(stepText) {
		return bareExitCaveat
	}
	return ""
}

// EndsWithBareExit reports whether the LAST statement of a PowerShell step is
// `exit` with no argument.
//
// It reads text, not a syntax tree, and is deliberately narrow: only the final
// statement, found by taking the last line that is not blank or a `#` comment
// and the part of it after the last `;`. A final line cannot be the inside of a
// here-string or a block (their terminators would follow it), so the cases this
// can misread are a comment after the keyword (handled) and an `exit` that is a
// bare-word argument continued from the line above with a backtick (refused).
// A bare `exit` earlier in the step, inside a branch, is not looked for.
func EndsWithBareExit(stepText string) bool {
	lines := strings.Split(strings.ReplaceAll(stepText, "\r\n", "\n"), "\n")
	last := -1
	for i := len(lines) - 1; i >= 0; i-- {
		if t := strings.TrimSpace(lines[i]); t != "" && !strings.HasPrefix(t, "#") {
			last = i
			break
		}
	}
	if last < 0 {
		return false
	}
	stmt := lines[last]
	if i := strings.Index(stmt, "#"); i >= 0 {
		stmt = stmt[:i] // a trailing comment
	}
	stmt = strings.TrimRight(strings.TrimSpace(stmt), ";")
	onlyStatement := true
	if i := strings.LastIndex(stmt, ";"); i >= 0 {
		stmt, onlyStatement = stmt[i+1:], false
	}
	if !strings.EqualFold(strings.TrimSpace(stmt), "exit") {
		return false
	}
	if onlyStatement && last > 0 {
		// `Write-Output `<newline>exit` prints the word; it does not exit.
		if prev := strings.TrimRight(lines[last-1], " \t"); strings.HasSuffix(prev, "`") {
			return false
		}
	}
	return true
}

// encodePowerShellCommand encodes a script for -EncodedCommand: UTF-16LE
// (little-endian, no BOM) then standard base64, which is precisely what
// PowerShell's -EncodedCommand expects.
//
// Encoding rather than quoting is a correctness decision, not a stylistic one.
// TTP commands are attacker-shaped strings — embedded quotes, `$`, backticks,
// `&`, newlines — and every layer between here and the interpreter (Go's
// Windows command-line builder, CreateProcess, PowerShell's own parser) applies
// a DIFFERENT escaping rule. Base64 removes all of them from the path.
func encodePowerShellCommand(script string) string {
	units := utf16.Encode([]rune(script))
	buf := make([]byte, 0, len(units)*2)
	for _, u := range units {
		buf = append(buf, byte(u), byte(u>>8)) // little-endian
	}
	return base64.StdEncoding.EncodeToString(buf)
}

// stripANSI removes ANSI/VT escape sequences from s.
//
// A PowerShell anchor reading statements from stdin runs a host prompt loop,
// and that loop emits terminal control sequences (e.g. the DECCKM pair
// "\x1b[?1h\x1b[?1l") around every statement it reads. They are glued onto
// whichever output line happens to follow, INCLUDING potentially a sentinel
// line — and a sentinel the scanner cannot match means the step never returns
// and the run hangs forever with no diagnostic.
//
// This is applied to the sentinel COMPARISON only, never to the step's captured
// output: what a TTP wrote is evidence and is stored byte-for-byte.
func stripANSI(s string) string {
	if !strings.ContainsRune(s, 0x1b) {
		return s // overwhelmingly the common case — no allocation
	}
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); {
		if s[i] != 0x1b {
			b.WriteByte(s[i])
			i++
			continue
		}
		// CSI: ESC [ <params/intermediates> <final byte 0x40..0x7E>
		if i+1 < len(s) && s[i+1] == '[' {
			j := i + 2
			for j < len(s) && (s[j] < 0x40 || s[j] > 0x7E) {
				j++
			}
			if j < len(s) {
				j++ // consume the final byte
			}
			i = j
			continue
		}
		// Any other two-byte escape (ESC c, ESC 7, ...). A lone trailing ESC is
		// dropped.
		i += 2
	}
	return b.String()
}

// trimEOL strips a trailing carriage return left by a CRLF line ending.
//
// bufio.Scanner's line splitter removes \n but leaves the \r, and PowerShell
// terminates every line with CRLF. Without this, the chained executor's stderr
// sentinel (an exact string comparison against the bare marker) never matches
// on Windows and the step blocks until the session is torn down.
func trimEOL(s string) string {
	return strings.TrimSuffix(s, "\r")
}
