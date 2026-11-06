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
}
