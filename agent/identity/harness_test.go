package identity

import (
	"strings"
	"testing"
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
