//go:build windows

package beacon

// Windows twin of the exec-probe tests in artifact_test.go, which skip where
// execProbeSupported() is false (CI-04). Windows has no noexec mount, so the
// probe must not run at all — and must not leave a probe file behind. Run only
// by the windows-native CI job; not observed passing on Windows.

import (
	"os"
	"path/filepath"
	"strings"
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

// placeBinary points the executablePath seam at exe for this test.
func placeBinary(t *testing.T, exe string) {
	t.Helper()
	old := executablePath
	executablePath = func() (string, error) { return exe, nil }
	t.Cleanup(func() { executablePath = old })
}

// BEACON-15 on a real Windows host: the cache is a `cache` directory next to
// the beacon binary, a second staging is served from it, and the drive-rooted
// POSIX literal (\opt\cortexsim\cache) is never created.
func TestArtifactCache_WindowsHostLivesNextToTheBinary(t *testing.T) {
	drive := filepath.VolumeName(os.TempDir())
	optCache := drive + `\opt\cortexsim\cache`
	_, statErr := os.Stat(optCache)
	existedBefore := statErr == nil

	install := filepath.Join(t.TempDir(), "CortexSim")
	if err := os.MkdirAll(install, 0o755); err != nil {
		t.Fatal(err)
	}
	placeBinary(t, filepath.Join(install, "cortexsim-agent.exe"))

	body := []byte("Write-Output 'tool'\r\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.artifactCacheDirFunc = nil // the real resolution, not the test default

	want := filepath.Join(install, "cache")
	if got := c.getArtifactCacheDir(); !strings.EqualFold(got, want) {
		t.Fatalf("getArtifactCacheDir() = %q, want %q (next to the beacon binary)", got, want)
	}
	art := Artifact{Name: "x.ps1", SHA256: digestOf(body), Path: "/api/shelf/payload/x.ps1",
		Dest: filepath.Join(root, "run-1", "x.ps1"), Mode: "0755"}
	set1 := stageOneArtifact(t, c, art)
	if !set1.OK() {
		t.Fatalf("first staging failed: %+v", set1.Failures)
	}
	set1.Cleanup(c, "")
	if _, err := os.Stat(filepath.Join(want, strings.ToLower(art.SHA256))); err != nil {
		t.Fatalf("no cache entry next to the binary after the first staging: %v", err)
	}
	set2 := stageOneArtifact(t, c, art)
	if !set2.OK() || !set2.Files[0].FromCache || srv.hits.Load() != 1 {
		t.Fatalf("second staging: ok=%v files=%+v hits=%d, want a cache hit and one fetch in total",
			set2.OK(), set2.Files, srv.hits.Load())
	}
	set2.Cleanup(c, "")

	if _, err := os.Stat(optCache); err == nil && !existedBefore {
		t.Fatalf("the beacon created %s — the drive-rooted POSIX cache literal", optCache)
	}
}

// A binary at a drive root gets no cache at all, and staging still works.
func TestArtifactCache_WindowsHostBinaryAtADriveRootHasNoCache(t *testing.T) {
	drive := filepath.VolumeName(os.TempDir())
	_, statErr := os.Stat(drive + `\cache`)
	existedBefore := statErr == nil
	placeBinary(t, drive+`\cortexsim-agent.exe`)

	body := []byte("Write-Output 'tool'\r\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.artifactCacheDirFunc = nil

	if got := c.getArtifactCacheDir(); got != "" {
		t.Fatalf("getArtifactCacheDir() = %q for a binary at the drive root, want no cache", got)
	}
	art := Artifact{Name: "x.ps1", SHA256: digestOf(body), Path: "/api/shelf/payload/x.ps1",
		Dest: filepath.Join(root, "run-1", "x.ps1"), Mode: "0755"}
	for i := 0; i < 2; i++ {
		set := stageOneArtifact(t, c, art)
		if !set.OK() || set.Files[0].FromCache {
			t.Fatalf("staging %d with no cache: ok=%v files=%+v", i, set.OK(), set.Files)
		}
		set.Cleanup(c, "")
	}
	if srv.hits.Load() != 2 {
		t.Fatalf("fetches = %d, want 2 (nothing is cached)", srv.hits.Load())
	}
	if _, err := os.Stat(drive + `\cache`); err == nil && !existedBefore {
		t.Fatalf("the beacon created %s\\cache", drive)
	}
}
