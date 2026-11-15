package beacon

// BEACON-20.
//
//  1. On Windows the agent log said "unknown identity ... mapping to runuser
//     (best-effort)" for `administrator` and `user`. Windows has no runuser and
//     maps nothing: a DC reading the endpoint's log would conclude the beacon
//     tried to impersonate, against the run record's IDENTITY NOT HONOURED.
//  2. The run record never said what a Windows step actually ran AS. A step
//     declared `identity=root` on an elevated SYSTEM service is "honoured", so
//     its frame carried no account at all — nothing told SYSTEM from an
//     elevated administrator from a standard user.
//
// Both are driven from any host through beaconGOOS; the frame on a real Windows
// host is client_windows_test.go.

import (
	"bytes"
	"context"
	"log"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

func captureLog(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	prev, flags := log.Writer(), log.Flags()
	log.SetOutput(&buf)
	log.SetFlags(0)
	t.Cleanup(func() {
		log.SetOutput(prev)
		log.SetFlags(flags)
	})
	return &buf
}

func TestLogIdentity_WindowsNeverSaysRunuser(t *testing.T) {
	withGOOS(t, "windows")
	buf := captureLog(t)
	for _, requested := range []string{"administrator", "user", "www-data"} {
		logIdentity("step-01", requested, windowsResolver(requested))
	}
	got := buf.String()
	if strings.Contains(got, "mapping to runuser") || strings.Contains(got, "best-effort") {
		t.Fatalf("the Windows agent log names a POSIX mechanism the beacon never uses:\n%s", got)
	}
	// What actually happened is still logged.
	if n := strings.Count(got, "identity NOT honoured"); n != 3 {
		t.Fatalf("want the degradation logged for all 3 steps, got %d:\n%s", n, got)
	}
}

// POSIX wording is unchanged on Linux, and names the real mechanism on macOS.
func TestLogIdentity_POSIXNamesTheMechanismItUses(t *testing.T) {
	for goos, mode := range map[string]string{"linux": "runuser", "darwin": "sudo_sh"} {
		withGOOS(t, goos)
		buf := captureLog(t)
		res := identity.ResolveForElevation(goos, "some-unlisted-account", "root", true)
		if !res.Unknown || res.Mode != mode {
			t.Fatalf("%s: precondition: resolution = %+v, want Unknown and mode %s", goos, res, mode)
		}
		logIdentity("step-01", "some-unlisted-account", res)
		want := `WARN unknown identity "some-unlisted-account" for step step-01 — mapping to ` + mode + " (best-effort)"
		if !strings.Contains(buf.String(), want) {
			t.Errorf("%s: log = %q, want it to contain %q", goos, buf.String(), want)
		}
	}
}

// Every Windows step frame says what the step executed as.
func TestExecuteTask_WindowsStepFramesNameTheExecutionAccount(t *testing.T) {
	withGOOS(t, "windows")
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-win", 0)
	c.resolveIdentity = windowsResolver
	c.execStep = (&execRecorder{}).exec
	c.executeTask(context.Background(), windowsTask())

	outs := rs.find("POST", "/output")
	if len(outs) != 2 {
		t.Fatalf("want 2 step frames, got %d", len(outs))
	}
	for i, o := range outs {
		if !strings.Contains(o.Body, "executed as ") {
			t.Errorf("step %d frame does not say what account the step executed as — identity=%s is only "+
				"what the scenario declared:\n%s", i+1, []string{"administrator", "root"}[i], o.Body)
		}
	}
}

func asBeaconAccount(t *testing.T, name string, elevated bool) {
	t.Helper()
	oldName, oldElev := beaconAccountName, beaconTokenElevated
	beaconAccountName = func() string { return name }
	beaconTokenElevated = func() bool { return elevated }
	t.Cleanup(func() { beaconAccountName, beaconTokenElevated = oldName, oldElev })
}

// The line itself: the account, the token, and that identity= is a declaration.
func TestFormatStepOutput_WindowsExecutionAccountLine(t *testing.T) {
	c := New("http://unused.invalid", "a-1", 0)
	step := &Step{ID: "step-01", MitreTechnique: "T1003"}
	frame := func() string {
		return c.formatStepOutput(1, 1, step, "root", identity.Resolution{Mode: "direct", Honoured: true},
			identity.ExecResult{Stdout: "ok"}, nil, "")
	}

	withGOOS(t, "windows")
	asBeaconAccount(t, `NT AUTHORITY\SYSTEM`, true)
	got := frame()
	if !strings.Contains(got, `[cortexsim] executed as NT AUTHORITY\SYSTEM (elevated token)`) {
		t.Errorf("the service account is not named:\n%s", got)
	}
	// It sits right under the header, above the step's own output.
	lines := strings.Split(got, "\n")
	if len(lines) < 3 || !strings.HasPrefix(lines[0], "=== STEP 1/1") || !strings.HasPrefix(lines[1], "[cortexsim] executed as") {
		t.Errorf("the account line is not the first line under the header:\n%s", got)
	}

	asBeaconAccount(t, `LAB\dc-user`, false)
	if got := frame(); !strings.Contains(got, `executed as LAB\dc-user (token NOT elevated)`) {
		t.Errorf("an unelevated beacon is not named as such:\n%s", got)
	}

	asBeaconAccount(t, "", false)
	if got := frame(); !strings.Contains(got, "name unavailable") {
		t.Errorf("an unknown account name is not stated:\n%s", got)
	}

	// POSIX frames are unchanged: there the harness impersonates and identity=
	// is the account the step really ran as.
	for _, goos := range []string{"linux", "darwin"} {
		withGOOS(t, goos)
		if got := frame(); strings.Contains(got, "executed as") {
			t.Errorf("%s: a POSIX step frame carries the Windows account line:\n%s", goos, got)
		}
	}
}

// A degraded Windows step carries both lines: what it ran as, and the marker.
func TestFormatStepOutput_WindowsDegradedStepKeepsItsMarker(t *testing.T) {
	withGOOS(t, "windows")
	asBeaconAccount(t, `CONTOSO\svc_cortexsim`, true)
	c := New("http://unused.invalid", "a-1", 0)
	got := c.formatStepOutput(1, 1, &Step{ID: "step-01"}, "administrator", windowsResolver("administrator"),
		identity.ExecResult{}, nil, "")
	if !strings.Contains(got, `executed as CONTOSO\svc_cortexsim (elevated token)`) ||
		!strings.Contains(got, identityDegradedMarker) {
		t.Fatalf("a degraded Windows step must name the account AND keep the marker:\n%s", got)
	}
}
