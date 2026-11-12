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
		// Elevated or not, an account the beacon is not cannot be honoured.
		for _, elevated := range []bool{true, false} {
			res := ResolveForElevation("windows", requested, `CONTOSO\svc_cortexsim`, elevated)
			assertWindowsDegradation(t, requested, res)
		}
	}
}

func assertWindowsDegradation(t *testing.T, requested string, res Resolution) {
	t.Helper()
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
		// An elevated beacon (SYSTEM always is; an administrator once UAC-elevated).
		res := ResolveForElevation("windows", tc.requested, tc.current, true)
		if !res.Honoured || res.Degraded() {
			t.Errorf("ResolveFor(windows, %q, current=%q) reported a degradation, but the beacon "+
				"IS that principal: %s", tc.requested, tc.current, res.Degradation)
		}
	}
	// CONT-14: being the Administrator ACCOUNT is not having its rights. Under
	// UAC an administrator's process runs with a filtered token; the account
	// name matches, the privilege does not.
	if res := ResolveForElevation("windows", "Administrator", `WIN-HOST\administrator`, false); res.Honoured {
		t.Errorf("a UAC-filtered (non-elevated) Administrator token was honoured as administrator")
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
		res := ResolveForElevation("windows", tc.requested, tc.current, true)
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
	// On an ELEVATED beacon; the non-elevated case is
	// TestResolveFor_WindowsElevatedIdentitiesNeedAnElevatedToken.
	for _, requested := range []string{"", "root", "direct", "container-runtime"} {
		res := ResolveForElevation("windows", requested, `WIN-HOST\dc`, true)
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
	// The WRAPPER differs by host family and the mode name says which: linux has
	// runuser (util-linux), darwin does not, and BSD su takes no -s flag either,
	// so darwin impersonates through `sudo -n -u <user> /bin/sh -c`. Everything
	// else below — honoured, never degraded, Unknown preserved — must hold
	// identically on both, because both ARE impersonation platforms.
	wantMode := map[string]string{"linux": "runuser", "darwin": "sudo_sh"}
	for _, goos := range []string{"linux", "darwin"} {
		res := ResolveFor(goos, "www-data", "root")
		if res.Mode != wantMode[goos] || res.Username != "www-data" {
			t.Errorf("ResolveFor(%s, www-data) = %+v, want %s/www-data", goos, res, wantMode[goos])
		}
		if !res.Honoured || res.Degraded() {
			t.Errorf("ResolveFor(%s, www-data) degraded a POSIX impersonation: %s", goos, res.Degradation)
		}

		direct := ResolveFor(goos, "root", "root")
		if direct.Mode != "direct" || direct.Username != "" || direct.Degraded() {
			t.Errorf("ResolveFor(%s, root) = %+v, want direct/\"\"", goos, direct)
		}

		unknown := ResolveFor(goos, "some-random-acct", "root")
		if !unknown.Unknown || unknown.Mode != wantMode[goos] {
			t.Errorf("ResolveFor(%s, unknown) = %+v, want %s with Unknown=true",
				goos, unknown, wantMode[goos])
		}
		// Unknown is best-effort, not a lie: the wrapper fails loudly at exec time.
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

func withElevation(t *testing.T, elevated bool) {
	t.Helper()
	old := processElevated
	processElevated = func() bool { return elevated }
	t.Cleanup(func() { processElevated = old })
}

// CONT-14 / CONT-07. `root`, `container-runtime` and `administrator` assert
// administrative rights. A beacon whose token is NOT elevated (installed under
// a standard account, or an admin account under UAC's filtered token) runs
// those steps without them: the TTP fails or behaves differently, and the run
// record must not say the identity was honoured.
func TestResolveFor_WindowsElevatedIdentitiesNeedAnElevatedToken(t *testing.T) {
	withElevation(t, false)
	for _, requested := range []string{"root", "container-runtime", "administrator"} {
		res := ResolveFor("windows", requested, `WIN-HOST\administrator`)
		if res.Honoured || !res.Degraded() {
			t.Errorf("ResolveFor(windows, %q) on a NON-elevated token was honoured", requested)
			continue
		}
		if !strings.Contains(res.Degradation, requested+" requires an elevated token; this beacon is not elevated") {
			t.Errorf("degradation for %q does not name the elevation gap: %s", requested, res.Degradation)
		}
		if res.Mode != "direct" {
			t.Errorf("Mode = %q, want direct", res.Mode)
		}
	}
	// Plain direct asks for nothing and stays honoured.
	for _, requested := range []string{"", "direct"} {
		if res := ResolveFor("windows", requested, `WIN-HOST\dc`); !res.Honoured || res.Degraded() {
			t.Errorf("ResolveFor(windows, %q) degraded plain direct: %s", requested, res.Degradation)
		}
	}
}

// Resolve must be ResolveWith bound to this host (and its account lookup) —
// otherwise the platform logic
// above is dead code that the beacon never reaches.
func TestResolve_DelegatesToThisHost(t *testing.T) {
	got := Resolve("www-data")
	want := ResolveWith(runtime.GOOS, "www-data", CurrentUsername(), lookupAccount)
	if got != want {
		t.Fatalf("Resolve(www-data) = %+v, want %+v", got, want)
	}
}
