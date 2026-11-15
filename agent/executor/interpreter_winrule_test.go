package executor

// BEACON-13 — the Windows interpreter rules, driven from any host through the
// interpreterGOOS seam. What needs a real Windows host (PATHEXT resolution of
// a .cmd inside a WindowsApps directory) is interpreter_windows_test.go.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func asWindowsInterpreterHost(t *testing.T) {
	t.Helper()
	old := interpreterGOOS
	interpreterGOOS = "windows"
	t.Cleanup(func() { interpreterGOOS = old })
}

func TestIsAppExecutionAlias(t *testing.T) {
	for path, want := range map[string]bool{
		`C:\Users\dc\AppData\Local\Microsoft\WindowsApps\python.exe`:  true,
		`C:\Users\dc\AppData\Local\Microsoft\WindowsApps\python3.exe`: true,
		`c:\users\dc\appdata\local\microsoft\windowsapps\PYTHON.EXE`:  true, // case-insensitive
		`C:/Users/dc/AppData/Local/Microsoft/WindowsApps/python.exe`:  true, // either separator
		// A real interpreter, wherever it lives, is not an alias.
		`C:\Python312\python.exe`:                              false,
		`C:\Program Files\Python312\python.exe`:                false,
		`C:\Users\dc\AppData\Local\Programs\Python\python.exe`: false,
		// The package store itself is not the alias directory (no `Microsoft\` parent).
		`C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python3.12.exe`: false,
		// Only a DIRECT child of ...\Microsoft\WindowsApps counts.
		`C:\Users\dc\AppData\Local\Microsoft\WindowsApps\sub\python.exe`: false,
		`C:\Users\dc\AppData\Local\Microsoft\WindowsAppsX\python.exe`:    false,
		`python.exe`: false,
		``:           false,
	} {
		if got := IsAppExecutionAlias(path); got != want {
			t.Errorf("IsAppExecutionAlias(%q) = %v, want %v", path, got, want)
		}
	}
}

// storeAliasDir makes a ...\Microsoft\WindowsApps directory holding
// "executables" with the given names and returns it.
func storeAliasDir(t *testing.T, names ...string) string {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "AppData", "Local", "Microsoft", "WindowsApps")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, n := range names {
		writeFakeBinary(t, dir, n, "STORE_STUB")
	}
	return dir
}

// The Store stub is found by LookPath like any executable. On Windows it must
// not count as an interpreter, and the check must say what it skipped.
func TestResolveInterpreter_WindowsRuleSkipsAnAppExecutionAlias(t *testing.T) {
	asWindowsInterpreterHost(t)
	apps := storeAliasDir(t, "python", "python3")
	t.Setenv("PATH", apps)

	check := ResolveInterpreter("python")
	if check.Found {
		t.Fatalf("the Microsoft Store alias was accepted as python: %+v", check)
	}
	if !strings.Contains(check.AppExecAlias, "WindowsApps") {
		t.Fatalf("AppExecAlias = %q — the check must record the alias it refused to count", check.AppExecAlias)
	}
	if got := AvailableLogicalNames(); len(got) != 0 {
		t.Fatalf("AvailableLogicalNames() = %v — the beacon would advertise the stub to SimCore", got)
	}
	adv := strings.Join(InterpreterAdvisories(), "\n")
	if !strings.Contains(adv, "App Execution Alias") || !strings.Contains(adv, "python") {
		t.Errorf("no advisory explains the unadvertised python: %q", adv)
	}
}

// A real interpreter further along PATH is still found, and the skipped alias
// is still reported.
func TestResolveInterpreter_WindowsRuleFindsARealInterpreterPastTheAlias(t *testing.T) {
	asWindowsInterpreterHost(t)
	apps := storeAliasDir(t, "python3")
	real := t.TempDir()
	writeFakeBinary(t, real, "python", "REAL")
	t.Setenv("PATH", apps+string(os.PathListSeparator)+real)

	check := ResolveInterpreter("python")
	if !check.Found || !check.Exact || IsAppExecutionAlias(check.Path) {
		t.Fatalf("the real python past the alias directory was not resolved: %+v", check)
	}
}

// Off Windows the directory name means nothing: the same layout resolves.
func TestResolveInterpreter_HostRuleIgnoresTheWindowsAppsName(t *testing.T) {
	if interpreterGOOS == "windows" {
		t.Skip("this is the non-Windows rule")
	}
	apps := storeAliasDir(t, "python3")
	t.Setenv("PATH", apps)
	if check := ResolveInterpreter("python"); !check.Found || check.AppExecAlias != "" {
		t.Fatalf("a POSIX host skipped %s because of its directory name: %+v", apps, check)
	}
}

// Windows has no alias shim, so an interpreter found only under an alias is not
// advertised there; POSIX (which shims it) still advertises it.
func TestAvailableLogicalNames_AliasOnlyIsAdvertisedOnlyWhereItCanBeShimmed(t *testing.T) {
	dir := t.TempDir()
	writeFakeBinary(t, dir, "python3", "ALIAS")
	t.Setenv("PATH", dir)

	if interpreterGOOS != "windows" {
		if got := AvailableLogicalNames(); len(got) != 1 || got[0] != "python" {
			t.Fatalf("POSIX rule: AvailableLogicalNames() = %v, want [python] (python3 is shimmed as python)", got)
		}
		if adv := InterpreterAdvisories(); len(adv) != 0 {
			t.Fatalf("POSIX rule: unexpected advisories %v", adv)
		}
	}

	asWindowsInterpreterHost(t)
	if PathShimSupported() {
		t.Fatal("PathShimSupported() is true under the Windows rule")
	}
	if got := AvailableLogicalNames(); len(got) != 0 {
		t.Fatalf("Windows rule: AvailableLogicalNames() = %v — python is only python3 here and cannot be shimmed", got)
	}
	adv := strings.Join(InterpreterAdvisories(), "\n")
	if !strings.Contains(adv, "python3") || !strings.Contains(adv, "POSIX-only") {
		t.Errorf("Windows rule: no advisory explains the alias-only python: %q", adv)
	}

	writeFakeBinary(t, dir, "python", "EXACT")
	if got := AvailableLogicalNames(); len(got) != 1 || got[0] != "python" {
		t.Fatalf("Windows rule: AvailableLogicalNames() = %v, want [python] once python itself is on PATH", got)
	}
}
