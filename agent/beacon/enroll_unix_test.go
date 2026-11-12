//go:build !windows

package beacon

// POSIX-only agent-id store tests (CI-04): each one reads or sets POSIX
// permission bits — a 0600 agent-id in a 0700 directory, a 0500 directory that
// refuses writes, a 0000 file that refuses reads. Windows reports synthetic
// mode bits and os.Chmod there only toggles the read-only attribute, so none of
// these can hold on a Windows host. The platform-neutral LoadOrEnroll and
// writeIDFile tests stay in enroll_test.go.

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadOrEnrollWritesIDWithMode0600(t *testing.T) {
	state := filepath.Join(t.TempDir(), "nested", "state")
	calls := 0
	id, reused, err := LoadOrEnroll(state, func() (string, error) { calls++; return "pod-1", nil })
	if err != nil || id != "pod-1" || reused || calls != 1 {
		t.Fatalf("got %q reused=%v calls=%d err=%v", id, reused, calls, err)
	}
	fi, err := os.Stat(filepath.Join(state, "agent-id"))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o600 {
		t.Errorf("file mode = %v, want 0600", fi.Mode().Perm())
	}
	di, _ := os.Stat(state)
	if di.Mode().Perm() != 0o700 {
		t.Errorf("dir mode = %v, want 0700", di.Mode().Perm())
	}
}

func TestLoadOrEnrollUnwritableDirFailsBeforeEnrolling(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores directory permissions")
	}
	parent := t.TempDir()
	state := filepath.Join(parent, "state")
	os.Mkdir(state, 0o500) // readable, not writable
	defer os.Chmod(state, 0o700)
	_, _, err := LoadOrEnroll(state, func() (string, error) {
		t.Fatal("enroll must not be called when the state dir is unwritable")
		return "", nil
	})
	var se *StateDirError
	if !errors.As(err, &se) {
		t.Fatalf("err = %v, want *StateDirError", err)
	}

	// A path under a regular file can never be created.
	blocker := filepath.Join(parent, "file")
	os.WriteFile(blocker, nil, 0o600)
	_, _, err = LoadOrEnroll(filepath.Join(blocker, "sub"), func() (string, error) {
		t.Fatal("enroll must not be called when the state dir cannot be created")
		return "", nil
	})
	if !errors.As(err, &se) {
		t.Fatalf("err = %v, want *StateDirError", err)
	}
}

func TestLoadOrEnrollUnreadableIDFileFailsBeforeEnrolling(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores file permissions")
	}
	state := t.TempDir()
	p := filepath.Join(state, "agent-id")
	os.WriteFile(p, []byte("pod-1\n"), 0o000)
	defer os.Chmod(p, 0o600)
	_, _, err := LoadOrEnroll(state, func() (string, error) {
		t.Fatal("enroll must not be called when the stored id cannot be read")
		return "", nil
	})
	var se *StateDirError
	if !errors.As(err, &se) {
		t.Fatalf("err = %v, want *StateDirError", err)
	}
}

func TestWriteIDFileSuccessContentAndMode(t *testing.T) {
	state := t.TempDir()
	if err := writeIDFile(state, "pod-9"); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(state, "agent-id"))
	if string(b) != "pod-9\n" {
		t.Errorf("content = %q", b)
	}
	if fi, _ := os.Stat(filepath.Join(state, "agent-id")); fi.Mode().Perm() != 0o600 {
		t.Errorf("mode = %v", fi.Mode().Perm())
	}
	if entries, _ := os.ReadDir(state); len(entries) != 1 {
		t.Errorf("stale temp file: %v", entries)
	}
}
