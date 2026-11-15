package beacon

// Scenario cleanup in pull mode (#324): what reaches the executor. No shell is
// started here, so this runs on every platform the beacon is tested on. What a
// POSIX host does with the commands is cleanup_unix_test.go.

import (
	"context"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// SimCore resolves each cleanup line for the agent's OS and names its
// interpreter, as it does for a step. The beacon runs each one as its own
// account, with that interpreter, after the steps.
func TestCleanupCommandsRunAsTheBeaconWithTheirShell(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := decodeTask(t, `{"task_id":"t","run_id":"r","scenario_id":"SIM-EDR-003",
	  "steps":[{"id":"step-01","name":"task","command":"schtasks /create /tn cortexsim /tr calc.exe /sc minute",
	            "identity":"root","mitre_technique":"T1053.005","shell":"cmd","platforms":["windows"]}],
	  "cleanup":{"commands":[
	     {"command":"Remove-Item -Force C:\\Windows\\Temp\\cortexsim.txt","shell":"powershell"},
	     {"command":"schtasks /delete /tn cortexsim /f","shell":"cmd"}],
	   "timeout_seconds":120}}`)

	rec := &execRecorder{}
	c := New(rs.URL, "a-win", 0)
	c.execStep = rec.exec
	c.executeTask(context.Background(), task)

	rec.mu.Lock()
	seen := append([]identity.ExecutionIdentity(nil), rec.seen...)
	rec.mu.Unlock()
	if len(seen) != 3 {
		t.Fatalf("executor saw %d commands, want the step and two cleanup commands: %+v", len(seen), seen)
	}
	want := []identity.ExecutionIdentity{
		{Mode: "direct", Command: `Remove-Item -Force C:\Windows\Temp\cortexsim.txt`, Shell: "powershell"},
		{Mode: "direct", Command: "schtasks /delete /tn cortexsim /f", Shell: "cmd"},
	}
	for i, w := range want {
		if got := seen[i+1]; got != w {
			t.Errorf("cleanup command %d reached the executor as %+v, want %+v", i+1, got, w)
		}
	}
}
