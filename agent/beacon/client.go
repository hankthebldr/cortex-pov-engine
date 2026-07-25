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
	"os"
	"runtime"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
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
	TaskID          string `json:"task_id"`
	RunID           string `json:"run_id"`
	ScenarioID      string `json:"scenario_id"`
	Steps           []Step `json:"steps"`
	IdentityContext string `json:"identity_context"` // launch-time username override; "" if null
	IdentityDefault string `json:"identity_default"` // scenario execution_identity.default
	CreatedAt       string `json:"created_at"`
	// CgoAnchor is the scenario's declared Causality Group Owner (a realistic
	// initial-access process). Present only for scenarios that declare the
	// causality contract; nil (and ignored on the wire) otherwise.
	CgoAnchor *CgoAnchor `json:"cgo_anchor,omitempty"`
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
	// expected_detections intentionally omitted — the server seeds Result rows.
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
}

// abortExitCode is the conventional exit code reported when a run is aborted by
// an operator (SIGINT convention = 128 + 2).
const abortExitCode = 130

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
	}
}

// -------------------------------------------------------------------------
// API methods
// -------------------------------------------------------------------------

// Register sends agent metadata to SimCore so it appears in the agent roster.
// Corresponds to: POST /api/agents/register
func (c *BeaconClient) Register(hostname, goos string, capabilities []string) error {
	// Remember the metadata so the run loop can transparently re-register on an
	// unknown-agent 404 (GAP-AGENT-003).
	c.regHostname = hostname
	c.regOS = goos
	c.regCapabilities = capabilities

	body := map[string]interface{}{
		"agent_id":     c.AgentID,
		"hostname":     hostname,
		"os":           goos,
		"capabilities": capabilities,
	}
	_, err := c.post("/api/agents/register", body)
	if err != nil {
		return fmt.Errorf("register: %w", err)
	}
	return nil
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
	return c.Register(hostname, goos, caps)
}

// PollTasks asks SimCore whether there is a pending task for this agent.
//
// Return contract (GAP-AGENT-003):
//   - (nil, nil)              → registered, but no task queued ({"task": null})
//   - (task, nil)            → a task to execute
//   - (nil, ErrUnknownAgent) → SimCore 404: this agent is not registered;
//     the caller should Register() and retry. This is NO LONGER conflated with
//     the idle "no task" case.
//
// Corresponds to: GET /api/agents/{id}/tasks
func (c *BeaconClient) PollTasks() (*Task, error) {
	url := fmt.Sprintf("%s/api/agents/%s/tasks", c.ServerURL, c.AgentID)

	resp, err := c.http.Get(url)
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
// A missing run (404) or any transport error is treated as a stop signal
// (abort=true): if the run row has vanished the agent should halt rather than
// spin. The server also returns abort=true for any terminal run status.
func (c *BeaconClient) Control(runID string) (ControlState, error) {
	url := fmt.Sprintf("%s/api/runs/%s/control", c.ServerURL, runID)

	resp, err := c.http.Get(url)
	if err != nil {
		return ControlState{Abort: true, RunID: runID, Status: "unknown"}, fmt.Errorf("control GET: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return ControlState{Abort: true, RunID: runID, Status: "unknown"}, nil
	}
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		return ControlState{Abort: true, RunID: runID, Status: "unknown"},
			fmt.Errorf("control: unexpected status %d: %s", resp.StatusCode, b)
	}

	var state ControlState
	if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
		return ControlState{Abort: true, RunID: runID, Status: "unknown"}, fmt.Errorf("control decode: %w", err)
	}
	return state, nil
}

// SendOutput streams partial or final command output back to SimCore, attributing
// it to a specific step. stepID may be empty for un-attributed output.
// Corresponds to: POST /api/runs/{run_id}/output  body {output, step_id?}
func (c *BeaconClient) SendOutput(runID, stepID, output string) error {
	body := map[string]string{
		"run_id": runID,
		"output": output,
	}
	if stepID != "" {
		body["step_id"] = stepID
	}
	_, err := c.post(fmt.Sprintf("/api/runs/%s/output", runID), body)
	if err != nil {
		return fmt.Errorf("sendOutput: %w", err)
	}
	return nil
}

// Complete marks a run as finished with its exit code and a human-readable summary.
// Corresponds to: POST /api/runs/{run_id}/complete
func (c *BeaconClient) Complete(runID string, exitCode int, summary string) error {
	body := map[string]interface{}{
		"run_id":    runID,
		"exit_code": exitCode,
		"summary":   summary,
	}
	_, err := c.post(fmt.Sprintf("/api/runs/%s/complete", runID), body)
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
	ticker := time.NewTicker(c.Interval)
	defer ticker.Stop()

	log.Printf("[beacon] agent %q started — polling %s every %s", c.AgentID, c.ServerURL, c.Interval)

	for {
		select {
		case <-ctx.Done():
			log.Printf("[beacon] context cancelled — shutting down cleanly")
			return

		case <-ticker.C:
			task, err := c.PollTasks()
			if err != nil {
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
	totalSteps := len(task.Steps)
	if totalSteps == 0 {
		log.Printf("[beacon] task run_id=%s has no steps — completing as no-op", task.RunID)
		_ = c.Complete(task.RunID, 0, fmt.Sprintf("SUCCESS | scenario=%s | no steps", task.ScenarioID))
		return
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

	for i, step := range task.Steps {
		// (a) Abort check before each step.
		if state, _ := c.Control(task.RunID); state.Abort {
			log.Printf("[beacon] abort detected before step %s (run_id=%s)", step.ID, task.RunID)
			_ = c.SendOutput(task.RunID, step.ID, "--- ABORTED (operator) ---")
			_ = c.Complete(task.RunID, abortExitCode, "aborted by operator")
			return
		}

		username := c.resolveStepUsername(task, &step)
		mode, user, unknown := identity.ResolveIdentity(username)
		if unknown {
			log.Printf("[beacon] WARN unknown identity %q for step %s — mapping to runuser (best-effort)",
				username, step.ID)
		}

		log.Printf("[beacon] step %d/%d id=%s name=%q technique=%s identity=%s mode=%s",
			i+1, totalSteps, step.ID, step.Name, step.MitreTechnique, username, mode)

		execID := identity.ExecutionIdentity{
			Mode:     mode,
			Username: user,
			Command:  step.Command,
		}

		res, aborted, execErr := c.runStep(ctx, task.RunID, execID)

		// Build the per-step output and attribute it to this step.
		stepOut := c.formatStepOutput(i+1, totalSteps, &step, username, res, execErr)
		if err := c.SendOutput(task.RunID, step.ID, stepOut); err != nil {
			log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, err)
		}

		if aborted {
			log.Printf("[beacon] step %s terminated by operator abort (run_id=%s)", step.ID, task.RunID)
			_ = c.Complete(task.RunID, abortExitCode,
				fmt.Sprintf("aborted by operator — step %s terminated", step.ID))
			return
		}

		// Honour agent shutdown (parent ctx cancel that is NOT an operator abort).
		if ctx.Err() != nil {
			log.Printf("[beacon] context cancelled during task run_id=%s", task.RunID)
			_ = c.Complete(task.RunID, abortExitCode, "agent shutdown — task interrupted")
			return
		}

		if execErr != nil {
			// A real execution error (e.g. /bin/sh missing) — fail the run.
			log.Printf("[beacon] execution error step %s run_id=%s: %v", step.ID, task.RunID, execErr)
			_ = c.Complete(task.RunID, res.ExitCode, fmt.Sprintf("execution error in step %s: %v", step.ID, execErr))
			return
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

	summary := buildSummary(task, finalExitCode)
	if err := c.Complete(task.RunID, finalExitCode, summary); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
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
	cgoImage := ""
	if task.CgoAnchor != nil {
		cgoImage = task.CgoAnchor.ImageName
	}

	// The session lives for the whole task; a cancel of sessCtx tears the whole
	// anchor group down (used for operator abort / agent shutdown).
	sessCtx, sessCancel := context.WithCancel(ctx)
	defer sessCancel()

	session, err := executor.NewChainSession(sessCtx, cgoImage)
	if err != nil {
		log.Printf("[beacon] chain-session start failed (run_id=%s): %v — falling back to per-step execution",
			task.RunID, err)
		c.executeTaskPerStep(ctx, task)
		return
	}
	defer session.Close()

	log.Printf("[beacon] causality-chained execution run_id=%s cgo=%q steps=%d",
		task.RunID, cgoImage, totalSteps)

	finalExitCode := 0
	for i, step := range task.Steps {
		if state, _ := c.Control(task.RunID); state.Abort {
			log.Printf("[beacon] abort detected before step %s (run_id=%s)", step.ID, task.RunID)
			_ = c.SendOutput(task.RunID, step.ID, "--- ABORTED (operator) ---")
			_ = c.Complete(task.RunID, abortExitCode, "aborted by operator")
			return
		}

		username := c.resolveStepUsername(task, &step)
		mode, user, unknown := identity.ResolveIdentity(username)
		if unknown {
			log.Printf("[beacon] WARN unknown identity %q for step %s — mapping to runuser (best-effort)",
				username, step.ID)
		}
		wrapped, wrapErr := identity.WrapCommand(identity.ExecutionIdentity{
			Mode: mode, Username: user, Command: step.Command,
		})
		if wrapErr != nil {
			log.Printf("[beacon] wrap error step %s run_id=%s: %v", step.ID, task.RunID, wrapErr)
			_ = c.Complete(task.RunID, 1, fmt.Sprintf("wrap error in step %s: %v", step.ID, wrapErr))
			return
		}

		log.Printf("[beacon] step %d/%d id=%s name=%q technique=%s identity=%s mode=%s (chained)",
			i+1, totalSteps, step.ID, step.Name, step.MitreTechnique, username, mode)

		res, aborted, execErr := c.runChainedStep(sessCtx, sessCancel, session, task.RunID, wrapped)

		stepOut := c.formatStepOutput(i+1, totalSteps, &step, username, res, execErr)
		if sendErr := c.SendOutput(task.RunID, step.ID, stepOut); sendErr != nil {
			log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, sendErr)
		}

		if aborted {
			log.Printf("[beacon] step %s terminated by operator abort (run_id=%s)", step.ID, task.RunID)
			_ = c.Complete(task.RunID, abortExitCode,
				fmt.Sprintf("aborted by operator — step %s terminated", step.ID))
			return
		}
		if ctx.Err() != nil {
			log.Printf("[beacon] context cancelled during task run_id=%s", task.RunID)
			_ = c.Complete(task.RunID, abortExitCode, "agent shutdown — task interrupted")
			return
		}
		if execErr != nil {
			log.Printf("[beacon] execution error step %s run_id=%s: %v", step.ID, task.RunID, execErr)
			_ = c.Complete(task.RunID, res.ExitCode, fmt.Sprintf("execution error in step %s: %v", step.ID, execErr))
			return
		}

		log.Printf("[beacon] step %s complete exit_code=%d duration=%s (chained)", step.ID, res.ExitCode, res.Duration)

		if res.ExitCode != 0 {
			finalExitCode = res.ExitCode
			log.Printf("[beacon] step %s exited non-zero (%d) — stopping remaining steps (fail-fast)",
				step.ID, res.ExitCode)
			break
		}
	}

	summary := buildSummary(task, finalExitCode)
	if err := c.Complete(task.RunID, finalExitCode, summary); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
}

// runChainedStep runs one already-wrapped command through the anchor session,
// polling the abort control channel while it runs. On abort it cancels the whole
// session (tearing down the anchor group) and reports the step aborted — mirror
// of runStep's semantics for the chained path.
func (c *BeaconClient) runChainedStep(
	sessCtx context.Context, sessCancel context.CancelFunc,
	session *executor.ChainSession, runID, wrapped string,
) (identity.ExecResult, bool, error) {
	type result struct {
		res identity.ExecResult
		err error
	}
	done := make(chan result, 1)
	go func() {
		start := time.Now()
		stdout, stderr, code, e := session.RunStep(wrapped)
		done <- result{
			res: identity.ExecResult{ExitCode: code, Stdout: stdout, Stderr: stderr, Duration: time.Since(start)},
			err: e,
		}
	}()

	ticker := time.NewTicker(controlPollInterval)
	defer ticker.Stop()

	aborted := false
	for {
		select {
		case <-sessCtx.Done():
			r := <-done
			return r.res, aborted, c.nonCancelErr(r.err)
		case <-ticker.C:
			if state, _ := c.Control(runID); state.Abort {
				aborted = true
				sessCancel() // tear the anchor group down
				r := <-done
				return r.res, true, nil
			}
		case r := <-done:
			return r.res, aborted, c.nonCancelErr(r.err)
		}
	}
}

// executeTaskPerStep is the default independent-per-step execution path, factored
// out so executeTaskChained can degrade to it if the anchor shell won't start.
func (c *BeaconClient) executeTaskPerStep(ctx context.Context, task *Task) {
	totalSteps := len(task.Steps)
	finalExitCode := 0
	for i, step := range task.Steps {
		if state, _ := c.Control(task.RunID); state.Abort {
			log.Printf("[beacon] abort detected before step %s (run_id=%s)", step.ID, task.RunID)
			_ = c.SendOutput(task.RunID, step.ID, "--- ABORTED (operator) ---")
			_ = c.Complete(task.RunID, abortExitCode, "aborted by operator")
			return
		}
		username := c.resolveStepUsername(task, &step)
		mode, user, unknown := identity.ResolveIdentity(username)
		if unknown {
			log.Printf("[beacon] WARN unknown identity %q for step %s — mapping to runuser (best-effort)",
				username, step.ID)
		}
		log.Printf("[beacon] step %d/%d id=%s name=%q technique=%s identity=%s mode=%s",
			i+1, totalSteps, step.ID, step.Name, step.MitreTechnique, username, mode)
		execID := identity.ExecutionIdentity{Mode: mode, Username: user, Command: step.Command}
		res, aborted, execErr := c.runStep(ctx, task.RunID, execID)
		stepOut := c.formatStepOutput(i+1, totalSteps, &step, username, res, execErr)
		if err := c.SendOutput(task.RunID, step.ID, stepOut); err != nil {
			log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, err)
		}
		if aborted {
			_ = c.Complete(task.RunID, abortExitCode,
				fmt.Sprintf("aborted by operator — step %s terminated", step.ID))
			return
		}
		if ctx.Err() != nil {
			_ = c.Complete(task.RunID, abortExitCode, "agent shutdown — task interrupted")
			return
		}
		if execErr != nil {
			_ = c.Complete(task.RunID, res.ExitCode, fmt.Sprintf("execution error in step %s: %v", step.ID, execErr))
			return
		}
		if res.ExitCode != 0 {
			finalExitCode = res.ExitCode
			break
		}
	}
	summary := buildSummary(task, finalExitCode)
	if err := c.Complete(task.RunID, finalExitCode, summary); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
}

// runStep executes one step via the identity harness, polling the abort control
// channel every controlPollInterval while it runs. It returns the exec result,
// whether the step was aborted by an operator, and any execution error.
//
// The step runs under a child context derived from ctx; on abort (or parent
// cancel) the child context is cancelled, which signals the step's process group
// via the executor's RunCommandCtx.
func (c *BeaconClient) runStep(ctx context.Context, runID string, execID identity.ExecutionIdentity) (identity.ExecResult, bool, error) {
	stepCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	type result struct {
		res identity.ExecResult
		err error
	}
	done := make(chan result, 1)
	go func() {
		r, e := identity.ExecuteCtx(stepCtx, execID)
		done <- result{r, e}
	}()

	controlTicker := time.NewTicker(controlPollInterval)
	defer controlTicker.Stop()

	aborted := false
	for {
		select {
		case <-ctx.Done():
			// Agent shutdown — cancel the step and wait for it to drain.
			cancel()
			r := <-done
			return r.res, false, c.nonCancelErr(r.err)

		case <-controlTicker.C:
			// (b) Abort check while the step runs.
			if state, _ := c.Control(runID); state.Abort {
				aborted = true
				cancel() // SIGTERM → SIGKILL the process group
				r := <-done
				return r.res, true, nil
			}

		case r := <-done:
			// ExecuteCtx returns context.Canceled if we cancelled mid-flight; the
			// only way that happens here is a race where ctx was cancelled (shutdown)
			// just as the step finished. Treat a clean finish as not-aborted.
			return r.res, aborted, c.nonCancelErr(r.err)
		}
	}
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

// post marshals body as JSON and POSTs it to path on the SimCore server.
// Returns the raw response body on success (2xx).
func (c *BeaconClient) post(path string, body interface{}) ([]byte, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}

	resp, err := c.http.Post(
		c.ServerURL+path,
		"application/json",
		bytes.NewReader(data),
	)
	if err != nil {
		return nil, fmt.Errorf("POST %s: %w", path, err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("POST %s: status %d: %s", path, resp.StatusCode, respBody)
	}
	return respBody, nil
}

// formatStepOutput renders one step's combined output with a step header so the
// SSE/log stream shows per-step progress.
func (c *BeaconClient) formatStepOutput(n, total int, step *Step, username string, res identity.ExecResult, execErr error) string {
	var b strings.Builder
	fmt.Fprintf(&b, "=== STEP %d/%d · %s · %s · identity=%s ===\n",
		n, total, step.ID, step.MitreTechnique, username)
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
func buildSummary(task *Task, exitCode int) string {
	status := "SUCCESS"
	if exitCode != 0 {
		status = fmt.Sprintf("FAILED (exit %d)", exitCode)
	}
	return fmt.Sprintf("%s | scenario=%s steps=%d", status, task.ScenarioID, len(task.Steps))
}
