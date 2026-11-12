//go:build !windows

package beacon

// POSIX-dialect staged-tool tests (CI-04): the staged tool is a `#!/bin/sh`
// script run with `sh <dest>`, and the permission trap needs runuser/su and a
// real www-data. PowerShell-dialect twins: client_artifact_windows_test.go.

import (
	"context"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

func TestExecuteTask_StagesThenRunsTheStagedTool(t *testing.T) {
	tool := []byte("#!/bin/sh\necho STAGED_TOOL_RAN\n")
	root := t.TempDir()
	dest := filepath.Join(root, "run-1", "sysinfo.sh")

	task := &Task{
		TaskID: "t-1", RunID: "run-1", ScenarioID: "SIM-EDR-999",
		Steps: []Step{{
			ID: "step-01", Name: "enumerate", MitreTechnique: "T1082",
			Identity: "root", Command: "sh " + dest,
		}},
		Artifacts: []Artifact{{
			Name: "linpeas.sh", SHA256: digestOf(tool), SizeBytes: int64(len(tool)),
			Path: "/api/shelf/payload/linpeas.sh", Dest: dest, Mode: "0755", Steps: []string{"step-01"},
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"linpeas.sh": tool})
	c := artifactClientFor(t, rs, root)

	c.executeTask(context.Background(), task)

	outs := rs.find("POST", "/output")
	if len(outs) < 2 {
		t.Fatalf("want a staging ledger frame and a step frame, got %d", len(outs))
	}
	if !strings.Contains(outs[0].Body, "ARTIFACT STAGING") || !strings.Contains(outs[0].Body, "staged linpeas.sh") {
		t.Fatalf("the ledger must be posted BEFORE step 1: %s", outs[0].Body)
	}
	joined := strings.Join(bodiesOf(outs), "\n")
	if !strings.Contains(joined, "STAGED_TOOL_RAN") {
		t.Fatalf("the step did not execute the staged tool:\n%s", joined)
	}
	// The rename negative control is self-documenting: the run record shows the
	// path the operator chose, not the tool's own name.
	if !strings.Contains(outs[0].Body, "sysinfo.sh") {
		t.Errorf("ledger must show the DESTINATION name: %s", outs[0].Body)
	}

	code, summary := rs.completeBody(t)
	if code != 0 {
		t.Fatalf("exit_code = %d, summary = %s", code, summary)
	}
	// Teardown: the tooling must not survive on a customer endpoint.
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived the run at %s", dest)
	}
	if _, err := os.Stat(filepath.Dir(dest)); !os.IsNotExist(err) {
		t.Errorf("staging directory survived the run")
	}
}

// The causality-chained path must inherit staging from the same place.
func TestExecuteTask_ChainedPathAlsoStages(t *testing.T) {
	tool := []byte("#!/bin/sh\necho CHAINED_TOOL_RAN\n")
	root := t.TempDir()
	dest := filepath.Join(root, "run-1", "x.sh")
	task := &Task{
		TaskID: "t", RunID: "run-1", ScenarioID: "SIM-CDR-001",
		CgoAnchor: &CgoAnchor{ImageName: "apache2", PrimaryUsername: "www-data"},
		Steps: []Step{{
			ID: "step-01", MitreTechnique: "T1082", Identity: "root", Command: "sh " + dest,
		}},
		Artifacts: []Artifact{{
			Name: "x.sh", SHA256: digestOf(tool), Path: "/api/shelf/payload/x.sh",
			Dest: dest, Mode: "0755", Steps: []string{"step-01"},
		}},
	}
	rs := newArtifactRecordingServer(t, task, map[string][]byte{"x.sh": tool})
	c := artifactClientFor(t, rs, root)
	c.executeTask(context.Background(), task)

	joined := strings.Join(bodiesOf(rs.find("POST", "/output")), "\n")
	if !strings.Contains(joined, "CHAINED_TOOL_RAN") {
		t.Fatalf("the chained path did not run the staged tool:\n%s", joined)
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived a chained run")
	}
}

// The fetch runs as the beacon's account (commonly root); the step runs as
// www-data via `runuser -l`. A root-owned 0700 staging dir, or a 0600 file,
// makes the step die EACCES — a "Permission denied" indistinguishable from the
// TTP failing on a hardened host. This is the most likely real-world break in
// the whole feature and it is invisible to any test run as root with no
// impersonation.
func TestStagedFileIsExecutableByAnotherUser(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("needs euid 0 to impersonate")
	}
	if _, err := user.Lookup("www-data"); err != nil {
		t.Skipf("no www-data on this host: %v", err)
	}
	// The `su` mode is used deliberately rather than whatever identity.Resolve
	// picks: on many images www-data's login shell is /usr/sbin/nologin, so
	// `runuser -l` cannot execute ANYTHING there — an image property that has
	// nothing to do with file permissions and would mask the thing under test.
	// `su -s /bin/bash` is a real harness mode and isolates the permission
	// question: can a DIFFERENT uid read and execute what root staged?
	res := identity.Resolution{Mode: "su", Username: "www-data"}

	// NOT t.TempDir(): it creates its parents 0700, which would mask the very
	// thing under test.
	root := filepath.Join("/tmp", fmt.Sprintf("cortexsim-perm-%d", os.Getpid()))
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(root, 0o755); err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(root)

	tool := []byte("#!/bin/sh\necho RAN_AS_$(id -un)\n")
	srv := newShelfServer(t, tool)
	c := stagingClient(t, srv.URL, root)
	c.artifactProbeExec = func(string) error { return nil }

	dest := filepath.Join(root, "run-1", "linpeas.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "linpeas.sh", SHA256: digestOf(tool),
		Path: "/api/shelf/payload/linpeas.sh", Dest: dest, Mode: "0755",
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	defer set.Cleanup(nil, "")

	// Through the REAL harness, not a hand-rolled runuser: the point is that the
	// path a scenario step takes can read and execute what the beacon staged.
	execRes, err := identity.Execute(identity.ExecutionIdentity{
		Mode: res.Mode, Username: res.Username, Command: dest,
	})
	if err != nil || execRes.ExitCode != 0 {
		t.Fatalf("a root-staged 0755 file in a 0755 dir was not executable by www-data "+
			"(mode=%s exit=%d err=%v)\nstdout: %s\nstderr: %s",
			res.Mode, execRes.ExitCode, err, execRes.Stdout, execRes.Stderr)
	}
	if !strings.Contains(execRes.Stdout, "RAN_AS_www-data") {
		t.Fatalf("unexpected output: %q / %q", execRes.Stdout, execRes.Stderr)
	}
	t.Logf("staged file executed by www-data via harness mode %q: %s", res.Mode, strings.TrimSpace(execRes.Stdout))
}
