package main

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
func agentCapabilities(goos string) []string {
	if goos == "windows" {
		// "powershell" names the interpreter the executor drives; the absence of
		// "identity-harness" is the load-bearing part.
		return []string{"shell", "powershell"}
	}
	return []string{"shell", "identity-harness"}
}
