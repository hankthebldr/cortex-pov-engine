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
	"fmt"
	"strings"
	"unicode/utf16"
)

// powerShellExe is the interpreter the Windows executor drives. Windows
// PowerShell 5.1 ships in-box on every supported Windows Server / client SKU
// including Server Core; `pwsh` (PowerShell 7) does not, and a beacon that
// requires an install step is a beacon that does not run on a customer's
// golden image.
const powerShellExe = "powershell.exe"

// powerShellArgs are the flags applied to every PowerShell invocation.
//
//	-NoProfile        a customer profile script must not alter TTP behaviour
//	-NonInteractive   never block a headless beacon on a prompt
//	-ExecutionPolicy Bypass   -EncodedCommand is not a file, but a machine
//	                  policy of AllSigned still blocks it
var powerShellArgs = []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"}

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
// Windows.
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
//
// $LASTEXITCODE is only populated after a NATIVE process runs; the encoded
// payload always ends in an explicit `exit`, so it is populated here — but the
// null-guard stays because a failure to even launch the child leaves it unset,
// and `'<marker> ' + $null` would emit a marker line with no rc for the beacon
// to parse.
func powerShellChainStepScript(marker, wrappedCmd string) string {
	enc := encodePowerShellCommand(powerShellExitWrapper(wrappedCmd))

	var b strings.Builder
	// Run the step as a child process and capture its exit code.
	fmt.Fprintf(&b, "& %s %s -EncodedCommand %s\n",
		powerShellExe, strings.Join(powerShellArgs, " "), enc)
	b.WriteString("$__cs_rc = $LASTEXITCODE; if ($null -eq $__cs_rc) { $__cs_rc = 1 }\n")
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
func powerShellExitWrapper(cmd string) string {
	var b strings.Builder
	// Continue (not Stop) so a step behaves like `sh -c`: a non-fatal error does
	// not abandon the remaining commands in the same step.
	b.WriteString("$ErrorActionPreference = 'Continue'\n")
	b.WriteString("$global:LASTEXITCODE = 0\n")
	b.WriteString(cmd)
	b.WriteString("\n$__cs_ok = $?\n")
	b.WriteString("$__cs_native = $LASTEXITCODE\n")
	b.WriteString("if ($null -ne $__cs_native -and $__cs_native -ne 0) { exit $__cs_native }\n")
	b.WriteString("if (-not $__cs_ok) { exit 1 }\n")
	b.WriteString("exit 0\n")
	return b.String()
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
