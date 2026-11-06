package executor

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

// A logical name no real host will ever have installed, used to prove the
// "genuinely absent" path is detected honestly rather than always finding
// something.
const noSuchInterpreter = "cortexsim-no-such-interpreter-xyz"

// writeFakeBinary puts an executable named name (resolvable by exec.LookPath)
// in dir: a `#!/bin/sh` script on POSIX, a `name.cmd` shim on Windows — which
// LookPath finds through PATHEXT, exactly as it finds a real python3.exe
// (CI-04: an extensionless shebang file is not executable on Windows).
func writeFakeBinary(t *testing.T, dir, name, prints string) string {
	t.Helper()
	path, body := filepath.Join(dir, name), "#!/bin/sh\necho "+prints+"\n"
	if runtime.GOOS == "windows" {
		path, body = path+".cmd", "@echo "+prints+"\r\n"
	}
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// alwaysPresentBinary is a binary every host of this OS family has on PATH and
// that is NOT in the alias table, so it can only resolve exactly.
func alwaysPresentBinary() string {
	if runtime.GOOS == "windows" {
		return "cmd"
	}
	return "sh"
}

func TestResolveInterpreter_ExactMatch(t *testing.T) {
	// sh (cmd on Windows) is not in the alias table at all, so this only passes
	// via the exact-match branch — proves ResolveInterpreter checks the literal
	// name first, not just the alias table.
	name := alwaysPresentBinary()
	check := ResolveInterpreter(name)
	if !check.Found || !check.Exact {
		t.Fatalf("expected %s to resolve exactly on this host, got %+v", name, check)
	}
	if check.Resolved != name {
		t.Errorf("Resolved = %q, want %q", check.Resolved, name)
	}
}

func TestResolveInterpreter_AliasFallback(t *testing.T) {
	// Build a scratch PATH containing ONLY a binary named "python3" (never
	// "python" itself) and confirm ResolveInterpreter("python") finds it via
	// the alias table rather than reporting absent.
	dir := t.TempDir()
	writeFakeBinary(t, dir, "python3", "fake")
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

func TestInstallPackageCommand_UnknownLogicalRefuses(t *testing.T) {
	if _, ok := InstallPackageCommand(noSuchInterpreter); ok {
		t.Fatalf("InstallPackageCommand must refuse an interpreter with no known package mapping")
	}
}

func TestInstallPackageCommand_NoPackageManagerOnPATHRefuses(t *testing.T) {
	dir := t.TempDir() // empty — no apt-get/dnf/yum/apk on this PATH
	t.Setenv("PATH", dir)

	if _, ok := InstallPackageCommand("python"); ok {
		t.Fatalf("InstallPackageCommand must refuse when no supported package manager is present")
	}
}

func TestInstallPackageCommand_DetectsFirstAvailableManager(t *testing.T) {
	dir := t.TempDir()
	writeFakeBinary(t, dir, "apt-get", "fake")
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
	writeFakeBinary(t, dir, "python3", "fake")
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
