//go:build !windows

package beacon

// The chained (causality-anchored) path runs every step through one anchor
// shell. `--stream-output` never streamed it; live output does.

import (
	"context"
	"strings"
	"testing"
)

func TestChainedStepStreams(t *testing.T) {
	srv := newLiveServer(t)
	c := liveClient(srv.URL)
	task := &Task{
		TaskID: "task-chain", RunID: "run-chain", ScenarioID: "SIM-TEST-CHAIN",
		CgoAnchor:  &CgoAnchor{ImageName: "sh"},
		LiveOutput: &LiveOutput{FlushMs: 20, MaxChunkBytes: 16384, MaxStepBytes: 262144},
		Steps: []Step{
			{ID: "step-01", Command: "echo one; echo oops >&2; sleep 0.6; echo two", Identity: "root"},
			{ID: "step-02", Command: "echo three", Identity: "root", Causality: &StepCausality{ParentStep: "step-01"}},
		},
	}
	c.executeTask(context.Background(), task)

	frames := srv.all()
	index := func(match func(liveFrame) bool) int {
		for i, f := range frames {
			if match(f) {
				return i
			}
		}
		return -1
	}
	start := index(func(f liveFrame) bool { return f.Kind == "start" && f.StepID == "step-01" })
	firstChunk := index(func(f liveFrame) bool { return f.Kind == "chunk" && f.StepID == "step-01" })
	final := index(func(f liveFrame) bool {
		return f.Kind == "" && f.StepID == "step-01" && strings.Contains(f.Output, "--- exit_code=")
	})
	if start < 0 || firstChunk < 0 || final < 0 {
		t.Fatalf("step-01: start=%d first chunk=%d final=%d, want all three: %+v", start, firstChunk, final, frames)
	}
	if !(start < firstChunk && firstChunk < final) {
		t.Errorf("step-01 frames arrived start=%d chunk=%d final=%d, want start, then output while it ran, then the final frame",
			start, firstChunk, final)
	}
	if got := frames[start].Command; got != "echo one; echo oops >&2; sleep 0.6; echo two" {
		t.Errorf("start frame command = %q, want the step's command, not the wrapped anchor script", got)
	}
	var live strings.Builder
	for _, f := range frames[:final] {
		if f.Kind == "chunk" && f.StepID == "step-01" {
			live.WriteString(f.Output)
		}
	}
	// Both streams of the first half reached SimCore before the step ended.
	if got := live.String(); !strings.Contains(got, "one\n") || !strings.Contains(got, "oops\n") {
		t.Errorf("output streamed before the step ended = %q, want the lines printed before the sleep", got)
	}
	if out := frames[final].Output; !strings.Contains(out, "one\ntwo") || !strings.Contains(out, "oops") {
		t.Errorf("final frame = %q, want the step's whole output", out)
	}
	if index(func(f liveFrame) bool { return f.Kind == "start" && f.StepID == "step-02" && f.Command == "echo three" }) < 0 {
		t.Errorf("no start frame for step-02: %+v", frames)
	}
}
