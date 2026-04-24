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
)

const (
	defaultServer   = "http://localhost:8888"
	defaultInterval = 10
)

func main() {
	// ----------------------------------------------------------------
	// CLI flags
	// ----------------------------------------------------------------
	serverFlag := flag.String("server", defaultServer, "SimCore server URL (e.g. http://localhost:8888)")
	idFlag := flag.String("id", "", "Agent ID — required (e.g. hostname or custom label)")
	intervalFlag := flag.Int("interval", defaultInterval, "Poll interval in seconds")
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

	// Configure stdlib logger to write to stderr with timestamps.
	log.SetOutput(os.Stderr)
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetPrefix("[cortexsim-agent] ")

	log.Printf("starting — server=%s id=%s interval=%ds", *serverFlag, *idFlag, *intervalFlag)

	// ----------------------------------------------------------------
	// Build beacon client
	// ----------------------------------------------------------------
	client := beacon.New(*serverFlag, *idFlag, time.Duration(*intervalFlag)*time.Second)

	// ----------------------------------------------------------------
	// Register with SimCore
	// ----------------------------------------------------------------
	hostname, err := os.Hostname()
	if err != nil {
		log.Printf("WARNING: could not determine hostname: %v — using agent ID", err)
		hostname = *idFlag
	}

	capabilities := []string{"shell", "identity-harness"}

	log.Printf("registering — hostname=%s os=%s capabilities=%v", hostname, runtime.GOOS, capabilities)

	if err := client.Register(hostname, runtime.GOOS, capabilities); err != nil {
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
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

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
