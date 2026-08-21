//go:build !windows

package main

import (
	"os"
	"os/signal"
	"syscall"
	"testing"
	"time"
)

// TestShutdownSignalsIncludesSIGHUP proves the beacon survives a hangup as a
// GRACEFUL shutdown rather than a kill.
//
// This is not a set-membership assertion dressed up as a test: it registers the
// production signal set and actually delivers SIGHUP to this process. If SIGHUP
// were missing from shutdownSignals, the default disposition would terminate the
// test binary outright — the run fails loudly rather than silently passing.
// That matters because the installer's nohup fallback (and any DC running the
// beacon by hand over ssh) gets SIGHUP the moment the controlling terminal goes
// away.
func TestShutdownSignalsIncludesSIGHUP(t *testing.T) {
	ch := make(chan os.Signal, 1)
	signal.Notify(ch, shutdownSignals...)
	defer signal.Stop(ch)

	if err := syscall.Kill(syscall.Getpid(), syscall.SIGHUP); err != nil {
		t.Fatalf("could not deliver SIGHUP to self: %v", err)
	}

	select {
	case got := <-ch:
		if got != syscall.SIGHUP {
			t.Fatalf("expected SIGHUP, got %v", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("SIGHUP was not delivered to the notified channel — it is not in shutdownSignals")
	}
}

// TestShutdownSignalsCoversInterruptAndTerm guards the pre-existing contract so
// adding SIGHUP cannot quietly drop the signals systemd and Ctrl-C rely on.
func TestShutdownSignalsCoversInterruptAndTerm(t *testing.T) {
	want := map[os.Signal]bool{syscall.SIGINT: false, syscall.SIGTERM: false, syscall.SIGHUP: false}
	for _, s := range shutdownSignals {
		if _, ok := want[s]; ok {
			want[s] = true
		}
	}
	for sig, seen := range want {
		if !seen {
			t.Errorf("shutdownSignals is missing %v", sig)
		}
	}
}
