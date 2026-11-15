package beacon

import (
	"os"
	"regexp"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
)

// A marked step runs this beacon's own binary as `eal ...`. Under `go test`
// that binary is the test binary, so it answers to the subcommand too: the
// tests in eal_relay_unix_test.go run a real step through a real child.
func TestMain(m *testing.M) {
	if len(os.Args) > 1 && os.Args[1] == "eal" {
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

var rewrittenCall = regexp.MustCompile(`'([^']+)' eal --relay (127\.0\.0\.1:\d+) --ticket ([0-9a-f]{32})`)

func TestEalCallRewrittenOnlyWhenMarked(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	task := &Task{RunID: "r-1", Steps: []Step{
		{ID: "step-01", Command: ealStepCommand},
		{ID: "step-02", Command: ealStepCommand, EalRelay: true,
			PlatformVariants: map[string]string{"windows": ealCall + " list-plugins"}},
		{ID: "step-03", Command: "echo no call here", EalRelay: true},
		{ID: "step-04", Command: ealCall + " list-plugins | jq -e '.total >= 4'", EalRelay: true},
	}}
	// The quoting under test is the POSIX one, whatever host runs the test.
	goos := beaconGOOS
	beaconGOOS = "linux"
	defer func() { beaconGOOS = goos }()

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

	// Each marked step carries its own ticket.
	m4 := rewrittenCall.FindStringSubmatch(task.Steps[3].Command)
	if m4 == nil || m4[3] == m[3] {
		t.Errorf("step-04 has no ticket of its own: %q", task.Steps[3].Command)
	}
	if !strings.HasSuffix(task.Steps[3].Command, " list-plugins | jq -e '.total >= 4'") {
		t.Errorf("the pipe after the call moved: %q", task.Steps[3].Command)
	}
}

func TestTaskWithNoMarkedStepOpensNothing(t *testing.T) {
	task := &Task{RunID: "r-1", Steps: []Step{{ID: "step-01", Command: ealStepCommand}}}
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
}
