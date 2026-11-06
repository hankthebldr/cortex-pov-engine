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
		if !has(agentCapabilities(goos), "artifact-fetch") {
			t.Errorf("agentCapabilities(%s) does not advertise artifact-fetch: %v",
				goos, agentCapabilities(goos))
		}
	}
}

func withBeaconElevated(t *testing.T, elevated bool) {
	t.Helper()
	old := beaconElevated
	beaconElevated = func() bool { return elevated }
	t.Cleanup(func() { beaconElevated = old })
}

// CONT-14. A DC picking a Windows host for a scenario whose steps assert
// `administrator` needs to know, at the roster, whether this beacon can give
// them an elevated token — before the run, not from a degraded step afterwards.
func TestAgentCapabilities_WindowsAdvertisesElevationOnlyWhenTrue(t *testing.T) {
	withBeaconElevated(t, true)
	if !has(agentCapabilities("windows"), "elevated") {
		t.Fatalf("elevated windows beacon does not advertise elevated: %v", agentCapabilities("windows"))
	}
	withBeaconElevated(t, false)
	if has(agentCapabilities("windows"), "elevated") {
		t.Fatalf("NON-elevated windows beacon claims elevated: %v", agentCapabilities("windows"))
	}
	// Linux/macOS lists are pinned elsewhere and must not grow from this.
	withBeaconElevated(t, true)
	for _, goos := range []string{"linux", "darwin"} {
		if has(agentCapabilities(goos), "elevated") {
			t.Errorf("agentCapabilities(%s) gained elevated: %v", goos, agentCapabilities(goos))
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
		caps := agentCapabilities(goos)
		if strings.Join(caps, ",") != "shell,identity-harness,artifact-fetch" {
			t.Errorf("agentCapabilities(%s) = %v, want [shell identity-harness artifact-fetch]", goos, caps)
		}
	}
}
