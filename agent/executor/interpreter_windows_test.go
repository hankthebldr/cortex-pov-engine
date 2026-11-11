//go:build windows

package executor

// Windows twin of interpreter_unix_test.go (CI-04). The PATH-shim mechanism is
// POSIX-only (resolveRuntimeDeps never builds one on Windows); what Windows
// must get right is RESOLUTION through PATHEXT, which is what the beacon
// advertises at registration. Run only by the windows-native CI job.

import (
	"os/exec"
	"strings"
	"testing"
)

func TestResolveInterpreter_WindowsFindsCmdAliases(t *testing.T) {
	dir := t.TempDir()
	writeFakeBinary(t, dir, "python3", "WIN_ALIAS_OK")
	t.Setenv("PATH", dir)

	check := ResolveInterpreter("python")
	if !check.Found || check.Exact || check.Resolved != "python3" {
		t.Fatalf("python3.cmd alias not resolved through PATHEXT: %+v", check)
	}
	if !strings.HasSuffix(strings.ToLower(check.Path), `\python3.cmd`) {
		t.Fatalf("resolved path %q is not the .cmd shim", check.Path)
	}
	out, err := exec.Command(check.Path).CombinedOutput()
	if err != nil || !strings.Contains(string(out), "WIN_ALIAS_OK") {
		t.Fatalf("running the resolved shim: out=%q err=%v", out, err)
	}
	// BEACON-13: found, but only under an alias, and Windows has no PATH shim —
	// so it is NOT advertised to SimCore as "python".
	if got := AvailableLogicalNames(); len(got) != 0 {
		t.Fatalf("AvailableLogicalNames() = %v with only python3.cmd on PATH, want none", got)
	}
}

// BEACON-13 on a real Windows host: a python.cmd that PATHEXT resolves inside a
// ...\Microsoft\WindowsApps directory (where the Microsoft Store stub lives) is
// not counted, the refusal is recorded, and nothing is advertised.
func TestResolveInterpreter_WindowsHostSkipsTheStoreAliasDirectory(t *testing.T) {
	if interpreterGOOS != "windows" || PathShimSupported() {
		t.Fatalf("interpreterGOOS = %q, PathShimSupported = %v on a Windows build", interpreterGOOS, PathShimSupported())
	}
	apps := storeAliasDir(t, "python", "python3")
	t.Setenv("PATH", apps)

	check := ResolveInterpreter("python")
	if check.Found {
		t.Fatalf("an App Execution Alias path was accepted as python: %+v", check)
	}
	if !strings.HasSuffix(strings.ToLower(check.AppExecAlias), `\microsoft\windowsapps\python.cmd`) {
		t.Fatalf("AppExecAlias = %q, want the skipped ...\\Microsoft\\WindowsApps\\python.cmd", check.AppExecAlias)
	}
	if got := AvailableLogicalNames(); len(got) != 0 {
		t.Fatalf("AvailableLogicalNames() = %v — the stub would be advertised", got)
	}
}
