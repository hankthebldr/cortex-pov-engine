//go:build !windows

package executor

// POSIX process control for the executor.
//
// Everything here relies on the process GROUP: a command is started with
// Setpgid so the kernel keeps its whole descendant tree addressable by a single
// negative-pid signal. That is what makes an operator abort actually stop a
// scenario rather than just the shell that launched it.

import (
	"fmt"
	"os/exec"
	"runtime"
	"sync"
	"syscall"
	"time"
)

// powerShellExePath is the interpreter name the chained PowerShell script names
// when it is rendered on a POSIX host. Only the tests render it here (they
// substitute a real pwsh path); Windows uses an absolute %SystemRoot% path.
func powerShellExePath() string { return powerShellExe }

// newShellCmd builds the per-step command: `sh -c <cmd>` in its own process
// group.
func newShellCmd(cmdStr string) *exec.Cmd {
	cmd := exec.Command("sh", "-c", cmdStr)
	// Run in a new process group so we can signal the whole tree on cancel.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	return cmd
}

// newShellCmdFor builds the per-step command for a step shell. POSIX has one
// interpreter; a step SimCore resolved to cmd or PowerShell cannot run here,
// and handing its text to sh would execute garbage that may still exit 0.
func newShellCmdFor(shell, cmdStr string) (*exec.Cmd, func(), error) {
	if shell != "" {
		return nil, func() {}, fmt.Errorf("step shell %q is not executable on %s (sh only)", shell, runtime.GOOS)
	}
	return newShellCmd(cmdStr), func() {}, nil
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

// normalizeStepStderr is the identity on POSIX: a step's stderr is reported
// byte-for-byte. Only a Windows host decodes PowerShell's CLIXML (clixml.go).
func normalizeStepStderr(s string) string { return s }

// stderrChunkFilter is the identity on POSIX; see the Windows twin.
func stderrChunkFilter(emit func(string)) (write func(string), flush func()) {
	return emit, func() {}
}

// chainStepScript renders one step for the anchor shell.
func chainStepScript(marker, wrappedCmd string) string {
	return posixChainStepScript(marker, wrappedCmd)
}

// chainStepScriptFor renders one step for the anchor shell under a step shell.
// See newShellCmdFor for why a non-POSIX shell is refused here.
func chainStepScriptFor(marker, stepID, shell, wrappedCmd string) (string, func(), error) {
	if shell != "" {
		return "", func() {}, fmt.Errorf("step shell %q is not executable on %s (sh only)", shell, runtime.GOOS)
	}
	return posixChainStepScript(marker, wrappedCmd), func() {}, nil
}

// treeKiller owns the right to tear a started command's whole tree down.
//
// POSIX holds the process GROUP id. A pgid cannot be reused while any member of
// the group is alive, so signalling it reaches exactly our descendants. Release
// marks the command reaped: a Kill that has not already started is refused
// after it, so nothing ever signals a group number the kernel may have handed
// to someone else once every member is gone.
type treeKiller struct {
	pid int

	mu       sync.Mutex
	killing  int
	released bool
}

// attachTreeKiller must be called right after cmd.Start().
func attachTreeKiller(cmd *exec.Cmd) *treeKiller {
	return &treeKiller{pid: cmd.Process.Pid}
}

// Kill sends SIGTERM to the whole process group, waits killGrace, then SIGKILLs
// anything still alive. A Kill already in progress finishes both legs even if
// Release is called meanwhile — the SIGKILL leg is what stops a tool that
// ignores SIGTERM.
func (k *treeKiller) Kill() {
	k.mu.Lock()
	if k.released {
		k.mu.Unlock()
		return
	}
	k.killing++
	k.mu.Unlock()
	defer func() {
		k.mu.Lock()
		k.killing--
		k.mu.Unlock()
	}()

	// Negative pid → signal the process group.
	_ = syscall.Kill(-k.pid, syscall.SIGTERM)
	time.Sleep(killGrace)
	_ = syscall.Kill(-k.pid, syscall.SIGKILL)
}

// Release is called once the command has been waited on.
func (k *treeKiller) Release() {
	k.mu.Lock()
	k.released = true
	k.mu.Unlock()
}

// sysKill is syscall.Kill. A var only so a test can record what is signalled.
var sysKill = syscall.Kill

// survivorPoll is how often reapSurvivors re-probes the group while it waits
// for SIGTERM to land.
const survivorPoll = 25 * time.Millisecond

// Outcomes reapSurvivors reports (stable text: the beacon logs it).
const (
	teardownTERM      = "process group stopped with SIGTERM"
	teardownKILL      = "process group stopped with SIGTERM, then SIGKILL after the grace period"
	teardownForbidden = "processes owned by another user could not be signalled (EPERM) and are still running"
)

// reapSurvivors is the end-of-chain teardown (RG-AGENT-01 / RG-AGENT-02): once
// the chain's anchor has exited and been reaped, stop whatever its steps left
// running in the anchor's process group. Pull mode runs no scenario cleanup, so
// this is the only teardown a chained run's background helpers ever get; without
// it a helper left by run N answers run N+1.
//
// POSIX tears the group down however the chain ended, so failed is unused here
// (it selects the Windows behaviour).
//
// The group is PROBED first (signal 0) and nothing is sent to an empty one. A
// pgid cannot be reused while any member is alive, so a non-empty group is
// still exactly our descendants; an empty group's number is free for the kernel
// to hand out again, and from the first ESRCH on this never signals it. SIGKILL
// is sent only if the group never read empty for the whole grace period.
//
// What is left, stated: between the anchor's reap and the first probe (and
// between two probes) a group that had ALREADY emptied could have its number
// recycled into an unrelated group, which would then probe as alive. That needs
// the pid space to wrap inside microseconds. Base had no probe at all — it sent
// SIGTERM to the number unconditionally and SIGKILL three seconds later.
//
// It returns "" when there was nothing to stop, else what was done.
func (k *treeKiller) reapSurvivors(failed bool) string {
	_ = failed
	k.mu.Lock()
	if k.released {
		k.mu.Unlock()
		return ""
	}
	k.killing++
	k.mu.Unlock()
	defer func() {
		k.mu.Lock()
		k.killing--
		k.mu.Unlock()
	}()

	switch sysKill(-k.pid, 0) {
	case nil:
	case syscall.EPERM:
		return teardownForbidden
	default: // ESRCH: nobody left
		return ""
	}
	_ = sysKill(-k.pid, syscall.SIGTERM)
	for deadline := time.Now().Add(killGrace); time.Now().Before(deadline); {
		time.Sleep(survivorPoll)
		switch sysKill(-k.pid, 0) {
		case nil:
			continue
		case syscall.EPERM:
			return teardownForbidden
		default:
			return teardownTERM
		}
	}
	_ = sysKill(-k.pid, syscall.SIGKILL)
	return teardownKILL
}
