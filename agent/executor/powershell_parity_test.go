package executor

// BEACON-17 — where the PowerShell exit wrapper does NOT behave like `sh -c`,
// stated as rows an interpreter runs rather than left for a DC to discover.
//
// The engine scores a step by its exit code and was built on sh semantics: a
// step's status is the status of its LAST command. The wrapper diverges in two
// places, both deliberate consequences of how PowerShell reports failure:
//
//	(v)  a bare `exit` ends the step with 0, whatever ran before it.
//	     sh: `false; exit` is 1.
//	(vi) a native command that failed EARLIER in the step fails the step even
//	     when later statements succeed, because $LASTEXITCODE is never cleared.
//	     sh: `false; echo ok` is 0.
//
// (vi) is the stricter reading and it is kept: the looser one would let
// `tool.exe | Out-String` report a PASS for a tool that failed. What changes is
// that the step now SAYS so — the wrapper writes one line to stderr when it
// fails a step whose last statement succeeded — and that the way to tolerate an
// expected failure (`$global:LASTEXITCODE = 0`) is a tested row.
//
// Like the rest of powershell_exec_test.go these run on whatever PowerShell is
// on PATH. pwsh 7 is not Windows PowerShell 5.1; the 5.1 reading of every row
// is owed by the windows CI job.

import (
	"strings"
	"testing"
)

// EndsWithBareExit is text matching, so the table is what defines it: only the
// step's LAST statement, and only `exit` with no argument.
func TestEndsWithBareExit(t *testing.T) {
	for text, want := range map[string]bool{
		"exit":                                true,
		"Exit":                                true,
		"tool.exe\nexit":                      true,
		"tool.exe\r\nexit\r\n":                true,
		"tool.exe; exit":                      true,
		"tool.exe; exit;":                     true,
		"tool.exe\nexit   # done":             true,
		"tool.exe\nexit\n\n# trailing note\n": true,

		// An argument is the author's decision, on sh as well.
		"tool.exe\nexit 0":             false,
		"tool.exe\nexit $LASTEXITCODE": false,
		"tool.exe; exit 3":             false,
		// Not the last statement, or not a statement at all.
		"exit\ntool.exe":                    false,
		"if ($x) { exit }":                  false,
		"if ($x) {\n  exit\n}":              false,
		"Write-Output 'exit'":               false,
		"Write-Output \"a; exit\"":          false,
		"Write-Output `\nexit":              false, // a continued bare-word argument
		"$exit":                             false,
		"my-exit":                           false,
		"tool.exe # exit":                   false,
		"":                                  false,
		"   \n# only a comment\n":           false,
		"@'\nexit\n'@":                      false, // inside a here-string: the terminator is last
		"@'\nexit\n'@ | Out-Null\ntool.exe": false,
	} {
		if got := EndsWithBareExit(text); got != want {
			t.Errorf("EndsWithBareExit(%q) = %v, want %v", text, got, want)
		}
		if got := BareExitCaveat(text) != ""; got != want {
			t.Errorf("BareExitCaveat(%q) non-empty = %v, want %v", text, got, want)
		}
	}
}

// The note the wrapper prints is passed through a .NET format string inside a
// single-quoted PowerShell literal: it must not carry a quote or a stray brace.
func TestEarlierNativeFailureNote_IsSafeInsideTheWrapper(t *testing.T) {
	n := earlierNativeFailureNote
	if strings.ContainsAny(n, "'\"`") {
		t.Errorf("the note carries a quote or backtick: %q", n)
	}
	if strings.Count(n, "{")+strings.Count(n, "}") != 2*strings.Count(n, "{0}") {
		t.Errorf("the note carries a brace that is not part of {0}: %q", n)
	}
	for _, r := range n {
		if r > 126 {
			t.Errorf("the note is not ASCII (%q): the chained step script must survive the console code page", r)
		}
	}
	if !strings.Contains(powerShellExitWrapper("x"), n) {
		t.Error("the wrapper does not carry the note verbatim")
	}
}

func TestPowerShellExitWrapper_ShParityRows(t *testing.T) {
	psExe := requirePowerShell(t)
	// A native child that exits with the given code.
	native := func(code string) string {
		return "& '" + psExe + "' -NoProfile -NonInteractive -Command 'exit " + code + "'"
	}
	const note = "[cortexsim] step exit code"
	const (
		noNote     = 0
		mustNote   = 1
		eitherNote = -1 // depends on what $? reads after the statement, which the versions do not agree on
	)

	rows := []struct {
		name, command string
		want          int
		note          int
	}{
		// (v) KNOWN DIVERGENCE, pinned so it cannot change silently in either
		// direction: sh would return 5 here.
		{"(v) a bare exit after a failed native is 0", native("5") + "\nexit", 0, noNote},
		// An explicit code is the author's decision on both shells.
		{"an explicit exit 0 after a failed native is 0", native("5") + "\nexit 0", 0, noNote},

		// (vi) KNOWN DIVERGENCE, kept and now explained in the step's stderr.
		{"(vi) a failed native followed by a successful cmdlet is the native's code",
			native("3") + "\nWrite-Output 'later-ok'", 3, mustNote},
		// The case the strict rule exists for. pwsh 7.6 clears $? for the whole
		// pipeline here, so it prints no note; the exit code is what is pinned.
		{"(vi) a failed native piped into a cmdlet is the native's code",
			native("3") + " | Out-Null", 3, eitherNote},

		// The documented way to tolerate an expected failure.
		{"resetting $LASTEXITCODE after a tolerated failure is 0",
			native("3") + "\n$global:LASTEXITCODE = 0\nWrite-Output 'later-ok'", 0, noNote},

		// The ordinary failures carry no note: the last statement is what failed.
		{"a failing native as the last statement has no note", native("3"), 3, noNote},
		{"a later successful native clears an earlier failure", native("3") + "\n" + native("0"), 0, noNote},
	}
	for _, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			_, stderr, code := runEncodedFull(t, psExe, r.command)
			if code != r.want {
				t.Errorf("exit = %d, want %d", code, r.want)
			}
			got := strings.Contains(stderr, note)
			if r.note != eitherNote && got != (r.note == mustNote) {
				t.Errorf("stderr carries the earlier-native-failure note = %v, want %v\nstderr: %q",
					got, r.note == mustNote, stderr)
			}
			if got && (!strings.Contains(stderr, "$global:LASTEXITCODE = 0") ||
				!strings.Contains(stderr, "step exit code 3:")) {
				t.Errorf("the note does not name the code and how to tolerate an expected failure: %q", stderr)
			}
		})
	}
}
