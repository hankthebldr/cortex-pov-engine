package beacon

// RG-AGENT-06. f72da6e made the beacon retry a step's /output, and SimCore's
// append is unconditional (`output = coalesce(output,'') || chunk`), so a frame
// whose first attempt was stored but whose reply was lost is stored twice. The
// beacon's half of the fix is a key that makes a retry recognisable; SimCore
// skipping a frame_id it has already stored for the run is the other half.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// A retry of a step's /output must be recognisable as the SAME
// frame, so SimCore can append it once: both attempts carry one frame_id, and
// the next frame carries a different one.
func TestSendOutput_ARetriedFrameKeepsItsFrameIDAndTheNextFrameGetsANewOne(t *testing.T) {
	shortPostBackoff(t)
	var mu sync.Mutex
	var bodies []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var b map[string]any
		_ = json.Unmarshal(raw, &b)
		mu.Lock()
		bodies = append(bodies, b)
		n := len(bodies)
		mu.Unlock()
		if n == 1 {
			// Recorded, then the reply is "lost": the beacon must retry.
			w.WriteHeader(http.StatusBadGateway)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "a-1", 0)
	if err := c.SendOutput("r", "step-01", "=== STEP 1/2 · step-01 ==="); err != nil {
		t.Fatalf("SendOutput (retried): %v", err)
	}
	if err := c.SendOutput("r", "step-02", "=== STEP 2/2 · step-02 ==="); err != nil {
		t.Fatalf("SendOutput (second frame): %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	if len(bodies) != 3 {
		t.Fatalf("want 3 POSTs (frame 1 twice, frame 2 once), got %d", len(bodies))
	}
	first, _ := bodies[0]["frame_id"].(string)
	retry, _ := bodies[1]["frame_id"].(string)
	second, _ := bodies[2]["frame_id"].(string)
	if first == "" {
		t.Fatalf("a retried /output carries no frame_id: SimCore cannot tell the retry from a new frame "+
			"and appends it twice (body: %v)", bodies[0])
	}
	if retry != first {
		t.Errorf("the retry carries frame_id %q, the first attempt %q — a retry must be the same frame", retry, first)
	}
	if second == "" || second == first {
		t.Errorf("the next frame carries frame_id %q, the previous one %q — every frame needs its own", second, first)
	}
}

// frameIDs returns the frame_id of every recorded /output body ("" = absent).
func (s *evidenceRefusingServer) frameIDs(t *testing.T) []string {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	var ids []string
	for _, raw := range s.bodies {
		var b map[string]any
		if err := json.Unmarshal([]byte(raw), &b); err != nil {
			t.Fatalf("decode /output body: %v", err)
		}
		id, _ := b["frame_id"].(string)
		ids = append(ids, id)
	}
	return ids
}

// The key is scoped to the TASK, not the run: a fan-out run is executed by
// several beacons under one run_id, and a per-run counter would make their
// frames collide. Two tasks of the same run never share a frame id, and within
// a task the ids count from 1.
func TestExecuteTask_FrameIDsAreScopedToTheTaskAndCountFromOne(t *testing.T) {
	srv := newEvidenceRefusingServer(t, http.StatusOK)
	c := New(srv.URL, "a-1", 0)
	c.execStep = func(context.Context, identity.ExecutionIdentity, executor.OutputChunkFunc) (identity.ExecResult, error) {
		return identity.ExecResult{Termination: TermExited}, nil
	}
	steps := []Step{{ID: "step-01", Command: "a", Identity: "root"}, {ID: "step-02", Command: "b", Identity: "root"}}
	c.executeTask(context.Background(), &Task{TaskID: "task-A", RunID: "run-1", ScenarioID: "S", Steps: steps})
	c.executeTask(context.Background(), &Task{TaskID: "task-B", RunID: "run-1", ScenarioID: "S", Steps: steps})

	got := srv.frameIDs(t)
	want := []string{"task-A:1", "task-A:2", "task-B:1", "task-B:2"}
	if len(got) != len(want) {
		t.Fatalf("frame ids = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("frame %d id = %q, want %q (all: %v)", i, got[i], want[i], got)
		}
	}
}

// A live streaming chunk is never retried, so it carries no key: a key on a
// frame that is sent once would only invite SimCore to drop a legitimate chunk.
func TestSendOutput_AStreamingChunkCarriesNoFrameID(t *testing.T) {
	srv := newEvidenceRefusingServer(t, http.StatusOK)
	c := New(srv.URL, "a-1", 0)
	if err := c.sendOutput("r", "step-01", "live chunk", false); err != nil {
		t.Fatalf("sendOutput: %v", err)
	}
	if ids := srv.frameIDs(t); len(ids) != 1 || ids[0] != "" {
		t.Fatalf("streaming chunk frame ids = %v, want one body with no frame_id", ids)
	}
}
