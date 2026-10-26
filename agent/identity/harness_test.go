package identity

import (
	"context"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestBuildWrappedCommand(t *testing.T) {
	cases := []struct {
		name      string
		id        ExecutionIdentity
		wantSub   []string // every substring must appear in the result
		wantNoSub []string // none of these may appear
		wantErr   bool
	}{
		{
			name:      "direct mode passes command through",
			id:        ExecutionIdentity{Mode: "direct", Command: "id"},
			wantSub:   []string{"id"},
			wantNoSub: []string{"sudo", "runuser", "su -s"},
		},
		{
			name:    "empty mode is treated as direct",
			id:      ExecutionIdentity{Mode: "", Command: "whoami"},
			wantSub: []string{"whoami"},
		},
		{
			name:    "runuser produces -l <user> -c <quoted-cmd>",
			id:      ExecutionIdentity{Mode: "runuser", Username: "www-data", Command: "id"},
			wantSub: []string{"runuser", "-l 'www-data'", "-c 'id'"},
		},
		{
			name:    "sudo_u splits command into args (not -c)",
			id:      ExecutionIdentity{Mode: "sudo_u", Username: "postgres", Command: "psql -V"},
			wantSub: []string{"sudo -u 'postgres'", "'psql'", "'-V'"},
		},
		{
			name:    "su uses -s /bin/bash and -c wrapper",
			id:      ExecutionIdentity{Mode: "su", Username: "nobody", Command: "echo hello"},
			wantSub: []string{"su -s /bin/bash 'nobody'", "-c 'echo hello'"},
		},
		{
			name:    "single quotes inside command get escaped",
			id:      ExecutionIdentity{Mode: "su", Username: "u", Command: "echo 'hi'"},
			wantSub: []string{`echo '\''hi'\''`},
		},
		{
			name:    "unknown mode errors",
			id:      ExecutionIdentity{Mode: "rootkit", Username: "u", Command: "x"},
			wantErr: true,
		},
		{
			name:    "sudo_u with empty command errors",
			id:      ExecutionIdentity{Mode: "sudo_u", Username: "u", Command: ""},
			wantErr: true,
		},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			out, err := buildWrappedCommand(c.id)
			if c.wantErr {
				if err == nil {
					t.Fatalf("expected error, got nil; out=%q", out)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			for _, sub := range c.wantSub {
				if !strings.Contains(out, sub) {
					t.Errorf("expected %q to contain %q", out, sub)
				}
			}
			for _, sub := range c.wantNoSub {
				if strings.Contains(out, sub) {
					t.Errorf("expected %q NOT to contain %q", out, sub)
				}
			}
		})
	}
}

func TestShellQuoteEscapesEmbeddedSingleQuotes(t *testing.T) {
	got := shellQuote("o'reilly")
	want := `'o'\''reilly'`
	if got != want {
		t.Errorf("shellQuote(%q) = %q, want %q", "o'reilly", got, want)
	}
}

func TestResolveIdentity(t *testing.T) {
	cases := []struct {
		username    string
		wantMode    string
		wantUser    string
		wantUnknown bool
	}{
		{"root", "direct", "", false},
		{"container-runtime", "direct", "", false},
		{"direct", "direct", "", false},
		{"", "direct", "", false},
		{"www-data", "runuser", "www-data", false},
		{"postgres", "runuser", "postgres", false},
		{"mysql", "runuser", "mysql", false},
		{"node", "runuser", "node", false},
		{"python3", "runuser", "python3", false},
		{"nobody", "runuser", "nobody", false},
		{"svc-backup", "runuser", "svc-backup", false},
		{"weird-svc", "runuser", "weird-svc", true},
	}
	for _, tc := range cases {
		t.Run(tc.username, func(t *testing.T) {
			mode, user, unknown := ResolveIdentity(tc.username)
			if mode != tc.wantMode || user != tc.wantUser || unknown != tc.wantUnknown {
				t.Errorf("ResolveIdentity(%q) = (%q,%q,%v); want (%q,%q,%v)",
					tc.username, mode, user, unknown, tc.wantMode, tc.wantUser, tc.wantUnknown)
			}
		})
	}
}

func TestExecuteCtx_DirectHappyPath(t *testing.T) {
	res, err := ExecuteCtx(context.Background(), ExecutionIdentity{Mode: "direct", Command: "echo hi"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if res.ExitCode != 0 {
		t.Errorf("expected exit 0, got %d", res.ExitCode)
	}
	if !strings.Contains(res.Stdout, "hi") {
		t.Errorf("stdout = %q", res.Stdout)
	}
}

func TestExecuteCtx_CancelTerminatesAndReturns130(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(300 * time.Millisecond)
		cancel()
	}()

	start := time.Now()
	res, err := ExecuteCtx(ctx, ExecutionIdentity{Mode: "direct", Command: "sleep 30"})
	elapsed := time.Since(start)

	if err != context.Canceled {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
	if res.ExitCode != 130 {
		t.Errorf("expected exit code 130 on cancel, got %d", res.ExitCode)
	}
	// 3s kill grace + slack — must be far under the 30s sleep.
	if elapsed > 10*time.Second {
		t.Errorf("cancel took too long: %s", elapsed)
	}
}

func TestExecuteCtxStream_CarriesTermination(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX signals")
	}
	r, err := ExecuteCtxStream(context.Background(),
		ExecutionIdentity{Mode: "direct", Command: "kill -9 $$"}, nil)
	if err != nil {
		t.Fatalf("err=%v", err)
	}
	if r.ExitCode != 137 || r.Termination != "signaled" || r.Signal != 9 {
		t.Fatalf("got exit=%d term=%q sig=%d, want 137/signaled/9", r.ExitCode, r.Termination, r.Signal)
	}
}

// wrapperSurfacesChildSignal is the shared body of the three owed wrapper tests
// (spec 5.2). It targets the current user's own account so it is runnable
// without a service account (sudo_u splits the command on whitespace and runs no shell, so it gets a self-killing script instead), skips when the wrapper binary or a non-interactive
// switch to that account is unavailable, and asserts ExitCode == 137 whichever
// Termination the wrapper surfaces (a wrapper that forwards the child's death
// as an exit code reports "exited"; one that re-raises reports "signaled").
// wrapperTimeout bounds both the probe and the kill run of a wrapper test.
const wrapperTimeout = 5 * time.Second

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

func wrapperSurfacesChildSignal(t *testing.T, mode, bin, killCmd string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX wrappers")
	}
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
