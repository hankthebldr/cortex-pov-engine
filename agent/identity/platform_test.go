package identity

import (
	"runtime"
	"strings"
	"testing"
)

// THE headline correctness property: on Windows the beacon has no unattended
// impersonation primitive, so a step declaring a service account CANNOT run as
// it. What must never happen is that it runs as the beacon user while the run
// record still says otherwise — a POV readout asserting a privileged actor that
// was never used is a lie a customer builds detection decisions on.
func TestResolveFor_WindowsServiceAccountIsDegradedNotSilent(t *testing.T) {
	for _, requested := range []string{"www-data", "administrator", "svc-account", "svc-backup"} {
		res := ResolveFor("windows", requested, `CONTOSO\svc_cortexsim`)

		if res.Honoured {
			t.Errorf("ResolveFor(windows, %q) claims the identity was honoured — it cannot be", requested)
		}
		if !res.Degraded() {
			t.Fatalf("ResolveFor(windows, %q) produced NO degradation — the run record would "+
				"claim the step ran as %q when it ran as the beacon", requested, requested)
		}
		// The notice has to name both the claim and the reality, or an operator
		// reading it cannot tell what was actually validated.
		if !strings.Contains(res.Degradation, requested) {
			t.Errorf("degradation does not name the requested identity %q: %s", requested, res.Degradation)
		}
		if !strings.Contains(res.Degradation, `CONTOSO\svc_cortexsim`) {
			t.Errorf("degradation does not name the account that actually ran: %s", res.Degradation)
		}
		// And it must NOT hand the executor a POSIX wrapper that would just fail
		// confusingly on Windows.
		if res.Mode != "direct" {
			t.Errorf("ResolveFor(windows, %q).Mode = %q, want direct", requested, res.Mode)
		}
	}
}

// The one honest exception: a beacon installed as a service under
// NT AUTHORITY\SYSTEM running a step that asks for SYSTEM really IS running as
// SYSTEM. Reporting that as degraded would be the opposite error — crying wolf
// until operators stop reading the notice.
func TestResolveFor_WindowsHonoursIdentityTheBeaconAlreadyHas(t *testing.T) {
	cases := []struct{ requested, current string }{
		{"SYSTEM", `NT AUTHORITY\SYSTEM`},
		{`NT AUTHORITY\SYSTEM`, `NT AUTHORITY\SYSTEM`},
		{"svc-backup", `CONTOSO\svc-backup`},
		{"Administrator", `WIN-HOST\administrator`}, // Windows accounts are case-insensitive
		{"svc-backup", "svc-backup@contoso.local"},
	}
	for _, tc := range cases {
		res := ResolveFor("windows", tc.requested, tc.current)
		if !res.Honoured || res.Degraded() {
			t.Errorf("ResolveFor(windows, %q, current=%q) reported a degradation, but the beacon "+
				"IS that principal: %s", tc.requested, tc.current, res.Degradation)
		}
	}
}

// Conservative matching: a partial or prefix match must NOT count as the same
// account, or the "already running as it" exception becomes a hole through which
// the original lie returns.
func TestResolveFor_WindowsDoesNotFuzzyMatchAccounts(t *testing.T) {
	for _, tc := range []struct{ requested, current string }{
		{"administrator", `CONTOSO\administrators`},
		{"svc-backup", `CONTOSO\svc-backup-2`},
		{"SYSTEM", `CONTOSO\SYSTEM-SVC`},
	} {
		res := ResolveFor("windows", tc.requested, tc.current)
		if res.Honoured {
			t.Errorf("ResolveFor(windows, %q, current=%q) matched two DIFFERENT accounts",
				tc.requested, tc.current)
		}
	}
}

// Direct mode asks for no impersonation, so running as the beacon account is
// exactly what was requested — on Windows as on POSIX. Flagging these would
// degrade the majority of scenarios for no reason.
func TestResolveFor_WindowsDirectIdentitiesAreNotDegraded(t *testing.T) {
	for _, requested := range []string{"", "root", "direct", "container-runtime"} {
		res := ResolveFor("windows", requested, `WIN-HOST\dc`)
		if res.Degraded() || !res.Honoured {
			t.Errorf("ResolveFor(windows, %q) degraded a direct identity: %s", requested, res.Degradation)
		}
		if res.Mode != "direct" {
			t.Errorf("ResolveFor(windows, %q).Mode = %q, want direct", requested, res.Mode)
		}
	}
}

// POSIX behaviour must be untouched by the Windows work: the harness really can
// impersonate there, so nothing is degraded and the mode/user pair is unchanged.
func TestResolveFor_POSIXIsUnchanged(t *testing.T) {
	for _, goos := range []string{"linux", "darwin"} {
		res := ResolveFor(goos, "www-data", "root")
		if res.Mode != "runuser" || res.Username != "www-data" {
			t.Errorf("ResolveFor(%s, www-data) = %+v, want runuser/www-data", goos, res)
		}
		if !res.Honoured || res.Degraded() {
			t.Errorf("ResolveFor(%s, www-data) degraded a POSIX impersonation: %s", goos, res.Degradation)
		}

		direct := ResolveFor(goos, "root", "root")
		if direct.Mode != "direct" || direct.Username != "" || direct.Degraded() {
			t.Errorf("ResolveFor(%s, root) = %+v, want direct/\"\"", goos, direct)
		}

		unknown := ResolveFor(goos, "some-random-acct", "root")
		if !unknown.Unknown || unknown.Mode != "runuser" {
			t.Errorf("ResolveFor(%s, unknown) = %+v, want runuser with Unknown=true", goos, unknown)
		}
		// Unknown is best-effort, not a lie: runuser fails loudly at exec time.
		if unknown.Degraded() {
			t.Errorf("an unknown POSIX account must not be pre-emptively degraded: %s", unknown.Degradation)
		}
	}
}

// Defence in depth: if a future caller hand-builds an ExecutionIdentity and
// skips resolution, wrapping must refuse rather than hand PowerShell a
// `runuser -l www-data -c ...` string, which would fail with an obscure
// "term not recognized" and a plausible-looking non-zero exit that reads as a
// failed TTP rather than a broken harness.
func TestWrapCommandFor_RefusesImpersonationOnWindows(t *testing.T) {
	for _, mode := range []string{"runuser", "sudo_u", "su"} {
		_, err := WrapCommandFor("windows", ExecutionIdentity{
			Mode: mode, Username: "www-data", Command: "whoami",
		})
		if err == nil {
			t.Errorf("WrapCommandFor(windows, %s) silently produced a POSIX wrapper", mode)
		}
	}

	// direct is fine everywhere and must pass the command through untouched.
	got, err := WrapCommandFor("windows", ExecutionIdentity{Mode: "direct", Command: "whoami"})
	if err != nil || got != "whoami" {
		t.Fatalf("WrapCommandFor(windows, direct) = (%q, %v), want (whoami, nil)", got, err)
	}

	// POSIX wrapping is unchanged.
	got, err = WrapCommandFor("linux", ExecutionIdentity{
		Mode: "runuser", Username: "www-data", Command: "id",
	})
	if err != nil || got != "runuser -l 'www-data' -c 'id'" {
		t.Fatalf("WrapCommandFor(linux, runuser) = (%q, %v)", got, err)
	}
}

// Resolve must be ResolveFor bound to this host — otherwise the platform logic
// above is dead code that the beacon never reaches.
func TestResolve_DelegatesToThisHost(t *testing.T) {
	got := Resolve("www-data")
	want := ResolveFor(runtime.GOOS, "www-data", CurrentUsername())
	if got != want {
		t.Fatalf("Resolve(www-data) = %+v, want %+v", got, want)
	}
}
