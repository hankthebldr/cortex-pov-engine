package executor

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

// A logical name no real host will ever have installed, used to prove the
// "genuinely absent" path is detected honestly rather than always finding
// something.
const noSuchInterpreter = "cortexsim-no-such-interpreter-xyz"

func TestResolveInterpreter_ExactMatch(t *testing.T) {
	// "sh" is not in the alias table at all, so this only passes via the
	// exact-match branch — proves ResolveInterpreter checks the literal name
	// first, not just the alias table.
	check := ResolveInterpreter("sh")
	if !check.Found || !check.Exact {
		t.Fatalf("expected sh to resolve exactly on any POSIX test host, got %+v", check)
	}
	if check.Resolved != "sh" {
		t.Errorf("Resolved = %q, want %q", check.Resolved, "sh")
	}
}

func TestResolveInterpreter_AliasFallback(t *testing.T) {
	// Build a scratch PATH containing ONLY a binary named "python3" (never
	// "python" itself) and confirm ResolveInterpreter("python") finds it via
	// the alias table rather than reporting absent.
	dir := t.TempDir()
	fake := filepath.Join(dir, "python3")
	if err := os.WriteFile(fake, []byte("#!/bin/sh\necho fake\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)

	check := ResolveInterpreter("python")
	if !check.Found {
		t.Fatalf("expected python to resolve via python3 alias, got %+v", check)
	}
	if check.Exact {
		t.Errorf("expected an ALIASED match (python3, not python), got Exact=true: %+v", check)
	}
	if check.Resolved != "python3" {
		t.Errorf("Resolved = %q, want %q", check.Resolved, "python3")
	}
}

// This is the case the whole feature exists for: a host that genuinely has
// NEITHER python nor any alias installed. ResolveInterpreter must report
// Found=false — not fabricate a match.
func TestResolveInterpreter_GenuinelyAbsent(t *testing.T) {
	dir := t.TempDir() // empty — no binaries at all
	t.Setenv("PATH", dir)

	check := ResolveInterpreter("python")
	if check.Found {
		t.Fatalf("expected python to be reported absent on an empty PATH, got %+v", check)
	}
}

func TestResolveInterpreter_UnknownLogicalStillChecksItsOwnName(t *testing.T) {
	if ResolveInterpreter(noSuchInterpreter).Found {
		t.Fatalf("a fabricated interpreter name must never resolve")
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

func TestInstallPackageCommand_UnknownLogicalRefuses(t *testing.T) {
	if _, ok := InstallPackageCommand(noSuchInterpreter); ok {
		t.Fatalf("InstallPackageCommand must refuse an interpreter with no known package mapping")
	}
}

func TestInstallPackageCommand_NoPackageManagerOnPATHRefuses(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("package-manager detection is POSIX-scoped")
	}
	dir := t.TempDir() // empty — no apt-get/dnf/yum/apk on this PATH
	t.Setenv("PATH", dir)

	if _, ok := InstallPackageCommand("python"); ok {
		t.Fatalf("InstallPackageCommand must refuse when no supported package manager is present")
	}
}

func TestInstallPackageCommand_DetectsFirstAvailableManager(t *testing.T) {
	dir := t.TempDir()
	fake := filepath.Join(dir, "apt-get")
	if err := os.WriteFile(fake, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)

	cmd, ok := InstallPackageCommand("python")
	if !ok {
		t.Fatalf("expected InstallPackageCommand to find the fake apt-get")
	}
	if cmd == "" {
		t.Errorf("expected a non-empty install command")
	}
}

func TestDetectInterpreters_CoversKnownRoster(t *testing.T) {
	checks := DetectInterpreters()
	if len(checks) != len(KnownLogicalInterpreters()) {
		t.Fatalf("DetectInterpreters returned %d entries, want %d (one per known logical name)",
			len(checks), len(KnownLogicalInterpreters()))
	}
}
