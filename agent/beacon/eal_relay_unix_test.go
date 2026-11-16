//go:build !windows

package beacon

// A marked step, run for real: the beacon's shell runs the rewritten command,
// which starts this test binary as `eal` (see TestMain), which asks the beacon
// over loopback, which asks a stand-in SimCore. The only listeners are on
// loopback.

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// realExec runs each command for real and records the text it was handed.
type realExec struct {
	mu   sync.Mutex
	cmds []string
}

func (r *realExec) exec(ctx context.Context, id identity.ExecutionIdentity, on executor.OutputChunkFunc) (identity.ExecResult, error) {
	r.mu.Lock()
	r.cmds = append(r.cmds, id.Command)
	r.mu.Unlock()
	return identity.ExecuteCtxStream(ctx, id, on)
}

// runMarkedStep runs a one-step task whose step is marked. It returns the
// task and the commands the executor was handed.
func runMarkedStep(t *testing.T, s *relaySimCore, command string) (*Task, []string) {
	t.Helper()
	task := &Task{TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-NDR-001", Steps: []Step{
		{ID: "step-03", Command: command, Identity: "root", EalRelay: true},
	}}
	run := &realExec{}
	c := directClient(s.URL)
	c.execStep = run.exec
	c.executeTask(context.Background(), task)
	return task, run.cmds
}

// askRelay makes one request to the beacon's listener, as anything on this
// host that had a ticket could.
func askRelay(t *testing.T, addr, ticket string) (status int, body string) {
	t.Helper()
	payload, _ := json.Marshal(ealrelay.Request{Ticket: ticket, Verb: "list-plugins"})
	resp, err := http.Post("http://"+addr+"/relay", "application/json", bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(raw)
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
	runMarkedStep(t, s, ealStepCommand)
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
	if !strings.Contains(out, "{\"status\": \"complete\"}") || !strings.Contains(out, "\"sent\": 2") {
		t.Errorf("step output:\n%s", out)
	}
	if code, _ := s.complete["exit_code"].(float64); code != 0 {
		t.Errorf("run completed with %v:\n%s", s.complete["exit_code"], out)
	}
}

// With the live runtime log on, the `eal` child is one more process of the
// step: what it prints streams as that step's chunks, the start frame shows
// the command the step's author wrote, and the relay posts no /output frame of
// its own.
func TestMarkedStepStreamsItsEalOutputAsTheStepsLiveChunks(t *testing.T) {
	dest := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer dest.Close()
	s := newRelaySimCore(t, func() ealrelay.Response {
		return ealrelay.Response{
			Stdout: "{\"status\": \"complete\"}\n",
			Ops: []ealrelay.Op{
				// The child has printed SimCore's output by now. The pause
				// keeps the step running long enough for a flush to fall in it.
				{Op: "sleep", Seconds: 0.5},
				{Op: "http", Method: "GET", URL: dest.URL + "/checkin", TimeoutS: 5},
			},
			Allowlist: []string{strings.TrimPrefix(dest.URL, "http://")},
		}
	})
	task := &Task{TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-NDR-001",
		LiveOutput: &LiveOutput{FlushMs: 10},
		Steps: []Step{
			{ID: "step-03", Command: ealStepCommand, Identity: "root", EalRelay: true},
		}}
	directClient(s.URL).executeTask(context.Background(), task)
	s.mu.Lock()
	defer s.mu.Unlock()

	var starts, finals []map[string]interface{}
	var live strings.Builder
	for _, f := range s.frames {
		switch f["kind"] {
		case "start":
			starts = append(starts, f)
		case "chunk":
			if f["step_id"] != "step-03" {
				t.Errorf("a chunk for another step: %v", f)
			}
			live.WriteString(f["output"].(string))
		case nil:
			finals = append(finals, f)
		default:
			t.Errorf("an /output frame of an unknown kind: %v", f)
		}
	}

	// The command on record is the step's own, as its author wrote it
	// (TestStartFrameCarriesAuthoredCommandForRelayedStep).
	if len(starts) != 1 || starts[0]["step_id"] != "step-03" {
		t.Fatalf("start frames: %v", starts)
	}
	if command, _ := starts[0]["command"].(string); command != ealStepCommand {
		t.Errorf("the start frame's command is not the step's own:\n%s", command)
	}

	// SimCore's output reached the live stream while the step was running.
	if !strings.Contains(live.String(), "{\"status\": \"complete\"}") {
		t.Errorf("the eal child's output was not streamed; chunks carried:\n%q", live.String())
	}

	// One stored frame, the step's own, with everything in it.
	if len(finals) != 1 || finals[0]["step_id"] != "step-03" {
		t.Fatalf("final frames: %v", finals)
	}
	out, _ := finals[0]["output"].(string)
	if !strings.Contains(out, "{\"status\": \"complete\"}") || !strings.Contains(out, "\"sent\": 1") {
		t.Errorf("the step's stored frame:\n%s", out)
	}
	if len(s.relays) != 1 {
		t.Errorf("SimCore saw %d relay calls, want 1", len(s.relays))
	}
}

// A scenario with a causality contract runs its steps through one anchor
// shell. A marked step there gets the same treatment: its call is swapped in
// what the anchor shell is fed, and its start frame shows its own text.
func TestMarkedStepRunsThroughTheChainedPath(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response {
		return ealrelay.Response{Stdout: "{\"status\": \"complete\"}\n"}
	})
	task := &Task{TaskID: "t-1", RunID: "r-chain", ScenarioID: "SIM-MP-001",
		CgoAnchor:  &CgoAnchor{ImageName: "sh"},
		LiveOutput: &LiveOutput{FlushMs: 10},
		Steps: []Step{
			{ID: "step-01", Command: "echo first", Identity: "root"},
			{ID: "step-02", Command: ealStepCommand, Identity: "root", EalRelay: true,
				Causality: &StepCausality{ParentStep: "step-01"}},
		}}
	directClient(s.URL).executeTask(context.Background(), task)
	s.mu.Lock()
	defer s.mu.Unlock()

	if len(s.relays) != 1 || s.relays[0]["step_id"] != "step-02" || s.relays[0]["run_id"] != "r-chain" {
		t.Fatalf("SimCore saw relay calls %v, want one for step-02 of this run", s.relays)
	}
	campaign, _ := s.relays[0]["campaign_yaml"].(string)
	if !strings.HasPrefix(campaign, "campaign_id: CMP-NDR-001\n") {
		t.Errorf("campaign text is not the file the step wrote:\n%s", campaign)
	}
	out := strings.Join(s.outputs, "\n")
	if !strings.Contains(out, "{\"status\": \"complete\"}") {
		t.Errorf("the step's output does not carry SimCore's:\n%s", out)
	}
	if code, _ := s.complete["exit_code"].(float64); code != 0 {
		t.Errorf("run completed with %v:\n%s", s.complete["exit_code"], out)
	}
	for _, f := range s.frames {
		if f["kind"] == "start" && f["step_id"] == "step-02" {
			if command, _ := f["command"].(string); command != ealStepCommand {
				t.Errorf("the start frame's command is not the step's own:\n%s", command)
			}
			return
		}
	}
	t.Errorf("no start frame for step-02: %v", s.frames)
}

func TestMarkedStepExitsWithSimCoresCode(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response {
		return ealrelay.Response{ExitCode: 3, Stdout: "EAL_WIRE_NOT_RELAYABLE: llm_provider_egress\n"}
	})
	runMarkedStep(t, s, ealStepCommand)
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

// What lets a step ask is a ticket. Endpoint telemetry records every command
// line, and any local account can read the process list, so the ticket must
// be in neither a command line nor an environment.
func TestTicketNeverInArgvOrEnv(t *testing.T) {
	dump := filepath.Join(t.TempDir(), "child.json")
	t.Setenv(ealChildDumpEnv, dump)

	s := newRelaySimCore(t, func() ealrelay.Response { return ealrelay.Response{Stdout: "ok\n"} })
	task, ran := runMarkedStep(t, s, ealStepCommand)
	s.mu.Lock()
	defer s.mu.Unlock()

	raw, err := os.ReadFile(dump)
	if err != nil {
		t.Fatalf("the step's eal child never ran: %v\n%s", err, strings.Join(s.outputs, "\n"))
	}
	var child ealChildDump
	if err := json.Unmarshal(raw, &child); err != nil {
		t.Fatal(err)
	}
	if !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(child.Ticket) {
		t.Fatalf("the child read %q from its ticket file, not a ticket", child.Ticket)
	}
	if len(s.relays) != 1 {
		t.Fatalf("the ticket the child read did not get its call served: %v", s.outputs)
	}

	// The command the step's shell was handed: a path, never the ticket.
	if len(ran) != 1 {
		t.Fatalf("the executor was handed %d commands, want the step's: %q", len(ran), ran)
	}
	command := ran[0]
	if strings.Contains(command, child.Ticket) {
		t.Errorf("the ticket is in the step's command:\n%s", command)
	}
	if !strings.Contains(command, " --ticket-file '") {
		t.Errorf("the command does not carry a ticket file:\n%s", command)
	}
	// The step itself still holds the text its author wrote.
	if task.Steps[0].Command != ealStepCommand {
		t.Errorf("the step's own command was changed:\n%s", task.Steps[0].Command)
	}
	// The child's own argv and environment.
	for _, arg := range child.Args {
		if strings.Contains(arg, child.Ticket) || arg == "--ticket" {
			t.Errorf("the ticket is on the child's command line: %q", child.Args)
		}
	}
	for _, kv := range child.Env {
		if strings.Contains(kv, child.Ticket) {
			t.Errorf("the ticket is in the child's environment: %q", strings.SplitN(kv, "=", 2)[0])
		}
	}
	// It stops at the beacon: SimCore is told the run and the step, not it.
	if strings.Contains(strings.Join(s.outputs, "\n")+mustJSON(s.relays), child.Ticket) {
		t.Error("the ticket reached SimCore")
	}
	// And its file went with the step.
	if _, err := os.Stat(ticketFileOf(t, command)); !os.IsNotExist(err) {
		t.Errorf("the ticket file outlived its step (err=%v)", err)
	}
}

func mustJSON(v interface{}) string {
	data, _ := json.Marshal(v)
	return string(data)
}

func statOwner(t *testing.T, path string) (os.FileMode, uint32) {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm(), info.Sys().(*syscall.Stat_t).Uid
}

func TestTicketFileModeAndOwner(t *testing.T) {
	me := uint32(os.Getuid())

	// A step that runs as the beacon's own account.
	task := &Task{RunID: "r-mode", Steps: []Step{
		{ID: "step-01", Command: ealStepCommand, Identity: "root", EalRelay: true}}}
	done := directClient("http://127.0.0.1:1").prepareEalRelay(task)
	file := ticketFileOf(t, executed(task, 0))
	dir := filepath.Dir(file)

	// The directory is the beacon's, and closed to every other account.
	if mode, uid := statOwner(t, dir); mode != 0o700 || uid != me {
		t.Errorf("ticket directory is %o owned by %d, want 700 owned by %d", mode, uid, me)
	}
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Fatalf("the ticket file exists before its step started (err=%v)", err)
	}

	end := ealRelayStep("r-mode", "step-01") // the step starts
	if mode, uid := statOwner(t, file); mode != 0o600 || uid != me {
		t.Errorf("ticket file is %o owned by %d, want 600 owned by %d", mode, uid, me)
	}
	if raw, _ := os.ReadFile(file); !regexp.MustCompile(`^[0-9a-f]{32}\n$`).Match(raw) {
		t.Errorf("ticket file holds %q", raw)
	}
	end() // the step ends
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Errorf("the ticket file outlived its step (err=%v)", err)
	}
	done() // the task ends
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Errorf("the ticket directory outlived its task (err=%v)", err)
	}

	// A step that runs as a service account: the file goes to that account,
	// and the directory lets it through without letting it look around.
	type chown struct {
		path     string
		uid, gid int
	}
	var chowned []chown
	realChown, realIDs := ealChown, ealAccountIDs
	ealChown = func(f *os.File, uid, gid int) error {
		chowned = append(chowned, chown{f.Name(), uid, gid})
		return nil // this test is not root; record who the file would go to
	}
	ealAccountIDs = func(name string) (int, int, error) {
		if name != "svc-backup" {
			t.Errorf("looked up %q, want the step's account", name)
		}
		return 4242, 4243, nil
	}
	defer func() { ealChown, ealAccountIDs = realChown, realIDs }()

	task = &Task{RunID: "r-mode-svc", Steps: []Step{
		{ID: "step-01", Command: ealStepCommand, Identity: "svc-backup", EalRelay: true}}}
	c := New("http://127.0.0.1:1", "agent-7", 0)
	c.resolveIdentity = func(name string) identity.Resolution {
		return identity.Resolution{Mode: "runuser", Username: name, Honoured: true}
	}
	done = c.prepareEalRelay(task)
	defer done()
	file = ticketFileOf(t, executed(task, 0))
	if mode, uid := statOwner(t, filepath.Dir(file)); mode != 0o711 || uid != me {
		t.Errorf("shared ticket directory is %o owned by %d, want 711 owned by %d", mode, uid, me)
	}
	end = ealRelayStep("r-mode-svc", "step-01")
	defer end()
	if mode, _ := statOwner(t, file); mode != 0o600 {
		t.Errorf("ticket file is %o, want 600", mode)
	}
	if len(chowned) != 1 || chowned[0] != (chown{file, 4242, 4243}) {
		t.Errorf("ticket file was handed to %+v, want the step's account 4242:4243", chowned)
	}
}

// A ticket is good while its step runs. Before the step starts there is none,
// and once it ends the same ticket is refused.
func TestTicketRefusedAfterStepEnds(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response { return ealrelay.Response{Stdout: "served\n"} })
	task := &Task{RunID: "r-life", Steps: []Step{
		{ID: "step-01", Command: ealCall + " list-plugins\n" + ealCall + " list-plugins", EalRelay: true}}}
	done := directClient(s.URL).prepareEalRelay(task)
	defer done()
	m := rewrittenCall.FindStringSubmatch(executed(task, 0))
	addr, file := m[2], m[3]

	end := ealRelayStep("r-life", "step-01") // the step starts
	raw, err := os.ReadFile(file)
	if err != nil {
		t.Fatal(err)
	}
	ticket := strings.TrimSpace(string(raw))
	if status, body := askRelay(t, addr, ticket); status != http.StatusOK {
		t.Fatalf("while the step runs: %d %s", status, body)
	}

	end() // the step ends, with one of its two calls unspent

	status, body := askRelay(t, addr, ticket)
	if status != http.StatusForbidden || !strings.Contains(body, "EAL_RELAY_TICKET_REFUSED") {
		t.Errorf("after the step ended: %d %s", status, body)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.relays) != 1 {
		t.Errorf("SimCore was asked %d times, want only the call made while the step ran", len(s.relays))
	}
}

// A step may ask as often as its command calls the EAL CLI. A command that
// calls it once and loops gets one call served.
func TestTicketRefusedBeyondDeclaredCalls(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response { return ealrelay.Response{Stdout: "served\n"} })
	runMarkedStep(t, s, "for i in 1 2 3; do "+ealCall+" list-plugins; done")
	s.mu.Lock()
	defer s.mu.Unlock()

	if len(s.relays) != 1 {
		t.Errorf("SimCore was asked %d times, want 1: the command contains one call", len(s.relays))
	}
	out := strings.Join(s.outputs, "\n")
	if strings.Count(out, "served") != 1 || strings.Count(out, "EAL_RELAY_TICKET_REFUSED") != 2 {
		t.Errorf("want one call served and two refused:\n%s", out)
	}
	// The step ends with the refused call's exit code.
	if code, _ := s.complete["exit_code"].(float64); code != 4 {
		t.Errorf("run completed with %v, want 4", s.complete["exit_code"])
	}
}
