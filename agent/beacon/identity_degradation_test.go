package beacon

// The Windows identity story is only correct if the degradation reaches the RUN
// RECORD. A WARN in the agent's stderr on a customer jumpbox is not evidence
// anybody reading the POV report will ever see — the /output and /complete POSTs
// are. These tests drive the beacon with a Windows-shaped resolver (which is
// unreachable from a Linux runner otherwise) and assert on the bytes that
// actually leave the process.

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// windowsResolver mimics identity.Resolve as it behaves on a Windows host where
// the beacon runs as CONTOSO\svc_cortexsim.
func windowsResolver(username string) identity.Resolution {
	return identity.ResolveFor("windows", username, `CONTOSO\svc_cortexsim`)
}

func windowsTask() *Task {
	return &Task{
		TaskID:     "t-win",
		RunID:      "r-win",
		ScenarioID: "SIM-EDR-013",
		Steps: []Step{
			{ID: "step-01", Name: "as admin", Command: "echo one", Identity: "administrator",
				MitreTechnique: "T1059", Platforms: []string{"windows"}},
			{ID: "step-02", Name: "as beacon", Command: "echo two", Identity: "root",
				MitreTechnique: "T1059", Platforms: []string{"windows"}},
		},
	}
}

// An unhonourable identity must be stamped into the step's own output, which is
// what SimCore stores against the Result row.
func TestExecuteTask_UnhonouredIdentityIsWrittenIntoTheRunRecord(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-win", 0)
	c.resolveIdentity = windowsResolver
	c.executeTask(context.Background(), windowsTask())

	outputs := rs.find("POST", "/output")
	if len(outputs) != 2 {
		t.Fatalf("expected one /output POST per step, got %d", len(outputs))
	}

	// Step 1 asked for `administrator`, which the beacon cannot become.
	if !strings.Contains(outputs[0].Body, "IDENTITY NOT HONOURED") {
		t.Fatalf("step-01 output does not record the identity degradation — the run record "+
			"claims it ran as administrator:\n%s", outputs[0].Body)
	}
	if !strings.Contains(outputs[0].Body, "administrator") ||
		!strings.Contains(outputs[0].Body, `svc_cortexsim`) {
		t.Errorf("degradation notice does not name both the claimed and the real actor:\n%s",
			outputs[0].Body)
	}

	// Step 2 asked for `root` → direct mode → nothing was claimed, nothing to warn
	// about. Crying wolf on every step trains operators to ignore the notice.
	if strings.Contains(outputs[1].Body, "IDENTITY NOT HONOURED") {
		t.Errorf("step-02 (direct identity) was wrongly flagged as degraded:\n%s", outputs[1].Body)
	}
}

// The completion summary is the one line a DC reads on the run card. A run with
// unhonoured identities must not present there as an unqualified SUCCESS.
func TestExecuteTask_SummaryFlagsDegradedIdentities(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-win", 0)
	c.resolveIdentity = windowsResolver
	c.executeTask(context.Background(), windowsTask())

	completes := rs.find("POST", "/complete")
	if len(completes) != 1 {
		t.Fatalf("expected 1 /complete, got %d", len(completes))
	}
	body := completes[0].Body
	if !strings.Contains(body, "IDENTITY DEGRADED: 1 step(s)") {
		t.Fatalf("completion summary hides the degradation:\n%s", body)
	}
	// The run itself still succeeded — the degradation is a caveat on the
	// evidence, not an execution failure, and conflating them would break
	// fail-fast and the verifier's threshold arithmetic.
	if !strings.Contains(body, `"exit_code":0`) {
		t.Errorf("a degraded identity must not change the run's exit code:\n%s", body)
	}
}

// A run in which every identity was honoured must carry NO degradation text —
// otherwise the marker is noise and stops being a signal.
func TestExecuteTask_NoDegradationTextWhenIdentitiesAreHonoured(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	c := New(rs.URL, "a-1", 0)
	c.resolveIdentity = func(u string) identity.Resolution {
		return identity.ResolveFor("linux", u, "root")
	}
	c.executeTask(context.Background(), &Task{
		TaskID: "t-1", RunID: "r-1", ScenarioID: "SIM-EDR-001",
		Steps: []Step{{ID: "step-01", Command: "echo ok", Identity: "root", MitreTechnique: "T1"}},
	})

	for _, rec := range append(rs.find("POST", "/output"), rs.find("POST", "/complete")...) {
		if strings.Contains(rec.Body, "IDENTITY NOT HONOURED") ||
			strings.Contains(rec.Body, "IDENTITY DEGRADED") {
			t.Fatalf("clean run carries a spurious degradation notice: %s", rec.Body)
		}
	}
}

// The chained (causality-contract) path shares the identity contract with the
// default path. It has its own copy of the step loop, so it needs its own proof
// — a scenario that opts into the causality contract must not lose the notice.
func TestExecuteTaskChained_AlsoRecordsUnhonouredIdentity(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := windowsTask()
	task.CgoAnchor = &CgoAnchor{ImageName: "w3wp.exe", PrimaryUsername: "administrator"}

	c := New(rs.URL, "a-win", 0)
	c.resolveIdentity = windowsResolver
	c.executeTask(context.Background(), task)

	outputs := rs.find("POST", "/output")
	if len(outputs) == 0 {
		t.Fatal("chained path produced no /output POSTs")
	}
	if !strings.Contains(outputs[0].Body, "IDENTITY NOT HONOURED") {
		t.Fatalf("chained path dropped the identity degradation:\n%s", outputs[0].Body)
	}

	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, "IDENTITY DEGRADED") {
		t.Fatalf("chained completion summary hides the degradation: %+v", completes)
	}
}

// A BeaconClient built with a struct literal (as several existing tests do)
// must still resolve identities rather than panic on a nil resolver.
func TestResolve_ZeroValueClientFallsBackToHostResolver(t *testing.T) {
	c := &BeaconClient{}
	if got := c.resolve("root"); got.Mode != "direct" {
		t.Fatalf("zero-value client resolve(root) = %+v, want direct", got)
	}
}

func shOnlyPathForBeacon(t *testing.T) {
	t.Helper()
	sh, err := exec.LookPath("sh")
	if err != nil {
		t.Skip("no sh on PATH")
	}
	dir := t.TempDir()
	if err := os.Symlink(sh, filepath.Join(dir, "sh")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
}

// A requested cgo_anchor that a multi-call-sh image cannot carry is recorded in
// the run record exactly once, not silently dropped.
func TestExecuteTaskChained_AnchorNotHonouredIsRecordedOnce(t *testing.T) {
	shOnlyPathForBeacon(t)
	rs := newRecordingServer(t, nil)
	defer rs.Close()

	task := &Task{
		TaskID: "t-a", RunID: "r-a", ScenarioID: "SIM-X-001",
		CgoAnchor: &CgoAnchor{ImageName: "nginx"},
		Steps: []Step{
			{ID: "step-01", Name: "one", Command: "echo one", Identity: "root", MitreTechnique: "T1059"},
			{ID: "step-02", Name: "two", Command: "echo two", Identity: "root", MitreTechnique: "T1059"},
		},
	}
	c := New(rs.URL, "a-anchor", 0)
	c.executeTask(context.Background(), task)

	n := 0
	for _, o := range rs.find("POST", "/output") {
		if strings.Contains(o.Body, "ANCHOR NOT HONOURED") {
			n++
			if !strings.Contains(o.Body, "requested nginx") || !strings.Contains(o.Body, "(no bash on PATH)") || !strings.Contains(o.Body, "'sh'") {
				t.Errorf("notice text wrong:\n%s", o.Body)
			}
		}
	}
	if n != 1 {
		t.Fatalf("ANCHOR NOT HONOURED bodies = %d, want exactly 1", n)
	}
	completes := rs.find("POST", "/complete")
	if len(completes) != 1 || !strings.Contains(completes[0].Body, "ANCHOR NOT HONOURED") {
		t.Fatalf("run summary hides the anchor degradation: %+v", completes)
	}
}

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

func TestResolveWith_AbsentAccountReasonIsInjectable(t *testing.T) {
	missing := func(string) error { return errors.New("unknown user") }
	present := func(string) error { return nil }
	if r := identity.ResolveWith("linux", "www-data", "root", missing); r.Mode != "direct" || !r.Degraded() ||
		!strings.Contains(r.Degradation, "www-data") {
		t.Fatalf("absent account not degraded: %+v", r)
	}
	if r := identity.ResolveWith("linux", "www-data", "root", present); r.Mode != "runuser" || r.Degraded() {
		t.Fatalf("present account wrongly degraded: %+v", r)
	}
	if r := identity.ResolveWith("linux", "root", "root", missing); r.Degraded() {
		t.Fatalf("direct identity must never be looked up: %+v", r)
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
