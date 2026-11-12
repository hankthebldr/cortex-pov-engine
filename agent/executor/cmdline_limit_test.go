package executor

// BEACON-18. A Windows step reaches powershell.exe as one command line:
//
//	<powershell.exe> -NoProfile ... -EncodedCommand <base64(UTF-16LE(wrapper + step))>
//
// and CreateProcess accepts at most 32,767 characters. Encoding expands the
// text about 2.67x, so the ceiling is roughly 11,000 characters of step text.
// No corpus step is near it (the largest encodes to under 11,000 characters),
// but nothing checked: an over-long step failed inside CreateProcess with
// "The filename or extension is too long" on the per-step path, and on the
// chained path as a child that "could not be launched" — neither naming the
// cause. The guard refuses the step BEFORE any process is created and says why.
//
// The arithmetic is platform-neutral and tested here; that Windows really
// rejects a longer line, and really runs one just under it, is
// executor_windows_test.go.

import (
	"errors"
	"strings"
	"testing"
)

const testPSExe = `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`

func TestWindowsCommandLineLen(t *testing.T) {
	// Every argument is counted with a pair of quotes and one separator: an
	// upper bound on what either Go or PowerShell's `&` builds.
	if got, want := windowsCommandLineLen([]string{"a", "bc"}), (1+3)+(2+3); got != want {
		t.Errorf("windowsCommandLineLen = %d, want %d", got, want)
	}
	// UTF-16 code units, not bytes: an astral rune is two units.
	if got, want := windowsCommandLineLen([]string{"é😀"}), (1+2)+3; got != want {
		t.Errorf("windowsCommandLineLen(non-ASCII) = %d, want %d", got, want)
	}
}

func TestCheckPowerShellCommandLine(t *testing.T) {
	// The largest step in the corpus (3,439 characters) is nowhere near.
	if err := checkPowerShellCommandLine(testPSExe, strings.Repeat("x", 3439)); err != nil {
		t.Fatalf("a corpus-sized step was refused: %v", err)
	}

	// Find the boundary by the function's own arithmetic, then pin both sides.
	fits := 0
	for n := 8000; n < 14000; n++ {
		if checkPowerShellCommandLine(testPSExe, strings.Repeat("x", n)) != nil {
			break
		}
		fits = n
	}
	if fits < 10000 || fits > 12500 {
		t.Fatalf("the largest step text that fits is %d characters — expected about 11,000 "+
			"(32,767 / 2.67 minus the wrapper)", fits)
	}
	t.Logf("largest step text the guard accepts: %d characters", fits)
	argv := powerShellArgv(testPSExe, strings.Repeat("x", fits))
	if n := windowsCommandLineLen(argv); n >= windowsMaxCommandLine {
		t.Errorf("a step the guard accepts has a %d-character command line (limit %d)", n, windowsMaxCommandLine)
	}

	err := checkPowerShellCommandLine(testPSExe, strings.Repeat("x", fits+2))
	if !errors.Is(err, ErrCommandLineTooLong) {
		t.Fatalf("a step two characters past the boundary was accepted (err=%v)", err)
	}
	for _, want := range []string{"COMMAND_LINE_TOO_LONG", "32767", "NOT launched", "-EncodedCommand"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("the refusal does not mention %q: %v", want, err)
		}
	}
}

// What is measured is the argv that is actually launched: the same exe, flags
// and encoded wrapper newShellCmd and the chained launch statement use.
func TestPowerShellArgv_IsWhatTheStepIsLaunchedWith(t *testing.T) {
	argv := powerShellArgv(testPSExe, "Write-Output hi")
	if argv[0] != testPSExe {
		t.Fatalf("argv[0] = %q", argv[0])
	}
	if got, want := argv[len(argv)-2], "-EncodedCommand"; got != want {
		t.Fatalf("argv[-2] = %q, want %q", got, want)
	}
	if got, want := argv[len(argv)-1], encodePowerShellCommand(powerShellExitWrapper("Write-Output hi")); got != want {
		t.Fatal("argv does not end with the encoded exit wrapper")
	}
	for i, a := range powerShellArgs {
		if argv[1+i] != a {
			t.Fatalf("argv[%d] = %q, want the shared flag %q", 1+i, argv[1+i], a)
		}
	}
	// The chained launch statement carries the same encoded payload.
	if script := powerShellChainStepScriptFor(testPSExe, "__M__", "s", "Write-Output hi"); !strings.Contains(script, argv[len(argv)-1]) {
		t.Fatal("the chained launch does not carry the payload the guard measured")
	}
}
