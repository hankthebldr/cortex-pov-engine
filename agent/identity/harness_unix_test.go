//go:build !windows

package identity

// POSIX-only harness tests (CI-04): POSIX signals (`kill -9 $$`) and the
// runuser / sudo / su wrappers, none of which exist on Windows. Windows twins:
// TestExecuteCtxStream_WindowsSelfTerminationIsExitedNotSignaled
// (harness_windows_test.go) and TestWrapCommandFor_RefusesImpersonationOnWindows
// (platform_test.go, every host).

import (
	"context"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"testing"
	"time"
)

// wrapperTimeout bounds both the probe and the kill run of a wrapper test.
const wrapperTimeout = 5 * time.Second

func TestExecuteCtxStream_CarriesTermination(t *testing.T) {
	r, err := ExecuteCtxStream(context.Background(),
		ExecutionIdentity{Mode: "direct", Command: "kill -9 $$"}, nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	if r.ExitCode != 137 || r.Termination != "signaled" || r.Signal != 9 {
		t.Fatalf("got exit=%d term=%q sig=%d, want 137/signaled/9", r.ExitCode, r.Termination, r.Signal)
	}
}

// killScript writes an executable that SIGKILLs itself and returns its path (no
// whitespace, so it survives sudo_u's naive argument split).
func killScript(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "selfkill.sh")
	if err := os.WriteFile(p, []byte("#!/bin/sh\nkill -9 $$\n"), 0o755); err != nil {
		t.Fatalf("write script: %v", err)
	}
	return p
}

// wrapperSurfacesChildSignal is the shared body of the three owed wrapper tests
// (spec 5.2). It targets the current user's own account so it is runnable
// without a service account (sudo_u splits the command on whitespace and runs no shell, so it gets a self-killing script instead), skips when the wrapper binary or a non-interactive
// switch to that account is unavailable, and asserts ExitCode == 137 whichever
// Termination the wrapper surfaces (a wrapper that forwards the child's death
// as an exit code reports "exited"; one that re-raises reports "signaled").
func wrapperSurfacesChildSignal(t *testing.T, mode, bin, killCmd string) {
	t.Helper()
	if _, err := exec.LookPath(bin); err != nil {
		t.Skipf("%s not present on this host", bin)
	}
	u, err := user.Current()
	if err != nil {
		t.Skipf("cannot resolve current user: %v", err)
	}
	// A wrapper that prompts for a password would otherwise block until the
	// package timeout; the deadline turns that into a skip.
	pctx, pcancel := context.WithTimeout(context.Background(), wrapperTimeout)
	defer pcancel()
	probe, perr := ExecuteCtxStream(pctx,
		ExecutionIdentity{Mode: mode, Username: u.Username, Command: "true"}, nil)
	if perr != nil || probe.ExitCode != 0 {
		t.Skipf("%s -> %s not usable non-interactively here (may be awaiting a password; exit=%d err=%v)", bin, u.Username, probe.ExitCode, perr)
	}
	kctx, kcancel := context.WithTimeout(context.Background(), wrapperTimeout)
	defer kcancel()
	r, err := ExecuteCtxStream(kctx,
		ExecutionIdentity{Mode: mode, Username: u.Username, Command: killCmd}, nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	t.Logf("%s surfaced exit=%d termination=%q signal=%d", bin, r.ExitCode, r.Termination, r.Signal)
	if r.ExitCode != 137 {
		t.Fatalf("ExitCode = %d, want 137 (termination=%q)", r.ExitCode, r.Termination)
	}
}

func TestExecuteCtxStream_RunuserSurfacesChildSignal(t *testing.T) {
	wrapperSurfacesChildSignal(t, "runuser", "runuser", "kill -9 $$")
}

func TestExecuteCtxStream_SudoSurfacesChildSignal(t *testing.T) {
	wrapperSurfacesChildSignal(t, "sudo_u", "sudo", killScript(t))
}

func TestExecuteCtxStream_SuSurfacesChildSignal(t *testing.T) {
	wrapperSurfacesChildSignal(t, "su", "su", "kill -9 $$")
}
