package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"time"
)

// shutdownGrace bounds how long main waits for the poll loop after a shutdown
// signal. The pod's termination grace period is 30 s; exiting inside it means
// the beacon is gone before the kubelet's SIGKILL, not killed by it.
const shutdownGrace = 20 * time.Second

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
// return. It reports whether that wait timed out.
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
		return true
	}
}
