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

func TestAccountChecker_HostileNameIsAbsentWithoutProbe(t *testing.T) {
	chk := NewAccountChecker(
		func(string) error { t.Fatal("lookup called for hostile name"); return nil },
		func(string) ProbeResult { t.Fatal("probe called for hostile name"); return ProbePresent })
	r := ResolveWith("linux", "x; rm -rf /", "root", chk)
	if !r.Degraded() || r.Mode != "direct" {
		t.Fatalf("hostile name not degraded: %+v", r)
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
