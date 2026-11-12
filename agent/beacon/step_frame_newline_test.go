package beacon

// Observed on pov-target-01 (lab run, beacon built at d7f4f29, and on a dev
// SimCore at cc26ddf): a multi-step run's stored output read
// "--- exit_code=0 duration=10ms ---=== STEP 2/4 ...". SimCore appends each
// /output body as-is (output = coalesce(output,'') || chunk), and the beacon's
// step block ended on its closing frame with no newline, so every frame after
// the first was glued to the previous step's footer. Anything that reads the
// record line by line (a `^=== STEP` match, the report's per-step split) saw
// only the first step.

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// storedOutput concatenates the recorded /output bodies exactly as SimCore's
// append does.
func (s *evidenceRefusingServer) storedOutput(t *testing.T) string {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	var b strings.Builder
	for _, raw := range s.bodies {
		var m map[string]any
		if err := json.Unmarshal([]byte(raw), &m); err != nil {
			t.Fatalf("decode /output body: %v", err)
		}
		out, _ := m["output"].(string)
		b.WriteString(out)
	}
	return b.String()
}

func TestExecuteTask_EveryStepFrameStartsALineInTheStoredOutput(t *testing.T) {
	srv := newEvidenceRefusingServer(t, http.StatusOK)
	c := New(srv.URL, "a-1", 0)
	n := 0
	c.execStep = func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error) {
		n++
		switch n {
		case 1:
			return identity.ExecResult{Stdout: "ok", Termination: TermExited}, nil
		case 2:
			return identity.ExecResult{Stderr: "warn\n", Termination: TermExited}, nil
		default: // step 3 fails; step 4 is not run (UPSTREAM_FAILED)
			return identity.ExecResult{ExitCode: 3, Termination: TermExited}, nil
		}
	}
	steps := []Step{
		{ID: "step-01", Command: "a", Identity: "root"},
		{ID: "step-02", Command: "b", Identity: "root"},
		{ID: "step-03", Command: "c", Identity: "root"},
		{ID: "step-04", Command: "d", Identity: "root"},
	}
	c.executeTask(context.Background(), &Task{TaskID: "t", RunID: "r", ScenarioID: "S", Steps: steps})

	out := srv.storedOutput(t)
	for _, frame := range []string{"=== STEP ", "--- exit_code="} {
		for i := 0; ; {
			j := strings.Index(out[i:], frame)
			if j < 0 {
				break
			}
			at := i + j
			if at != 0 && out[at-1] != '\n' {
				lo := at - 40
				if lo < 0 {
					lo = 0
				}
				t.Errorf("frame %q at byte %d does not start a line: …%q…", frame, at, out[lo:at+len(frame)+10])
			}
			i = at + len(frame)
		}
	}
	if got := strings.Count(out, "=== STEP "); got < 3 {
		t.Fatalf("want at least 3 step headers in the stored output, got %d:\n%s", got, out)
	}
	// Every closing frame is still whole on its own line.
	for _, line := range strings.Split(out, "\n") {
		if strings.Contains(line, "--- exit_code=") && !strings.HasPrefix(line, "--- exit_code=") {
			t.Errorf("closing frame shares its line: %q", line)
		}
	}
}
