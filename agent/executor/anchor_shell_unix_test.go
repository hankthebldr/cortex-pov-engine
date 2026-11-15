//go:build !windows

package executor

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The anchor rule (host behaviour preserved): the anchor is `sh` with argv[0]
// rewritten, UNLESS `sh` resolves to a multi-call binary (busybox / toybox),
// where argv[0] selects the applet. Only then: bash renamed if bash is on PATH,
// else sh un-renamed (ANCHOR NOT HONOURED).

func realBin(t *testing.T, name string) string {
	t.Helper()
	p, err := exec.LookPath(name)
	if err != nil {
		t.Skipf("no %s on PATH", name)
	}
	r, err := filepath.EvalSymlinks(p)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

// hostShIsReal skips when the host's own sh is already a multi-call binary
// (an alpine CI runner), where the "dash-like" fixture cannot be built.
func hostShIsReal(t *testing.T) string {
	t.Helper()
	sh := realBin(t, "sh")
	if b := filepath.Base(sh); b == "busybox" || b == "toybox" {
		t.Skip("host sh is a multi-call binary")
	}
	return sh
}

// dashLikePath: PATH holds sh -> the host's real (single-purpose) sh, plus bash.
func dashLikePath(t *testing.T) string {
	t.Helper()
	sh := hostShIsReal(t)
	bash := realBin(t, "bash")
	dir := t.TempDir()
	for name, target := range map[string]string{"sh": sh, "bash": bash} {
		if err := os.Symlink(target, filepath.Join(dir, name)); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", dir)
	return dir
}

// busyboxPath: PATH holds sh -> ./busybox and, when withBash, bash -> the host
// bash. ./busybox is a tiny script that execs the host sh, so it runs anywhere
// (a byte copy of a signed macOS /bin/sh is SIGKILLed); what the rule reads is
// only the resolved base name.
func busyboxPath(t *testing.T, withBash bool) string {
	t.Helper()
	sh := hostShIsReal(t)
	dir := t.TempDir()
	script := "#!" + sh + "\nexec " + sh + " \"$@\"\n"
	if err := os.WriteFile(filepath.Join(dir, "busybox"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(dir, "busybox"), filepath.Join(dir, "sh")); err != nil {
		t.Fatal(err)
	}
	if withBash {
		if err := os.Symlink(realBin(t, "bash"), filepath.Join(dir, "bash")); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", dir)
	return dir
}

func runEchoOK(t *testing.T, wantRenamed bool) *ChainSession {
	t.Helper()
	s, err := NewChainSession(context.Background(), "nginx")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	if s.AnchorRenamed() != wantRenamed {
		t.Fatalf("AnchorRenamed() = %v, want %v", s.AnchorRenamed(), wantRenamed)
	}
	out, _, rc, err := s.RunStep("echo ok")
	if err != nil || rc != 0 || strings.TrimSpace(out) != "ok" {
		t.Fatalf("step: out=%q rc=%d err=%v", out, rc, err)
	}
	return s
}

func assertCmdline(t *testing.T, s *ChainSession, prefix string) {
	t.Helper()
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", s.cmd.Process.Pid))
	if err != nil {
		return // no /proc (macOS)
	}
	if !strings.HasPrefix(string(raw), prefix) {
		t.Fatalf("anchor cmdline = %q, want prefix %q", raw, prefix)
	}
}

func TestNewAnchorCmd_RealShIsRenamedAndBashIsNotUsed(t *testing.T) {
	dir := dashLikePath(t)
	cmd, renamed := newAnchorCmd("nginx")
	if !renamed || cmd.Args[0] != "nginx" {
		t.Fatalf("renamed=%v Args[0]=%q, want true/nginx", renamed, cmd.Args[0])
	}
	if cmd.Path != filepath.Join(dir, "sh") {
		t.Fatalf("anchor binary = %q, want %q (sh, not bash)", cmd.Path, filepath.Join(dir, "sh"))
	}
	if !AnchorRenameSupported() {
		t.Fatal("AnchorRenameSupported() = false on a real sh")
	}
	assertCmdline(t, runEchoOK(t, true), "nginx")
}

func TestNewAnchorCmd_BusyboxShWithBashRenamesBash(t *testing.T) {
	dir := busyboxPath(t, true)
	cmd, renamed := newAnchorCmd("nginx")
	if !renamed || cmd.Args[0] != "nginx" {
		t.Fatalf("renamed=%v Args[0]=%q, want true/nginx", renamed, cmd.Args[0])
	}
	if cmd.Path != filepath.Join(dir, "bash") {
		t.Fatalf("anchor binary = %q, want %q", cmd.Path, filepath.Join(dir, "bash"))
	}
	if !AnchorRenameSupported() {
		t.Fatal("AnchorRenameSupported() = false with bash present")
	}
	assertCmdline(t, runEchoOK(t, true), "nginx")
}

func TestNewAnchorCmd_BusyboxShWithoutBashRunsUnrenamedSh(t *testing.T) {
	busyboxPath(t, false)
	cmd, renamed := newAnchorCmd("nginx")
	if renamed {
		t.Fatal("renamed = true: a busybox sh cannot be renamed and there is no bash")
	}
	if cmd.Args[0] != "sh" {
		t.Fatalf("Args[0] = %q, want sh", cmd.Args[0])
	}
	if AnchorRenameSupported() {
		t.Fatal("AnchorRenameSupported() = true on busybox sh without bash")
	}
	runEchoOK(t, false)
}

func TestNewAnchorCmd_NoAnchorRequestedIsPlainSh(t *testing.T) {
	dashLikePath(t)
	cmd, renamed := newAnchorCmd("")
	if renamed || cmd.Args[0] != "sh" {
		t.Fatalf("renamed=%v Args[0]=%q, want false/sh", renamed, cmd.Args[0])
	}
}

func TestAnchorRenameSupportedWith_FollowsItsSeams(t *testing.T) {
	never := func(a, b string) bool { return false }
	look := func(present ...string) func(string) (string, error) {
		return func(n string) (string, error) {
			for _, p := range present {
				if p == n {
					return "/bin/" + n, nil
				}
			}
			return "", errors.New("not found")
		}
	}
	resolve := func(m map[string]string) func(string) (string, error) {
		return func(p string) (string, error) {
			if r, ok := m[p]; ok {
				return r, nil
			}
			return p, nil
		}
	}
	cases := []struct {
		name    string
		present []string
		links   map[string]string
		want    bool
	}{
		{"dash sh, bash present", []string{"sh", "bash"}, map[string]string{"/bin/sh": "/usr/bin/dash"}, true},
		{"dash sh, no bash", []string{"sh"}, map[string]string{"/bin/sh": "/usr/bin/dash"}, true},
		{"busybox sh, bash present", []string{"sh", "bash"}, map[string]string{"/bin/sh": "/bin/busybox"}, true},
		{"busybox sh, no bash", []string{"sh"}, map[string]string{"/bin/sh": "/bin/busybox"}, false},
		{"toybox sh, no bash", []string{"sh"}, map[string]string{"/bin/sh": "/system/bin/toybox"}, false},
		{"no sh, bash present", []string{"bash"}, nil, true},
		{"no sh, no bash", nil, nil, false},
	}
	for _, c := range cases {
		if got := AnchorRenameSupportedWith(look(c.present...), resolve(c.links), never); got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}

// A hard-linked busybox: `sh` has no symlink to follow, so its resolved base
// name is `sh`. Only comparing it with `busybox`/`toybox` on PATH finds it.
func TestAnchorShellWith_HardLinkedBusyboxIsMultiCall(t *testing.T) {
	look := func(present ...string) func(string) (string, error) {
		return func(n string) (string, error) {
			for _, p := range present {
				if p == n {
					return "/bin/" + n, nil
				}
			}
			return "", errors.New("not found")
		}
	}
	plain := func(p string) (string, error) { return p, nil }
	same := func(a, b string) bool { return a == "/bin/sh" && b == "/bin/busybox" }
	notSame := func(a, b string) bool { return false }
	sameToybox := func(a, b string) bool { return a == "/bin/sh" && b == "/bin/toybox" }

	cases := []struct {
		name       string
		present    []string
		sameFile   func(a, b string) bool
		wantShell  string
		wantRename bool
	}{
		{"hardlinked busybox, bash present", []string{"sh", "bash", "busybox"}, same, "bash", true},
		{"hardlinked busybox, no bash", []string{"sh", "busybox"}, same, "sh", false},
		{"hardlinked toybox, no bash", []string{"sh", "toybox"}, sameToybox, "sh", false},
		{"distinct busybox on PATH (dash-like sh), bash present", []string{"sh", "bash", "busybox"}, notSame, "sh", true},
		{"no busybox on PATH", []string{"sh"}, same, "sh", true},
	}
	for _, c := range cases {
		shell, renamed := anchorShellWith(look(c.present...), plain, c.sameFile)
		if shell != c.wantShell || renamed != c.wantRename {
			t.Errorf("%s: got (%s, %v), want (%s, %v)", c.name, shell, renamed, c.wantShell, c.wantRename)
		}
	}
}

// A stat failure is "not the same file": the old name-only rule stands.
func TestSameFile_StatErrorIsNotTheSameFile(t *testing.T) {
	if SameFile("/nonexistent/a", "/nonexistent/b") {
		t.Fatal("SameFile reported two missing paths as the same file")
	}
}

// The real filesystem: `sh` is a HARD link to `busybox` (no symlink anywhere).
func TestNewAnchorCmd_HardLinkedBusyboxShWithoutBashRunsUnrenamedSh(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "busybox"), []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(filepath.Join(dir, "busybox"), filepath.Join(dir, "sh")); err != nil {
		t.Skipf("hard links unavailable here: %v", err)
	}
	t.Setenv("PATH", dir)

	if shell, renamed := anchorShellWith(exec.LookPath, filepath.EvalSymlinks, SameFile); shell != "sh" || renamed {
		t.Fatalf("anchorShellWith = (%s, %v), want (sh, false)", shell, renamed)
	}
	cmd, renamed := newAnchorCmd("nginx")
	if renamed {
		t.Fatal("renamed = true: a hard-linked busybox sh cannot be renamed and there is no bash")
	}
	if cmd.Args[0] != "sh" {
		t.Fatalf("Args[0] = %q, want sh", cmd.Args[0])
	}
	if AnchorRenameSupported() {
		t.Fatal("AnchorRenameSupported() = true on a hard-linked busybox sh without bash")
	}
}
