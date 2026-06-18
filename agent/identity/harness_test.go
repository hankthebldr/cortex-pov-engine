package identity

import (
	"context"
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
			name: "direct mode passes command through",
			id:   ExecutionIdentity{Mode: "direct", Command: "id"},
			wantSub: []string{"id"},
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
