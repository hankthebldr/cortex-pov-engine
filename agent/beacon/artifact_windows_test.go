//go:build windows

package beacon

// Windows twin of the exec-probe tests in artifact_test.go, which skip where
// execProbeSupported() is false (CI-04). Windows has no noexec mount, so the
// probe must not run at all — and must not leave a probe file behind. Run only
// by the windows-native CI job; not observed passing on Windows.

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStage_WindowsWritesNoExecProbe(t *testing.T) {
	if execProbeSupported() {
		t.Fatal("execProbeSupported() is true on Windows — artifact_windows.go says there is nothing to probe")
	}
	body := []byte("Write-Output 'x'\r\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	probed := false
	c.artifactProbeExec = func(string) error { probed = true; return nil }

	dest := filepath.Join(root, "run-1", "x.ps1")
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.ps1", SHA256: digestOf(body), Path: "/api/shelf/payload/x.ps1", Dest: dest, Mode: "0755",
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	defer set.Cleanup(c, "")
	if probed {
		t.Fatal("the exec probe ran on Windows")
	}
	if _, err := os.Stat(filepath.Join(root, "run-1", execProbeName)); !os.IsNotExist(err) {
		t.Fatalf("a probe file was written on Windows (err=%v)", err)
	}
}
