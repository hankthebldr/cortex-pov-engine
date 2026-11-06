package main

import "github.com/hankthebldr/cortexsim/agent/identity"

// beaconElevated reports whether this beacon's token is elevated. A package var
// so the Windows registration roster is testable from any host.
var beaconElevated = identity.ProcessElevated

// agentCapabilities reports what this beacon can actually do, keyed off the host
// family it was compiled for.
//
// The roster is not decoration — SimCore's agent list is how a DC picks where to
// launch a scenario. Advertising "identity-harness" from a Windows beacon would
// tell the operator a Windows host can honour a scenario's `identity:` when it
// cannot (see agent/identity/platform.go), which is the same class of lie the
// per-step degradation notice exists to prevent, just one layer earlier and
// harder to notice.
//
// goos is a runtime.GOOS value, passed in so the mapping is testable from any
// host rather than only from the platform it describes.
// "artifact-fetch" is advertised on every platform and is NOT decoration
// either: an OLD beacon receiving a task that declares `artifacts` would have
// encoding/json silently drop the unknown key, run every step WITHOUT its
// tooling, exit 0 on all of them, and the missing detections would read in a POV
// report as gaps in the customer's coverage — the exact manufactured false
// negative artifact staging exists to prevent, delivered by the back-compat
// mechanism itself. SimCore refuses to hand an artifact-carrying task to an
// agent that does not advertise this, so the claim must be true whenever the
// beacon can honour it.
func agentCapabilities(goos string) []string {
	if goos == "windows" {
		// "powershell" names the interpreter the executor drives; the absence of
		// "identity-harness" is the load-bearing part.
		caps := []string{"shell", "powershell", "artifact-fetch"}
		// CONT-14: steps that assert root / container-runtime / administrator
		// need an elevated token, and a non-elevated beacon records each one as
		// IDENTITY NOT HONOURED. Advertising it lets the roster say so BEFORE a
		// run. Claimed only when TOKEN_ELEVATION says so — never assumed.
		if beaconElevated() {
			caps = append(caps, "elevated")
		}
		return caps
	}
	return []string{"shell", "identity-harness", "artifact-fetch"}
}
