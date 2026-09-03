package identity

import (
	"strings"
	"testing"
)

// macOS is a DECLARED impersonation platform (spec impersonation_platforms =
// [linux, darwin]) and 110 corpus steps target it, but Darwin has no runuser —
// that is util-linux — and BSD su takes no -s flag. Emitting the Linux wrapper
// there made every identity step exit 127 "command not found", which a run
// record shows as a failed TTP rather than as a wrapper the host cannot run.
func TestResolveFor_DarwinDoesNotEmitRunuser(t *testing.T) {
	for _, acct := range []string{"www-data", "svc-backup", "postgres", "vertex-agent"} {
		res := ResolveFor("darwin", acct, "root")

		if res.Mode == "runuser" {
			t.Fatalf("ResolveFor(darwin, %q) still emits runuser — macOS has no such binary", acct)
		}
		if !res.Honoured {
			t.Errorf("ResolveFor(darwin, %q) not honoured; darwin IS an impersonation platform", acct)
		}
		if res.Username != acct {
			t.Errorf("ResolveFor(darwin, %q) lost the username: %q", acct, res.Username)
		}

		wrapped, err := WrapCommandFor("darwin", ExecutionIdentity{
			Mode: res.Mode, Username: res.Username, Command: "echo hi && id",
		})
		if err != nil {
			t.Fatalf("WrapCommandFor(darwin, %q): %v", acct, err)
		}
		if strings.Contains(wrapped, "runuser") {
			t.Errorf("darwin wrapper contains runuser: %s", wrapped)
		}
		// -n or the step hangs on a password prompt until the timeout kills it,
		// reporting 124 with no diagnostic.
		if !strings.Contains(wrapped, "sudo -n -u") {
			t.Errorf("darwin wrapper is not non-interactive sudo: %s", wrapped)
		}
		// The shell must survive: sudo_u's whitespace split would drop the `&&`.
		if !strings.Contains(wrapped, "/bin/sh -c") {
			t.Errorf("darwin wrapper does not preserve shell semantics: %s", wrapped)
		}
		if !strings.Contains(wrapped, "echo hi && id") {
			t.Errorf("darwin wrapper mangled the command: %s", wrapped)
		}
	}
}

// Linux must be untouched — runuser is correct there and is what every golden
// push bundle and every existing run record already encodes.
func TestResolveFor_LinuxStillUsesRunuser(t *testing.T) {
	res := ResolveFor("linux", "www-data", "root")
	if res.Mode != "runuser" {
		t.Fatalf("ResolveFor(linux, www-data).Mode = %q, want runuser", res.Mode)
	}
	wrapped, err := WrapCommandFor("linux", ExecutionIdentity{
		Mode: res.Mode, Username: res.Username, Command: "id",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(wrapped, "runuser -l ") {
		t.Errorf("linux wrapper changed: %s", wrapped)
	}
}

// direct means "no impersonation" on every platform, darwin included.
func TestResolveFor_DarwinDirectIsUnwrapped(t *testing.T) {
	for _, ident := range []string{"root", "container-runtime", "direct", ""} {
		res := ResolveFor("darwin", ident, "root")
		if res.Mode != "direct" {
			t.Errorf("ResolveFor(darwin, %q).Mode = %q, want direct", ident, res.Mode)
		}
		wrapped, err := WrapCommandFor("darwin", ExecutionIdentity{
			Mode: res.Mode, Username: res.Username, Command: "id",
		})
		if err != nil || wrapped != "id" {
			t.Errorf("ResolveFor(darwin, %q) wrapped a direct command: %q (%v)", ident, wrapped, err)
		}
	}
}

// An undeclared account on darwin still resolves and still reports Unknown, so
// the beacon's WARN survives the platform switch.
func TestResolveFor_DarwinUnknownAccountStillFlagged(t *testing.T) {
	res := ResolveFor("darwin", "not-a-declared-account", "root")
	if !res.Unknown {
		t.Error("darwin lost the Unknown flag for an undeclared account")
	}
	if res.Mode != "sudo_sh" {
		t.Errorf("Mode = %q, want sudo_sh", res.Mode)
	}
}
