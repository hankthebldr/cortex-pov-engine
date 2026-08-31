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

// Previously this only compared len(DetectInterpreters()) against
// len(KnownLogicalInterpreters()) — tautological, since DetectInterpreters is
// implemented by iterating KnownLogicalInterpreters(): the length can never
// disagree with itself no matter what ResolveInterpreter actually returns.
// This version pins a deterministic PATH with exactly one interpreter present
// (python, and only via its python3 alias) and asserts DetectInterpreters
// reflects THAT real, specific PATH shape — it fails if DetectInterpreters
// stops calling ResolveInterpreter, resolves the wrong logical name, or
// reports something present that genuinely is not.
func TestDetectInterpreters_CoversKnownRoster(t *testing.T) {
	dir := t.TempDir()
	fake := filepath.Join(dir, "python3")
	if err := os.WriteFile(fake, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir) // ONLY python3 exists — perl/ruby/node are genuinely absent

	checks := DetectInterpreters()
	want := KnownLogicalInterpreters()
	if len(checks) != len(want) {
		t.Fatalf("DetectInterpreters returned %d entries, want %d (one per known logical name)",
			len(checks), len(want))
	}

	byLogical := make(map[string]InterpreterCheck, len(checks))
	for _, c := range checks {
		byLogical[c.Logical] = c
	}
	for _, logical := range want {
		if _, ok := byLogical[logical]; !ok {
			t.Fatalf("DetectInterpreters is missing logical name %q from the known roster: %+v", logical, checks)
		}
	}

	python, ok := byLogical["python"]
	if !ok || !python.Found || python.Exact || python.Resolved != "python3" {
		t.Errorf("expected python to resolve via the python3 alias on this scoped PATH, got %+v (present=%v)", python, ok)
	}
	for _, logical := range []string{"perl", "ruby", "node"} {
		if c := byLogical[logical]; c.Found {
			t.Errorf("expected %q to be reported ABSENT on an interpreter-empty PATH, got %+v", logical, c)
		}
	}
}
