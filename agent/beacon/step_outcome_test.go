package beacon

import (
	"os"
	"testing"
)

func TestCheckRequiredBinaries(t *testing.T) {
	// The check itself is host-neutral: drive the POSIX branch explicitly and
	// use THIS test binary as the present tool, so it runs on every host (CI-04).
	old := runtimeDepsGOOS
	runtimeDepsGOOS = "linux"
	defer func() { runtimeDepsGOOS = old }()
	present, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	if got := checkRequiredBinaries(&Step{}); got != nil {
		t.Errorf("step with no binaries must not be checked, got %v", got)
	}
	got := checkRequiredBinaries(&Step{RequiredBinaries: []string{present, "cortexsim-no-such-tool", "cortexsim-also-missing"}})
	if len(got) != 2 || got[0] != "cortexsim-no-such-tool" || got[1] != "cortexsim-also-missing" {
		t.Errorf("unexpected missing set: %v", got)
	}
}

func TestCheckRequiredBinaries_SkippedOnWindows(t *testing.T) {
	old := runtimeDepsGOOS
	runtimeDepsGOOS = "windows"
	defer func() { runtimeDepsGOOS = old }()
	if got := checkRequiredBinaries(&Step{RequiredBinaries: []string{"cortexsim-no-such-tool"}}); got != nil {
		t.Errorf("must be skipped on windows, got %v", got)
	}
}
