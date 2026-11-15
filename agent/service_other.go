//go:build !windows

package main

import "context"

// runAsService is the Windows service dispatcher; there is no service control
// manager on Linux or macOS (systemd and launchd supervise the beacon as an
// ordinary process and stop it with a signal), so it always declines and main
// stays on the console path.
func runAsService(name string, run func(ctx context.Context) int) (handled bool, exitCode int, err error) {
	return false, 0, nil
}

// underSCM reports whether the Windows service control manager connected to
// this process. Never true off Windows.
func underSCM() bool { return false }
