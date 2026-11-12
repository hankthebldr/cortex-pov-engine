package beacon

// RG-AGENT-03, beacon half. SimCore counts a fan-out run's /complete once per
// task (run_task_completions, keyed task:<id>, else agent:<id>), but only when
// the body names its task or agent. The beacon RETRIES /complete, so a body
// with neither is counted per POST: one endpoint's lost reply decremented the
// run's pending endpoints twice and closed it before a second endpoint was done.

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

func TestExecuteTask_CompleteNamesItsTaskAndAgent(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-7", 0)
	c.execStep = func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error) {
		return identity.ExecResult{Termination: TermExited}, nil
	}
	c.executeTask(context.Background(), &Task{TaskID: "task-9", RunID: "run-1", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", Command: "a", Identity: "root"}}})

	var body map[string]any
	for _, r := range rs.records() {
		if strings.HasSuffix(r.Path, "/complete") {
			_ = json.Unmarshal([]byte(r.Body), &body)
		}
	}
	if body == nil {
		t.Fatalf("no /complete was posted")
	}
	if body["task_id"] != "task-9" || body["agent_id"] != "a-7" {
		t.Fatalf("/complete body names task_id=%v agent_id=%v, want task-9 / a-7: SimCore cannot "+
			"tell a retried /complete from a second endpoint's (%v)", body["task_id"], body["agent_id"], body)
	}
}
