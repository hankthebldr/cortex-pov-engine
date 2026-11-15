//go:build !windows

package beacon

// A marked step, run for real: the beacon's shell runs the rewritten command,
// which starts this test binary as `eal` (see TestMain), which asks the beacon
// over loopback, which asks a stand-in SimCore. The only listeners are on
// loopback.

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// relaySimCore is a SimCore that answers the beacon's run callbacks and
// records what the relay endpoint was asked.
type relaySimCore struct {
	*httptest.Server
	mu       sync.Mutex
	relays   []map[string]interface{}
	outputs  []string
	complete map[string]interface{}
}

func newRelaySimCore(t *testing.T, answer func() ealrelay.Response) *relaySimCore {
	t.Helper()
	s := &relaySimCore{}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var decoded map[string]interface{}
		_ = json.Unmarshal(body, &decoded)
		s.mu.Lock()
		defer s.mu.Unlock()
		switch {
		case r.URL.Path == "/api/eal/relay":
			s.relays = append(s.relays, decoded)
			_ = json.NewEncoder(w).Encode(answer())
		case strings.HasSuffix(r.URL.Path, "/control"):
			_ = json.NewEncoder(w).Encode(ControlState{Status: "running"})
		case strings.HasSuffix(r.URL.Path, "/output"):
			s.outputs = append(s.outputs, decoded["output"].(string))
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.complete = decoded
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	t.Cleanup(s.Close)
	return s
}

func runMarkedStep(t *testing.T, s *relaySimCore) {
	t.Helper()
	task := &Task{TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-NDR-001", Steps: []Step{
		{ID: "step-03", Command: ealStepCommand, Identity: "root", EalRelay: true},
	}}
	c := New(s.URL, "agent-7", 0)
	c.resolveIdentity = func(string) identity.Resolution {
		return identity.Resolution{Mode: "direct", Username: "root"}
	}
	c.executeTask(context.Background(), task)
}

func TestMarkedStepRunsItsCampaignThroughSimCoreAndSendsFromHere(t *testing.T) {
	// The destination a c2 beacon step would call.
	var seen []string
	var seenMu sync.Mutex
	c2 := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenMu.Lock()
		seen = append(seen, r.Method+" "+r.URL.RequestURI()+" "+r.Header.Get("X-Simulation-Run-ID"))
		seenMu.Unlock()
	}))
	defer c2.Close()
	dest := strings.TrimPrefix(c2.URL, "http://")

	s := newRelaySimCore(t, func() ealrelay.Response {
		return ealrelay.Response{
			Stdout: "{\"status\": \"complete\"}\n",
			Ops: []ealrelay.Op{
				{Op: "http", Method: "GET", URL: c2.URL + "/checkin?q=one", TimeoutS: 5,
					Headers: map[string]string{"X-Simulation-Run-ID": "cortexsim-e2e"}},
				{Op: "sleep", Seconds: 0.01},
				{Op: "http", Method: "GET", URL: c2.URL + "/checkin?q=two", TimeoutS: 5,
					Headers: map[string]string{"X-Simulation-Run-ID": "cortexsim-e2e"}},
			},
			Allowlist: []string{dest},
		}
	})
	runMarkedStep(t, s)
	s.mu.Lock()
	defer s.mu.Unlock()
	seenMu.Lock()
	defer seenMu.Unlock()

	// SimCore was asked once, for this run's step, with the file the step's
	// own shell wrote (the heredoc expanded $(hostname) before it was read).
	if len(s.relays) != 1 {
		t.Fatalf("SimCore saw %d relay calls, want 1: %v", len(s.relays), s.relays)
	}
	asked := s.relays[0]
	for key, want := range map[string]interface{}{
		"agent_id": "agent-7", "run_id": "r-1", "step_id": "step-03", "verb": "run", "live": true,
	} {
		if asked[key] != want {
			t.Errorf("relay %s = %v, want %v", key, asked[key], want)
		}
	}
	campaign, _ := asked["campaign_yaml"].(string)
	if !strings.HasPrefix(campaign, "campaign_id: CMP-NDR-001\n") ||
		!strings.Contains(campaign, "plugin: c2_http_beacon") || strings.Contains(campaign, "$(hostname)") {
		t.Errorf("campaign text is not the file the step wrote:\n%s", campaign)
	}

	// The requests left this host, in order, as rendered.
	want := []string{"GET /checkin?q=one cortexsim-e2e", "GET /checkin?q=two cortexsim-e2e"}
	if strings.Join(seen, "|") != strings.Join(want, "|") {
		t.Errorf("destination saw %v, want %v", seen, want)
	}

	// The step's stored output is SimCore's, then the account of what was sent.
	out := strings.Join(s.outputs, "\n")
	if !strings.Contains(out, "{\"status\": \"complete\"}") || !strings.Contains(out, "\"reached\": 2") {
		t.Errorf("step output:\n%s", out)
	}
	if code, _ := s.complete["exit_code"].(float64); code != 0 {
		t.Errorf("run completed with %v:\n%s", s.complete["exit_code"], out)
	}
}

func TestMarkedStepExitsWithSimCoresCode(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response {
		return ealrelay.Response{ExitCode: 3, Stdout: "EAL_WIRE_NOT_RELAYABLE: llm_provider_egress\n"}
	})
	runMarkedStep(t, s)
	s.mu.Lock()
	defer s.mu.Unlock()

	out := strings.Join(s.outputs, "\n")
	if !strings.Contains(out, "EAL_WIRE_NOT_RELAYABLE: llm_provider_egress") {
		t.Errorf("step output does not carry the line:\n%s", out)
	}
	if code, _ := s.complete["exit_code"].(float64); code != 3 {
		t.Errorf("run completed with %v, want 3", s.complete["exit_code"])
	}
	steps, _ := s.complete["steps"].([]interface{})
	if len(steps) != 1 {
		t.Fatalf("complete carried %v", s.complete["steps"])
	}
	step := steps[0].(map[string]interface{})
	if step["termination"] != TermExited || step["exit_code"].(float64) != 3 {
		t.Errorf("the step's host record: %v", step)
	}
}
