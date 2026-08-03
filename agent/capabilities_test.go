package main

import (
	"strings"
	"testing"
)

func has(caps []string, want string) bool {
	for _, c := range caps {
		if c == want {
			return true
		}
	}
	return false
}

// SimCore's agent roster is how a DC decides where to launch a scenario. A
// Windows beacon advertising "identity-harness" would tell the operator that
// host can honour a scenario's `identity:` — it cannot (agent/identity/platform.go),
// and the lie is harder to notice here than in the per-step notice because it is
// made once, at registration, before any run exists.
func TestAgentCapabilities_WindowsDoesNotClaimIdentityHarness(t *testing.T) {
	caps := agentCapabilities("windows")
	if has(caps, "identity-harness") {
		t.Fatalf("windows beacon advertises identity-harness it cannot provide: %v", caps)
	}
	if !has(caps, "shell") {
		t.Errorf("windows beacon must still advertise shell: %v", caps)
	}
	if !has(caps, "powershell") {
		t.Errorf("windows beacon should name its interpreter: %v", caps)
	}
}

// POSIX registration is unchanged — the roster contract other tooling reads must
// not shift as a side effect of the Windows work.
func TestAgentCapabilities_POSIXUnchanged(t *testing.T) {
	for _, goos := range []string{"linux", "darwin"} {
		caps := agentCapabilities(goos)
		if strings.Join(caps, ",") != "shell,identity-harness" {
			t.Errorf("agentCapabilities(%s) = %v, want [shell identity-harness]", goos, caps)
		}
	}
}
