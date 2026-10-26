package beacon

import (
	"fmt"
	"log"
	"os/exec"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// Termination values for StepOutcome.Termination. These are the wire
// vocabulary on POST /api/runs/{id}/complete (steps[]).
const (
	TermExited           = "exited"
	TermSignaled         = "signaled"
	TermAnchorTerminated = "anchor_terminated"
	TermTimeout          = "timeout"
	TermAborted          = "aborted"
	TermNotRun           = "not_run"
)

// Reasons attached to a not_run step (and TOOL_ABSENT, the reason a step with a
// missing binary was not run).
const (
	ReasonUpstreamSevered     = "UPSTREAM_SEVERED"
	ReasonUpstreamNotExecuted = "UPSTREAM_NOT_EXECUTED"
	ReasonToolAbsent          = "TOOL_ABSENT"
)

// toolAbsentMarker prefixes the /output line written for a step whose declared
// required binary is not on this host's PATH.
const toolAbsentMarker = "!! TOOL_ABSENT:"

// StepOutcome is the beacon's account of how one step ended. ExitCode is a
// pointer so a step that never produced one (anchor death, not_run) encodes
// JSON null rather than a misleading 0.
type StepOutcome struct {
	StepID      string `json:"step_id"`
	ExitCode    *int   `json:"exit_code"`
	Termination string `json:"termination"`
	Signal      int    `json:"signal,omitempty"`
	Reason      string `json:"reason,omitempty"`
}

func intPtr(v int) *int { return &v }

// outcomeLedger accumulates StepOutcomes for one task. steps is always
// non-nil so an empty ledger encodes as [].
type outcomeLedger struct {
	task  *Task
	steps []StepOutcome
}

func newOutcomeLedger(task *Task) *outcomeLedger {
	return &outcomeLedger{task: task, steps: []StepOutcome{}}
}

// record appends a finished-step outcome.
func (l *outcomeLedger) record(o StepOutcome) { l.steps = append(l.steps, o) }

// markRemaining records every step from index from onward as not_run.
func (l *outcomeLedger) markRemaining(from int, reason string) {
	for j := from; j < len(l.task.Steps); j++ {
		l.steps = append(l.steps, StepOutcome{
			StepID: l.task.Steps[j].ID, Termination: TermNotRun, Reason: reason,
		})
	}
}

// recordFinished classifies a step that ran (or was blocked by a runtime
// dependency) and returns severed=true when the step was killed by a signal, in
// which case the remaining steps are marked UPSTREAM_SEVERED and the caller must
// stop and complete with 128+signal.
func (l *outcomeLedger) recordFinished(i int, res identity.ExecResult, depBlocked bool) (severed bool) {
	id := l.task.Steps[i].ID
	if depBlocked {
		l.record(StepOutcome{StepID: id, Termination: TermNotRun, Reason: ReasonToolAbsent})
		l.markRemaining(i+1, ReasonUpstreamNotExecuted)
		return false
	}
	if res.Termination == TermSignaled {
		l.record(StepOutcome{StepID: id, ExitCode: intPtr(res.ExitCode), Termination: TermSignaled, Signal: res.Signal})
		l.markRemaining(i+1, ReasonUpstreamSevered)
		return true
	}
	l.record(StepOutcome{StepID: id, ExitCode: intPtr(res.ExitCode), Termination: TermExited})
	return false
}

// classifyChainedRC marks a chained step's result as signaled when its integer
// rc is in 129..159 (session.RunStep reports no termination detail).
func classifyChainedRC(res *identity.ExecResult) {
	if res.ExitCode >= 129 && res.ExitCode <= 159 {
		res.Termination = TermSignaled
		res.Signal = res.ExitCode - 128
	}
}

// severedAt returns the step id and termination of the step that severed the
// task (signaled or anchor_terminated), if any.
func severedAt(steps []StepOutcome) (id, termination string, ok bool) {
	for _, s := range steps {
		if s.Termination == TermSignaled || s.Termination == TermAnchorTerminated {
			return s.StepID, s.Termination, true
		}
	}
	return "", "", false
}

// checkRequiredBinaries returns the step's declared required binaries that are
// not on PATH. A step that declares none is not checked. The check is skipped on
// Windows, exactly as resolveRuntimeDeps is.
func checkRequiredBinaries(step *Step) (missing []string) {
	if len(step.RequiredBinaries) == 0 || runtimeDepsGOOS == "windows" {
		return nil
	}
	for _, name := range step.RequiredBinaries {
		if _, err := exec.LookPath(name); err != nil {
			missing = append(missing, name)
		}
	}
	return missing
}

// preflightTools runs the tool-presence check for step i. When a binary is
// missing it writes the marker to /output, records the step not_run/TOOL_ABSENT,
// marks the rest UPSTREAM_NOT_EXECUTED, completes the run with 127 and returns
// true — the caller must stop.
func (c *BeaconClient) preflightTools(task *Task, i int, led *outcomeLedger, degradedSteps int) bool {
	step := &task.Steps[i]
	missing := checkRequiredBinaries(step)
	if len(missing) == 0 {
		return false
	}
	log.Printf("[beacon] step %s required binary %q absent — not running (run_id=%s)", step.ID, missing[0], task.RunID)
	out := fmt.Sprintf("%s %s", toolAbsentMarker, missing[0])
	if err := c.SendOutput(task.RunID, step.ID, out); err != nil {
		log.Printf("[beacon] sendOutput (step %s) error: %v", step.ID, err)
	}
	led.record(StepOutcome{StepID: step.ID, Termination: TermNotRun, Reason: ReasonToolAbsent})
	led.markRemaining(i+1, ReasonUpstreamNotExecuted)
	summary := buildSummary(task, 127, degradedSteps, led.steps) +
		fmt.Sprintf(" | TOOL_ABSENT: %s at %s — step not run", missing[0], step.ID)
	if err := c.Complete(task.RunID, 127, summary, led.steps); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
	return true
}
