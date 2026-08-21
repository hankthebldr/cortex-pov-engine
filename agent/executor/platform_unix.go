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
// command line reflects the intended CGO image (e.g. "nginx"); the executed
// binary is still /bin/sh.
func newAnchorCmd(cgoImage string) *exec.Cmd {
	cmd := exec.Command("sh")
	if cgoImage != "" {
		// Rewrite argv[0] only (Args[0]); the binary remains sh.
		cmd.Args[0] = cgoImage
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
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
