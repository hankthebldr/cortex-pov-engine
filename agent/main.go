// main is the CLI entry point for the CortexSim beacon agent.
//
// Usage:
//
//	cortexsim-agent --server http://localhost:8888 --id <agent-id> [--interval 10]
//
// The agent registers with SimCore on startup, then enters a polling loop to
// fetch and execute simulation tasks.  It handles SIGINT/SIGTERM cleanly.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"github.com/hankthebldr/cortexsim/agent/beacon"
	"github.com/hankthebldr/cortexsim/agent/executor"
)

const (
	defaultServer   = "http://localhost:8888"
	defaultInterval = 10
)

// shutdownSignals are the signals that trigger a graceful stop.
//
// SIGHUP matters as much as the other two: a beacon started from an SSH session
// (the nohup fallback in the installer, or a DC running it by hand) receives
// SIGHUP when the controlling terminal goes away. Un-Notified, its DEFAULT
// action terminates the process — the beacon would die with the laptop lid, mid
// scenario, with no shutdown path. Catching it converts that into the same clean
// cancel as Ctrl-C, and under systemd/launchd it is simply never delivered.
var shutdownSignals = []os.Signal{syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP}

func main() {
	// ----------------------------------------------------------------
	// CLI flags
	// ----------------------------------------------------------------
	serverFlag := flag.String("server", defaultServer, "SimCore server URL (e.g. http://localhost:8888)")
	idFlag := flag.String("id", "", "Agent ID — required (e.g. hostname or custom label)")
	intervalFlag := flag.Int("interval", defaultInterval, "Poll interval in seconds")
	// The shelf defaults to open; this is only needed when SimCore runs
	// CORTEXSIM_K8S_PAYLOAD_AUTH=token. It is supplied at INSTALL time (baked
	// into the systemd unit / launchd plist) and never travels in the task body:
	// queued_tasks.payload is plaintext JSON in SQLite.
	artifactTokenFlag := flag.String("artifact-token", "",
		"Bearer token for the payload shelf when SimCore runs it in token mode (env: CORTEXSIM_ARTIFACT_TOKEN)")
	// Both default to OFF so an existing deployment's behaviour is byte-identical
	// after an upgrade: 0 keeps the interval ticker, false keeps whole-step
	// output delivered once on completion. Opt in per host.
	longPollFlag := flag.Int("long-poll", 0,
		"Long-poll the task queue for N seconds per request instead of ticking every --interval (0 = off, max 60). Cuts task pickup from up to --interval down to milliseconds.")
	streamOutputFlag := flag.Bool("stream-output", false,
		"Stream step stdout/stderr to SimCore as it is produced rather than only on step completion.")
	flag.Parse()

	// Validate required flag.
	if *idFlag == "" {
		fmt.Fprintln(os.Stderr, "ERROR: --id is required (e.g. --id myhost-01)")
		flag.Usage()
		os.Exit(1)
	}
	if *intervalFlag < 1 {
		fmt.Fprintln(os.Stderr, "ERROR: --interval must be at least 1 second")
		os.Exit(1)
	}
	// Mirror the server-side bound (core/api/agents.py declares wait as
	// ge=0, le=60) so a bad value is rejected here with a clear message
	// instead of becoming a 422 on every poll.
	if *longPollFlag < 0 || *longPollFlag > 60 {
		fmt.Fprintln(os.Stderr, "ERROR: --long-poll must be between 0 and 60 seconds (0 disables it)")
		os.Exit(1)
	}

	// Configure stdlib logger to write to stderr with timestamps.
	log.SetOutput(os.Stderr)
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetPrefix("[cortexsim-agent] ")

	log.Printf("starting — server=%s id=%s interval=%ds long-poll=%ds stream-output=%t",
		*serverFlag, *idFlag, *intervalFlag, *longPollFlag, *streamOutputFlag)

	// ----------------------------------------------------------------
	// Build beacon client
	// ----------------------------------------------------------------
	client := beacon.New(*serverFlag, *idFlag, time.Duration(*intervalFlag)*time.Second)
	client.LongPollTimeout = *longPollFlag
	client.StreamOutput = *streamOutputFlag

	artifactToken := *artifactTokenFlag
	if artifactToken == "" {
		artifactToken = os.Getenv("CORTEXSIM_ARTIFACT_TOKEN")
	}
	if artifactToken != "" {
		client.SetArtifactToken(artifactToken)
		log.Printf("payload-shelf token configured for artifact fetches")
	}

	// Remove staging directories a previous beacon left behind when it was
	// SIGKILLed, OOM-killed, or the host rebooted — the cases where the deferred
	// per-run Cleanup does not run. Offensive tooling must not accumulate on a
	// customer endpoint across a POV.
	if n := beacon.SweepStaleStaging(); n > 0 {
		log.Printf("swept %d stale artifact staging director(ies) from a previous run", n)
	}

	// ----------------------------------------------------------------
	// Register with SimCore
	// ----------------------------------------------------------------
	hostname, err := os.Hostname()
	if err != nil {
		log.Printf("WARNING: could not determine hostname: %v — using agent ID", err)
		hostname = *idFlag
	}

	capabilities := agentCapabilities(runtime.GOOS)

	// Honest, live snapshot of what interpreters this host actually has right
	// now — never a guess, never derived from GOOS. SimCore uses it to
	// preflight a scenario's declared `requires_interpreters` before dispatch;
	// the beacon re-checks live at execution time regardless (see
	// resolveRuntimeDeps in agent/beacon/client.go), so a stale registration
	// snapshot can never cause a silent false success — only a slower-to-refuse
	// one, which the beacon's own live check closes.
	interpreters := executor.AvailableLogicalNames()

	log.Printf("registering — hostname=%s os=%s capabilities=%v interpreters=%v",
		hostname, runtime.GOOS, capabilities, interpreters)

	if err := client.Register(hostname, runtime.GOOS, capabilities, interpreters); err != nil {
		// Registration failure is logged as a warning but does not abort startup —
		// SimCore may be momentarily unavailable and the first poll can re-register.
		log.Printf("WARNING: registration failed (will retry on next poll cycle): %v", err)
	} else {
		log.Printf("registration OK")
	}

	// ----------------------------------------------------------------
	// Signal handling — honour SIGINT and SIGTERM
	// ----------------------------------------------------------------
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, shutdownSignals...)

	go func() {
		sig := <-sigCh
		log.Printf("received signal %s — initiating graceful shutdown", sig)
		cancel()
	}()

	// ----------------------------------------------------------------
	// Main poll loop (blocks until context is cancelled)
	// ----------------------------------------------------------------
	client.Run(ctx)

	log.Printf("agent exited cleanly")
}
