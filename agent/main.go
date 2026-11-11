// main is the CLI entry point for the CortexSim beacon agent.
//
// Usage:
//
//	cortexsim-agent --server http://localhost:8888 --id <agent-id> [--interval 10] [--log-file <path>]
//
// The agent registers with SimCore on startup, then enters a polling loop to
// fetch and execute simulation tasks. Run from a console, or supervised by
// systemd or launchd, it handles SIGINT/SIGTERM/SIGHUP cleanly. Started by the
// Windows service control manager (the elevated Windows installer registers it
// as the CortexSimAgent service) it runs as that service instead, and an SCM
// STOP or SHUTDOWN takes the same graceful path.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
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

	// serviceName is the Windows service the elevated installer registers
	// (`sc.exe create CortexSimAgent ...` in core/api/agents.py).
	serviceName = "CortexSimAgent"
)

// shutdownSignals are the signals that trigger a graceful stop on the console
// path (console, systemd, launchd).
//
// SIGHUP matters as much as the other two: a beacon started from an SSH session
// (the nohup fallback in the installer, or a DC running it by hand) receives
// SIGHUP when the controlling terminal goes away. Un-Notified, its DEFAULT
// action terminates the process — the beacon would die with the laptop lid, mid
// scenario, with no shutdown path. Catching it converts that into the same clean
// cancel as Ctrl-C, and under systemd/launchd it is simply never delivered.
var shutdownSignals = []os.Signal{syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP}

// beaconConfig is the validated command line.
type beaconConfig struct {
	server        string
	id            string
	interval      time.Duration
	longPoll      int // seconds; 0 = off
	streamOutput  bool
	stepTimeout   time.Duration // 0 = built-in default
	artifactToken string
	logFile       string // --log-file as given; see logsink.go for the resolution
}

// invalidConfigError is a command line that parsed but does not validate.
type invalidConfigError struct {
	msg       string
	showUsage bool
}

func (e *invalidConfigError) Error() string { return e.msg }

// configExitCode maps a parseConfig error to the exit code the beacon has
// always used: 0 for -h, 1 for a value that fails validation, 2 for a
// flag-syntax error (flag.ExitOnError's code).
func configExitCode(err error) int {
	var inv *invalidConfigError
	switch {
	case err == nil, errors.Is(err, flag.ErrHelp):
		return 0
	case errors.As(err, &inv):
		return 1
	}
	return 2
}

type rawFlags struct {
	server, id, artifactToken, logFile *string
	interval, longPoll, stepTimeout    *int
	streamOutput                       *bool
}

func defineFlags(fs *flag.FlagSet) *rawFlags {
	f := &rawFlags{}
	f.server = fs.String("server", defaultServer, "SimCore server URL (e.g. http://localhost:8888)")
	f.id = fs.String("id", "", "Agent ID — required (e.g. hostname or custom label)")
	f.interval = fs.Int("interval", defaultInterval, "Poll interval in seconds")
	// The shelf defaults to open; this is only needed when SimCore runs
	// CORTEXSIM_K8S_PAYLOAD_AUTH=token. It is supplied at INSTALL time (baked
	// into the systemd unit / launchd plist) and never travels in the task body:
	// queued_tasks.payload is plaintext JSON in SQLite.
	f.artifactToken = fs.String("artifact-token", "",
		"Bearer token for the payload shelf when SimCore runs it in token mode (env: CORTEXSIM_ARTIFACT_TOKEN)")
	// Both default to OFF so an existing deployment's behaviour is byte-identical
	// after an upgrade: 0 keeps the interval ticker, false keeps whole-step
	// output delivered once on completion. Opt in per host.
	f.longPoll = fs.Int("long-poll", 0,
		"Long-poll the task queue for N seconds per request instead of ticking every --interval (0 = off, max 60). Cuts task pickup from up to --interval down to milliseconds.")
	f.streamOutput = fs.Bool("stream-output", false,
		"Stream step stdout/stderr to SimCore as it is produced rather than only on step completion.")
	// 0 keeps the built-in default (180s). Raise it on a slow or proxied link:
	// tier-4 adapter packs install their tool inline at dispatch (apt-get
	// install hydra / john / hashcat / metasploit), and a step killed mid
	// install reports exit 124 with no detection — which reads in a POV report
	// as a miss by the customer's stack rather than a beacon budget.
	f.stepTimeout = fs.Int("step-timeout", 0,
		"Per-step execution timeout in seconds (0 = built-in default of 180). A step exceeding it is terminated and reported as exit 124.")
	f.logFile = registerLogFileFlag(fs)
	return f
}

// parseConfig parses and validates the beacon's command line. It prints
// nothing: main decides where an error can be seen (stderr on a console, the
// service log under the SCM).
func parseConfig(args []string, getenv func(string) string) (beaconConfig, error) {
	fs := flag.NewFlagSet("cortexsim-agent", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	f := defineFlags(fs)
	if err := fs.Parse(args); err != nil {
		return beaconConfig{}, err
	}

	if *f.id == "" {
		return beaconConfig{}, &invalidConfigError{msg: "--id is required (e.g. --id myhost-01)", showUsage: true}
	}
	if *f.interval < 1 {
		return beaconConfig{}, &invalidConfigError{msg: "--interval must be at least 1 second"}
	}
	// Mirror the server-side bound (core/api/agents.py declares wait as
	// ge=0, le=60) so a bad value is rejected here with a clear message
	// instead of becoming a 422 on every poll.
	if *f.longPoll < 0 || *f.longPoll > 60 {
		return beaconConfig{}, &invalidConfigError{msg: "--long-poll must be between 0 and 60 seconds (0 disables it)"}
	}
	if *f.stepTimeout < 0 {
		return beaconConfig{}, &invalidConfigError{msg: "--step-timeout must be >= 0 (0 selects the built-in default)"}
	}

	token := *f.artifactToken
	if token == "" {
		token = getenv("CORTEXSIM_ARTIFACT_TOKEN")
	}
	return beaconConfig{
		server:        *f.server,
		id:            *f.id,
		interval:      time.Duration(*f.interval) * time.Second,
		longPoll:      *f.longPoll,
		streamOutput:  *f.streamOutput,
		stepTimeout:   time.Duration(*f.stepTimeout) * time.Second,
		artifactToken: token,
		logFile:       *f.logFile,
	}, nil
}

// printUsage writes the flag reference, in flag.Usage's format.
func printUsage(w io.Writer) {
	fs := flag.NewFlagSet("cortexsim-agent", flag.ContinueOnError)
	fs.SetOutput(w)
	defineFlags(fs)
	fmt.Fprintf(w, "Usage of %s:\n", os.Args[0])
	fs.PrintDefaults()
}

// exitOnConfigError reports a bad command line where the operator can see it,
// then exits with the console exit code. On a console that is stderr, as it
// always was. Under the SCM there is no stderr — an early exit there is
// recorded only as "the process terminated unexpectedly" (1067) — so the error
// goes to the service log instead and the service stops with the same code as
// its service-specific exit code (`sc.exe query CortexSimAgent` shows it as
// SERVICE_EXIT_CODE). The command line itself is not logged: it can carry
// --artifact-token.
func exitOnConfigError(err error) {
	code := configExitCode(err)
	handled, _, _ := runAsService(serviceName, func(context.Context) int {
		installLogSink(log.Default(), logPathFor("", underSCM(), os.Getenv), logRotateBytes, os.Stderr)
		log.Printf("ERROR: invalid service command line: %v — the service is stopping with exit code %d", err, code)
		return code
	})
	if handled {
		os.Exit(code)
	}
	if errors.Is(err, flag.ErrHelp) {
		printUsage(os.Stderr)
		os.Exit(0)
	}
	fmt.Fprintln(os.Stderr, "ERROR: "+err.Error())
	var inv *invalidConfigError
	if !errors.As(err, &inv) || inv.showUsage {
		printUsage(os.Stderr)
	}
	os.Exit(code)
}

func main() {
	// ----------------------------------------------------------------
	// CLI flags
	// ----------------------------------------------------------------
	cfg, err := parseConfig(os.Args[1:], os.Getenv)
	if err != nil {
		exitOnConfigError(err)
	}

	// Configure stdlib logger to write to stderr with timestamps.
	log.SetOutput(os.Stderr)
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetPrefix("[cortexsim-agent] ")

	// An explicit --log-file / CORTEXSIM_AGENT_LOG_FILE applies from the first
	// line, in console and service mode alike.
	logPath := logPathFor(cfg.logFile, false, os.Getenv)
	if logPath != "" {
		defer installLogSink(log.Default(), logPath, logRotateBytes, os.Stderr)()
	}

	// ----------------------------------------------------------------
	// Windows service dispatcher — FIRST, before anything that can block.
	// ----------------------------------------------------------------
	// The SCM fails a service whose process has not called
	// StartServiceCtrlDispatcherW within 30 s (error 1053) and tears it down.
	// Register alone can block for its full 30 s HTTP timeout against a SimCore
	// that is briefly unreachable — the normal state just after a reboot — so
	// beacon construction, the staging sweep and registration all live in
	// runBeacon, which the dispatcher calls only after it has connected. On
	// Linux, macOS, and a Windows console this returns handled=false at once.
	handled, code, err := runAsService(serviceName, func(ctx context.Context) int {
		if logPath == "" {
			// No stderr under the SCM: default to the path the installer
			// prints. Not closed on return — serve can still log after the
			// beacon stops (a forced stop, a failed status report), and the
			// process exits right after.
			installLogSink(log.Default(), logPathFor("", underSCM(), os.Getenv), logRotateBytes, os.Stderr)
		}
		log.Printf("running as Windows service %s", serviceName)
		return runBeacon(ctx, cfg)
	})
	if handled {
		if err != nil {
			log.Printf("ERROR: service dispatcher: %v", err)
		}
		os.Exit(code)
	}

	// ----------------------------------------------------------------
	// Console path: signal handling — honour SIGINT, SIGTERM and SIGHUP
	// ----------------------------------------------------------------
	// Deliberately console-only. A Go process that subscribes to SIGTERM on
	// Windows turns CTRL_LOGOFF_EVENT into a shutdown, and a service receives
	// that event whenever any interactive user logs off — so the service path
	// must never reach this.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, shutdownSignals...)

	go func() {
		sig := <-sigCh
		log.Printf("received signal %s — initiating graceful shutdown", sig)
		cancel()
	}()

	if code := runBeacon(ctx, cfg); code != 0 {
		os.Exit(code)
	}
}

// runBeacon is the beacon itself: build the client, sweep stale staging,
// register, then poll until ctx is cancelled. It is shared by the console path
// (ctx cancelled by a signal) and the Windows service (ctx cancelled by an SCM
// STOP or SHUTDOWN). Cancellation is the ONE shutdown path: client.Run
// tree-kills the in-flight step and completes its run as "agent shutdown —
// task interrupted" before returning. It returns the process exit code.
func runBeacon(ctx context.Context, cfg beaconConfig) int {
	mode := "console"
	if underSCM() {
		mode = "service"
	}
	log.Printf("starting — server=%s id=%s interval=%ds long-poll=%ds stream-output=%t step-timeout=%ds mode=%s",
		cfg.server, cfg.id, int(cfg.interval/time.Second), cfg.longPoll, cfg.streamOutput,
		int(cfg.stepTimeout/time.Second), mode)

	// ----------------------------------------------------------------
	// Build beacon client
	// ----------------------------------------------------------------
	client := beacon.New(cfg.server, cfg.id, cfg.interval)
	// RG-AGENT-05: before anything that touches the network. The console signal
	// handler (and the SCM STOP handler) only cancel ctx, so Register — which
	// runs before client.Run — has to be able to see it, or a stop during a
	// blocked registration waits out the 30 s HTTP timeout.
	client.BindShutdown(ctx)
	client.LongPollTimeout = cfg.longPoll
	client.StreamOutput = cfg.streamOutput
	if cfg.stepTimeout > 0 {
		client.StepTimeout = cfg.stepTimeout
	}

	if cfg.artifactToken != "" {
		client.SetArtifactToken(cfg.artifactToken)
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
		hostname = cfg.id
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
		if ctx.Err() != nil {
			// The stop cancelled the registration; there is no next poll cycle.
			log.Printf("registration interrupted — the beacon was told to stop before SimCore answered: %v", err)
		} else {
			// Registration failure is logged as a warning but does not abort startup —
			// SimCore may be momentarily unavailable and the first poll can re-register.
			log.Printf("WARNING: registration failed (will retry on next poll cycle): %v", err)
		}
	} else {
		log.Printf("registration OK")
	}

	// ----------------------------------------------------------------
	// Main poll loop (blocks until context is cancelled)
	// ----------------------------------------------------------------
	client.Run(ctx)

	log.Printf("agent exited cleanly")
	return 0
}
