package identity

import (
	"errors"
	"testing"
)

func missing(string) error { return errors.New("no such user") }

func TestAccountChecker_LookupMissProbePresentIsHonoured(t *testing.T) {
	chk := NewAccountChecker(missing, func(string) ProbeResult { return ProbePresent })
	r := ResolveWith("linux", "ldapuser", "root", chk)
	if r.Mode != "runuser" || r.Degraded() {
		t.Fatalf("NSS-only account degraded: %+v", r)
	}
}

func TestAccountChecker_LookupMissProbeAbsentDegrades(t *testing.T) {
	chk := NewAccountChecker(missing, func(string) ProbeResult { return ProbeAbsent })
	r := ResolveWith("linux", "ghost", "root", chk)
	if r.Mode != "direct" || !r.Degraded() {
		t.Fatalf("absent account not degraded: %+v", r)
	}
}

func TestAccountChecker_ProbeUnknownIsTreatedAsPresentAndNotCached(t *testing.T) {
	calls := 0
	chk := NewAccountChecker(missing, func(string) ProbeResult { calls++; return ProbeUnknown })
	for i := 0; i < 2; i++ {
		if r := ResolveWith("linux", "ghost", "root", chk); r.Degraded() {
			t.Fatalf("unknown probe degraded: %+v", r)
		}
	}
	if calls != 2 {
		t.Fatalf("unknown result was cached: calls = %d, want 2", calls)
	}
}

func TestAccountChecker_ProbeCalledOnceAcrossThreeSteps(t *testing.T) {
	calls := 0
	chk := NewAccountChecker(missing, func(string) ProbeResult { calls++; return ProbeAbsent })
	for i := 0; i < 3; i++ {
		if r := ResolveWith("linux", "ghost", "root", chk); !r.Degraded() {
			t.Fatalf("step %d not degraded", i)
		}
	}
	if calls != 1 {
		t.Fatalf("probe calls = %d, want 1", calls)
	}
}

// A name outside the portable account-name shape is INCONCLUSIVE, not absent:
// uppercase, user@domain and DOMAIN\user are real accounts on directory-joined
// hosts, and runuser honoured them before the check existed. Such a name is not
// probed (never handed to `id`), not cached, and read as present, so runuser
// decides. The name is single-quoted into the wrapper (harness shellQuote), so
// this cannot become shell; see TestWrappedUsernameIsOneArgvElement.
func TestAccountChecker_NonPortableNameIsInconclusiveAndUnprobed(t *testing.T) {
	for _, name := range []string{"Administrator", "jdoe@corp.example", `CORP\jdoe`, "x; rm -rf /", "a'b"} {
		chk := NewAccountChecker(
			func(string) error { t.Fatalf("lookup called for %q", name); return nil },
			func(string) ProbeResult { t.Fatalf("probe called for %q", name); return ProbeAbsent })
		for i := 0; i < 2; i++ {
			if err := chk(name); err != nil {
				t.Fatalf("%q read as absent: %v", name, err)
			}
			if r := ResolveWith("linux", name, "root", chk); r.Degraded() || r.Mode != "runuser" {
				t.Fatalf("%q degraded: %+v", name, r)
			}
		}
	}
}

// Two shapes stay absent without running anything: a leading '-' would be read
// by runuser/su/sudo as an OPTION (the quoting stops shell injection, not option
// injection), and a control character or NUL is no account on any host (Go's
// exec refuses a NUL argument, which would read as a failed TTP).
func TestAccountChecker_OptionOrControlCharNameIsAbsentWithoutProbe(t *testing.T) {
	for _, name := range []string{"-c", "--session-command=id", "a\nb", "a\x00b", "a\tb", ""} {
		chk := NewAccountChecker(
			func(string) error { t.Fatalf("lookup called for %q", name); return nil },
			func(string) ProbeResult { t.Fatalf("probe called for %q", name); return ProbePresent })
		if err := chk(name); err == nil {
			t.Fatalf("%q read as present", name)
		}
	}
}

func TestIdProbe_RealIdSaysRootPresentAndGhostAbsent(t *testing.T) {
	if r := idProbe("root"); r == ProbeUnknown {
		t.Skip("no usable id on PATH")
	} else if r != ProbePresent {
		t.Fatalf("idProbe(root) = %v", r)
	}
	if r := idProbe("no-such-acct-xyz"); r != ProbeAbsent {
		t.Fatalf("idProbe(ghost) = %v, want absent", r)
	}
}
