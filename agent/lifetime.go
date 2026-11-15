package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/agent/beacon"
)

// shutdownGrace bounds how long main waits for the poll loop after a shutdown
// signal. The pod's termination grace period is 30 s; exiting inside it means
// the beacon is gone before the kubelet's SIGKILL, not killed by it.
const shutdownGrace = 20 * time.Second

// cleanupInFlight reports whether the beacon is running a scenario's cleanup
// (beacon/cleanup.go). A package var only so a test can drive it.
var cleanupInFlight = beacon.CleanupInFlight

// cleanupGrace is how much longer than shutdownGrace main waits when a
// scenario cleanup is still running at the end of it. The cleanup has its own
// bound from the signal (beacon.CleanupShutdownBudget); exiting under it would
// leave the host half cleaned and the run without its completion. A supervisor
// with a shorter stop timer (a pod's 30 s, launchd's 20 s) still kills the
// beacon at its own deadline. A var only so a test can shrink it.
var cleanupGrace = beacon.CleanupShutdownBudget

// expiredAtStartup reports whether a non-zero expiry is already at or before now.
func expiredAtStartup(expiresAt, now time.Time) bool {
	return !expiresAt.IsZero() && !now.Before(expiresAt)
}

// parseExpiresAt reads --expires-at. Empty means the beacon never expires; any
// other value must be RFC3339. A value that does not parse is an error, never a
// silent "no expiry": a pod would otherwise outlive the lifetime it was granted.
func parseExpiresAt(s string) (time.Time, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return time.Time{}, nil
	}
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		return time.Time{}, fmt.Errorf("--expires-at %q is not an RFC3339 timestamp: %w", s, err)
	}
	return t, nil
}

// runUntilShutdown runs the poll loop until it returns on its own (lifetime
// expiry) or a signal arrives. On a signal it cancels ctx — which kills the
// in-flight step's process group — and waits at most grace for the loop to
// return, or grace plus cleanupGrace when a scenario cleanup is running at the
// end of grace. It reports whether that wait timed out.
func runUntilShutdown(ctx context.Context, cancel context.CancelFunc,
	run func(context.Context), sigCh <-chan os.Signal, grace time.Duration) (timedOut bool) {

	done := make(chan struct{})
	go func() {
		run(ctx)
		close(done)
	}()
	select {
	case <-done:
		return false
	case sig := <-sigCh:
		log.Printf("received signal %s — initiating graceful shutdown", sig)
		cancel()
	}
	select {
	case <-done:
		return false
	case <-time.After(grace):
	}
	if !cleanupInFlight() {
		return true
	}
	log.Printf("shutdown grace %s spent with a scenario cleanup still running — waiting up to %s more",
		grace, cleanupGrace)
	select {
	case <-done:
		return false
	case <-time.After(cleanupGrace):
		return true
	}
}
