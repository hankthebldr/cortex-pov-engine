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
	caps := agentCapabilities("windows", false)
	if has(caps, "identity-harness") {
		t.Fatalf("windows beacon advertises identity-harness it cannot provide: %v", caps)
	}
	if !has(caps, "shell") {
		t.Errorf("windows beacon must still advertise shell: %v", caps)
	}
	if !has(caps, "powershell") {
		t.Errorf("windows beacon should name its interpreter: %v", caps)
	}
	if !has(caps, "artifact-fetch") {
		t.Errorf("windows beacon must advertise artifact-fetch: %v", caps)
	}
}

// The capability gate is the ONLY thing standing between an artifact-carrying
// task and a beacon that would silently ignore the `artifacts` key, run every
// step without its tooling, and report green. SimCore refuses such a launch for
// an agent that does not advertise this string, so a beacon that CAN stage must
// say so — on every platform it compiles for.
func TestAgentCapabilities_ArtifactFetchAdvertisedOnEveryPlatform(t *testing.T) {
	for _, goos := range []string{"linux", "darwin", "windows"} {
		if !has(agentCapabilities(goos, false), "artifact-fetch") {
			t.Errorf("agentCapabilities(%s) does not advertise artifact-fetch: %v",
				goos, agentCapabilities(goos, false))
		}
	}
}

// POSIX registration is unchanged — the roster contract other tooling reads must
// not shift as a side effect of the Windows work.
func TestAgentCapabilities_POSIXUnchanged(t *testing.T) {
	// Deliberately changed in the artifact-staging pass: the roster gained
	// "artifact-fetch". The load-bearing half of this assertion is unchanged —
	// POSIX still claims identity-harness and the exact set is still pinned, so
	// a future capability cannot be added by accident.
	for _, goos := range []string{"linux", "darwin"} {
		caps := agentCapabilities(goos, false)
		if strings.Join(caps, ",") != "shell,identity-harness,artifact-fetch" {
			t.Errorf("agentCapabilities(%s) = %v, want [shell identity-harness artifact-fetch]", goos, caps)
		}
	}
}

// "container" is how SimCore knows a beacon runs in a pod; it must appear only
// when the beacon is in one, and must not change what the host family claims.
func TestAgentCapabilities_ContainerFlag(t *testing.T) {
	if !has(agentCapabilities("linux", true), "container") {
		t.Error("linux in-container beacon must advertise container")
	}
	if has(agentCapabilities("linux", false), "container") {
		t.Error("beacon outside a container must not advertise container")
	}
	if !has(agentCapabilities("linux", true), "identity-harness") {
		t.Error("container flag must keep the default linux capabilities")
	}
	w := agentCapabilities("windows", true)
	if has(w, "identity-harness") {
		t.Errorf("windows must still omit identity-harness: %v", w)
	}
	if !has(w, "container") {
		t.Errorf("windows in-container should advertise container: %v", w)
	}
}
