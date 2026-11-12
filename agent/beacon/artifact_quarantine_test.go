package beacon

// BEACON-16. Endpoint protection routinely quarantines a staged offensive tool
// in the window between the beacon writing it and the beacon renaming it into
// place: on Windows the rename fails with a sharing violation or access denied
// while the scanner holds the file, or the .part simply vanishes. Base reported
// that as ARTIFACT_WRITE_FAILED and then told the DC it was "NOT a gap in the
// customer's detection coverage" — when a quarantine is the customer's
// prevention working. The frame must not prejudge which it is.

import (
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func shortRenameRetry(t *testing.T) {
	t.Helper()
	old := renameRetryDelay
	renameRetryDelay = time.Millisecond
	t.Cleanup(func() { renameRetryDelay = old })
}

func withRename(t *testing.T, f func(oldpath, newpath string) error) {
	t.Helper()
	old := renameFile
	renameFile = f
	t.Cleanup(func() { renameFile = old })
}

// The .part vanished after a successful, digest-verified write: removed by
// something other than the beacon. That is quarantine-shaped, not a write bug.
func TestStage_VanishedPartIsQuarantineSuspected(t *testing.T) {
	shortRenameRetry(t)
	var calls atomic.Int64
	withRename(t, func(oldpath, newpath string) error {
		calls.Add(1)
		_ = os.Remove(oldpath) // the scanner took it
		return os.Rename(oldpath, newpath)
	})

	body := []byte("#!/bin/sh\necho mimikatz\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.DisableArtifactCache = true
	set := stageOneArtifact(t, c, Artifact{
		Name: "mimikatz.exe", SHA256: digestOf(body), SizeBytes: int64(len(body)),
		Path: "/api/shelf/payload/mimikatz.exe", Dest: filepath.Join(root, "run-1", "mimikatz.exe"),
	})
	f := onlyFailure(t, set)
	if f.Code != "ARTIFACT_QUARANTINE_SUSPECTED" {
		t.Fatalf("code = %s, want ARTIFACT_QUARANTINE_SUSPECTED (detail: %s)", f.Code, f.Detail)
	}
	if got := calls.Load(); got != 4 {
		t.Errorf("rename attempts = %d, want 4 (1 + 3 retries)", got)
	}
}

// A scanner that holds the file briefly (sharing violation / access denied)
// and then releases it must not fail the run: the rename is retried.
func TestStage_TransientRenameLockRecovers(t *testing.T) {
	shortRenameRetry(t)
	var calls atomic.Int64
	withRename(t, func(oldpath, newpath string) error {
		if calls.Add(1) <= 2 {
			return &os.LinkError{Op: "rename", Old: oldpath, New: newpath, Err: fs.ErrPermission}
		}
		return os.Rename(oldpath, newpath)
	})

	body := []byte("#!/bin/sh\necho ok\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.DisableArtifactCache = true
	dest := filepath.Join(root, "run-1", "tool.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "tool.sh", SHA256: digestOf(body), SizeBytes: int64(len(body)),
		Path: "/api/shelf/payload/tool.sh", Dest: dest,
	})
	if !set.OK() {
		t.Fatalf("a transient lock failed staging: %+v", set.Failures)
	}
	if _, err := os.Stat(dest); err != nil {
		t.Fatalf("dest missing after recovered rename: %v", err)
	}
}

// Any OTHER rename error is still a plain write failure.
func TestStage_OtherRenameErrorStaysWriteFailed(t *testing.T) {
	shortRenameRetry(t)
	withRename(t, func(oldpath, newpath string) error {
		return &os.LinkError{Op: "rename", Old: oldpath, New: newpath, Err: errors.New("disk on fire")}
	})
	body := []byte("x")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.DisableArtifactCache = true
	set := stageOneArtifact(t, c, Artifact{
		Name: "x", SHA256: digestOf(body), Path: "/api/shelf/payload/x", Dest: filepath.Join(root, "run-1", "x"),
	})
	if f := onlyFailure(t, set); f.Code != codeWriteFailed {
		t.Fatalf("code = %s, want %s", f.Code, codeWriteFailed)
	}
}

// The frame for a suspected quarantine must say the step did not run and send
// the DC to the endpoint's prevention alerts — and must NOT assert it is "NOT a
// gap", because a quarantine is the customer's prevention working.
func TestReportStagingFailure_QuarantineFrameDoesNotPrejudgeCoverage(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", time.Millisecond)

	task := &Task{RunID: "run-1", ScenarioID: "SIM-EDR-006",
		Steps: []Step{{ID: "step-01", MitreTechnique: "T1003.001", Identity: "administrator"}}}
	set := &stagedSet{Server: rs.URL, Total: 1, Failures: []artifactFailure{{
		Artifact: Artifact{Name: "mimikatz.exe", Path: "/api/shelf/payload/mimikatz.exe",
			Dest: `C:\ProgramData\CortexSim\stage\mimikatz.exe`, Steps: []string{"step-01"}},
		Code:   "ARTIFACT_QUARANTINE_SUSPECTED",
		Detail: "rename failed after 3 retries",
	}}}
	c.reportStagingFailure(task, set)

	outs := rs.find("POST", "/output")
	if len(outs) < 2 {
		t.Fatalf("want a step frame and a summary frame, got %d", len(outs))
	}
	var posted struct {
		Output string `json:"output"`
	}
	if err := json.Unmarshal([]byte(outs[0].Body), &posted); err != nil {
		t.Fatal(err)
	}
	frame := strings.Join(strings.Fields(posted.Output), " ")
	want := "THIS STEP DID NOT RUN. The staged file was removed or locked after it was written, " +
		"which is consistent with endpoint protection quarantining it. Check the endpoint's " +
		"prevention alerts before reading this as either a CortexSim failure or a coverage gap."
	if !strings.Contains(frame, want) {
		t.Errorf("quarantine frame missing the required wording:\n%s", posted.Output)
	}
	if strings.Contains(frame, "NOT a gap") {
		t.Errorf("quarantine frame prejudges coverage with \"NOT a gap\":\n%s", posted.Output)
	}
	sum := decodeComplete(t, rs).Summary
	if strings.Contains(sum, "proves NOTHING") {
		t.Errorf("a suspected quarantine IS possible prevention evidence; summary must not say it proves nothing: %s", sum)
	}
}
