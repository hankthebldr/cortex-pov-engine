//go:build !windows

package executor

// The PATH-shim mechanism is POSIX-only by design: it is a symlink named after
// the logical interpreter, and resolveRuntimeDeps (beacon) never builds one on
// Windows. Windows twin: TestResolveInterpreter_WindowsFindsCmdAliases.

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// I3 — os.MkdirTemp defaults to 0700, owned by whichever euid the beacon
// process runs as (root — the systemd unit declares no `User=`). A step that
// runs as a different, unprivileged identity (runuser -l 'www-data') cannot
// TRAVERSE a 0700 root-owned directory, so it can never reach the shim even
// though the step's own output asserts "PATH-shimmed". This is latent on the
// current corpus (its only `requires_interpreters` declaration is
// identity: root) but goes live the moment anyone declares it on a non-root
// step. World execute+read (0755) is what makes the directory traversable
// and listable by any identity while the symlink inside still only points
// at a binary that was already resolvable on this host.
func TestNewInterpreterShim_DirIsWorldTraversable(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "python3")
	if err := os.WriteFile(target, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}

	shim, err := NewInterpreterShim("python", target)
	if err != nil {
		t.Fatalf("NewInterpreterShim: %v", err)
	}
	defer shim.Cleanup()

	info, err := os.Stat(shim.Dir)
	if err != nil {
		t.Fatalf("stat shim dir: %v", err)
	}
	perm := info.Mode().Perm()
	if perm&0o755 != 0o755 {
		t.Fatalf("expected shim dir %s to be at least 0755 (world-traversable so a non-root step "+
			"identity can reach it), got %v (this is the I3 defect: os.MkdirTemp's default 0700 locks "+
			"out any identity other than the beacon's own euid)", shim.Dir, perm)
	}
}

func TestNewInterpreterShim_ExposesBinaryUnderLogicalName(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "python3")
	script := "#!/bin/sh\necho SHIM_OK\n"
	if err := os.WriteFile(target, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}

	shim, err := NewInterpreterShim("python", target)
	if err != nil {
		t.Fatalf("NewInterpreterShim: %v", err)
	}
	defer shim.Cleanup()

	// The shim directory must contain a binary literally named "python" that,
	// when invoked, runs the aliased target.
	cmd := exec.Command(filepath.Join(shim.Dir, "python"))
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("running shimmed python failed: %v (output: %s)", err, out)
	}
	if got := string(out); got != "SHIM_OK\n" {
		t.Errorf("shimmed python output = %q, want %q", got, "SHIM_OK\n")
	}
}

func TestInterpreterShim_CleanupRemovesDir(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "python3")
	if err := os.WriteFile(target, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	shim, err := NewInterpreterShim("python", target)
	if err != nil {
		t.Fatal(err)
	}
	shimDir := shim.Dir
	shim.Cleanup()
	if _, statErr := os.Stat(shimDir); !os.IsNotExist(statErr) {
		t.Errorf("expected shim dir %q to be removed after Cleanup, stat err = %v", shimDir, statErr)
	}
	// Safe to call twice / on the returned (now-empty) receiver.
	shim.Cleanup()
}
