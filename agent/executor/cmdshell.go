package executor

// cmd.exe step execution (PUSH-18), platform-neutral so the argv and the file
// contents are unit-testable from a POSIX runner. Only the Windows platform
// file ever executes it.
//
// About 15 of the corpus' platform_variants.windows entries are cmd batch, not
// PowerShell (`for /L %i in (...) do ...`, `%TEMP%`). Pasted into PowerShell
// they parse and then fail at runtime, so when SimCore resolves a step to
// shell=cmd the beacon runs it under cmd.exe — and does so from a FILE, exactly
// as the .ps1 push bundle's Invoke-CsShell does, because Windows PowerShell 5.1
// mangles embedded quotes when it passes an argument string to a native exe
// and cmd's own /c quote-stripping rules are a second, different layer.

import (
	"fmt"
	"os"
	"strings"
)

// ShellCmd is the step shell value that selects cmd.exe. "" (and "powershell")
// select the host default interpreter.
const ShellCmd = "cmd"

// ShellPowerShell names PowerShell explicitly; on Windows it is the default.
const ShellPowerShell = "powershell"

// cmdScriptBytes renders author batch text for a .cmd file: CRLF line endings
// (cmd.exe misparses labels and `goto` in LF-only files), otherwise verbatim.
// No `@echo off` is prepended, matching the push bundle byte-for-byte: the
// author text is the evidence.
//
// The bytes are the UTF-8 of the author text. cmd.exe reads a batch file in the
// console's OEM code page, so a non-ASCII byte in a cmd step is NOT
// code-page converted — no shipped cmd variant carries one, and this is stated
// rather than silently assumed away.
func cmdScriptBytes(command string) []byte {
	s := strings.ReplaceAll(command, "\r\n", "\n")
	s = strings.ReplaceAll(s, "\n", "\r\n")
	if !strings.HasSuffix(s, "\r\n") {
		s += "\r\n"
	}
	return []byte(s)
}

// cmdInvocation returns the argv and the exact Windows command line for running
// file under cmd.exe: `"<cmdExe>" /d /c "<file>"`.
//
//	/d  skip the AutoRun registry commands — a customer's AutoRun must not
//	    alter TTP behaviour (the cmd analogue of -NoProfile)
//	/c  run the file, then exit with its errorlevel
//
// The command line is built explicitly (SysProcAttr.CmdLine) so the quoting is
// fixed and testable rather than whatever Go's argv escaper produces.
func cmdInvocation(cmdExe, file string) (argv []string, cmdLine string) {
	return []string{cmdExe, "/d", "/c", file}, fmt.Sprintf(`"%s" /d /c "%s"`, cmdExe, file)
}

// writeCmdScript writes command to a fresh `cortexsim-step-*.cmd` temp file and
// returns its absolute path and a cleanup that removes it.
func writeCmdScript(command string) (path string, cleanup func(), err error) {
	f, err := os.CreateTemp("", "cortexsim-step-*.cmd")
	if err != nil {
		return "", func() {}, fmt.Errorf("cmd step script: %w", err)
	}
	path = f.Name()
	cleanup = func() { _ = os.Remove(path) }
	if _, werr := f.Write(cmdScriptBytes(command)); werr != nil {
		_ = f.Close()
		cleanup()
		return "", func() {}, fmt.Errorf("cmd step script write: %w", werr)
	}
	if cerr := f.Close(); cerr != nil {
		cleanup()
		return "", func() {}, fmt.Errorf("cmd step script close: %w", cerr)
	}
	return path, cleanup, nil
}
