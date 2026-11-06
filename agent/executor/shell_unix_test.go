//go:build !windows

package executor

import (
	"context"
	"runtime"
	"strings"
	"testing"
)

// On POSIX a step SimCore resolved to cmd or PowerShell is refused, never
// handed to sh: running cmd batch or PowerShell text under sh executes garbage
// that may still exit 0. Windows twin: TestRunShell_WindowsRefusesAnUnknownShell.
func TestRunShell_POSIXRefusesAForeignShell(t *testing.T) {
	for _, shell := range []string{ShellCmd, ShellPowerShell, "bash"} {
		o, err := RunShellCtxStreamOutcome(context.Background(), shell, "echo should-not-run", nil)
		if err == nil {
			t.Errorf("shell %q ran on %s: %+v", shell, runtime.GOOS, o)
		}
		if strings.Contains(o.Stdout, "should-not-run") {
			t.Errorf("shell %q text reached sh", shell)
		}
	}
	// The chained anchor refuses the same way.
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	if _, _, rc, err := s.RunStepShell("step-01", ShellCmd, "echo should-not-run"); err == nil || rc == 0 {
		t.Errorf("chained POSIX anchor ran a cmd step: rc=%d err=%v", rc, err)
	}
}
