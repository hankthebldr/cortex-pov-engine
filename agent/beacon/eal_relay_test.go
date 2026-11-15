package beacon

import (
	"encoding/json"
	"os"
	"regexp"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
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

func TestEalCallRewrittenOnlyWhenMarked(t *testing.T) {
	forcePosix(t)
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	task := &Task{RunID: "r-rewrite", Steps: []Step{
		{ID: "step-01", Command: ealStepCommand},
		{ID: "step-02", Command: ealStepCommand, EalRelay: true,
			PlatformVariants: map[string]string{"windows": ealCall + " list-plugins"}},
		{ID: "step-03", Command: "echo no call here", EalRelay: true},
		{ID: "step-04", Command: ealCall + " list-plugins | jq -e '.total >= 4'", EalRelay: true},
	}}
	c := New("http://127.0.0.1:1", "a-1", 0)
	done := c.prepareEalRelay(task)
	defer done()

	// Not marked: SimCore did not ask, so the command is the one it sent.
	if task.Steps[0].Command != ealStepCommand {
		t.Errorf("an unmarked step was rewritten:\n%s", task.Steps[0].Command)
	}

	// Marked: the call is this binary's eal subcommand, and nothing else moved.
	m := rewrittenCall.FindStringSubmatch(task.Steps[1].Command)
	if m == nil {
		t.Fatalf("the marked step's call was not rewritten:\n%s", task.Steps[1].Command)
	}
	if m[1] != self {
		t.Errorf("the call runs %q, want this binary %q", m[1], self)
	}
	if want := strings.Replace(ealStepCommand, ealCall, m[0], 1); task.Steps[1].Command != want {
		t.Errorf("more than the call changed\n got: %q\nwant: %q", task.Steps[1].Command, want)
	}
	if v := task.Steps[1].PlatformVariants["windows"]; !strings.Contains(v, " eal --relay ") ||
		strings.Contains(v, "python") || !strings.HasSuffix(v, " list-plugins") {
		t.Errorf("the platform variant kept its python call: %q", v)
	}

	// Marked with nothing to rewrite: untouched.
	if task.Steps[2].Command != "echo no call here" {
		t.Errorf("a step with no call was changed: %q", task.Steps[2].Command)
	}

	// Each marked step has a ticket file of its own, and neither exists yet:
	// a ticket is made when its step starts.
	m4 := rewrittenCall.FindStringSubmatch(task.Steps[3].Command)
	if m4 == nil || m4[3] == m[3] {
		t.Fatalf("step-04 has no ticket file of its own: %q", task.Steps[3].Command)
	}
	for _, file := range []string{m[3], m4[3]} {
		if _, err := os.Stat(file); !os.IsNotExist(err) {
			t.Errorf("ticket file %s exists before its step started (err=%v)", file, err)
		}
	}
	if !strings.HasSuffix(task.Steps[3].Command, " list-plugins | jq -e '.total >= 4'") {
		t.Errorf("the pipe after the call moved: %q", task.Steps[3].Command)
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
	if task.Steps[0].Command != ealStepCommand {
		t.Errorf("the command changed:\n%s", task.Steps[0].Command)
	}
	if _, ok := relayTasks.Load("r-unmarked"); ok {
		t.Error("relay state was kept for a task with no marked step")
	}
}
