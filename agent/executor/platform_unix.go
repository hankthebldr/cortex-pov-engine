//go:build !windows

package executor

// POSIX process control for the executor.
//
// Everything here relies on the process GROUP: a command is started with
// Setpgid so the kernel keeps its whole descendant tree addressable by a single
// negative-pid signal. That is what makes an operator abort actually stop a
// scenario rather than just the shell that launched it.

import (
	"os/exec"
	"path/filepath"
	"syscall"
	"time"
)

// newShellCmd builds the per-step command: `sh -c <cmd>` in its own process
// group.
func newShellCmd(cmdStr string) *exec.Cmd {
	cmd := exec.Command("sh", "-c", cmdStr)
	// Run in a new process group so we can signal the whole tree on cancel.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
}

// newAnchorCmd builds the persistent anchor shell for a ChainSession.
//
// cgoImage, when non-empty, is used as the anchor's argv[0] so the process's
// command line reflects the intended CGO image (e.g. "nginx"). The anchor is
// `sh` renamed — the behaviour every host beacon has always had (dash on
// Debian/Ubuntu) — UNLESS `sh` resolves to a multi-call binary (busybox,
// toybox), where argv[0] selects the APPLET and an unknown name dies with
// "applet not found". Only then: bash renamed if bash is on PATH, else plain sh
// un-renamed (renamed = false), which the caller records as ANCHOR NOT HONOURED.
func newAnchorCmd(cgoImage string) (cmd *exec.Cmd, renamed bool) {
	shell := "sh"
	if cgoImage != "" {
		shell, renamed = anchorShellWith(exec.LookPath, filepath.EvalSymlinks, SameFile)
	}
	cmd = exec.Command(shell)
	if renamed {
		cmd.Args[0] = cgoImage
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd, renamed
}

// multiCallShells are the binaries whose behaviour is chosen by argv[0].
var multiCallShells = map[string]bool{"busybox": true, "toybox": true}

// anchorShellWith picks the anchor binary and whether it can carry a renamed
// argv[0]. `sh` that is a single-purpose shell is renamed. `sh` that is a
// multi-call binary, or that is not on PATH, falls back to bash renamed, else
// to `sh` un-renamed. `sh` is multi-call when its symlink-resolved base name is
// busybox/toybox, OR when it is the very same file as a `busybox`/`toybox` on
// PATH (a hard-linked applet has no symlink to follow, so its resolved name is
// just `sh`). A stat failure in sameFile means "not the same file".
func anchorShellWith(
	lookPath func(string) (string, error),
	evalSymlinks func(string) (string, error),
	sameFile func(a, b string) bool,
) (shell string, renamed bool) {
	resolve := func(p string) string {
		if r, err := evalSymlinks(p); err == nil {
			return r
		}
		return p
	}
	if p, err := lookPath("sh"); err == nil {
		resolved := resolve(p)
		multiCall := multiCallShells[filepath.Base(resolved)]
		for name := range multiCallShells {
			if multiCall {
				break
			}
			if bp, berr := lookPath(name); berr == nil && sameFile(resolved, resolve(bp)) {
				multiCall = true
			}
		}
		if !multiCall {
			return "sh", true
		}
	}
	if _, err := lookPath("bash"); err == nil {
		return "bash", true
	}
	return "sh", false
}

// AnchorRenameSupportedWith reports whether the anchor can carry a scenario's
// cgo_anchor name on this host: sh is a single-purpose shell, or bash exists.
func AnchorRenameSupportedWith(
	lookPath func(string) (string, error),
	evalSymlinks func(string) (string, error),
	sameFile func(a, b string) bool,
) bool {
	_, renamed := anchorShellWith(lookPath, evalSymlinks, sameFile)
	return renamed
}

// AnchorRenameSupported is AnchorRenameSupportedWith over the real PATH.
func AnchorRenameSupported() bool {
	return AnchorRenameSupportedWith(exec.LookPath, filepath.EvalSymlinks, SameFile)
}

// anchorExitScript is what Close() writes to end the anchor cleanly.
const anchorExitScript = "exit\n"

// chainStepScript renders one step for the anchor shell.
func chainStepScript(marker, wrappedCmd string) string {
	return posixChainStepScript(marker, wrappedCmd)
}

// killProcessTree sends SIGTERM to the whole process group led by pid, waits a
// grace period, then SIGKILLs anything still alive. The negative pid targets the
// entire group (requires the child to have been started with Setpgid).
func killProcessTree(pid int) {
	// Negative pid → signal the process group.
	_ = syscall.Kill(-pid, syscall.SIGTERM)
	time.Sleep(killGrace)
	_ = syscall.Kill(-pid, syscall.SIGKILL)
}
