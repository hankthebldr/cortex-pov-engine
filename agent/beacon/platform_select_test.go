package beacon

// BEACON-01 — Windows step selection. On base the Step struct had no
// platform_variants field, so encoding/json dropped it and every Windows step
// ran its POSIX primary through PowerShell (SIM-EDR-003 step-01's cron text
// exits 0 after its final echo, so the step reported SUCCESS while doing
// nothing). These tests drive the Windows branch from a POSIX runner by
// injecting beaconGOOS and recording what WOULD reach the executor.

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/executor"
	"github.com/hankthebldr/cortexsim/agent/identity"
)

// execRecorder stands in for identity.ExecuteCtxStream and records every
// ExecutionIdentity that reached it.
type execRecorder struct {
	mu   sync.Mutex
	seen []identity.ExecutionIdentity
}

func (r *execRecorder) exec(_ context.Context, id identity.ExecutionIdentity, _ executor.OutputChunkFunc) (identity.ExecResult, error) {
	r.mu.Lock()
	r.seen = append(r.seen, id)
	r.mu.Unlock()
	return identity.ExecResult{ExitCode: 0, Stdout: "ran: " + id.Command, Termination: TermExited}, nil
}

func (r *execRecorder) commands() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]string, len(r.seen))
	for i, s := range r.seen {
		out[i] = s.Command
	}
	return out
}

func withGOOS(t *testing.T, goos string) {
	t.Helper()
	old := beaconGOOS
	beaconGOOS = goos
	t.Cleanup(func() { beaconGOOS = old })
}

func decodeTask(t *testing.T, raw string) *Task {
	t.Helper()
	var task Task
	if err := json.Unmarshal([]byte(raw), &task); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return &task
}

// The wire shape the server already sends: platform_variants is a map keyed by
// OS. On Windows the variant — not the POSIX primary — must reach the executor.
func TestExecuteTask_WindowsRunsThePlatformVariantNotThePrimary(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"SIM-EDR-003","steps":[
	  {"id":"step-01","name":"cron","command":"(crontab -l; echo '* * * * * x') | crontab -; echo done",
	   "identity":"root","mitre_technique":"T1053.003","platforms":["linux","windows"],
	   "platform_variants":{"windows":"schtasks /create /tn cortexsim /tr calc.exe /sc minute"}}]}`)

	rec := &execRecorder{}
	c := New(rs.URL, "a-win", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), task)

	got := rec.commands()
	if len(got) != 1 {
		t.Fatalf("executor saw %d commands, want 1: %v", len(got), got)
	}
	if got[0] != "schtasks /create /tn cortexsim /tr calc.exe /sc minute" {
		t.Fatalf("Windows ran %q — the POSIX primary, not platform_variants.windows", got[0])
	}
}

// A step whose declared platforms exclude windows must NOT run on Windows. It
// is recorded not_run / PLATFORM_UNSATISFIABLE with a greppable marker, so its
// detections read not_executed — never "missed".
func TestExecuteTask_WindowsRefusesAStepWhosePlatformsExcludeWindows(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"SIM-MP-030","steps":[
	  {"id":"step-01","command":"echo LINUX-ONLY","identity":"root","platforms":["linux"]},
	  {"id":"step-02","command":"echo WINDOWS-STEP","identity":"root","platforms":["windows"]}]}`)

	rec := &execRecorder{}
	c := New(rs.URL, "a-win", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), task)

	for _, cmd := range rec.commands() {
		if strings.Contains(cmd, "LINUX-ONLY") {
			t.Fatalf("a linux-only step reached the Windows executor: %q", cmd)
		}
	}
	var marked bool
	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "!! STEP NOT RUN: PLATFORM_UNSATISFIABLE step=step-01 declared=linux host=windows") {
			marked = true
		}
	}
	if !marked {
		t.Fatalf("no PLATFORM_UNSATISFIABLE marker for step-01 in /output: %+v", rs.find("POST", "/output"))
	}
	cb := decodeComplete(t, rs)
	if len(cb.Steps) == 0 {
		t.Fatalf("no step outcomes posted")
	}
	assertNotRun(t, cb.Steps[0], "step-01", "PLATFORM_UNSATISFIABLE")
}

// On POSIX hosts labels are NOT enforced: ~100 corpus steps are labelled
// windows-only while carrying POSIX text, and refusing them would break every
// Linux run that works today.
func TestExecuteTask_POSIXDoesNotRefuseOnPlatformLabels(t *testing.T) {
	withGOOS(t, "linux")
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"S","steps":[
	  {"id":"step-01","command":"echo mislabelled","identity":"root","platforms":["windows"],
	   "platform_variants":{"windows":"Write-Output win"}}]}`)

	rec := &execRecorder{}
	c := New(rs.URL, "a-lin", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), task)

	if got := rec.commands(); len(got) != 1 || got[0] != "echo mislabelled" {
		t.Fatalf("POSIX host must run the primary regardless of labels, ran %v", got)
	}
}

// The whole decision table, both host families.
func TestPlanStep_DecisionTable(t *testing.T) {
	win := map[string]string{"windows": "Write-Output win"}
	cases := []struct {
		name, goos string
		step       Step
		cmd, shell string
		refused    bool
	}{
		{"win: server resolved cmd", "windows",
			Step{ID: "s", Command: "for /L %i in (1,1,2) do @echo %i", Shell: "cmd", PlatformVariants: win}, "for /L %i in (1,1,2) do @echo %i", "cmd", false},
		{"win: server resolved powershell", "windows",
			Step{ID: "s", Command: "Get-Process", Shell: "PowerShell", Platforms: []string{"linux"}}, "Get-Process", "", false},
		{"win: unknown server shell is refused, not defaulted", "windows",
			Step{ID: "s", Command: "ls", Shell: "bash"}, "", "", true},
		{"win: variant beats primary", "windows",
			Step{ID: "s", Command: "ls -la", Platforms: []string{"linux", "windows"}, PlatformVariants: win}, "Write-Output win", "", false},
		{"win: blank variant does not count", "windows",
			Step{ID: "s", Command: "ls -la", Platforms: []string{"linux"}, PlatformVariants: map[string]string{"windows": "  "}}, "", "", true},
		{"win: platforms exclude windows", "windows",
			Step{ID: "s", Command: "ls -la", Platforms: []string{"linux", "macos"}}, "", "", true},
		{"win: platforms include Windows (case)", "windows",
			Step{ID: "s", Command: "whoami", Platforms: []string{"Windows"}}, "whoami", "", false},
		{"win: no platforms runs primary", "windows",
			Step{ID: "s", Command: "whoami"}, "whoami", "", false},
		{"linux: labels ignored", "linux",
			Step{ID: "s", Command: "id", Platforms: []string{"windows"}, PlatformVariants: win}, "id", "", false},
		{"linux: bash shell runs", "linux",
			Step{ID: "s", Command: "id", Shell: "bash"}, "id", "", false},
		{"linux: cmd shell refused", "linux",
			Step{ID: "s", Command: "dir", Shell: "cmd"}, "", "", true},
		{"darwin: powershell shell refused", "darwin",
			Step{ID: "s", Command: "Get-Item x", Shell: "powershell"}, "", "", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p := planStep(&c.step, c.goos)
			if c.refused {
				if p.refuse == "" || !strings.HasPrefix(p.refuse, platformNotRunMarker+" step=s ") {
					t.Fatalf("want refusal, got %+v", p)
				}
				return
			}
			if p.refuse != "" || p.command != c.cmd || p.shell != c.shell {
				t.Fatalf("plan = %+v, want command=%q shell=%q", p, c.cmd, c.shell)
			}
		})
	}
}

// BEACON-17: a PowerShell step that ends in a bare `exit` exits 0 whatever its
// last tool returned (sh would return the tool's status). The wrapper cannot
// see it happen, so the plan carries a caveat into the step's output — on every
// Windows route that runs PowerShell, and on none that does not.
func TestPlanStep_WindowsNotesABareTrailingExit(t *testing.T) {
	const caveat = "exit-code caveat"
	bare := "tool.exe --run\nexit"
	cases := []struct {
		name, goos string
		step       Step
		want       bool
	}{
		{"win: primary", "windows", Step{ID: "s", Command: bare}, true},
		{"win: platform variant", "windows",
			Step{ID: "s", Command: "ls", PlatformVariants: map[string]string{"windows": bare}}, true},
		{"win: server-resolved powershell", "windows", Step{ID: "s", Command: bare, Shell: "powershell"}, true},
		// cmd's bare exit keeps ERRORLEVEL: no caveat.
		{"win: server-resolved cmd", "windows", Step{ID: "s", Command: bare, Shell: "cmd"}, false},
		{"win: explicit exit code", "windows", Step{ID: "s", Command: "tool.exe\nexit $LASTEXITCODE"}, false},
		{"win: no exit", "windows", Step{ID: "s", Command: "tool.exe"}, false},
		// sh's bare exit IS the last status: nothing to say on POSIX.
		{"linux: bare exit", "linux", Step{ID: "s", Command: bare}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p := planStep(&c.step, c.goos)
			if p.refuse != "" {
				t.Fatalf("unexpected refusal: %+v", p)
			}
			if got := strings.Contains(p.note, caveat); got != c.want {
				t.Fatalf("note carries the bare-exit caveat = %v, want %v (note=%q)", got, c.want, p.note)
			}
		})
	}
}

// The caveat reaches the run record: it is in the step's /output frame.
func TestExecuteTask_WindowsBareExitCaveatIsInTheStepOutput(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", 0)
	c.execStep = (&execRecorder{}).exec
	c.executeTask(context.Background(), &Task{TaskID: "t", RunID: "r", ScenarioID: "S",
		Steps: []Step{{ID: "step-01", Command: "tool.exe --run\nexit", Identity: "root"}}})

	outs := rs.find("POST", "/output")
	if len(outs) != 1 || !strings.Contains(outs[0].Body, "exit-code caveat") ||
		!strings.Contains(outs[0].Body, "exit $LASTEXITCODE") {
		t.Fatalf("the step frame does not carry the bare-exit caveat: %+v", outs)
	}
}

// The chained (causality-contract) path has its own loop and needs its own
// proof: a refused step never reaches the anchor, the variant does.
func TestExecuteTaskChained_WindowsSelectsVariantAndRefusesUnsatisfiable(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	// The session here is the host's real POSIX anchor; the variant text is
	// sh-valid so its execution is observable in /output.
	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"S","cgo_anchor":{"image_name":"w3wp.exe"},"steps":[
	  {"id":"step-01","command":"echo LINUX-ONLY-RAN","identity":"root","platforms":["linux"]},
	  {"id":"step-02","command":"echo PRIMARY-RAN","identity":"root","platforms":["linux","windows"],
	   "platform_variants":{"windows":"echo VARIANT-RAN"}}]}`)
	c := New(rs.URL, "a-win", 0)
	c.resolveIdentity = func(u string) identity.Resolution { return identity.ResolveFor("linux", u, "root") }
	c.executeTask(context.Background(), task)

	var all strings.Builder
	for _, o := range rs.find("POST", "/output") {
		all.WriteString(o.Body)
	}
	out := all.String()
	if strings.Contains(out, "LINUX-ONLY-RAN") || strings.Contains(out, "PRIMARY-RAN") {
		t.Fatalf("chained Windows path ran a refused step or the primary:\n%s", out)
	}
	if !strings.Contains(out, "VARIANT-RAN") {
		t.Fatalf("chained Windows path did not run platform_variants.windows:\n%s", out)
	}
	cb := decodeComplete(t, rs)
	assertNotRun(t, cb.Steps[0], "step-01", ReasonPlatformUnsatisfiable)
	if cb.ExitCode == nil || *cb.ExitCode != 0 {
		t.Fatalf("exit = %v, want 0 (the runnable step passed)", cb.ExitCode)
	}
	if !strings.Contains(cb.Summary, "PLATFORM_UNSATISFIABLE: 1 step(s) not run on this host (step-01)") {
		t.Errorf("summary does not qualify the run: %s", cb.Summary)
	}
}

// A zero is degraded, not ok: when NO step can run on this host the run must
// not complete as exit 0 / SUCCESS.
func TestExecuteTask_NothingRunnableOnThisHostIsNotASuccess(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"S","steps":[
	  {"id":"step-01","command":"crontab -l","identity":"root","platforms":["linux"]},
	  {"id":"step-02","command":"systemctl list-units","identity":"root","platforms":["linux"]}]}`)
	rec := &execRecorder{}
	c := New(rs.URL, "a-win", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), task)

	if len(rec.commands()) != 0 {
		t.Fatalf("a refused step ran: %v", rec.commands())
	}
	cb := decodeComplete(t, rs)
	if cb.ExitCode == nil || *cb.ExitCode != platformUnsatisfiableExitCode {
		t.Fatalf("exit = %v, want %d — a run that executed nothing read as success", cb.ExitCode, platformUnsatisfiableExitCode)
	}
	if strings.HasPrefix(cb.Summary, "SUCCESS") {
		t.Fatalf("summary reads SUCCESS for a run that executed nothing: %s", cb.Summary)
	}
	for i, id := range []string{"step-01", "step-02"} {
		assertNotRun(t, cb.Steps[i], id, ReasonPlatformUnsatisfiable)
	}
}

// The server-resolved shell reaches the executor (shell=cmd on Windows).
func TestExecuteTask_WindowsServerResolvedCmdShellReachesTheExecutor(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"S","steps":[
	  {"id":"step-01","command":"for /L %i in (1,1,2) do @echo %i","identity":"root",
	   "shell":"cmd","resolved_from":"variant:windows"}]}`)
	rec := &execRecorder{}
	c := New(rs.URL, "a-win", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), task)

	rec.mu.Lock()
	defer rec.mu.Unlock()
	if len(rec.seen) != 1 || rec.seen[0].Shell != "cmd" || rec.seen[0].Command != "for /L %i in (1,1,2) do @echo %i" {
		t.Fatalf("executor saw %+v, want the cmd text under shell=cmd", rec.seen)
	}
}

// BEACON-09. A Windows process has no signal: rc 137 is a process that exited
// 137 (or was TerminateProcess-ed with 137), never "killed by SIGKILL". Mapping
// it to signaled severs the chain and writes a witness the host never produced.
func TestClassifyChainedRC_WindowsHasNoSignals(t *testing.T) {
	withGOOS(t, "windows")
	res := identity.ExecResult{ExitCode: 137}
	classifyChainedRC(&res)
	if res.Termination != TermExited || res.Signal != 0 {
		t.Fatalf("windows rc 137 classified as %q signal=%d, want exited/0", res.Termination, res.Signal)
	}
}

// The POSIX mapping is unchanged.
func TestClassifyChainedRC_POSIXStillMapsSignals(t *testing.T) {
	withGOOS(t, "linux")
	res := identity.ExecResult{ExitCode: 137}
	classifyChainedRC(&res)
	if res.Termination != TermSignaled || res.Signal != 9 {
		t.Fatalf("linux rc 137 classified as %q signal=%d, want signaled/9", res.Termination, res.Signal)
	}
}
