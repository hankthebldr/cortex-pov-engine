// main is the CLI entry point for the CortexSim beacon agent.
//
// Usage:
//
//	cortexsim-agent --server http://localhost:8888 --id <agent-id> [--interval 10] [--log-file <path>]
//	cortexsim-agent --server http://simcore:8888 --enroll-token-file <path> [--state-dir <dir>] [--expires-at <RFC3339>]
//
// The agent registers with SimCore on startup — first enrolling itself from a
// token file when it is given one instead of an --id — then enters a polling
// loop to fetch and execute simulation tasks, and takes no further task once
// --expires-at has passed. Run from a console, or supervised by systemd,
// launchd or a pod's supervisor, it handles SIGINT/SIGTERM/SIGHUP cleanly.
// Started by the Windows service control manager (the elevated Windows
// installer registers it as the CortexSimAgent service) it runs as that service
// instead, and an SCM STOP or SHUTDOWN takes the same graceful path.
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

	// serviceName is the Windows service the elevated installer registers
	// (`sc.exe create CortexSimAgent ...` in core/api/agents.py).
	serviceName = "CortexSimAgent"
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

// shutdownSignals are the signals that trigger a graceful stop on the console
// path (console, systemd, launchd, a pod's supervisor).
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
	proxy         string // --proxy as given: "" (environment), "direct", or a proxy URL

	// Native enrollment and the container deployment. Every zero value is the
	// behaviour a beacon had before these flags existed.
	enrollTokenFile   string    // --enroll-token-file; mutually exclusive with id
	stateDir          string    // --state-dir; carried only with enrollTokenFile, the one place it is read
	postureProfile    string    // --posture-profile, reported as a runtime fact
	prepareStatusFile string    // --prepare-status-file, reported as a runtime fact
	expiresAt         time.Time // --expires-at; zero = never
	subreaper         bool      // --subreaper
}

// agentProxyEnv is the environment fallback for --proxy. Like
// CORTEXSIM_AGENT_LOG_FILE it carries AGENT in its name so it cannot be
// mistaken for a SimCore setting on a box that runs both.
const agentProxyEnv = "CORTEXSIM_AGENT_PROXY"

// invalidConfigError is a command line that parsed but does not validate.
type invalidConfigError struct {
	msg       string
	showUsage bool

	// fatalCode, when set, also goes to the termination log as one
	// "FATAL <code>: <fatalReason>" line, so `kubectl describe pod` shows why
	// the pod is crash-looping (termination_log.go).
	fatalCode   string
	fatalReason string
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
	server, id, artifactToken, logFile, proxy                             *string
	enrollTokenFile, stateDir, postureProfile, prepareStatusFile, expires *string
	interval, longPoll, stepTimeout                                       *int
	streamOutput, subreaper                                               *bool
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
	// BEACON-19. Empty keeps Go's default (the HTTP_PROXY / HTTPS_PROXY /
	// NO_PROXY environment), so an existing deployment is unchanged. The
	// Windows SYSTEM PROXY is not read by the beacon — see beacon/proxy.go —
	// which is why a service-mode beacon behind a proxy needs this flag.
	f.proxy = fs.String("proxy", "",
		"Proxy for reaching SimCore: an http://, https:// or socks5:// URL, or 'direct' to never use one "+
			"(env: "+agentProxyEnv+"). Empty uses HTTP_PROXY / HTTPS_PROXY / NO_PROXY. "+
			"The Windows system proxy (WinINET/WinHTTP, PAC) is NOT read.")
	// Native enrollment (container deployment): redeem a token from a file
	// instead of being handed an --id, and remember the id across restarts.
	f.enrollTokenFile = fs.String("enroll-token-file", "",
		"Path to a file holding an enrollment token. The beacon enrolls itself and stores the assigned id under --state-dir. Mutually exclusive with --id.")
	f.stateDir = fs.String("state-dir", defaultStateDir,
		"Directory where the enrolled agent id is kept across restarts (used with --enroll-token-file)")
	f.postureProfile = fs.String("posture-profile", "",
		"Posture profile this pod was deployed with, reported to SimCore as a runtime fact")
	f.prepareStatusFile = fs.String("prepare-status-file", "",
		"File whose first line is the pod's tool-preparation status, reported to SimCore as a runtime fact")
	f.expires = fs.String("expires-at", "",
		"RFC3339 time after which the beacon takes no further tasks and exits 0 (empty = never)")
	f.subreaper = fs.Bool("subreaper", false,
		"Become a child subreaper (linux) and reap orphaned processes left by finished steps")
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

	if err := validateIdentityFlags(*f.id, *f.enrollTokenFile); err != nil {
		return beaconConfig{}, &invalidConfigError{msg: err.Error(), showUsage: true}
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
	// A value that does not parse is an error, never a silent "no expiry": a
	// pod would otherwise outlive the lifetime it was granted.
	expiresAt, err := parseExpiresAt(*f.expires)
	if err != nil {
		return beaconConfig{}, &invalidConfigError{msg: "BAD_EXPIRES_AT: " + err.Error(),
			fatalCode: "BAD_EXPIRES_AT", fatalReason: err.Error()}
	}

	token := *f.artifactToken
	if token == "" {
		token = getenv("CORTEXSIM_ARTIFACT_TOKEN")
	}
	proxy := *f.proxy
	if proxy == "" {
		proxy = getenv(agentProxyEnv)
	}
	if _, _, err := beacon.ParseProxySpec(proxy); err != nil {
		// The value is not echoed: a proxy URL can carry credentials, and this
		// message reaches the console and the service log.
		return beaconConfig{}, &invalidConfigError{msg: "--proxy (or " + agentProxyEnv + ") is not usable: " +
			err.Error() + " — give an http://, https:// or socks5:// proxy URL, or 'direct'"}
	}
	cfg := beaconConfig{
		server:            *f.server,
		id:                *f.id,
		interval:          time.Duration(*f.interval) * time.Second,
		longPoll:          *f.longPoll,
		streamOutput:      *f.streamOutput,
		stepTimeout:       time.Duration(*f.stepTimeout) * time.Second,
		artifactToken:     token,
		logFile:           *f.logFile,
		proxy:             proxy,
		enrollTokenFile:   *f.enrollTokenFile,
		postureProfile:    *f.postureProfile,
		prepareStatusFile: *f.prepareStatusFile,
		expiresAt:         expiresAt,
		subreaper:         *f.subreaper,
	}
	if cfg.enrollTokenFile != "" {
		cfg.stateDir = *f.stateDir
	}
	return cfg, nil
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
// --artifact-token. In a pod, an error that carries a fatal code is also
// written to the termination log.
func exitOnConfigError(err error) {
	code := configExitCode(err)
	var inv *invalidConfigError
	isInvalid := errors.As(err, &inv)
	if isInvalid && inv.fatalCode != "" {
		writeTerminationLog(terminationLogPath(), fatalLine(inv.fatalCode, inv.fatalReason))
	}
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
	if !isInvalid || inv.showUsage {
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
	// beacon construction, enrollment, the staging sweep and registration all
	// live in runBeacon, which the dispatcher calls only after it has
	// connected. On Linux, macOS, and a Windows console this returns
	// handled=false at once.
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

	// ----------------------------------------------------------------
	// The beacon: returns on lifetime expiry or a fatal startup error, or
	// within shutdownGrace of a shutdown signal.
	// ----------------------------------------------------------------
	// runUntilShutdown (lifetime.go) owns the signal: it cancels ctx — the one
	// shutdown path, which tree-kills the in-flight step and completes its run
	// as "agent shutdown — task interrupted" — then waits at most shutdownGrace
	// for runBeacon to return, so a stuck loop still exits inside a pod's 30 s
	// termination grace instead of being SIGKILLed by the kubelet.
	exitCode := 0
	if runUntilShutdown(ctx, cancel, func(ctx context.Context) { exitCode = runBeacon(ctx, cfg) }, sigCh, shutdownGrace) {
		log.Printf("SHUTDOWN_TIMEOUT: poll loop still running %s after the signal — exiting", shutdownGrace)
		return
	}
	if exitCode != 0 {
		os.Exit(exitCode)
	}
}

// runBeacon is the beacon itself: gather what it reports about itself, enroll
// when it was given a token file, build the client, sweep stale staging,
// register, then poll until ctx is cancelled or its lifetime ends. It is
// shared by the console path (ctx cancelled by a signal) and the Windows
// service (ctx cancelled by an SCM STOP or SHUTDOWN). Cancellation is the ONE
// shutdown path: client.Run tree-kills the in-flight step and completes its
// run as "agent shutdown — task interrupted" before returning. It returns the
// process exit code instead of exiting, so the service path can report it to
// the SCM.
func runBeacon(ctx context.Context, cfg beaconConfig) int {
	// A lifetime that ran out before this process started (a container restart
	// after expiry, a pod rescheduled late) must not redeem the enrollment token
	// or register: exit 0 and let the supervisor idle until the reaper removes it.
	if expiredAtStartup(cfg.expiresAt, time.Now()) {
		log.Printf("LIFETIME_EXPIRED — expires-at %s passed before startup; not enrolling or registering",
			cfg.expiresAt.UTC().Format(time.RFC3339))
		return 0
	}

	// The server URL usually comes from a Kubernetes Secret and may carry a
	// trailing slash or newline: normalise it once, before any use.
	serverURL := beacon.NormalizeServerURL(cfg.server)

	// Everything the agent reports about itself is gathered once, up front, so
	// the same snapshot goes on Enroll and on Register.
	hostname, err := os.Hostname()
	if err != nil {
		log.Printf("WARNING: could not determine hostname: %v — using agent ID", err)
		hostname = cfg.id
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
		PostureProfile:    cfg.postureProfile,
		PrepareStatusFile: cfg.prepareStatusFile,
		ProbeTools:        runtimefacts.DefaultProbeTools,
		ProbeAccounts:     identity.ServiceAccountNames(),
	})

	agentID := cfg.id
	if cfg.enrollTokenFile != "" {
		// Any failure is fatal, so a pod that cannot enroll crash-loops visibly
		// rather than idling.
		id, code, err := enrollAgent(serverURL, cfg.enrollTokenFile, cfg.stateDir,
			hostname, capabilities, interpreters, &facts)
		if err != nil {
			return fatalStartup(code, err)
		}
		agentID = id
	}

	mode := "console"
	if underSCM() {
		mode = "service"
	}
	log.Printf("starting — server=%s id=%s interval=%ds long-poll=%ds stream-output=%t step-timeout=%ds mode=%s",
		serverURL, agentID, int(cfg.interval/time.Second), cfg.longPoll, cfg.streamOutput,
		int(cfg.stepTimeout/time.Second), mode)

	// ----------------------------------------------------------------
	// Build beacon client
	// ----------------------------------------------------------------
	client := beacon.New(serverURL, agentID, cfg.interval)
	// RG-AGENT-05: before anything that touches the network. The console signal
	// handler (and the SCM STOP handler) only cancel ctx, so Register — which
	// runs before client.Run — has to be able to see it, or a stop during a
	// blocked registration waits out the 30 s HTTP timeout.
	client.BindShutdown(ctx)
	// BEACON-19: apply --proxy, then say which route to SimCore this beacon
	// uses — and, on Windows, that the system proxy is not consulted — where a
	// DC whose agent never appeared will look: the first lines of the log.
	if err := client.SetProxy(cfg.proxy); err != nil {
		log.Printf("ERROR: --proxy is not usable: %v", err)
		return 1
	}
	for _, line := range client.ProxyStartupLines(runtime.GOOS) {
		log.Print(line)
	}
	client.SetRuntimeFacts(&facts)
	client.LongPollTimeout = cfg.longPoll
	client.StreamOutput = cfg.streamOutput
	client.ExpiresAt = cfg.expiresAt
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
	log.Printf("registering — hostname=%s os=%s capabilities=%v interpreters=%v",
		hostname, runtime.GOOS, capabilities, interpreters)
	// BEACON-13: say why an interpreter a DC can see on the host is not in that
	// list (the Microsoft Store stub; an alias Windows cannot shim).
	for _, note := range executor.InterpreterAdvisories() {
		log.Printf("interpreter not advertised: %s", note)
	}

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

	// Orphan reaping for the container deployment. Started only now, after the
	// startup probes above have been waited on: the reaper's blanket Wait4(-1)
	// skips only children the executor's child gate tracks.
	if cfg.subreaper {
		if err := setChildSubreaper(); err != nil {
			log.Printf("WARNING SUBREAPER_UNAVAILABLE: %v — orphans of finished steps reparent to PID 1", err)
		} else {
			log.Printf("child subreaper enabled — orphans of finished steps are reaped here")
		}
		startChildReaper(ctx)
	}

	// ----------------------------------------------------------------
	// Main poll loop: returns once ctx is cancelled, or once --expires-at has
	// passed (the run in flight finishes first). Both are a clean exit 0.
	// ----------------------------------------------------------------
	client.Run(ctx)

	log.Printf("agent exited cleanly")
	return 0
}

// fatalStartup is a fatal startup failure inside runBeacon: the FATAL line on
// the log and on the termination log, so `kubectl describe pod` shows why the
// pod is crash-looping, then exit code 1 for the caller. It returns instead of
// calling os.Exit because runBeacon also runs under the Windows SCM, where an
// os.Exit inside the service handler is recorded only as "terminated
// unexpectedly" (1067) instead of as the service's exit code.
func fatalStartup(code string, err error) int {
	log.Printf("FATAL %s: %v", code, err)
	writeTerminationLog(terminationLogPath(), fatalLine(code, err.Error()))
	return 1
}

// enrollSleep is the pause between SERVER_UNREACHABLE retries; tests replace it.
var enrollSleep = time.Sleep

// enrollAgent redeems the enrollment token (or reuses the id stored by an
// earlier life of this pod) and returns the agent id. On failure it returns a
// stable code for the one fatal log line. The token file is read lazily, only
// when an enrollment is actually needed, so a restart that reuses a stored id
// still works after the Secret's token was rotated or removed. The token is
// never logged; only its last 6 characters are.
func enrollAgent(serverURL, tokenFile, stateDir, hostname string,
	capabilities, interpreters []string, facts *runtimefacts.Facts) (id, code string, err error) {

	desired := strings.TrimSpace(os.Getenv("POD_NAME"))
	if desired == "" {
		desired = hostname
	}
	doEnroll := func() (string, error) {
		token, err := beacon.ReadTokenFile(tokenFile)
		if err != nil {
			return "", err
		}
		log.Printf("enrolling — server=%s token=%s", serverURL, beacon.TokenSuffix(token))
		return beacon.EnrollWithRetry(enrollAttempts, enrollRetryDelay, enrollSleep, func() (string, error) {
			return beacon.Enroll(serverURL, token, hostname, runtime.GOOS, desired,
				capabilities, interpreters, facts)
		})
	}
	id, reused, err := beacon.LoadOrEnroll(stateDir, doEnroll)
	if err != nil {
		return "", enrollFailureCode(err), err
	}
	if reused {
		log.Printf("enrollment reused — agent id %s read from %s", id, stateDir)
	} else {
		log.Printf("enrolled — agent id %s", id)
	}
	return id, "", nil
}

// enrollFailureCode maps an enrollment error to the code on the fatal log line.
func enrollFailureCode(err error) string {
	var se *beacon.StateDirError
	var ee *beacon.EnrollError
	switch {
	case errors.As(err, &se):
		return beacon.CodeStateDirUnwritable
	case errors.Is(err, beacon.ErrEnrollTokenEmpty):
		return "ENROLL_TOKEN_EMPTY"
	case errors.Is(err, beacon.ErrEnrollTokenUnreadable):
		return "ENROLL_TOKEN_UNREADABLE"
	case errors.As(err, &ee) && ee.Status >= 300 && ee.Status < 400:
		return "ENROLL_REDIRECTED"
	case errors.As(err, &ee) && ee.Code != "":
		return ee.Code
	}
	return "ENROLL_FAILED"
}
