package beacon

import (
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
	"testing"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// ealChildDumpEnv names a file the `eal` child writes what it was started with
// to. Test only: it lets a test see a real child's argv and environment.
const ealChildDumpEnv = "CORTEXSIM_TEST_EAL_CHILD_DUMP"

// ealChildDump is what the child records about itself before it does anything.
type ealChildDump struct {
	Args   []string `json:"args"`
	Env    []string `json:"env"`
	Ticket string   `json:"ticket"` // what it read from --ticket-file
}

// A marked step runs this beacon's own binary as `eal ...`. Under `go test`
// that binary is the test binary, so it answers to the subcommand too: the
// tests in eal_relay_unix_test.go run a real step through a real child.
func TestMain(m *testing.M) {
	if len(os.Args) > 1 && os.Args[1] == "eal" {
		if path := os.Getenv(ealChildDumpEnv); path != "" {
			dump := ealChildDump{Args: os.Args, Env: os.Environ()}
			for i, a := range os.Args {
				if a == "--ticket-file" && i+1 < len(os.Args) {
					raw, _ := os.ReadFile(os.Args[i+1])
					dump.Ticket = strings.TrimSpace(string(raw))
				}
			}
			data, _ := json.Marshal(dump)
			_ = os.WriteFile(path, data, 0o600)
		}
		os.Exit(ealrelay.Main(os.Args[2:], os.Stdout, os.Stderr))
	}
	os.Exit(m.Run())
}

const ealCall = "python3 -m scripts.eal_simulator.cli"

const ealStepCommand = `C=$(mktemp -d) || exit 1; trap 'rm -rf "$C"' EXIT
cat > "$C/c.yml" <<YML
campaign_id: CMP-NDR-001
name: "beacon from $(hostname)"
steps:
  - step_id: step-01
    plugin: c2_http_beacon
YML
` + ealCall + ` run "$C/c.yml" --live
`

var rewrittenCall = regexp.MustCompile(`'([^']+)' eal --relay (127\.0\.0\.1:\d+) --ticket-file '([^']+)'`)

// forcePosix makes the quoting under test the POSIX one, whatever host runs it.
func forcePosix(t *testing.T) {
	t.Helper()
	goos := beaconGOOS
	beaconGOOS = "linux"
	t.Cleanup(func() { beaconGOOS = goos })
}

// relaySimCore is a SimCore that answers the beacon's run callbacks and
// records what the relay endpoint was asked.
type relaySimCore struct {
	*httptest.Server
	mu       sync.Mutex
	relays   []map[string]interface{}
	outputs  []string
	frames   []map[string]interface{} // every /output body, live frames included
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
			s.frames = append(s.frames, decoded)
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.complete = decoded
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	t.Cleanup(s.Close)
	return s
}

func directClient(url string) *BeaconClient {
	c := New(url, "agent-7", 0)
	c.resolveIdentity = func(string) identity.Resolution {
		return identity.Resolution{Mode: "direct", Username: "root"}
	}
	return c
}

// executed is what a step's command becomes on its way to the executor.
func executed(task *Task, i int) string {
	return ealRelayCommand(task.RunID, task.Steps[i].ID, task.Steps[i].Command, "")
}

// ticketFileOf is the ticket file a swapped command names.
func ticketFileOf(t *testing.T, command string) string {
	t.Helper()
	m := rewrittenCall.FindStringSubmatch(command)
	if m == nil {
		t.Fatalf("no swapped call in: %s", command)
	}
	return m[3]
}

// relayExec stands in for the executor. It runs nothing: it records each
// command it is handed and how many ticket files existed at that moment.
type relayExec struct {
	mu      sync.Mutex
	cmds    []string
	tickets []int
	dir     string // the task's ticket directory, once a command has named it
}

func (r *relayExec) exec(_ context.Context, id identity.ExecutionIdentity, _ executor.OutputChunkFunc) (identity.ExecResult, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if m := rewrittenCall.FindStringSubmatch(id.Command); m != nil {
		r.dir = filepath.Dir(m[3])
	}
	held := -1
	if entries, err := os.ReadDir(r.dir); r.dir != "" && err == nil {
		held = len(entries)
	}
	r.cmds = append(r.cmds, id.Command)
	r.tickets = append(r.tickets, held)
	return identity.ExecResult{ExitCode: 0, Stdout: "ran\n", Termination: TermExited}, nil
}

func TestEalCallRewrittenOnlyWhenMarked(t *testing.T) {
	forcePosix(t)
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	const windowsVariant = ealCall + " list-plugins"
	const piped = ealCall + " list-plugins | jq -e '.total >= 4'"
	task := &Task{RunID: "r-rewrite", Steps: []Step{
		{ID: "step-01", Command: ealStepCommand},
		{ID: "step-02", Command: ealStepCommand, EalRelay: true,
			PlatformVariants: map[string]string{"windows": windowsVariant}},
		{ID: "step-03", Command: "echo no call here", EalRelay: true},
		{ID: "step-04", Command: piped, EalRelay: true},
	}}
	c := New("http://127.0.0.1:1", "a-1", 0)
	done := c.prepareEalRelay(task)
	defer done()

	// Not marked: SimCore did not ask, so the command runs as it was sent.
	if got := executed(task, 0); got != ealStepCommand {
		t.Errorf("an unmarked step was rewritten:\n%s", got)
	}

	// Marked: the call is this binary's eal subcommand, and nothing else moved.
	second := executed(task, 1)
	m := rewrittenCall.FindStringSubmatch(second)
	if m == nil {
		t.Fatalf("the marked step's call was not rewritten:\n%s", second)
	}
	if m[1] != self {
		t.Errorf("the call runs %q, want this binary %q", m[1], self)
	}
	if want := strings.Replace(ealStepCommand, ealCall, m[0], 1); second != want {
		t.Errorf("more than the call changed\n got: %q\nwant: %q", second, want)
	}
	// Whatever text the host picked for the step is what gets swapped.
	if v := ealRelayCommand(task.RunID, "step-02", windowsVariant, ""); !strings.Contains(v, " eal --relay ") ||
		strings.Contains(v, "python") || !strings.HasSuffix(v, " list-plugins") {
		t.Errorf("the platform variant kept its python call: %q", v)
	}

	// Marked with nothing to rewrite: untouched.
	if got := executed(task, 2); got != "echo no call here" {
		t.Errorf("a step with no call was changed: %q", got)
	}

	// Each marked step has a ticket file of its own, and neither exists yet:
	// a ticket is made when its step starts.
	fourth := executed(task, 3)
	m4 := rewrittenCall.FindStringSubmatch(fourth)
	if m4 == nil || m4[3] == m[3] {
		t.Fatalf("step-04 has no ticket file of its own: %q", fourth)
	}
	for _, file := range []string{m[3], m4[3]} {
		if _, err := os.Stat(file); !os.IsNotExist(err) {
			t.Errorf("ticket file %s exists before its step started (err=%v)", file, err)
		}
	}
	if !strings.HasSuffix(fourth, " list-plugins | jq -e '.total >= 4'") {
		t.Errorf("the pipe after the call moved: %q", fourth)
	}

	// The task keeps the commands SimCore sent: only what is executed changes.
	for i, want := range []string{ealStepCommand, ealStepCommand, "echo no call here", piped} {
		if task.Steps[i].Command != want {
			t.Errorf("%s's own command was changed:\n%s", task.Steps[i].ID, task.Steps[i].Command)
		}
	}
	if v := task.Steps[1].PlatformVariants["windows"]; v != windowsVariant {
		t.Errorf("the step's own platform variant was changed: %q", v)
	}
}

func TestTaskWithNoMarkedStepOpensNothing(t *testing.T) {
	task := &Task{RunID: "r-unmarked", Steps: []Step{{ID: "step-01", Command: ealStepCommand}}}
	self := ealSelfPath
	ealSelfPath = func() (string, error) {
		t.Error("a task with no marked step looked for the beacon's binary")
		return self()
	}
	defer func() { ealSelfPath = self }()
	New("http://127.0.0.1:1", "a-1", 0).prepareEalRelay(task)()
	if got := executed(task, 0); got != ealStepCommand {
		t.Errorf("the command changed:\n%s", got)
	}
	if _, ok := relayTasks.Load("r-unmarked"); ok {
		t.Error("relay state was kept for a task with no marked step")
	}
}

// The live runtime log shows an operator "the command that was attempted".
// For a relayed step that is the command the scenario's author wrote, not the
// beacon's own call with a loopback port and a path into its ticket directory.
// What is executed is the swapped text.
func TestStartFrameCarriesAuthoredCommandForRelayedStep(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response { return ealrelay.Response{} })
	const authored = ealCall + " list-plugins"
	task := &Task{TaskID: "t-1", RunID: "r-start", ScenarioID: "SIM-NDR-001",
		LiveOutput: &LiveOutput{FlushMs: 10},
		Steps: []Step{
			{ID: "step-03", Command: authored, Identity: "root", EalRelay: true},
		}}
	run := &relayExec{}
	c := directClient(s.URL)
	c.execStep = run.exec
	c.executeTask(context.Background(), task)
	s.mu.Lock()
	defer s.mu.Unlock()

	var starts []map[string]interface{}
	for _, f := range s.frames {
		if f["kind"] == "start" {
			starts = append(starts, f)
		}
	}
	if len(starts) != 1 || starts[0]["step_id"] != "step-03" {
		t.Fatalf("start frames: %v", starts)
	}
	command, _ := starts[0]["command"].(string)
	if !strings.Contains(command, "scripts.eal_simulator.cli") {
		t.Errorf("the start frame does not carry the authored command: %q", command)
	}
	for _, own := range []string{"--ticket-file", "127.0.0.1", " eal --relay "} {
		if strings.Contains(command, own) {
			t.Errorf("the start frame carries the beacon's own call (%q): %q", own, command)
		}
	}
	if command != authored {
		t.Errorf("start frame command = %q, want the step's own %q", command, authored)
	}

	// What ran is the swapped call, and the step still holds its own text.
	if len(run.cmds) != 1 || !rewrittenCall.MatchString(run.cmds[0]) ||
		strings.Contains(run.cmds[0], "scripts.eal_simulator.cli") {
		t.Errorf("the executor was handed: %q", run.cmds)
	}
	if task.Steps[0].Command != authored {
		t.Errorf("the step's own command was changed: %q", task.Steps[0].Command)
	}
}

// Cleanup commands are not steps. One that names the EAL CLI reaches the
// executor exactly as SimCore sent it, and no ticket exists while it runs.
func TestCleanupCommandIsNeverRelayed(t *testing.T) {
	s := newRelaySimCore(t, func() ealrelay.Response { return ealrelay.Response{} })
	const cleanup = ealCall + " list-plugins"
	task := &Task{TaskID: "t-1", RunID: "r-cleanup", ScenarioID: "SIM-NDR-001",
		Steps: []Step{
			{ID: "step-03", Command: ealCall + " list-plugins", Identity: "root", EalRelay: true},
		},
		Cleanup: &TaskCleanup{Commands: []CleanupCommand{{Command: cleanup}}}}
	run := &relayExec{}
	c := directClient(s.URL)
	c.execStep = run.exec
	c.executeTask(context.Background(), task)

	if len(run.cmds) != 2 {
		t.Fatalf("the executor saw %d commands, want the step and its cleanup: %q", len(run.cmds), run.cmds)
	}
	// The step: its call swapped, its ticket in place while it ran.
	if !rewrittenCall.MatchString(run.cmds[0]) || run.tickets[0] != 1 {
		t.Errorf("the step ran %q with %d ticket file(s), want the swapped call and one", run.cmds[0], run.tickets[0])
	}
	// The cleanup: untouched, and nothing to ask the relay with.
	if run.cmds[1] != cleanup {
		t.Errorf("the cleanup command was changed: %q", run.cmds[1])
	}
	if run.tickets[1] != 0 {
		t.Errorf("%d ticket file(s) existed while cleanup ran, want none", run.tickets[1])
	}
}
