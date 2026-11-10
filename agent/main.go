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
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/hankthebldr/cortexsim/agent/beacon"
	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
	"github.com/hankthebldr/cortexsim/agent/runtimefacts"
)

const (
	defaultServer   = "http://localhost:8888"
	defaultInterval = 10
	defaultStateDir = "/var/lib/cortexsim-agent"

	// enrollAttempts / enrollRetryDelay bound how long a pod waits for SimCore
	// to become reachable before it crash-loops visibly.
	enrollAttempts   = 5
	enrollRetryDelay = 3 * time.Second
)

// validateIdentityFlags enforces that exactly one of --id / --enroll-token-file
// names how this beacon gets its agent id.
func validateIdentityFlags(id, tokenFile string) error {
	if id != "" && tokenFile != "" {
		return fmt.Errorf("--id and --enroll-token-file are mutually exclusive")
	}
	if id == "" && tokenFile == "" {
		return fmt.Errorf("--id or --enroll-token-file is required (e.g. --id myhost-01)")
	}
	return nil
}

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
	// 0 keeps the built-in default (180s). Raise it on a slow or proxied link:
	// tier-4 adapter packs install their tool inline at dispatch (apt-get
	// install hydra / john / hashcat / metasploit), and a step killed mid
	// install reports exit 124 with no detection — which reads in a POV report
	// as a miss by the customer's stack rather than a beacon budget.
	stepTimeoutFlag := flag.Int("step-timeout", 0,
		"Per-step execution timeout in seconds (0 = built-in default of 180). A step exceeding it is terminated and reported as exit 124.")
	// Native enrollment (container deployment): redeem a token from a file
	// instead of being handed an --id, and remember the id across restarts.
	enrollTokenFileFlag := flag.String("enroll-token-file", "",
		"Path to a file holding an enrollment token. The beacon enrolls itself and stores the assigned id under --state-dir. Mutually exclusive with --id.")
	stateDirFlag := flag.String("state-dir", defaultStateDir,
		"Directory where the enrolled agent id is kept across restarts (used with --enroll-token-file)")
	postureProfileFlag := flag.String("posture-profile", "",
		"Posture profile this pod was deployed with, reported to SimCore as a runtime fact")
	prepareStatusFileFlag := flag.String("prepare-status-file", "",
		"File whose first line is the pod's tool-preparation status, reported to SimCore as a runtime fact")
	flag.Parse()

	// Validate required flag.
	if err := validateIdentityFlags(*idFlag, *enrollTokenFileFlag); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
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
	if *stepTimeoutFlag < 0 {
		fmt.Fprintln(os.Stderr, "ERROR: --step-timeout must be >= 0 (0 selects the built-in default)")
		os.Exit(1)
	}

	// Configure stdlib logger to write to stderr with timestamps.
	log.SetOutput(os.Stderr)
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetPrefix("[cortexsim-agent] ")

	// The server URL usually comes from a Kubernetes Secret and may carry a
	// trailing slash or newline: normalise it once, before any use.
	serverURL := beacon.NormalizeServerURL(*serverFlag)

	// Everything the agent reports about itself is gathered once, up front, so
	// the same snapshot goes on Enroll and on Register.
	hostname, err := os.Hostname()
	if err != nil {
		log.Printf("WARNING: could not determine hostname: %v — using agent ID", err)
		hostname = *idFlag
		if hostname == "" {
			hostname = "unknown"
		}
	}
	capabilities := agentCapabilities(runtime.GOOS, runtimefacts.InContainer(nil))
	// Honest, live snapshot of what interpreters this host actually has right
	// now — never a guess, never derived from GOOS. SimCore uses it to
	// preflight a scenario's declared `requires_interpreters` before dispatch;
	// the beacon re-checks live at execution time regardless (see
	// resolveRuntimeDeps in agent/beacon/client.go), so a stale registration
	// snapshot can never cause a silent false success — only a slower-to-refuse
	// one, which the beacon's own live check closes.
	interpreters := executor.AvailableLogicalNames()
	facts := runtimefacts.Collect(runtimefacts.Options{
		PostureProfile:    *postureProfileFlag,
		PrepareStatusFile: *prepareStatusFileFlag,
		ProbeTools:        runtimefacts.DefaultProbeTools,
		ProbeAccounts:     identity.ServiceAccountNames(),
	})

	agentID := *idFlag
	if *enrollTokenFileFlag != "" {
		agentID = enrollOrExit(serverURL, *enrollTokenFileFlag, *stateDirFlag,
			hostname, capabilities, interpreters, &facts)
	}

	log.Printf("starting — server=%s id=%s interval=%ds long-poll=%ds stream-output=%t step-timeout=%ds",
		serverURL, agentID, *intervalFlag, *longPollFlag, *streamOutputFlag, *stepTimeoutFlag)

	// ----------------------------------------------------------------
	// Build beacon client
	// ----------------------------------------------------------------
	client := beacon.New(serverURL, agentID, time.Duration(*intervalFlag)*time.Second)
	client.SetRuntimeFacts(&facts)
	client.LongPollTimeout = *longPollFlag
	client.StreamOutput = *streamOutputFlag
	if *stepTimeoutFlag > 0 {
		client.StepTimeout = time.Duration(*stepTimeoutFlag) * time.Second
	}

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

// enrollOrExit redeems the enrollment token (or reuses the id stored from an
// earlier life of this pod) and returns the agent id. Any failure is fatal: a
// pod that cannot enroll must crash-loop visibly rather than idle. The token is
// never logged; only its last 6 characters are.
func enrollOrExit(serverURL, tokenFile, stateDir, hostname string,
	capabilities, interpreters []string, facts *runtimefacts.Facts) string {

	token, err := beacon.ReadTokenFile(tokenFile)
	if err != nil {
		if errors.Is(err, beacon.ErrEnrollTokenEmpty) {
			log.Printf("FATAL ENROLL_TOKEN_EMPTY: %s is empty", tokenFile)
		} else {
			log.Printf("FATAL ENROLL_TOKEN_UNREADABLE: %v", err)
		}
		os.Exit(1)
	}

	desired := strings.TrimSpace(os.Getenv("POD_NAME"))
	if desired == "" {
		desired = hostname
	}

	doEnroll := func() (string, error) {
		log.Printf("enrolling — server=%s token=…%s", serverURL, beacon.TokenSuffix(token))
		return beacon.EnrollWithRetry(enrollAttempts, enrollRetryDelay, time.Sleep, func() (string, error) {
			return beacon.Enroll(serverURL, token, hostname, runtime.GOOS, desired,
				capabilities, interpreters, facts)
		})
	}
	id, reused, err := beacon.LoadOrEnroll(stateDir, doEnroll)
	if err != nil {
		var ee *beacon.EnrollError
		switch {
		case errors.As(err, &ee) && ee.Code != "":
			log.Printf("FATAL %s: %v", ee.Code, err)
			os.Exit(1)
		case id == "":
			log.Printf("FATAL ENROLL_FAILED: %v", err)
			os.Exit(1)
		default:
			// Enrolled, but the id could not be stored: this life is fine, the
			// next restart will have to enroll again.
			log.Printf("WARNING: %v", err)
		}
	}
	if reused {
		log.Printf("enrollment reused — agent id %s read from %s", id, stateDir)
	} else {
		log.Printf("enrolled — agent id %s", id)
	}
	return id
}
