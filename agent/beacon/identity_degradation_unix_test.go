//go:build !windows

package beacon

// POSIX-only identity-degradation tests (CI-04): `#!/bin/sh` stubs for
// runuser / sudo / su on PATH, steps whose command is `true`, and a
// linux-shaped resolver in runuser mode, which identity.WrapCommandFor refuses
// on windows (runuser/sudo/su are POSIX-only). The Windows identity contract is
// proven by the Windows-shaped resolver tests in identity_degradation_test.go
// and windows_account_test.go.

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// An account the host does not know must not reach runuser: the step runs
// direct, the record says so and names the user it actually ran as.
func TestExecuteTask_AbsentAccountDegradesInsteadOfInvokingRunuser(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, "runuser-was-called")
	for _, name := range []string{"runuser", "sudo", "su"} {
		stub := "#!/bin/sh\ntouch " + marker + "\nexit 99\n"
		if err := os.WriteFile(filepath.Join(dir, name), []byte(stub), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-u", RunID: "r-u", ScenarioID: "SIM-X-002",
		Steps: []Step{{ID: "step-01", Name: "ghost", Command: "true",
			Identity: "no-such-acct-xyz", MitreTechnique: "T1059"}},
	}
	c := New(rs.URL, "a-ghost", 0)
	c.executeTask(context.Background(), task)

	outputs := rs.find("POST", "/output")
	if len(outputs) != 1 {
		t.Fatalf("outputs = %d, want 1", len(outputs))
	}
	body := outputs[0].Body
	for _, want := range []string{"IDENTITY NOT HONOURED", "no-such-acct-xyz", "exit_code=0"} {
		if !strings.Contains(body, want) {
			t.Errorf("output missing %q:\n%s", want, body)
		}
	}
	if me := identity.CurrentUsername(); me != "" && !strings.Contains(body, me) {
		t.Errorf("output does not name the user it ran as (%q):\n%s", me, body)
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("runuser/sudo/su was exec'd for an account that does not exist")
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, "IDENTITY DEGRADED") {
		t.Fatalf("summary hides the degradation: %+v", completes)
	}
}

func stubRunuser(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	marker := filepath.Join(dir, "runuser-was-called")
	for _, name := range []string{"runuser", "sudo", "su"} {
		stub := "#!/bin/sh\ntouch " + marker + "\nexit 0\n"
		if err := os.WriteFile(filepath.Join(dir, name), []byte(stub), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	return marker
}

// Chained-path twin: the same absent-account contract holds under a causality
// anchor.
func TestExecuteTaskChained_AbsentAccountDegradesInsteadOfInvokingRunuser(t *testing.T) {
	marker := stubRunuser(t)
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	task := &Task{
		TaskID: "t-uc", RunID: "r-uc", ScenarioID: "SIM-X-003",
		CgoAnchor: &CgoAnchor{ImageName: "nginx"},
		Steps: []Step{{ID: "step-01", Name: "ghost", Command: "true",
			Identity: "no-such-acct-xyz", MitreTechnique: "T1059"}},
	}
	c := New(rs.URL, "a-ghost-c", 0)
	c.executeTask(context.Background(), task)

	var body string
	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "=== STEP") {
			body = o.Body
		}
	}
	for _, want := range []string{"IDENTITY NOT HONOURED", "no-such-acct-xyz", "exit_code=0"} {
		if !strings.Contains(body, want) {
			t.Errorf("chained output missing %q:\n%s", want, body)
		}
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("runuser/sudo/su was exec'd for an absent account on the chained path")
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, "IDENTITY DEGRADED") {
		t.Fatalf("chained summary hides the degradation: %+v", completes)
	}
}

// Lookup misses (NSS-only account) but the probe confirms it: honoured, and the
// wrapper is genuinely invoked.
func TestExecuteTask_NSSOnlyAccountIsHonouredAndRunuserInvoked(t *testing.T) {
	marker := stubRunuser(t)
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	check := identity.NewAccountChecker(
		func(string) error { return errors.New("not in /etc/passwd") },
		func(string) identity.ProbeResult { return identity.ProbePresent })
	task := &Task{TaskID: "t-n", RunID: "r-n", ScenarioID: "SIM-X-004",
		Steps: []Step{{ID: "step-01", Name: "nss", Command: "true", Identity: "ldapuser", MitreTechnique: "T1059"}}}
	c := New(rs.URL, "a-nss", 0)
	c.resolveIdentity = func(u string) identity.Resolution {
		return identity.ResolveWith("linux", u, "root", check)
	}
	c.executeTask(context.Background(), task)
	if _, err := os.Stat(marker); err != nil {
		t.Fatal("runuser was not invoked for an account the probe confirmed")
	}
	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "IDENTITY NOT HONOURED") {
			t.Fatalf("honoured account was flagged:\n%s", o.Body)
		}
	}
}
