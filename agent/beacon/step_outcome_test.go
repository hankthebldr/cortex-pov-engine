package beacon

import "testing"

func TestCheckRequiredBinaries(t *testing.T) {
	if got := checkRequiredBinaries(&Step{}); got != nil {
		t.Errorf("step with no binaries must not be checked, got %v", got)
	}
	got := checkRequiredBinaries(&Step{RequiredBinaries: []string{"sh", "cortexsim-no-such-tool", "cortexsim-also-missing"}})
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
