// Package beacon implements the HTTP polling client that connects the Go agent
// to the SimCore orchestrator.  It handles registration, task polling, output
// streaming, run completion, and abort control — all over plain JSON/HTTP
// (no WebSocket, no gRPC).
package beacon

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	neturl "net/url" // aliased: several functions here have a local `url`
	"os"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
	"github.com/hankthebldr/cortexsim/agent/runtimefacts"
)

// ErrUnknownAgent is returned by PollTasks when SimCore responds 404 — i.e. the
// agent is not (or no longer) registered. This is distinct from a normal idle
// "no task" response so the run loop can re-Register() and recover, instead of
// polling forever doing nothing (GAP-AGENT-003).
var ErrUnknownAgent = errors.New("agent not registered with SimCore")

// -------------------------------------------------------------------------
// Domain types — the canonical pull-mode wire contract (commit b7eebc5+).
//
// The server's orchestrator.Task.to_dict() emits:
//
//	{
//	  "task_id":          "uuid",
//	  "run_id":           "uuid",
//	  "scenario_id":      "SIM-EDR-001",
//	  "steps":            [ <Step>, ... ],
//	  "identity_context": "www-data" | null,
//	  "identity_default": "www-data" | null,
//	  "created_at":       "2026-06-07T12:00:00.000000"
//	}
//
// wrapped on the wire as {"task": <that> | null}. Each Step's `identity` is a
// USERNAME string (e.g. "www-data", "root"), NOT a {mode,username} object — the
// username→mode resolution happens agent-side via identity.ResolveIdentity,
// mirroring the push bash harness allowlist.
// -------------------------------------------------------------------------

// Task is one pull-mode work item: an ordered list of steps to execute.
type Task struct {
	TaskID     string `json:"task_id"`
	RunID      string `json:"run_id"`
	ScenarioID string `json:"scenario_id"`
	Steps      []Step `json:"steps"`
	// anchorNotHonoured is set by the chained path when the requested cgo_anchor
	// could not be applied; buildSummary reports it.
	anchorNotHonoured bool
	IdentityContext   string `json:"identity_context"` // launch-time username override; "" if null
	IdentityDefault   string `json:"identity_default"` // scenario execution_identity.default
	CreatedAt         string `json:"created_at"`
	// CgoAnchor is the scenario's declared Causality Group Owner (a realistic
	// initial-access process). Present only for scenarios that declare the
	// causality contract; nil (and ignored on the wire) otherwise.
	CgoAnchor *CgoAnchor `json:"cgo_anchor,omitempty"`
	// Artifacts are staged tools this task requires BEFORE any step runs. The
	// server omits the key entirely when empty, so a task for any of today's
	// scenarios decodes to a nil slice and both execution paths are unchanged.
	// See artifact.go for the integrity model.
	Artifacts []Artifact `json:"artifacts,omitempty"`
	// RuntimeInstallAuthorized mirrors CORTEXSIM_XSIAM_ALLOW_WRITE's posture:
	// an explicit, per-run, off-by-default flag. Only when the server sets this
	// (which itself requires BOTH a launch-time request flag AND the deployment
	// env var CORTEXSIM_AGENT_ALLOW_RUNTIME_INSTALL — see
	// core/engine/orchestrator.py) may the beacon attempt a package-manager
	// install to satisfy a step's declared `requires_interpreters`. Omitted from
	// the wire entirely when false, so a task for any scenario without a
	// runtime-dependency gap is byte-identical to before this feature existed.
	RuntimeInstallAuthorized bool `json:"runtime_install_authorized,omitempty"`
}

// CgoAnchor labels the causality-chain root so the endpoint sensor's Causality
// View reads as a real intrusion rather than the beacon.
type CgoAnchor struct {
	ImageName       string `json:"image_name"`
	PrimaryUsername string `json:"primary_username"`
}

// StepCausality declares this step's place in the causality chain (which prior
// step it pivots from, and how). Consumed by the graph projection; its presence
// also signals the beacon to execute the run as a connected process chain.
type StepCausality struct {
	ParentStep string `json:"parent_step"`
	Pivot      string `json:"pivot"`
}

// Step is one TTP command within a Task.
type Step struct {
	ID             string         `json:"id"`
	Name           string         `json:"name"`
	Command        string         `json:"command"`
	Identity       string         `json:"identity"` // a USERNAME string, not a mode
	MitreTechnique string         `json:"mitre_technique"`
	Causality      *StepCausality `json:"causality,omitempty"`
	Platforms      []string       `json:"platforms,omitempty"`
	// PlatformVariants are per-OS equivalents of Command keyed by OS
	// (`windows`, `linux`, ...). BEACON-01: before this field existed
	// encoding/json dropped the key, so on Windows every step ran its POSIX
	// primary through PowerShell. See planStep.
	PlatformVariants map[string]string `json:"platform_variants,omitempty"`
	// Shell is set when SimCore has ALREADY resolved the step for this host:
	// "powershell" | "cmd" on Windows. Empty means "the beacon decides".
	Shell string `json:"shell,omitempty"`
	// ResolvedFrom names where SimCore took Command from (e.g.
	// "variant:windows"); informational, echoed into the step output.
	ResolvedFrom string `json:"resolved_from,omitempty"`
	// RequiresInterpreters names the logical interpreters (e.g. "python") this
	// step's command depends on but does not itself install. Checked against
	// the target's real PATH before the command ever runs — see
	// resolveRuntimeDeps in client.go and docs/design/agent-runtime-dependencies.md.
	// Omitted from the wire when empty, so every existing scenario's task is
	// byte-identical to before this field existed.
	RequiresInterpreters []string `json:"requires_interpreters,omitempty"`
	// TimeoutSeconds defines the maximum execution duration for this step in
	// seconds. If zero or unset, the client's StepTimeout or defaultStepTimeout is used.
	TimeoutSeconds int `json:"timeout_seconds,omitempty"`
	// RequiredBinaries names executables the step's command needs on PATH. Each
	// is checked with exec.LookPath before the step runs; a miss records the
	// step not_run / TOOL_ABSENT instead of running it (see preflightTools).
	RequiredBinaries []string `json:"required_binaries,omitempty"`
	// expected_detections intentionally omitted — the server seeds Result rows.
}

// platformNotRunMarker prefixes the /output line for a step this host refused
// to run because the step cannot run here. A stable literal, like
// toolAbsentMarker: it is what a DC — and any parser of the run record — greps
// for to tell "never ran on this host" from "ran and Cortex saw nothing".
const platformNotRunMarker = "!! STEP NOT RUN: PLATFORM_UNSATISFIABLE"

// platformUnsatisfiableExitCode is reported when NO step of a task could run on
// this host. EX_CONFIG, the same "this never ran" code artifact staging uses.
const platformUnsatisfiableExitCode = 78

// stepPlan is how THIS host will run one step.
type stepPlan struct {
	command string
	shell   string // executor step shell: "" = host default, executor.ShellCmd = cmd.exe
	note    string // written into the step's output when non-empty
	refuse  string // non-empty: do NOT run the step; this is its marker line
}

// planStep decides what text this host runs for a step, under which shell — or
// that the step cannot run here at all (BEACON-01).
//
// Windows, in order:
//  1. Shell set: SimCore already resolved the step for this host. Run Command
//     under that shell (powershell | cmd). Any other value is refused — an
//     unrecognised shape raises, it does not read as the default.
//  2. platform_variants.windows present: run it under PowerShell.
//  3. platforms declared and windows not among them: refuse. Running the POSIX
//     primary through PowerShell is how SIM-EDR-003 step-01 (cron text that
//     exits 0 after its final echo) reported SUCCESS while doing nothing.
//  4. Otherwise the primary, under PowerShell, as before.
//
// POSIX hosts do NOT refuse on platform labels: ~100 corpus steps are labelled
// windows-only while carrying POSIX text, and refusing them would break Linux
// runs that work today. A step SimCore resolved to a non-POSIX shell is refused,
// because handing cmd or PowerShell text to sh runs garbage that may exit 0.
func planStep(step *Step, goos string) stepPlan {
	shell := strings.ToLower(strings.TrimSpace(step.Shell))
	if goos != "windows" {
		switch shell {
		case "", "sh", "bash":
			return stepPlan{command: step.Command}
		}
		return stepPlan{refuse: fmt.Sprintf("%s step=%s declared=shell:%s host=%s",
			platformNotRunMarker, step.ID, shell, goos)}
	}

	switch shell {
	case executor.ShellPowerShell, executor.ShellCmd:
		sh := ""
		if shell == executor.ShellCmd {
			sh = executor.ShellCmd
		}
		note := fmt.Sprintf("[cortexsim] platform: SimCore resolved this step to shell=%s", shell)
		if step.ResolvedFrom != "" {
			note += " (from " + step.ResolvedFrom + ")"
		}
		if sh != executor.ShellCmd {
			// BEACON-17: only PowerShell's bare `exit` is 0; cmd's keeps ERRORLEVEL.
			note = joinNotes(note, executor.BareExitCaveat(step.Command))
		}
		return stepPlan{command: step.Command, shell: sh, note: note}
	case "":
	default:
		return stepPlan{refuse: fmt.Sprintf("%s step=%s declared=shell:%s host=windows",
			platformNotRunMarker, step.ID, shell)}
	}

	if v, ok := step.PlatformVariants["windows"]; ok && strings.TrimSpace(v) != "" {
		return stepPlan{command: v,
			note: joinNotes("[cortexsim] platform: ran platform_variants.windows under PowerShell, not the primary command",
				executor.BareExitCaveat(v))}
	}
	if len(step.Platforms) > 0 && !declaresPlatform(step.Platforms, "windows") {
		return stepPlan{refuse: fmt.Sprintf("%s step=%s declared=%s host=windows",
			platformNotRunMarker, step.ID, strings.Join(step.Platforms, ","))}
	}
	return stepPlan{command: step.Command, note: executor.BareExitCaveat(step.Command)}
}

func declaresPlatform(platforms []string, want string) bool {
	for _, p := range platforms {
		if strings.EqualFold(strings.TrimSpace(p), want) {
			return true
		}
	}
	return false
}

// joinNotes joins the non-empty notes with newlines.
func joinNotes(notes ...string) string {
	var kept []string
	for _, n := range notes {
		if n != "" {
			kept = append(kept, n)
		}
	}
	return strings.Join(kept, "\n")
}

// hasCausalityContract reports whether the task declares the causality contract
// (a scenario cgo_anchor or any step causality). When true the beacon executes
// the steps as children of one anchor shell so they form a connected causality
// chain; when false it uses the default independent-per-step path unchanged.
func (t *Task) hasCausalityContract() bool {
	if t.CgoAnchor != nil {
		return true
	}
	for i := range t.Steps {
		if t.Steps[i].Causality != nil {
			return true
		}
	}
	return false
}

// ControlState is the response from GET /api/runs/{run_id}/control.
type ControlState struct {
	Abort  bool   `json:"abort"`
	RunID  string `json:"run_id"`
	Status string `json:"status"`

	// Lost is local only: the control channel itself failed (transport error,
	// non-200, undecodable body). Abort is still true — the beacon fails CLOSED
	// (BEACON-11 / OPS-04; Henry's decision is pending) — but the run record
	// must not then say a person stopped the run.
	Lost       bool   `json:"-"`
	LostDetail string `json:"-"`

	// Shutdown is local only: the poll was cut short because this beacon was
	// told to stop (BindShutdown), not because the channel failed or anyone
	// aborted the run. Abort is true so every caller stops where it stands.
	Shutdown bool `json:"-"`
}

// ReasonControlChannelLost is the stable literal carried in the /complete
// summary and the step output when a run was stopped because the control
// channel failed, not because anyone asked. It is deliberately NOT a
// StepOutcome reason: SimCore's step-reason vocabulary does not carry it.
const ReasonControlChannelLost = "CONTROL_CHANNEL_LOST"

// stopLine is the /output line written when the run stops before a step.
func (s ControlState) stopLine() string {
	if s.Shutdown {
		return "--- STOPPED: agent shutdown (NOT an operator abort) ---"
	}
	if s.Lost {
		return "--- STOPPED: control channel lost (NOT an operator abort) [" + ReasonControlChannelLost + "] ---"
	}
	return "--- ABORTED (operator) ---"
}

// stopBeforeSummary is the /complete summary for a run stopped before stepID.
func (s ControlState) stopBeforeSummary(stepID string) string {
	if s.Shutdown {
		return fmt.Sprintf("agent shutdown — task interrupted before step %s", stepID)
	}
	if s.Lost {
		return fmt.Sprintf("control channel lost — run stopped before step %s (NOT an operator abort) | reason=%s%s",
			stepID, ReasonControlChannelLost, s.lostDetail())
	}
	return "aborted by operator"
}

// stopDuringSummary is the /complete summary for a run whose in-flight step was
// terminated.
func (s ControlState) stopDuringSummary(stepID string) string {
	if s.Shutdown {
		// The literal every other shutdown path posts (executeTask & co.).
		return "agent shutdown — task interrupted"
	}
	if s.Lost {
		return fmt.Sprintf("control channel lost — step terminated (NOT an operator abort) | step=%s reason=%s%s",
			stepID, ReasonControlChannelLost, s.lostDetail())
	}
	return fmt.Sprintf("aborted by operator — step %s terminated", stepID)
}

func (s ControlState) lostDetail() string {
	if s.LostDetail == "" {
		return ""
	}
	return " detail=" + s.LostDetail
}

func controlLost(runID, detail string) ControlState {
	return ControlState{Abort: true, RunID: runID, Status: "unknown", Lost: true, LostDetail: detail}
}

func controlShutdown(runID string) ControlState {
	return ControlState{Abort: true, RunID: runID, Status: "unknown", Shutdown: true}
}

// abortExitCode is the conventional exit code reported when a run is aborted by
// an operator (SIGINT convention = 128 + 2).
const abortExitCode = 130

// timeoutExitCode is the conventional exit code reported when a step times out
// (GNU coreutils timeout convention = 124).
const timeoutExitCode = 124

// defaultStepTimeout is the fallback execution timeout if neither the step nor
// the client declares one.
const defaultStepTimeout = 180 * time.Second

// stepTimeoutMarker prefixes the notice for a step that was terminated by timeout.
const stepTimeoutMarker = "!! STEP TIMEOUT:"

// controlPollInterval is how often the agent polls the abort control channel
// while a long-running step is executing.
const controlPollInterval = 2 * time.Second

// -------------------------------------------------------------------------
// BeaconClient
// -------------------------------------------------------------------------

// BeaconClient manages communication with the SimCore server.
type BeaconClient struct {
	ServerURL string
	AgentID   string
	Interval  time.Duration
	http      *http.Client

	// Registration metadata, captured on the first Register() call so the run
	// loop can re-register on an unknown-agent 404 without main.go re-supplying
	// it (GAP-AGENT-003).
	regHostname     string
	regOS           string
	regCapabilities []string
	regInterpreters []string
	regFacts        *runtimefacts.Facts
	// registerPending is true while the last Register attempt failed. The run
	// loop retries it before every poll until it succeeds (INST-16): an
	// ENROLLED row never answers /tasks with the 404 that triggers
	// reRegister, so a failed boot Register used to leave the installer's
	// capability roster on the row for the life of the process.
	registerPending bool

	// resolveIdentity maps a step's identity USERNAME to something this host can
	// actually execute. Injectable so the Windows degradation path — which is the
	// whole point of the mechanism and cannot be reached from a Linux runner — is
	// exercised by the test suite rather than merely asserted about.
	resolveIdentity func(string) identity.Resolution

	// artifactHTTP is a SEPARATE client for artifact fetches. The shared one
	// carries Timeout: 30s, which would kill a large fetch mid-body and surface
	// as a truncation of unknown cause. Timeout is 0 here on purpose: the
	// deadline is per-artifact and context-driven.
	artifactHTTP  *http.Client
	artifactToken string

	// StepTimeout overrides defaultStepTimeout (180s) when non-zero.
	StepTimeout time.Duration

	// Injectable seams for the staging phase, same idiom as resolveIdentity:
	// getenv drives the per-platform dest allowlist, artifactProbeExec drives
	// the noexec pre-flight branch that CI cannot reach by mounting anything.
	artifactGetenv       func(string) string
	artifactProbeExec    func(string) error
	artifactCacheDirFunc func() string
	DisableArtifactCache bool

	// LongPollTimeout defines the duration in seconds for HTTP long-polling tasks.
	// When 0, the client uses interval ticker polling.
	LongPollTimeout int

	// StreamOutput enables real-time line-by-line output streaming to SimCore
	// during command execution.
	StreamOutput bool

	// execStep runs one per-step command. nil means identity.ExecuteCtxStream.
	// Injectable so the Windows step-selection path is observable from a POSIX
	// runner: the test records what WOULD reach the executor.
	execStep func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error)

	// shutdown is the context whose cancellation means "this beacon has been
	// told to stop" (BindShutdown). Unset = never.
	shutdown atomic.Pointer[context.Context]

	// The --proxy decision (proxy.go). All zero = Go's default, the environment.
	proxyTransport *http.Transport
	proxyURL       *neturl.URL
	proxyDirect    bool

	// frameScope / frameSeq key every non-streaming /output frame (RG-AGENT-06).
	frameMu    sync.Mutex
	frameScope string
	frameSeq   uint64

	// ExpiresAt, when non-zero, is the absolute end of this beacon's lifetime
	// (--expires-at). Run takes no task once it has passed; a task already
	// executing finishes first.
	ExpiresAt time.Time
}

// lifetimeExpired reports, and logs, that ExpiresAt has passed.
func (c *BeaconClient) lifetimeExpired() bool {
	if c.ExpiresAt.IsZero() || time.Now().Before(c.ExpiresAt) {
		return false
	}
	log.Printf("[beacon] LIFETIME_EXPIRED — expires-at %s has passed; taking no further tasks",
		c.ExpiresAt.UTC().Format(time.RFC3339))
	return true
}

// shutdownPostGrace is how long a step's /output or the run's /complete may
// still take once the beacon has been told to stop: long enough for the
// "agent shutdown — task interrupted" record to land on a healthy SimCore,
// short enough that a stop is honoured well inside launchd's 20 s and systemd's
// 90 s kill timers. A var only so tests can shrink it.
var shutdownPostGrace = 3 * time.Second

// BindShutdown tells the client which context means "the beacon is stopping"
// (RG-AGENT-04 / RG-AGENT-05). From its cancellation on:
//
//   - a blocked Register, task poll or live output chunk is cancelled at once —
//     nothing of evidential value is in flight;
//   - a step's /output and the run's /complete get shutdownPostGrace to land,
//     and the retry ladder is not climbed.
//
// Run and executeTask bind their own ctx; main binds before Register, which
// runs before Run does.
func (c *BeaconClient) BindShutdown(ctx context.Context) {
	if ctx != nil {
		c.shutdown.Store(&ctx)
	}
}

// stopCtx is the bound shutdown context, or one that is never cancelled.
func (c *BeaconClient) stopCtx() context.Context {
	if p := c.shutdown.Load(); p != nil {
		return *p
	}
	return context.Background()
}

// beginFrames starts a new frame-id scope for a task (see nextFrameID).
func (c *BeaconClient) beginFrames(taskID string) {
	c.frameMu.Lock()
	c.frameScope = taskID
	c.frameSeq = 0
	c.frameMu.Unlock()
}

// nextFrameID returns the idempotency key for the next non-streaming /output
// frame: "<task_id>:<n>", n counting from 1 within the task ("<agent_id>:<n>"
// when no task is in flight). It is assigned ONCE per frame, before the first
// attempt, so every retry of that frame carries the same key.
//
// RG-AGENT-06: postRetry re-sends a frame whose reply was lost, and SimCore's
// append is unconditional, so without a key the frame is stored twice. The key
// travels as an extra body field; a SimCore that does not read it ignores it
// (OutputRequest tolerates unknown fields — it already ignores `run_id`).
func (c *BeaconClient) nextFrameID() string {
	c.frameMu.Lock()
	defer c.frameMu.Unlock()
	c.frameSeq++
	scope := c.frameScope
	if scope == "" {
		scope = c.AgentID
	}
	return fmt.Sprintf("%s:%d", scope, c.frameSeq)
}

// beaconGOOS is the host OS for step-shape decisions. A package var only so a
// test can drive the Windows branches from a POSIX runner (runtimeDepsGOOS idiom).
var beaconGOOS = runtime.GOOS

// postRetryBackoff is the wait before each retry of a /complete or step /output
// POST. A var only so tests can shrink it.
var postRetryBackoff = []time.Duration{1 * time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second}

// stepTimeout returns the effective execution timeout for a step.
func (c *BeaconClient) stepTimeout(step *Step) time.Duration {
	if step != nil && step.TimeoutSeconds > 0 {
		return time.Duration(step.TimeoutSeconds) * time.Second
	}
	if c.StepTimeout > 0 {
		return c.StepTimeout
	}
	return defaultStepTimeout
}

// New constructs a BeaconClient with a sensible default HTTP timeout.
func New(serverURL, agentID string, interval time.Duration) *BeaconClient {
	return &BeaconClient{
		ServerURL: strings.TrimRight(serverURL, "/"),
		AgentID:   agentID,
		Interval:  interval,
		http: &http.Client{
			Timeout: 30 * time.Second,
		},
		resolveIdentity: identity.Resolve,
	}
}

// SetArtifactToken supplies the bearer token used when the payload shelf runs in
// `token` mode. It comes from --artifact-token / CORTEXSIM_ARTIFACT_TOKEN, baked
// into the systemd unit at install time — deliberately NOT from the task body,
// because queued_tasks.payload is plaintext JSON in SQLite.
func (c *BeaconClient) SetArtifactToken(token string) { c.artifactToken = strings.TrimSpace(token) }

// SetRuntimeFacts supplies the runtime facts Register (and so reRegister) sends.
func (c *BeaconClient) SetRuntimeFacts(f *runtimefacts.Facts) { c.regFacts = f }

// artifactClient returns the artifact-fetch HTTP client, building it on first
// use so a zero-value BeaconClient built by a test with a struct literal works.
func (c *BeaconClient) artifactClient() *http.Client {
	if c.artifactHTTP == nil {
		c.artifactHTTP = &http.Client{
			// See the struct field comment: no client-wide timeout.
			Timeout: 0,
			CheckRedirect: func(*http.Request, []*http.Request) error {
				// The fetch origin is fixed by the operator at install time.
				// Following a redirect off-origin would defeat that and would
				// carry the bearer token to a host the operator never named.
				// A 3xx therefore surfaces as ARTIFACT_UNAVAILABLE — loud, not
				// silent. This repo has already shipped a 302-to-a-captive-portal
				// counted as a delivered record.
				return http.ErrUseLastResponse
			},
		}
		if c.proxyTransport != nil {
			// The same --proxy decision as the SimCore API client (proxy.go).
			c.artifactHTTP.Transport = c.proxyTransport
		}
	}
	return c.artifactHTTP
}

func (c *BeaconClient) getenv(key string) string {
	if c.artifactGetenv != nil {
		return c.artifactGetenv(key)
	}
	return os.Getenv(key)
}

func (c *BeaconClient) probeExec(probePath string) error {
	if c.artifactProbeExec != nil {
		return c.artifactProbeExec(probePath)
	}
	return defaultExecProbe(probePath)
}

// resolve applies the identity resolver, tolerating a zero-value BeaconClient
// built by a test with a struct literal rather than New().
func (c *BeaconClient) resolve(username string) identity.Resolution {
	if c.resolveIdentity != nil {
		return c.resolveIdentity(username)
	}
	return identity.Resolve(username)
}

// logIdentity emits the per-step identity diagnostics to the agent log. The
// degradation ALSO goes into the run record via formatStepOutput — a log line on
// the customer's jumpbox is not evidence anybody reading the POV report sees.
func logIdentity(stepID, username string, res identity.Resolution) {
	// BEACON-20: this line names the impersonation mechanism the step is about
	// to use, so it is only written where there is one. It used to say "mapping
	// to runuser" on Windows too — for `administrator` and `user` — where
	// nothing is mapped and runuser does not exist; a DC reading the endpoint's
	// log would conclude the beacon tried to impersonate, against the run
	// record's IDENTITY NOT HONOURED. On Windows an unknown identity is always
	// a degradation, and the line below says exactly what happened.
	if res.Unknown && beaconGOOS != "windows" {
		log.Printf("[beacon] WARN unknown identity %q for step %s — mapping to %s (best-effort)",
			username, stepID, res.Mode)
	}
	if res.Degraded() {
		log.Printf("[beacon] WARN identity NOT honoured for step %s: %s", stepID, res.Degradation)
	}
}

// Seams for windowsExecutionAccount: the beacon's own account and whether its
// token is elevated. Package vars only so the line is testable off Windows.
var (
	beaconAccountName   = identity.CurrentUsername
	beaconTokenElevated = identity.ProcessElevated
)

// windowsExecutionAccount is the line every Windows step frame carries, naming
// what the step actually executed as (BEACON-20).
//
// The header's `identity=` is what the SCENARIO declared. Windows has no
// unattended impersonation, so every step runs as the beacon's own account —
// NT AUTHORITY\SYSTEM under the service, the installing user otherwise — and
// whether that token is elevated decides what a privileged TTP can do. A step
// whose declared identity is "honoured" (root on an elevated beacon) carried no
// account at all before this, so a run record could not tell SYSTEM from an
// elevated administrator from a standard user. Informational, not a marker:
// the unhonoured case still gets its own `!! IDENTITY NOT HONOURED` line.
func windowsExecutionAccount() string {
	who := strings.TrimSpace(beaconAccountName())
	if who == "" {
		who = "the beacon's account (name unavailable)"
	}
	token := "token NOT elevated"
	if beaconTokenElevated() {
		token = "elevated token"
	}
	return fmt.Sprintf("[cortexsim] executed as %s (%s) — a Windows step always runs as the beacon's own "+
		"account; identity= above is what the scenario declared", who, token)
}

// -------------------------------------------------------------------------
// API methods
// -------------------------------------------------------------------------

// Register sends agent metadata to SimCore so it appears in the agent roster.
// Corresponds to: POST /api/agents/register
//
// interpreters is this beacon's honest snapshot of what interpreters are
// resolvable on THIS host right now (executor.AvailableLogicalNames()) —
// never a guess, never a static list. SimCore uses it to preflight a
// scenario's declared `requires_interpreters` against the real target before
// dispatch instead of discovering the gap mid-run. A nil/empty slice is a
// legitimate answer (the host has none of the interpreters this beacon knows
// how to check for), not an omission — it is always sent (as [], never null:
// SimCore refused null with a 422), unlike capabilities' omit-when-default idiom, so a host that regresses from "has python" to "has
// nothing" is visible on its next registration rather than silently sticky.
func (c *BeaconClient) Register(hostname, goos string, capabilities, interpreters []string) error {
	// Remember the metadata so the run loop can transparently re-register on an
	// unknown-agent 404 (GAP-AGENT-003).
	c.regHostname = hostname
	c.regOS = goos
	c.regCapabilities = capabilities
	c.regInterpreters = interpreters

	body := map[string]interface{}{
		"agent_id":     c.AgentID,
		"hostname":     hostname,
		"os":           goos,
		"capabilities": nonNilStrings(capabilities),
		"interpreters": nonNilStrings(interpreters),
	}
	if c.regFacts != nil {
		body["runtime_facts"] = c.regFacts
	}
	_, err := c.post("/api/agents/register", body)
	c.registerPending = err != nil
	if err != nil {
		return fmt.Errorf("register: %w", err)
	}
	return nil
}

// retryPendingRegistration re-sends a registration that has not yet
// succeeded. Called by the run loop before each poll; a no-op once Register
// has succeeded (INST-16).
func (c *BeaconClient) retryPendingRegistration() {
	if !c.registerPending {
		return
	}
	if err := c.reRegister(); err != nil {
		log.Printf("[beacon] registration still not accepted (will retry next poll): %v", err)
		return
	}
	log.Printf("[beacon] registration OK (retried) — SimCore now holds this beacon's own capabilities")
}

// reRegister re-sends this agent's registration using the metadata captured on
// the first Register() call. Used by the run loop to recover from an
// unknown-agent 404. If Register was never called (metadata empty), it falls
// back to the local hostname/GOOS so a never-registered agent still recovers.
func (c *BeaconClient) reRegister() error {
	hostname := c.regHostname
	if hostname == "" {
		if h, err := os.Hostname(); err == nil {
			hostname = h
		} else {
			hostname = c.AgentID
		}
	}
	goos := c.regOS
	if goos == "" {
		goos = runtime.GOOS
	}
	caps := c.regCapabilities
	if caps == nil {
		caps = []string{"shell", "identity-harness"}
	}
	// regInterpreters is re-sent verbatim (including nil, a legitimate "none
	// found" answer) rather than re-detected — reRegister recovers from an
	// unknown-agent 404 and must not silently claim different runtime facts
	// than the boot-time Register() call did.
	return c.Register(hostname, goos, caps, c.regInterpreters)
}

// PollTasks asks SimCore whether there is a pending task for this agent (immediate).
func (c *BeaconClient) PollTasks() (*Task, error) {
	return c.PollTasksWait(0)
}

// PollTasksWait asks SimCore whether there is a pending task for this agent,
// optionally long-polling up to waitSec seconds on the server.
//
// Return contract (GAP-AGENT-003):
//   - (nil, nil)              → registered, but no task queued ({"task": null})
//   - (task, nil)            → a task to execute
//   - (nil, ErrUnknownAgent) → SimCore 404: this agent is not registered;
//     the caller should Register() and retry.
//
// Corresponds to: GET /api/agents/{id}/tasks(?wait=N)
func (c *BeaconClient) PollTasksWait(waitSec int) (*Task, error) {
	url := fmt.Sprintf("%s/api/agents/%s/tasks", c.ServerURL, c.AgentID)
	if waitSec > 0 {
		url = fmt.Sprintf("%s?wait=%d", url, waitSec)
	}

	httpClient := c.http
	if waitSec > 0 && httpClient != nil && httpClient.Timeout > 0 && httpClient.Timeout <= time.Duration(waitSec)*time.Second {
		cloned := *httpClient
		cloned.Timeout = time.Duration(waitSec+10) * time.Second
		httpClient = &cloned
	}

	// Bound to the shutdown context (RG-AGENT-05): an idle poll against a
	// SimCore that silently drops packets must not hold a stop for the whole
	// HTTP timeout (30 s, or wait+10 s when long-polling).
	req, err := http.NewRequestWithContext(c.stopCtx(), http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("pollTasks GET: %w", err)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("pollTasks GET: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		// 404 → this agent is unknown to SimCore (never registered, or the
		// registry was reset). Surface it distinctly so the run loop can
		// re-register rather than silently idling forever.
		return nil, ErrUnknownAgent
	}
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("pollTasks: unexpected status %d: %s", resp.StatusCode, b)
	}

	// SimCore wraps the payload as {"task": null | {...}} — decode the
	// envelope so a null body cleanly maps to (nil, nil).
	var env struct {
		Task *Task `json:"task"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
		return nil, fmt.Errorf("pollTasks decode: %w", err)
	}
	return env.Task, nil
}

// Control polls the run's abort control channel.
// Corresponds to: GET /api/runs/{run_id}/control
//
// It fails CLOSED: a missing run (404), any other non-200, a transport error or
// an undecodable body all return abort=true, so a beacon that cannot see the
// control channel stops rather than runs on unsupervised. Those cases are
// marked Lost so the run record says the CHANNEL stopped the run; only an
// explicit `abort:true` body is an operator abort. (SimCore itself answers a
// vanished run with 200 abort:true, so a 404 here is a proxy or a different
// server, not SimCore saying "stop".)
//
// The GET is bound to the shutdown context (RG-AGENT-04): a stop that lands
// while it is blocked on an unresponsive SimCore cancels it at once instead of
// after the 30 s client timeout, which outlived launchd's 20 s stop window.
// A request the stop cut short is reported as Shutdown, not Lost: the channel
// did not fail, the beacon was told to stop.
func (c *BeaconClient) Control(runID string) (ControlState, error) {
	url := fmt.Sprintf("%s/api/runs/%s/control", c.ServerURL, runID)

	stop := c.stopCtx()
	req, err := http.NewRequestWithContext(stop, http.MethodGet, url, nil)
	if err != nil {
		return controlLost(runID, "transport error"), fmt.Errorf("control GET: %w", err)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		if stop.Err() != nil {
			return controlShutdown(runID), fmt.Errorf("control GET: %w", err)
		}
		return controlLost(runID, "transport error"), fmt.Errorf("control GET: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return controlLost(runID, "HTTP 404"), nil
	}
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		return controlLost(runID, fmt.Sprintf("HTTP %d", resp.StatusCode)),
			fmt.Errorf("control: unexpected status %d: %s", resp.StatusCode, b)
	}

	var state ControlState
	if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
		return controlLost(runID, "undecodable body"), fmt.Errorf("control decode: %w", err)
	}
	return state, nil
}

// SendOutput streams partial or final command output back to SimCore, attributing
// it to a specific step. stepID may be empty for un-attributed output.
// Corresponds to: POST /api/runs/{run_id}/output  body {output, step_id?}
//
// Every frame sent here ends with a newline. SimCore appends each body as-is,
// and the step block (formatStepOutput), the refused-step block and the
// fail-fast stop line all end on their closing "--- … ---" frame, so without
// it the next step's "=== STEP" header was glued onto the previous footer and
// a line-oriented reader of the run record saw only the first step (observed
// on a lab run). Live streaming chunks (sendOutput with retry=false) are raw
// pieces of a child's output and are not touched.
func (c *BeaconClient) SendOutput(runID, stepID, output string) error {
	if output != "" && !strings.HasSuffix(output, "\n") {
		output += "\n"
	}
	return c.sendOutput(runID, stepID, output, true)
}

// sendOutput posts one output frame. retry=false is for live streaming chunks:
// those are written from inside the child's pipe writer, and a 31-second retry
// ladder there would stall the TTP itself; the step's full output is still
// posted (with retry) when the step ends.
func (c *BeaconClient) sendOutput(runID, stepID, output string, retry bool) error {
	body := map[string]string{
		"run_id": runID,
		"output": output,
	}
	if stepID != "" {
		body["step_id"] = stepID
	}
	path := fmt.Sprintf("/api/runs/%s/output", runID)
	var err error
	if retry {
		// Assigned once, before the first attempt: a retry is the same frame.
		body["frame_id"] = c.nextFrameID()
		_, err = c.postRetry(path, body)
	} else {
		_, err = c.post(path, body)
	}
	if err != nil {
		return fmt.Errorf("sendOutput: %w", err)
	}
	return nil
}

// Complete marks a run as finished with its exit code and a human-readable summary.
// Corresponds to: POST /api/runs/{run_id}/complete
//
// The body names this beacon (agent_id) and, through completeTask, the task it
// finishes (task_id). The beacon retries /complete, and SimCore counts a
// fan-out run's completion once per task only when the body says which task it
// is (RG-AGENT-03): an anonymous retry used to count as a second endpoint.
func (c *BeaconClient) Complete(runID string, exitCode int, summary string, steps []StepOutcome) error {
	return c.complete(runID, "", exitCode, summary, steps)
}

// completeTask is Complete for a task this beacon collected: it adds task_id.
func (c *BeaconClient) completeTask(task *Task, exitCode int, summary string, steps []StepOutcome) error {
	return c.complete(task.RunID, task.TaskID, exitCode, summary, steps)
}

func (c *BeaconClient) complete(runID, taskID string, exitCode int, summary string, steps []StepOutcome) error {
	if steps == nil {
		steps = []StepOutcome{} // encode [] never null
	}
	body := map[string]interface{}{
		"run_id":    runID,
		"exit_code": exitCode,
		"summary":   summary,
		"steps":     steps,
	}
	if c.AgentID != "" {
		body["agent_id"] = c.AgentID
	}
	if taskID != "" {
		body["task_id"] = taskID
	}
	_, err := c.postRetry(fmt.Sprintf("/api/runs/%s/complete", runID), body)
	if err != nil {
		return fmt.Errorf("complete: %w", err)
	}
	return nil
}

// -------------------------------------------------------------------------
// Main poll loop
// -------------------------------------------------------------------------

// Run is the agent's main execution loop.  It polls for tasks on every tick,
// executes them step-by-step via the identity harness, streams per-step output,
// and reports the final exit code.  It exits cleanly when ctx is cancelled
// (SIGINT / SIGTERM).
func (c *BeaconClient) Run(ctx context.Context) {
	c.BindShutdown(ctx)
	if c.lifetimeExpired() {
		return
	}
	if c.LongPollTimeout > 0 {
		log.Printf("[beacon] agent %q started — long-polling %s (wait=%ds)", c.AgentID, c.ServerURL, c.LongPollTimeout)
		for {
			if ctx.Err() != nil {
				log.Printf("[beacon] context cancelled — shutting down cleanly")
				return
			}
			// Lifetime first: an expired beacon takes no task, so it has no
			// reason to retry a pending registration either.
			if c.lifetimeExpired() {
				return
			}
			c.retryPendingRegistration()
			task, err := c.PollTasksWait(c.LongPollTimeout)
			if err != nil {
				if ctx.Err() != nil {
					// The poll was cut by the shutdown itself, not by SimCore.
					log.Printf("[beacon] context cancelled — shutting down cleanly")
					return
				}
				if errors.Is(err, ErrUnknownAgent) {
					log.Printf("[beacon] SimCore reports agent %q unknown — re-registering", c.AgentID)
					if rerr := c.reRegister(); rerr != nil {
						log.Printf("[beacon] re-register failed (will retry): %v", rerr)
					} else {
						log.Printf("[beacon] re-registration OK")
					}
				} else {
					log.Printf("[beacon] poll error: %v", err)
				}
				select {
				case <-ctx.Done():
					log.Printf("[beacon] context cancelled — shutting down cleanly")
					return
				case <-time.After(c.Interval):
					continue
				}
			}
			if task == nil {
				continue
			}
			log.Printf("[beacon] received task task_id=%s run_id=%s scenario=%s steps=%d",
				task.TaskID, task.RunID, task.ScenarioID, len(task.Steps))
			c.executeTask(ctx, task)
		}
	}

	ticker := time.NewTicker(c.Interval)
	defer ticker.Stop()

	log.Printf("[beacon] agent %q started — polling %s every %s", c.AgentID, c.ServerURL, c.Interval)

	for {
		select {
		case <-ctx.Done():
			log.Printf("[beacon] context cancelled — shutting down cleanly")
			return

		case <-ticker.C:
			if c.lifetimeExpired() {
				return
			}
			c.retryPendingRegistration()
			task, err := c.PollTasks()
			if err != nil {
				if ctx.Err() != nil {
					// The poll was cut by the shutdown itself, not by SimCore.
					log.Printf("[beacon] context cancelled — shutting down cleanly")
					return
				}
				// An unknown-agent 404 means SimCore lost (or never had) our
				// registration — re-register and retry on the next tick rather
				// than idling forever (GAP-AGENT-003).
				if errors.Is(err, ErrUnknownAgent) {
					log.Printf("[beacon] SimCore reports agent %q unknown — re-registering", c.AgentID)
					if rerr := c.reRegister(); rerr != nil {
						log.Printf("[beacon] re-register failed (will retry next tick): %v", rerr)
					} else {
						log.Printf("[beacon] re-registration OK")
					}
					continue
				}
				log.Printf("[beacon] poll error: %v", err)
				continue
			}
			if task == nil {
				log.Printf("[beacon] no task pending")
				continue
			}

			log.Printf("[beacon] received task task_id=%s run_id=%s scenario=%s steps=%d",
				task.TaskID, task.RunID, task.ScenarioID, len(task.Steps))

			c.executeTask(ctx, task)
		}
	}
}

// -------------------------------------------------------------------------
// Task execution
// -------------------------------------------------------------------------

// executeTask runs a task's steps in order, each through the identity harness.
//
// Semantics (matching the push bundle's `set -euo pipefail` fail-fast model):
//   - Steps execute sequentially; output is streamed per step (one /output POST
//     per step, attributed via step_id).
//   - Execution stops at the FIRST step with a non-zero exit code; the run's
//     final exit code is that first failing step's code (0 only if every step
//     exited 0).
//   - Abort is checked (a) once before each step, and (b) every controlPollInterval
//     while a step is running. On abort the in-flight step's process group is
//     SIGTERM'd (→SIGKILL after a grace period), remaining steps are skipped, and
//     the run is reported complete with exit code 130.
func (c *BeaconClient) executeTask(ctx context.Context, task *Task) {
	c.BindShutdown(ctx)
	c.beginFrames(task.TaskID)
	totalSteps := len(task.Steps)
	led := newOutcomeLedger(task)
	if totalSteps == 0 {
		log.Printf("[beacon] task run_id=%s has no steps — completing as no-op", task.RunID)
		_ = c.completeTask(task, 0, fmt.Sprintf("SUCCESS | scenario=%s | no steps", task.ScenarioID), []StepOutcome{})
		return
	}

	// ---- Artifact staging: all-or-nothing, BEFORE step 1 ----
	//
	// Placed above the hasCausalityContract() dispatch so ALL THREE execution
	// paths (this loop, executeTaskChained, executeTaskPerStep) inherit it from
	// one place. Teardown is registered in the same statement: pull mode never
	// executes a scenario's cleanup.commands — only push_generator emits those —
	// so the beacon owns removal outright and it cannot be delegated to content.
	// Every abort/shutdown/fail-fast branch in the three executors returns
	// normally, so this defer fires on every exit path.
	if len(task.Artifacts) > 0 {
		// Do not stage tooling for a run the operator has already killed.
		if state, _ := c.Control(task.RunID); state.Abort {
			log.Printf("[beacon] stop detected before artifact staging (run_id=%s lost=%v)", task.RunID, state.Lost)
			_ = c.SendOutput(task.RunID, "", strings.TrimSuffix(state.stopLine(), " ---")+" before artifact staging ---")
			led.markRemaining(0, ReasonUpstreamFailed)
			_ = c.completeTask(task, abortExitCode, state.stopBeforeSummary("(artifact staging)"), led.steps)
			return
		}
	}
	staged := c.stageArtifacts(ctx, task)
	defer staged.Cleanup(c, task.RunID)

	if !staged.OK() {
		// A step whose tool could not be staged must not run, must not be
		// skipped silently, and must be reported as not-having-run.
		c.reportStagingFailure(task, staged)
		return
	}
	if staged.Total > 0 {
		if err := c.SendOutput(task.RunID, "", staged.Ledger()); err != nil {
			log.Printf("[beacon] sendOutput (staging ledger) error: %v", err)
		}
	}

	// Contract mode: run the steps as children of ONE anchor shell so the
	// endpoint sensor traces a connected causality chain rooted at the CGO. The
	// default independent-per-step path below is used for every other scenario,
	// byte-for-byte unchanged.
	if task.hasCausalityContract() {
		c.executeTaskChained(ctx, task)
		return
	}

	finalExitCode := 0
	degradedSteps := 0
	refused := 0

	for i, step := range task.Steps {
		// (a) Abort check before each step.
		if state, _ := c.Control(task.RunID); state.Abort {
			c.stopBeforeStep(task, i, led, state)
			return
		}
		plan := planStep(&step, beaconGOOS)
		if plan.refuse != "" {
			c.refuseStep(task, i, led, plan)
			refused++
			continue
		}
		step.Command = plan.command

		username := c.resolveStepUsername(task, &step)
		idRes := c.resolve(username)
		logIdentity(step.ID, username, idRes)
		if idRes.Degraded() {
			degradedSteps++
		}

		log.Printf("[beacon] step %d/%d id=%s name=%q technique=%s identity=%s mode=%s",
			i+1, totalSteps, step.ID, step.Name, step.MitreTechnique, username, idRes.Mode)

		if c.preflightTools(task, i, led, degradedSteps) {
			return
		}
		depOutcome := c.resolveRuntimeDeps(task, &step)

		var res identity.ExecResult
		var stop *ControlState
		var timedOut bool
		var execErr error
		stTimeout := c.stepTimeout(&step)
		if depOutcome.blocked {
			res = depOutcome.synthetic
		} else {
			execID := identity.ExecutionIdentity{
				Mode:     idRes.Mode,
				Username: idRes.Username,
				Command:  step.Command,
				Shell:    plan.shell,
			}
			res, stop, timedOut, execErr = c.runStep(ctx, task.RunID, step.ID, execID, stTimeout)
		}
		depOutcome.cleanup()

		notes := joinNotes(plan.note, depOutcome.note)
		if timedOut {
			tn := fmt.Sprintf("%s command exceeded %s timeout and was terminated", stepTimeoutMarker, stTimeout)
			if notes != "" {
				notes += "\n" + tn
			} else {
				notes = tn
			}
		}

		// Build the per-step output and attribute it to this step.
		stepOut := c.formatStepOutput(i+1, totalSteps, &step, username, idRes, res, execErr, notes)
		if err := c.SendOutput(task.RunID, step.ID, stepOut); err != nil {
			log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, err)
		}

		if stop != nil {
			led.recordAborted(i)
			log.Printf("[beacon] step %s terminated (lost-control=%v, run_id=%s)", step.ID, stop.Lost, task.RunID)
			_ = c.completeTask(task, abortExitCode, stop.stopDuringSummary(step.ID), led.steps)
			return
		}

		if timedOut {
			led.recordTimeout(i)
			log.Printf("[beacon] step %s timed out after %s (run_id=%s)", step.ID, stTimeout, task.RunID)
			_ = c.completeTask(task, timeoutExitCode,
				fmt.Sprintf("step %s timed out after %s", step.ID, stTimeout), led.steps)
			return
		}

		// Honour agent shutdown (parent ctx cancel that is NOT an operator abort).
		if ctx.Err() != nil {
			led.recordAborted(i)
			log.Printf("[beacon] context cancelled during task run_id=%s", task.RunID)
			_ = c.completeTask(task, abortExitCode, "agent shutdown — task interrupted", led.steps)
			return
		}

		if execErr != nil {
			led.recordExecError(i, res.ExitCode)
			// A real execution error (e.g. /bin/sh missing) — fail the run.
			log.Printf("[beacon] execution error step %s run_id=%s: %v", step.ID, task.RunID, execErr)
			_ = c.completeTask(task, res.ExitCode, fmt.Sprintf("execution error in step %s: %v", step.ID, execErr), led.steps)
			return
		}

		if c.afterStep(task, i, led, res, depOutcome.blocked) {
			finalExitCode = 128 + res.Signal
			log.Printf("[beacon] step %s killed by signal %d — severing remaining steps", step.ID, res.Signal)
			break
		}

		log.Printf("[beacon] step %s complete exit_code=%d duration=%s", step.ID, res.ExitCode, res.Duration)

		// Fail-fast: stop at the first non-zero step.
		if res.ExitCode != 0 {
			finalExitCode = res.ExitCode
			log.Printf("[beacon] step %s exited non-zero (%d) — stopping remaining steps (fail-fast)",
				step.ID, res.ExitCode)
			break
		}
	}

	if refused == totalSteps {
		// Nothing ran on this host. A zero here would read as SUCCESS for a run
		// that executed no TTP at all.
		finalExitCode = platformUnsatisfiableExitCode
	}
	summary := buildSummary(task, finalExitCode, degradedSteps, led.steps)
	if err := c.completeTask(task, finalExitCode, summary, led.steps); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
}

// newChainSession starts the anchor shell. A package var only so a test can force
// the start failure that makes executeTaskChained degrade to executeTaskPerStep.
var newChainSession = executor.NewChainSession

// anchorNotHonouredLine is the run-record notice for a cgo_anchor the host's
// shell could not carry (sh is busybox/toybox, which cannot be renamed, and
// there is no bash).
func anchorNotHonouredLine(image string) string {
	return fmt.Sprintf("ANCHOR NOT HONOURED: requested %s; this image's sh is a multi-call binary (busybox/toybox) "+
		"that cannot be started under another name and there is no bash on PATH, "+
		"so the chain runs under 'sh'. Detections keyed on the anchor process name will not match.", image)
}

// executeTaskChained runs the task through a single persistent anchor shell so
// every step is a child of the same Causality Group Owner — a connected process
// chain, not a star of independent `sh -c` invocations. It preserves the exact
// observable contract of the default path: per-step /output POSTs, fail-fast on
// the first non-zero step, pre-step and in-step abort checks, and the same
// completion codes. If the anchor cannot be started it degrades to the default
// per-step path rather than failing the run.
func (c *BeaconClient) executeTaskChained(ctx context.Context, task *Task) {
	totalSteps := len(task.Steps)
	led := newOutcomeLedger(task)
	cgoImage := ""
	if task.CgoAnchor != nil {
		cgoImage = task.CgoAnchor.ImageName
	}

	// The session lives for the whole task; a cancel of sessCtx tears the whole
	// anchor group down (used for operator abort / agent shutdown).
	sessCtx, sessCancel := context.WithCancel(ctx)
	defer sessCancel()

	session, err := newChainSession(sessCtx, cgoImage)
	if err != nil {
		log.Printf("[beacon] chain-session start failed (run_id=%s): %v — falling back to per-step execution",
			task.RunID, err)
		c.executeTaskPerStep(ctx, task)
		return
	}
	// RG-AGENT-01 / RG-AGENT-02: pull mode runs no scenario cleanup, so ending
	// the session is the only teardown this run's background helpers get. Every
	// exit below that is not a clean finish — abort or control loss before or
	// during a step, timeout, a refused tool, an execution error, fail-fast —
	// leaves chainClean false and the session is Abandoned: everything the chain
	// started is stopped. A clean finish Closes it (POSIX still tears the
	// anchor's process group down; Windows leaves a successful chain's children
	// alone — see ChainSession.Close).
	chainClean := false
	defer func() {
		if chainClean {
			_ = session.Close()
		} else {
			_ = session.Abandon()
		}
		if how := session.Teardown(); how != "" {
			log.Printf("[beacon] chain end run_id=%s clean=%t: %s", task.RunID, chainClean, how)
		}
	}()

	// A requested cgo_anchor the host could not honour must say so in the run
	// record, once, before any step: otherwise anchor-keyed detections read as
	// "Cortex missed it".
	if cgoImage != "" && !session.AnchorRenamed() && len(task.Steps) > 0 {
		task.anchorNotHonoured = true
		if err := c.SendOutput(task.RunID, task.Steps[0].ID, anchorNotHonouredLine(cgoImage)); err != nil {
			log.Printf("[beacon] sendOutput (anchor notice, run_id=%s) error: %v", task.RunID, err)
		}
	}

	log.Printf("[beacon] causality-chained execution run_id=%s cgo=%q steps=%d",
		task.RunID, cgoImage, totalSteps)

	finalExitCode := 0
	degradedSteps := 0
	refused := 0
	for i, step := range task.Steps {
		if state, _ := c.Control(task.RunID); state.Abort {
			c.stopBeforeStep(task, i, led, state)
			return
		}
		plan := planStep(&step, beaconGOOS)
		if plan.refuse != "" {
			c.refuseStep(task, i, led, plan)
			refused++
			continue
		}
		step.Command = plan.command

		username := c.resolveStepUsername(task, &step)
		idRes := c.resolve(username)
		logIdentity(step.ID, username, idRes)
		if idRes.Degraded() {
			degradedSteps++
		}

		if c.preflightTools(task, i, led, degradedSteps) {
			return
		}
		depOutcome := c.resolveRuntimeDeps(task, &step)

		var res identity.ExecResult
		var stop *ControlState
		var timedOut bool
		var execErr error
		stTimeout := c.stepTimeout(&step)
		if depOutcome.blocked {
			// The anchor session stays alive and untouched — a runtime-dependency
			// gap on ONE step must not tear down the causality chain for the rest.
			res = depOutcome.synthetic
		} else {
			wrapped, wrapErr := identity.WrapCommand(identity.ExecutionIdentity{
				Mode: idRes.Mode, Username: idRes.Username, Command: step.Command,
			})
			if wrapErr != nil {
				depOutcome.cleanup()
				log.Printf("[beacon] wrap error step %s run_id=%s: %v", step.ID, task.RunID, wrapErr)
				led.markRemaining(i, ReasonUpstreamFailed)
				_ = c.completeTask(task, 1, fmt.Sprintf("wrap error in step %s: %v", step.ID, wrapErr), led.steps)
				return
			}

			log.Printf("[beacon] step %d/%d id=%s name=%q technique=%s identity=%s mode=%s (chained)",
				i+1, totalSteps, step.ID, step.Name, step.MitreTechnique, username, idRes.Mode)

			res, stop, timedOut, execErr = c.runChainedStep(sessCtx, sessCancel, session, task.RunID, step.ID, plan.shell, wrapped, stTimeout)
		}
		depOutcome.cleanup()

		notes := joinNotes(plan.note, depOutcome.note)
		if timedOut {
			tn := fmt.Sprintf("%s command exceeded %s timeout and was terminated", stepTimeoutMarker, stTimeout)
			if notes != "" {
				notes += "\n" + tn
			} else {
				notes = tn
			}
		}

		stepOut := c.formatStepOutput(i+1, totalSteps, &step, username, idRes, res, execErr, notes)
		if sendErr := c.SendOutput(task.RunID, step.ID, stepOut); sendErr != nil {
			log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, sendErr)
		}

		if stop != nil {
			led.recordAborted(i)
			log.Printf("[beacon] step %s terminated (lost-control=%v, run_id=%s)", step.ID, stop.Lost, task.RunID)
			_ = c.completeTask(task, abortExitCode, stop.stopDuringSummary(step.ID), led.steps)
			return
		}
		if timedOut {
			led.recordTimeout(i)
			log.Printf("[beacon] step %s timed out after %s (run_id=%s)", step.ID, stTimeout, task.RunID)
			_ = c.completeTask(task, timeoutExitCode,
				fmt.Sprintf("step %s timed out after %s", step.ID, stTimeout), led.steps)
			return
		}
		if ctx.Err() != nil {
			led.recordAborted(i)
			log.Printf("[beacon] context cancelled during task run_id=%s", task.RunID)
			_ = c.completeTask(task, abortExitCode, "agent shutdown — task interrupted", led.steps)
			return
		}
		if errors.Is(execErr, executor.ErrAnchorTerminated) {
			// The anchor shell died mid-step: the step's outcome is unknown and
			// the chain is gone. Not an execution error — a sever.
			log.Printf("[beacon] anchor terminated during step %s run_id=%s — severing remaining steps", step.ID, task.RunID)
			led.record(StepOutcome{StepID: step.ID, Termination: TermAnchorTerminated})
			led.markRemaining(i+1, ReasonUpstreamSevered)
			_ = c.completeTask(task, 137, buildSummary(task, 137, degradedSteps, led.steps), led.steps)
			return
		}
		if execErr != nil {
			led.recordExecError(i, res.ExitCode)
			log.Printf("[beacon] execution error step %s run_id=%s: %v", step.ID, task.RunID, execErr)
			_ = c.completeTask(task, res.ExitCode, fmt.Sprintf("execution error in step %s: %v", step.ID, execErr), led.steps)
			return
		}

		if !depOutcome.blocked {
			classifyChainedRC(&res)
		}
		if c.afterStep(task, i, led, res, depOutcome.blocked) {
			finalExitCode = 128 + res.Signal
			log.Printf("[beacon] step %s killed by signal %d — severing remaining steps (chained)", step.ID, res.Signal)
			break
		}

		log.Printf("[beacon] step %s complete exit_code=%d duration=%s (chained)", step.ID, res.ExitCode, res.Duration)

		if res.ExitCode != 0 {
			finalExitCode = res.ExitCode
			log.Printf("[beacon] step %s exited non-zero (%d) — stopping remaining steps (fail-fast)",
				step.ID, res.ExitCode)
			break
		}
	}

	if refused == totalSteps {
		// Nothing ran on this host. A zero here would read as SUCCESS for a run
		// that executed no TTP at all.
		finalExitCode = platformUnsatisfiableExitCode
	}
	// Only a chain whose every executed step exited 0 ended cleanly; a fail-fast
	// or signal-severed break arrives here with a non-zero code.
	chainClean = finalExitCode == 0
	summary := buildSummary(task, finalExitCode, degradedSteps, led.steps)
	if err := c.completeTask(task, finalExitCode, summary, led.steps); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
}

// runChainedStep runs one already-wrapped command through the anchor session,
// polling the abort control channel while it runs and enforcing step execution timeout.
// On abort or timeout it cancels the session (tearing down the anchor group).
func (c *BeaconClient) runChainedStep(
	sessCtx context.Context, sessCancel context.CancelFunc,
	session *executor.ChainSession, runID, stepID, shell, wrapped string, timeout time.Duration,
) (identity.ExecResult, *ControlState, bool, error) {
	if timeout <= 0 {
		timeout = defaultStepTimeout
	}
	type result struct {
		res identity.ExecResult
		err error
	}
	done := make(chan result, 1)
	go func() {
		start := time.Now()
		stdout, stderr, code, e := session.RunStepShell(stepID, shell, wrapped)
		done <- result{
			res: identity.ExecResult{ExitCode: code, Stdout: stdout, Stderr: stderr, Duration: time.Since(start)},
			err: e,
		}
	}()

	ticker := time.NewTicker(controlPollInterval)
	defer ticker.Stop()

	timeoutTimer := time.NewTimer(timeout)
	defer timeoutTimer.Stop()

	for {
		select {
		case <-sessCtx.Done():
			r := <-done
			return r.res, nil, false, c.nonCancelErr(r.err)
		case <-timeoutTimer.C:
			sessCancel() // tear down the chain session's whole tree
			r := <-done
			r.res.ExitCode = timeoutExitCode
			return r.res, nil, true, nil
		case <-ticker.C:
			if state, _ := c.Control(runID); state.Abort {
				sessCancel() // tear the anchor's whole tree down
				r := <-done
				return r.res, &state, false, nil
			}
		case r := <-done:
			return r.res, nil, false, c.nonCancelErr(r.err)
		}
	}
}

// executeTaskPerStep is the default independent-per-step execution path, factored
// out so executeTaskChained can degrade to it if the anchor shell won't start.
func (c *BeaconClient) executeTaskPerStep(ctx context.Context, task *Task) {
	totalSteps := len(task.Steps)
	led := newOutcomeLedger(task)
	finalExitCode := 0
	degradedSteps := 0
	refused := 0
	for i, step := range task.Steps {
		if state, _ := c.Control(task.RunID); state.Abort {
			c.stopBeforeStep(task, i, led, state)
			return
		}
		plan := planStep(&step, beaconGOOS)
		if plan.refuse != "" {
			c.refuseStep(task, i, led, plan)
			refused++
			continue
		}
		step.Command = plan.command
		username := c.resolveStepUsername(task, &step)
		idRes := c.resolve(username)
		logIdentity(step.ID, username, idRes)
		if idRes.Degraded() {
			degradedSteps++
		}
		log.Printf("[beacon] step %d/%d id=%s name=%q technique=%s identity=%s mode=%s",
			i+1, totalSteps, step.ID, step.Name, step.MitreTechnique, username, idRes.Mode)
		if c.preflightTools(task, i, led, degradedSteps) {
			return
		}
		depOutcome := c.resolveRuntimeDeps(task, &step)
		var res identity.ExecResult
		var stop *ControlState
		var timedOut bool
		var execErr error
		stTimeout := c.stepTimeout(&step)
		if depOutcome.blocked {
			res = depOutcome.synthetic
		} else {
			execID := identity.ExecutionIdentity{Mode: idRes.Mode, Username: idRes.Username, Command: step.Command, Shell: plan.shell}
			res, stop, timedOut, execErr = c.runStep(ctx, task.RunID, step.ID, execID, stTimeout)
		}
		depOutcome.cleanup()

		notes := joinNotes(plan.note, depOutcome.note)
		if timedOut {
			tn := fmt.Sprintf("%s command exceeded %s timeout and was terminated", stepTimeoutMarker, stTimeout)
			if notes != "" {
				notes += "\n" + tn
			} else {
				notes = tn
			}
		}

		stepOut := c.formatStepOutput(i+1, totalSteps, &step, username, idRes, res, execErr, notes)
		if err := c.SendOutput(task.RunID, step.ID, stepOut); err != nil {
			log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, err)
		}
		if stop != nil {
			led.recordAborted(i)
			_ = c.completeTask(task, abortExitCode, stop.stopDuringSummary(step.ID), led.steps)
			return
		}
		if timedOut {
			led.recordTimeout(i)
			log.Printf("[beacon] step %s timed out after %s (run_id=%s)", step.ID, stTimeout, task.RunID)
			_ = c.completeTask(task, timeoutExitCode,
				fmt.Sprintf("step %s timed out after %s", step.ID, stTimeout), led.steps)
			return
		}
		if ctx.Err() != nil {
			led.recordShutdown(i)
			_ = c.completeTask(task, abortExitCode, "agent shutdown — task interrupted", led.steps)
			return
		}
		if execErr != nil {
			led.recordExecError(i, res.ExitCode)
			_ = c.completeTask(task, res.ExitCode, fmt.Sprintf("execution error in step %s: %v", step.ID, execErr), led.steps)
			return
		}
		if c.afterStep(task, i, led, res, depOutcome.blocked) {
			finalExitCode = 128 + res.Signal
			break
		}
		if res.ExitCode != 0 {
			finalExitCode = res.ExitCode
			break
		}
	}
	if refused == totalSteps {
		// Nothing ran on this host. A zero here would read as SUCCESS for a run
		// that executed no TTP at all.
		finalExitCode = platformUnsatisfiableExitCode
	}
	summary := buildSummary(task, finalExitCode, degradedSteps, led.steps)
	if err := c.completeTask(task, finalExitCode, summary, led.steps); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
}

// runStep executes one step via the identity harness, polling the abort control
// channel every controlPollInterval while it runs. It returns the exec result,
// whether the step was aborted by an operator, whether it timed out, and any execution error.
//
// The step runs under a child context derived from ctx with a step execution timeout;
// on timeout, abort (or parent cancel) the child context is cancelled, which signals
// the step's process group via the executor's RunCommandCtx.
func (c *BeaconClient) runStep(ctx context.Context, runID, stepID string, execID identity.ExecutionIdentity, timeout time.Duration) (identity.ExecResult, *ControlState, bool, error) {
	if timeout <= 0 {
		timeout = defaultStepTimeout
	}
	stepCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	type result struct {
		res identity.ExecResult
		err error
	}
	done := make(chan result, 1)

	var onChunk executor.OutputChunkFunc
	if c.StreamOutput {
		onChunk = func(chunk string, isStderr bool) {
			_ = c.sendOutput(runID, stepID, chunk, false)
		}
	}

	run := c.execStep
	if run == nil {
		run = identity.ExecuteCtxStream
	}
	go func() {
		r, e := run(stepCtx, execID, onChunk)
		done <- result{r, e}
	}()

	controlTicker := time.NewTicker(controlPollInterval)
	defer controlTicker.Stop()

	for {
		select {
		case <-ctx.Done():
			// Agent shutdown — cancel the step and wait for it to drain.
			cancel()
			r := <-done
			return r.res, nil, false, c.nonCancelErr(r.err)

		case <-stepCtx.Done():
			if errors.Is(stepCtx.Err(), context.DeadlineExceeded) {
				r := <-done
				r.res.ExitCode = timeoutExitCode
				return r.res, nil, true, nil
			}
			r := <-done
			return r.res, nil, false, c.nonCancelErr(r.err)

		case <-controlTicker.C:
			// (b) Abort check while the step runs. A lost control channel stops
			// the step too (fail-closed); the returned state says which it was.
			if state, _ := c.Control(runID); state.Abort {
				cancel() // tear the step's whole tree down
				r := <-done
				return r.res, &state, false, nil
			}

		case r := <-done:
			// ExecuteCtx returns context.Canceled if we cancelled mid-flight; the
			// only way that happens here is a race where ctx was cancelled (shutdown)
			// just as the step finished. Treat a clean finish as not-aborted.
			return r.res, nil, false, c.nonCancelErr(r.err)
		}
	}
}

// stopBeforeStep reports a run stopped by the control channel before step i ran.
func (c *BeaconClient) stopBeforeStep(task *Task, i int, led *outcomeLedger, state ControlState) {
	stepID := task.Steps[i].ID
	log.Printf("[beacon] stop detected before step %s (run_id=%s lost-control=%v)", stepID, task.RunID, state.Lost)
	_ = c.SendOutput(task.RunID, stepID, state.stopLine())
	led.markRemaining(i, ReasonUpstreamFailed)
	_ = c.completeTask(task, abortExitCode, state.stopBeforeSummary(stepID), led.steps)
}

// refuseStep records step i as not run on this host (BEACON-01) and writes the
// marker into the run record. The run continues with the next step: a step
// that cannot run HERE is not a failed step, and its detections read
// not_executed — never "missed".
func (c *BeaconClient) refuseStep(task *Task, i int, led *outcomeLedger, plan stepPlan) {
	step := &task.Steps[i]
	log.Printf("[beacon] %s (run_id=%s)", plan.refuse, task.RunID)
	out := fmt.Sprintf("=== STEP %d/%d · %s · %s ===\n%s\n"+
		"   THIS STEP DID NOT RUN on this host. Its detections are not_executed, not missed.\n"+
		"--- exit_code=none duration=0s ---",
		i+1, len(task.Steps), step.ID, step.MitreTechnique, plan.refuse)
	if err := c.SendOutput(task.RunID, step.ID, out); err != nil {
		log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, err)
	}
	led.record(StepOutcome{StepID: step.ID, Termination: TermNotRun, Reason: ReasonPlatformUnsatisfiable})
}

// nonCancelErr filters out context.Canceled (which is an expected abort/shutdown
// signal, not an execution failure) so the caller does not treat it as an error.
func (c *BeaconClient) nonCancelErr(err error) error {
	if err == context.Canceled {
		return nil
	}
	return err
}

// resolveStepUsername applies the spec's identity precedence:
//  1. step.Identity (per-step username — always present in practice)
//  2. task.IdentityContext (launch-time override)
//  3. task.IdentityDefault (scenario execution_identity.default)
//  4. "root" (direct)
func (c *BeaconClient) resolveStepUsername(task *Task, step *Step) string {
	if step.Identity != "" {
		return step.Identity
	}
	if task.IdentityContext != "" {
		return task.IdentityContext
	}
	if task.IdentityDefault != "" {
		return task.IdentityDefault
	}
	return "root"
}

// -------------------------------------------------------------------------
// Internal helpers
// -------------------------------------------------------------------------

// post marshals body as JSON and POSTs it to path on the SimCore server, once.
// Returns the raw response body on success (2xx). It carries nothing whose loss
// costs evidence (registration, a live output chunk), so a shutdown cancels it
// at once.
func (c *BeaconClient) post(path string, body interface{}) ([]byte, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}
	respBody, status, err := c.postOnce(path, data, 0)
	if err != nil {
		return nil, fmt.Errorf("POST %s: %w", path, err)
	}
	if status < 200 || status >= 300 {
		return nil, fmt.Errorf("POST %s: status %d: %s", path, status, respBody)
	}
	return respBody, nil
}

// postOnce makes one POST attempt. Once the beacon has been told to stop
// (BindShutdown) the attempt is cancelled: at once when grace is 0, otherwise
// grace after the stop — whether the stop arrived before the attempt or during
// it. Without this an attempt against a SimCore that drops packets holds the
// stop for the client's whole 30 s timeout.
func (c *BeaconClient) postOnce(path string, data []byte, grace time.Duration) ([]byte, int, error) {
	stop := c.stopCtx()
	reqCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if stop.Done() != nil {
		finished := make(chan struct{})
		defer close(finished)
		go func() {
			select {
			case <-finished:
				return
			case <-stop.Done():
			}
			if grace > 0 {
				t := time.NewTimer(grace)
				defer t.Stop()
				select {
				case <-finished:
					return
				case <-t.C:
				}
			}
			cancel()
		}()
	}

	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, c.ServerURL+path, bytes.NewReader(data))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		if stop.Err() != nil && reqCtx.Err() != nil {
			return nil, 0, fmt.Errorf("%w (cut short: the beacon is shutting down)", err)
		}
		return nil, 0, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	return respBody, resp.StatusCode, nil
}

// postRetry is post for the two POSTs whose loss costs evidence: a step's
// /output and the run's /complete (BEACON-11 / OPS-04). Base made ONE attempt,
// so a SimCore restart or a proxy blip at the moment a step finished lost that
// step's output, or left the run "running" until the beacon-lost sweep.
//
// It retries ONLY transport errors and 5xx, waiting postRetryBackoff[i] before
// retry i (1+2+4+8+16 s: up to 5 retries, 6 attempts, ~31 s). 409 means the run
// is already terminal on SimCore — nothing a retry can change — and is treated
// as success. 404 (unknown run) and every other 4xx stop at once.
//
// RG-AGENT-04: the ladder yields to a shutdown. A stop that arrives during a
// backoff wait ends the wait and buys ONE final attempt; an attempt in flight
// when the stop arrives, or started after it, is cut shutdownPostGrace later;
// and no attempt that fails after the stop is retried. So a stopping beacon
// spends at most shutdownPostGrace per POST here — the ladder used to hold
// SIGTERM for 62 s with SimCore down (measured), past launchd's kill timer, and
// a SIGKILL skips the staged-artifact cleanup.
func (c *BeaconClient) postRetry(path string, body interface{}) ([]byte, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}
	stop := c.stopCtx()
	for attempt := 0; ; attempt++ {
		respBody, status, err := c.postOnce(path, data, shutdownPostGrace)
		var retryErr error
		switch {
		case err != nil:
			retryErr = fmt.Errorf("POST %s: %w", path, err)
		case status >= 200 && status < 300:
			return respBody, nil
		case status == http.StatusConflict:
			log.Printf("[beacon] POST %s: 409 — run already terminal on SimCore; treating as delivered", path)
			return respBody, nil
		case status >= 500:
			retryErr = fmt.Errorf("POST %s: status %d: %s", path, status, respBody)
		default:
			return nil, fmt.Errorf("POST %s: status %d: %s", path, status, respBody)
		}
		if attempt >= len(postRetryBackoff) {
			return nil, retryErr
		}
		if stop.Err() != nil {
			return nil, fmt.Errorf("%w — not retried: the beacon is shutting down", retryErr)
		}
		wait := postRetryBackoff[attempt]
		log.Printf("[beacon] %v — retry %d/%d in %s", retryErr, attempt+1, len(postRetryBackoff), wait)
		timer := time.NewTimer(wait)
		select {
		case <-timer.C:
		case <-stop.Done():
			timer.Stop()
			log.Printf("[beacon] shutdown during the retry wait for POST %s — one final attempt (up to %s)",
				path, shutdownPostGrace)
		}
	}
}

// identityDegradedMarker prefixes an unhonoured-identity notice. It is a stable
// literal because it is what an operator (and any downstream parser of the run
// record) greps for to find steps whose actor attribution cannot be trusted.
const identityDegradedMarker = "!! IDENTITY NOT HONOURED:"

// runtimeDepMissingMarker prefixes the notice for a step whose declared
// `requires_interpreters` could not be satisfied on this target AND was not
// (successfully) resolved by an authorized runtime install. It is a stable
// literal for the same reason identityDegradedMarker is: deploy/tier-d/classify.py
// and any downstream reader greps for it to tell "the tool never ran" apart
// from "the tool ran and found nothing" — the exact distinction this feature
// exists to make legible instead of silently absorbed by a step's own
// `|| echo 'complete'` fallback.
const runtimeDepMissingMarker = "!! RUNTIME_DEPENDENCY_MISSING:"

// formatStepOutput renders one step's combined output with a step header so the
// SSE/log stream shows per-step progress.
//
// An unhonoured identity is written into this output — not just the agent log —
// because THIS is what reaches SimCore, the Result rows and the POV report. A
// step whose actor could not be impersonated but whose readout says otherwise
// is the exact false-green the engine exists to eliminate. runtimeNote carries
// the same guarantee for a step's interpreter dependency — see resolveRuntimeDeps.
func (c *BeaconClient) formatStepOutput(n, total int, step *Step, username string, idRes identity.Resolution, res identity.ExecResult, execErr error, runtimeNote string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "=== STEP %d/%d · %s · %s · identity=%s ===\n",
		n, total, step.ID, step.MitreTechnique, username)
	if beaconGOOS == "windows" {
		fmt.Fprintf(&b, "%s\n", windowsExecutionAccount())
	}
	if idRes.Degraded() {
		fmt.Fprintf(&b, "%s %s\n", identityDegradedMarker, idRes.Degradation)
	}
	if runtimeNote != "" {
		fmt.Fprintf(&b, "%s\n", runtimeNote)
	}
	if execErr != nil {
		fmt.Fprintf(&b, "ERROR: %v\n", execErr)
	}
	combined := combineOutput(res.Stdout, res.Stderr)
	if combined != "" {
		b.WriteString(combined)
		if !strings.HasSuffix(combined, "\n") {
			b.WriteString("\n")
		}
	}
	fmt.Fprintf(&b, "--- exit_code=%d duration=%s ---", res.ExitCode, res.Duration.Round(time.Millisecond))
	return b.String()
}

// runtimeDepOutcome is the result of resolveRuntimeDeps for one step.
type runtimeDepOutcome struct {
	// blocked is true when a declared interpreter dependency could not be
	// satisfied (directly, via a shim, or via an authorized install) — the
	// step's own (possibly masking) command must NOT run.
	blocked bool
	// synthetic is the result to report when blocked is true. It is never the
	// product of running step.Command.
	synthetic identity.ExecResult
	// note is prefixed into the step's output regardless of outcome — the
	// runtime-dependency story is always visible, not only on failure.
	note string
	// shims must be Cleanup()'d after the step (whichever path was taken).
	shims []*executor.InterpreterShim
}

func (o runtimeDepOutcome) cleanup() {
	for _, s := range o.shims {
		s.Cleanup()
	}
}

// resolveRuntimeDeps checks step.RequiresInterpreters against what genuinely
// exists on THIS host right now and returns how the step should proceed. It
// mutates step.Command in place for the two paths that keep going (PATH-shim,
// authorized install) and leaves step.Command untouched when nothing is
// declared, and always on Windows (see resolveRuntimeDepsWindows).
//
// THE GUARANTEE: when a required interpreter is absent and no authorized
// install can supply it, this function returns blocked=true and the caller
// MUST use the synthetic result instead of executing step.Command. This is
// what makes "a missing runtime dependency must never present as success"
// true by construction rather than by scenario authors remembering not to
// write `|| echo done`. It holds on Windows as well (BEACON-13).
//
// runtimeDepsGOOS is the runtime-dependency and tool-presence checks' view of
// the host OS. It defaults to runtime.GOOS and exists as a package var — rather
// than a bare runtime.GOOS reference — solely so a test can flip it to "windows"
// and drive the Windows rule without a Windows host. See
// runtime_deps_winrule_test.go.
var runtimeDepsGOOS = runtime.GOOS

func (c *BeaconClient) resolveRuntimeDeps(task *Task, step *Step) runtimeDepOutcome {
	if len(step.RequiresInterpreters) == 0 {
		return runtimeDepOutcome{}
	}
	if runtimeDepsGOOS == "windows" {
		return resolveRuntimeDepsWindows(task, step)
	}

	var notes []string
	var shims []*executor.InterpreterShim
	// installSteps pairs each authorized install command with the logical
	// name it was meant to supply, so the composed command can re-verify and
	// shim EACH one individually after its own install runs (C2) rather than
	// trusting `apt-get install` exit 0 as proof the step's own hardcoded
	// interpreter name will resolve.
	type installStep struct {
		cmd     string
		logical string
	}
	var installSteps []installStep

	for _, logical := range step.RequiresInterpreters {
		check := executor.ResolveInterpreter(logical)
		if check.Found {
			if !check.Exact {
				shim, err := executor.NewInterpreterShim(logical, check.Path)
				if err != nil {
					notes = append(notes, fmt.Sprintf(
						"[cortexsim] runtime dependency %q found (%s) but the PATH shim could not be created: %v — running unmodified",
						logical, check.Resolved, err))
					continue
				}
				shims = append(shims, shim)
				notes = append(notes, fmt.Sprintf(
					"[cortexsim] runtime dependency %q satisfied via %s (%s) — PATH-shimmed for this step only, host unchanged",
					logical, check.Resolved, check.Path))
			}
			continue
		}

		if task.RuntimeInstallAuthorized {
			if installCmd, ok := executor.InstallPackageCommand(logical); ok {
				installSteps = append(installSteps, installStep{cmd: installCmd, logical: logical})
				notes = append(notes, fmt.Sprintf(
					"[cortexsim] RUNTIME_INSTALL_AUTHORIZED: %s missing — attempting %q, then re-verifying (and "+
						"PATH-shimming if it only landed under an alias) before the step's own command runs "+
						"(operator-authorized, recorded in the run record)",
					logical, installCmd))
				continue
			}
			notes = append(notes, fmt.Sprintf(
				"[cortexsim] runtime dependency %q missing and no supported package manager was found on this host — cannot deliver a system update",
				logical))
		}

		// Genuinely unresolved: neither a path nor an authorized install.
		// step.Command must never run.
		for _, s := range shims {
			s.Cleanup()
		}
		return runtimeDepOutcome{
			blocked: true,
			note: fmt.Sprintf(
				"%s %s — this step's command was NOT executed, so it produced no signal. This is an ENVIRONMENT gap, not a detection result.",
				runtimeDepMissingMarker, logical),
			synthetic: identity.ExecResult{
				ExitCode: 127,
				Stderr:   fmt.Sprintf("%s %s (no interpreter path and no authorized runtime install)", runtimeDepMissingMarker, logical),
			},
		}
	}

	cmd := step.Command
	if len(installSteps) > 0 {
		// `&&` is load-bearing throughout: if the install fails, or the
		// post-install verification exits 127 (executor.PostInstallVerifyShim),
		// the original command — including any `|| echo ...` fallback IT
		// authored — never runs. An install (or verification) failure must
		// surface as this step's own non-zero exit, never be absorbed by the
		// scenario's own masking.
		//
		// C2: a bare `apt-get install -y python3` exiting 0 is NOT proof that
		// the step's own hardcoded `python` will resolve — apt-get installing
		// the python3 package produces /usr/bin/python3 and no /usr/bin/python.
		// PostInstallVerifyShim re-resolves the LOGICAL name after its install
		// ran and, if it only landed under an alias, shims it exactly like the
		// "already there" branch above — the two branches converge instead of
		// diverging.
		parts := make([]string, 0, len(installSteps)*2)
		for _, is := range installSteps {
			parts = append(parts, is.cmd, executor.PostInstallVerifyShim(is.logical))
		}
		cmd = strings.Join(parts, " && ") + " && (" + cmd + ")"
	}
	if len(shims) > 0 {
		dirs := make([]string, len(shims))
		for i, s := range shims {
			dirs[i] = s.Dir
		}
		cmd = fmt.Sprintf("export PATH=%q:\"$PATH\"\n%s", strings.Join(dirs, ":"), cmd)
	}
	step.Command = cmd

	return runtimeDepOutcome{note: strings.Join(notes, "\n"), shims: shims}
}

// resolveRuntimeDepsWindows is resolveRuntimeDeps for a Windows host (BEACON-13).
// Until it existed the check was skipped there without a word: a step whose
// interpreter was missing ran, and under PowerShell a command that is not found
// does not fail the step — `python tool.py; Write-Output done` exits 0 — so a
// run that executed nothing reported a PASS.
//
// Windows gets the guarantee, not the two repairs:
//   - resolvable under its OWN logical name: the step runs, unmodified.
//   - absent: blocked.
//   - present only under an alias (python3, not python): blocked as well. The
//     repair for that on POSIX is a symlink shim on PATH, which Windows cannot
//     use, and the step's own text would not find the interpreter by the name
//     it declared.
//   - an authorised runtime install is never composed: InstallPackageCommand
//     and PostInstallVerifyShim emit POSIX sh, which must not reach PowerShell.
//
// It never rewrites step.Command.
func resolveRuntimeDepsWindows(task *Task, step *Step) runtimeDepOutcome {
	for _, logical := range step.RequiresInterpreters {
		check := executor.ResolveInterpreter(logical)
		if check.Found && check.Exact {
			continue
		}
		var why, short string
		switch {
		case check.Found:
			why = fmt.Sprintf("it is on PATH only as %s (%s), and the PATH shim that exposes an alias under "+
				"its logical name is POSIX-only, so a step that calls %q would not find it",
				check.Resolved, check.Path, logical)
			short = fmt.Sprintf("only %s is on PATH and Windows has no alias shim", check.Resolved)
		case check.AppExecAlias != "":
			why = fmt.Sprintf("the only match on PATH is the App Execution Alias %s — the Microsoft Store "+
				"stub unless Python was installed from the Store — which is not counted as an interpreter",
				check.AppExecAlias)
			short = "only a Microsoft Store App Execution Alias is on PATH"
		default:
			why = "no interpreter of that name is on this host's PATH"
			short = "no interpreter path"
		}
		note := fmt.Sprintf("%s %s — %s. This step's command was NOT executed, so it produced no signal. "+
			"This is an ENVIRONMENT gap, not a detection result.", runtimeDepMissingMarker, logical, why)
		if task.RuntimeInstallAuthorized {
			note += " An authorised runtime install is not supported on Windows and was not attempted."
		}
		return runtimeDepOutcome{
			blocked: true,
			note:    note,
			synthetic: identity.ExecResult{
				ExitCode: 127,
				Stderr:   fmt.Sprintf("%s %s (%s)", runtimeDepMissingMarker, logical, short),
			},
		}
	}
	return runtimeDepOutcome{}
}

// combineOutput merges stdout and stderr into a single log-friendly string.
func combineOutput(stdout, stderr string) string {
	var b strings.Builder
	if stdout != "" {
		b.WriteString("--- STDOUT ---\n")
		b.WriteString(stdout)
	}
	if stderr != "" {
		if b.Len() > 0 {
			b.WriteString("\n")
		}
		b.WriteString("--- STDERR ---\n")
		b.WriteString(stderr)
	}
	return b.String()
}

// buildSummary creates a human-readable completion summary for the whole task.
//
// degradedSteps is carried into the summary rather than left in the per-step
// output alone: the summary is the one line a DC reads on the run card, and a
// run where N steps could not honour their declared identity must not present
// as an unqualified SUCCESS.
func buildSummary(task *Task, exitCode, degradedSteps int, steps []StepOutcome) string {
	status := "SUCCESS"
	if exitCode != 0 {
		status = fmt.Sprintf("FAILED (exit %d)", exitCode)
	}
	summary := fmt.Sprintf("%s | scenario=%s steps=%d", status, task.ScenarioID, len(task.Steps))
	if degradedSteps > 0 {
		summary += fmt.Sprintf(" | IDENTITY DEGRADED: %d step(s) ran as the beacon account "+
			"instead of their declared identity — actor attribution is UNVALIDATED (see step output)",
			degradedSteps)
	}
	if task.anchorNotHonoured {
		summary += " | ANCHOR NOT HONOURED: the chain ran under 'sh', not the requested cgo_anchor " +
			"name — detections keyed on the anchor process name will not match (see first step output)"
	}
	if id, term, ok := severedAt(steps); ok {
		summary += fmt.Sprintf(" | SEVERED at %s (%s)", id, term)
	}
	var notHere []string
	for _, st := range steps {
		if st.Termination == TermNotRun && st.Reason == ReasonPlatformUnsatisfiable {
			notHere = append(notHere, st.StepID)
		}
	}
	if len(notHere) > 0 {
		summary += fmt.Sprintf(" | %s: %d step(s) not run on this host (%s) — their detections are "+
			"not_executed, not missed", ReasonPlatformUnsatisfiable, len(notHere), strings.Join(notHere, ","))
	}
	for _, st := range steps {
		if st.terminationUnknown {
			summary += " | TERMINATION_UNKNOWN: " + st.StepID
		}
	}
	return summary
}
