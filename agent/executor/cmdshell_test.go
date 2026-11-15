package executor

import (
	"bytes"
	"os"
	"strings"
	"testing"
)

// BEACON-12: the interpreters are named by absolute path under %SystemRoot%,
// never resolved through PATH by a SYSTEM beacon.
func TestWindowsInterpreterPaths_AreAbsoluteUnderSystemRoot(t *testing.T) {
	cases := []struct{ root, ps, cmd string }{
		{`C:\Windows`, `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`, `C:\Windows\System32\cmd.exe`},
		{`D:\WINNT\`, `D:\WINNT\System32\WindowsPowerShell\v1.0\powershell.exe`, `D:\WINNT\System32\cmd.exe`},
		// An unset SystemRoot (a scrubbed service environment) falls back to the
		// default install root, never to a bare name PATH would resolve.
		{"", `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`, `C:\Windows\System32\cmd.exe`},
		{"   ", `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`, `C:\Windows\System32\cmd.exe`},
	}
	for _, c := range cases {
		if got := windowsPowerShellPath(c.root); got != c.ps {
			t.Errorf("windowsPowerShellPath(%q) = %q, want %q", c.root, got, c.ps)
		}
		if got := windowsCmdPath(c.root); got != c.cmd {
			t.Errorf("windowsCmdPath(%q) = %q, want %q", c.root, got, c.cmd)
		}
	}
}

// The chained launch line names the absolute interpreter, single-quoted (a path
// with a space or a quote must not split the statement).
func TestPowerShellChainStepScriptFor_LaunchesTheAbsoluteQuotedInterpreter(t *testing.T) {
	ps := windowsPowerShellPath(`C:\Windows`)
	s := powerShellChainStepScriptFor(ps, "__M__", "step-04", "whoami")
	want := `try { & 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -NoProfile -NonInteractive ` +
		`-ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand `
	if !strings.Contains(s, want) {
		t.Fatalf("launch line does not use the absolute quoted interpreter:\n%s", s)
	}
	if !strings.Contains(s, "'!! STEP CHILD NOT LAUNCHED step=step-04'") {
		t.Fatalf("not-launched marker does not name the step:\n%s", s)
	}
	// A quote in a path is doubled, not left to end the literal.
	if q := psSingleQuote(`C:\Users\o'brien\x.cmd`); q != `'C:\Users\o''brien\x.cmd'` {
		t.Fatalf("psSingleQuote = %s", q)
	}
}

// PUSH-18: a cmd step runs `"<abs cmd.exe>" /d /c "<file>"`.
func TestCmdInvocation_ArgvAndCommandLine(t *testing.T) {
	argv, line := cmdInvocation(`C:\Windows\System32\cmd.exe`, `C:\Windows\SystemTemp\cortexsim-step-1.cmd`)
	if strings.Join(argv, "|") != `C:\Windows\System32\cmd.exe|/d|/c|C:\Windows\SystemTemp\cortexsim-step-1.cmd` {
		t.Fatalf("argv = %q", argv)
	}
	if line != `"C:\Windows\System32\cmd.exe" /d /c "C:\Windows\SystemTemp\cortexsim-step-1.cmd"` {
		t.Fatalf("command line = %s", line)
	}
}

// The batch file is the author's text verbatim, with CRLF line endings.
func TestCmdScriptBytes_IsVerbatimCRLF(t *testing.T) {
	cases := map[string]string{
		"echo one\necho two":                     "echo one\r\necho two\r\n",
		"echo one\r\necho two\r\n":               "echo one\r\necho two\r\n",
		`for /L %i in (1,1,3) do @echo "%i" & x`: "for /L %i in (1,1,3) do @echo \"%i\" & x\r\n",
	}
	for in, want := range cases {
		if got := string(cmdScriptBytes(in)); got != want {
			t.Errorf("cmdScriptBytes(%q) = %q, want %q", in, got, want)
		}
	}
	if bytes.HasPrefix(cmdScriptBytes("x"), []byte("@echo off")) {
		t.Fatalf("author text must not be altered (no @echo off), matching the push bundle")
	}
}

func TestWriteCmdScript_WritesAndCleansUp(t *testing.T) {
	path, cleanup, err := writeCmdScript("echo hi\nexit /b 3")
	if err != nil {
		t.Fatalf("writeCmdScript: %v", err)
	}
	if !strings.HasSuffix(path, ".cmd") || !strings.Contains(path, "cortexsim-step-") {
		t.Fatalf("temp file name %q is not cortexsim-step-*.cmd", path)
	}
	b, err := os.ReadFile(path)
	if err != nil || string(b) != "echo hi\r\nexit /b 3\r\n" {
		t.Fatalf("contents = %q err=%v", b, err)
	}
	cleanup()
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("cleanup left %s behind", path)
	}
}

// The chained anchor runs a cmd step as a direct child: `& '<cmd>' /d /c '<file>'`.
func TestPowerShellChainCmdStepScript_LaunchesCmdAsAChild(t *testing.T) {
	s := powerShellChainCmdStepScript(`C:\Windows\System32\cmd.exe`, "__M__", "step-02", `C:\T\cortexsim-step-9.cmd`)
	if !strings.Contains(s, `try { & 'C:\Windows\System32\cmd.exe' /d /c 'C:\T\cortexsim-step-9.cmd' } catch {`) {
		t.Fatalf("cmd step is not launched as a child of the anchor:\n%s", s)
	}
	if !strings.Contains(s, "$global:LASTEXITCODE = $null") || !strings.Contains(s, "$__cs_rc = 126") {
		t.Fatalf("cmd step lacks the BEACON-03 exit-code guard:\n%s", s)
	}
}
