package beacon

// Scenario cleanup in pull mode (#324).
//
// A push bundle runs the scenario's cleanup.commands when it exits. A pull run
// did not: the beacon removed only the tools it staged (artifact.go), so the
// scenario's files, units, cron lines and accounts stayed on the target, and
// some of them (a cron line, an enabled unit, a sudo-capable user) kept working
// after the run ended.
//
// SimCore now sends the commands this host's OS can run in the task's `cleanup`
// key. The beacon runs them once, when the task completes, and only if at
// least one step started: after the last step, after a failed step, after a
// timeout and after an abort. It writes one section to the run output and
// sends the outcome on /complete. A task without the key (an older SimCore, or
// a scenario with nothing to clean here) is handled exactly as before.

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strings"
	"sync/atomic"
	"time"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// TaskCleanup is the task's `cleanup` key.
type TaskCleanup struct {
	Commands []CleanupCommand `json:"commands"`
	// TimeoutSeconds bounds each command. Zero means defaultCleanupTimeout.
	TimeoutSeconds int `json:"timeout_seconds,omitempty"`
}

// CleanupCommand is one cleanup line. Shell follows Step.Shell: "" is the host
// default (sh on POSIX, PowerShell on Windows); "powershell" and "cmd" are what
// SimCore resolved for a Windows host.
type CleanupCommand struct {
	Command string `json:"command"`
	Shell   string `json:"shell,omitempty"`
}

// CleanupOutcome is the `cleanup` object of the /complete body.
//
// Ran counts the commands that were started and Failed those of them that did
// not exit 0. Commands never started (the shutdown budget ran out) are in
// neither; Status is "failed" and Reason says why.
type CleanupOutcome struct {
	Status string  `json:"status"`
	Ran    int     `json:"ran"`
	Failed int     `json:"failed"`
	Reason *string `json:"reason"`
}

// Status values of CleanupOutcome.
const (
	CleanupClean  = "clean"
	CleanupFailed = "failed"
	CleanupNotRun = "not_run"
)

// Reason values of CleanupOutcome.
const (
	cleanupReasonNoStepStarted = "NO_STEP_STARTED"
	cleanupReasonCommandFailed = "COMMAND_FAILED"
	cleanupReasonTimeout       = "COMMAND_TIMEOUT"
	cleanupReasonShutdown      = "SHUTDOWN_BUDGET_SPENT"
)

// defaultCleanupTimeout bounds one cleanup command when the task names no
// timeout.
const defaultCleanupTimeout = 120 * time.Second

// CleanupShutdownBudget is how long the whole cleanup may still take once the
// beacon has been told to stop. main waits this much longer than its usual
// grace while a cleanup is in flight (agent/lifetime.go).
const CleanupShutdownBudget = 30 * time.Second

// cleanupShutdownBudget is CleanupShutdownBudget; a var only so tests can
// shrink it.
var cleanupShutdownBudget = CleanupShutdownBudget

// cleanupsInFlight counts cleanups running in this process.
var cleanupsInFlight atomic.Int32

// CleanupInFlight reports whether a scenario cleanup is running right now.
func CleanupInFlight() bool { return cleanupsInFlight.Load() > 0 }

// anyStepStarted reports whether at least one step of the task was started.
// Every outcome but not_run is a step that ran, was killed or timed out.
func anyStepStarted(steps []StepOutcome) bool {
	for _, s := range steps {
		if s.Termination != TermNotRun {
			return true
		}
	}
	return false
}

// runTaskCleanup runs the task's cleanup commands and returns what to report
// on /complete, or nil when the task carries none (the body is then exactly
// what it was before cleanup existed).
//
// It is called from completeTask, the one place every task exit passes
// through, with the step outcomes that exit is about to report. A cleanup
// failure never changes the task's exit code or a step's outcome: the caller
// has already decided both.
func (c *BeaconClient) runTaskCleanup(task *Task, steps []StepOutcome) *CleanupOutcome {
	if task == nil || task.Cleanup == nil || len(task.Cleanup.Commands) == 0 || task.cleanupRan {
		return nil
	}
	task.cleanupRan = true
	if !anyStepStarted(steps) {
		// Nothing ran on this host, so there is nothing of this run's to remove.
		log.Printf("[beacon] cleanup skipped run_id=%s — no step started", task.RunID)
		return &CleanupOutcome{Status: CleanupNotRun, Reason: strPtr(cleanupReasonNoStepStarted)}
	}

	cleanupsInFlight.Add(1)
	defer cleanupsInFlight.Add(-1)

	ctx, cancel := c.cleanupContext()
	defer cancel()
	go c.cleanupHeartbeat(ctx, task.RunID)

	cmds := task.Cleanup.Commands
	total := len(cmds)
	timeout := defaultCleanupTimeout
	if task.Cleanup.TimeoutSeconds > 0 {
		timeout = time.Duration(task.Cleanup.TimeoutSeconds) * time.Second
	}
	log.Printf("[beacon] cleanup run_id=%s commands=%d timeout=%s", task.RunID, total, timeout)

	out := &CleanupOutcome{}
	timedOut, cutShort := false, false
	var b strings.Builder
	fmt.Fprintf(&b, "=== CLEANUP · %d commands · identity=direct ===\n", total)
	for i, cmd := range cmds {
		fmt.Fprintf(&b, "$ %s\n", cmd.Command)
		if ctx.Err() != nil {
			cutShort = true
			b.WriteString("not run: the agent is shutting down and its cleanup time is spent\n")
			fmt.Fprintf(&b, "--- cleanup %d/%d exit_code=none duration=0s ---\n", i+1, total)
			continue
		}

		cmdCtx, cancelCmd := context.WithTimeout(ctx, timeout)
		res, killed, err := c.execCleanup(cmdCtx, cmd)
		// A command we killed was stopped by its own timeout or, when the
		// parent context went first, by the shutdown budget. One that ended on
		// its own keeps its exit code, whatever has happened since.
		hitTimeout := killed && errors.Is(cmdCtx.Err(), context.DeadlineExceeded)
		cancelCmd()
		out.Ran++

		if combined := combineOutput(res.Stdout, res.Stderr); combined != "" {
			b.WriteString(combined)
			if !strings.HasSuffix(combined, "\n") {
				b.WriteString("\n")
			}
		}
		switch {
		case killed && !hitTimeout:
			cutShort = true
			res.ExitCode = abortExitCode
			b.WriteString("terminated: the agent is shutting down and its cleanup time is spent\n")
		case hitTimeout:
			timedOut = true
			res.ExitCode = timeoutExitCode
			fmt.Fprintf(&b, "terminated: the command ran longer than %s\n", timeout)
		case err != nil:
			fmt.Fprintf(&b, "ERROR: %v\n", err)
			if res.ExitCode == 0 {
				res.ExitCode = -1
			}
		}
		if res.ExitCode != 0 {
			out.Failed++
		}
		fmt.Fprintf(&b, "--- cleanup %d/%d exit_code=%d duration=%s ---\n",
			i+1, total, res.ExitCode, res.Duration.Round(time.Millisecond))
	}

	switch {
	case cutShort:
		out.Status, out.Reason = CleanupFailed, strPtr(cleanupReasonShutdown)
	case timedOut:
		out.Status, out.Reason = CleanupFailed, strPtr(cleanupReasonTimeout)
	case out.Failed > 0:
		out.Status, out.Reason = CleanupFailed, strPtr(cleanupReasonCommandFailed)
	default:
		out.Status = CleanupClean
	}
	log.Printf("[beacon] cleanup done run_id=%s status=%s ran=%d/%d failed=%d",
		task.RunID, out.Status, out.Ran, total, out.Failed)

	// One frame, with no step id: SimCore reads a step id on /output as that
	// step having started.
	if err := c.SendOutput(task.RunID, "", b.String()); err != nil {
		log.Printf("[beacon] sendOutput (cleanup, run_id=%s) error: %v", task.RunID, err)
	}
	return out
}

// execCleanup runs one cleanup command as the beacon's own account. It goes
// through the same executor a step does (and the same test seam), never
// through a chain session: cleanup is not part of the scenario's process tree.
// killed reports that ctx ended the command (the executor's context.Canceled),
// which is not an execution error.
func (c *BeaconClient) execCleanup(ctx context.Context, cmd CleanupCommand) (res identity.ExecResult, killed bool, err error) {
	run := c.execStep
	if run == nil {
		run = identity.ExecuteCtxStream
	}
	res, err = run(ctx, identity.ExecutionIdentity{Mode: "direct", Command: cmd.Command, Shell: cmd.Shell}, nil)
	if err == context.Canceled {
		return res, true, nil
	}
	return res, false, err
}

// cleanupContext is the context cleanup commands run under.
//
// It is deliberately not the task's context: cleanup runs after an abort and
// after a shutdown, and both cancel that one. From the moment the beacon is
// told to stop, the whole cleanup has cleanupShutdownBudget left; the command
// then in flight is killed and the rest are reported as not run.
func (c *BeaconClient) cleanupContext() (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(context.Background())
	stop := c.stopCtx()
	budget := cleanupShutdownBudget
	go func() {
		select {
		case <-ctx.Done():
			return
		case <-stop.Done():
		}
		t := time.NewTimer(budget)
		defer t.Stop()
		select {
		case <-ctx.Done():
		case <-t.C:
			cancel()
		}
	}()
	return ctx, cancel
}

// cleanupHeartbeat polls the run's control channel while cleanup runs.
//
// The poll is what tells SimCore this beacon is alive and still working on the
// run. Steps make it every controlPollInterval; without it a cleanup longer
// than SimCore's beacon-lost window would read as a lost beacon. The answer is
// ignored: cleanup is what follows an abort, so an abort does not stop it.
func (c *BeaconClient) cleanupHeartbeat(ctx context.Context, runID string) {
	ticker := time.NewTicker(controlPollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_, _ = c.Control(runID)
		}
	}
}

func strPtr(s string) *string { return &s }
