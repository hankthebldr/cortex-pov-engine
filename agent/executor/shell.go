// Package executor provides low-level shell command execution with output capture.
package executor

import (
	"bytes"
	"context"
	"os/exec"
	"sync/atomic"
	"syscall"
	"time"
)

// killGrace is how long a cancelled command's process group is given to exit on
// SIGTERM before it is escalated to SIGKILL.
const killGrace = 3 * time.Second

// RunCommand executes cmdStr via "sh -c" and captures stdout and stderr separately.
// A non-zero exit code is NOT treated as an error — it is returned as exitCode.
// err is only non-nil for failures that prevent execution from starting (e.g. exec not found).
//
// It is a thin wrapper around RunCommandCtx with a background (non-cancellable)
// context, preserved for existing call sites and tests.
func RunCommand(cmdStr string) (stdout, stderr string, exitCode int, err error) {
	return RunCommandCtx(context.Background(), cmdStr)
}

// RunCommandCtx executes cmdStr via "sh -c" and captures stdout and stderr
// separately, honouring ctx for cancellation.
//
// The command is launched in its own process group (Setpgid) so that when ctx is
// cancelled the WHOLE group — including children spawned by runuser/sudo/su and
// shell pipelines — can be signalled. On cancellation the group receives SIGTERM,
// is given killGrace to exit, then SIGKILL. A cancelled execution returns
// exitCode 130 (the SIGINT/operator-abort convention) and err == context.Canceled
// so the caller can treat it as a non-fatal "aborted" outcome rather than an
// execution failure.
//
// A non-zero exit code (from the command itself) is NOT treated as an error — it
// is returned as exitCode with a nil err. err is only non-nil for failures that
// prevent execution from starting (e.g. exec not found) or for cancellation.
func RunCommandCtx(ctx context.Context, cmdStr string) (stdout, stderr string, exitCode int, err error) {
	cmd := exec.Command("sh", "-c", cmdStr)
	// Run in a new process group so we can signal the whole tree on cancel.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf

	if startErr := cmd.Start(); startErr != nil {
		// Process never launched (e.g. /bin/sh missing) — a real execution error.
		return outBuf.String(), errBuf.String(), -1, startErr
	}

	// Watch ctx in a goroutine; on cancel, SIGTERM the process group then SIGKILL
	// after a grace period. waitDone stops the watcher once the command returns.
	// cancelled is read by the main goroutine after Wait() returns and written by
	// the watcher, so it must be accessed atomically.
	waitDone := make(chan struct{})
	var cancelled atomic.Bool
	go func() {
		select {
		case <-ctx.Done():
			cancelled.Store(true)
			killProcessGroup(cmd.Process.Pid)
		case <-waitDone:
		}
	}()

	runErr := cmd.Wait()
	close(waitDone)

	stdout = outBuf.String()
	stderr = errBuf.String()

	if cancelled.Load() {
		// Operator/abort-driven termination. Surface a stable, non-fatal signal.
		return stdout, stderr, 130, context.Canceled
	}

	if runErr != nil {
		if exitErr, ok := runErr.(*exec.ExitError); ok {
			if status, ok := exitErr.Sys().(syscall.WaitStatus); ok {
				if status.Signaled() {
					// Killed by a signal we did not initiate via ctx — convention 128+signal.
					exitCode = 128 + int(status.Signal())
				} else {
					exitCode = status.ExitStatus()
				}
			} else {
				exitCode = 1
			}
			// Non-zero exit is not an error from the caller's perspective.
			return stdout, stderr, exitCode, nil
		}
		// Real error (should be rare after a successful Start).
		return stdout, stderr, -1, runErr
	}

	return stdout, stderr, 0, nil
}

// killProcessGroup sends SIGTERM to the whole process group led by pid, waits a
// grace period, then SIGKILLs anything still alive. The negative pid targets the
// entire group (requires the child to have been started with Setpgid).
func killProcessGroup(pid int) {
	// Negative pid → signal the process group.
	_ = syscall.Kill(-pid, syscall.SIGTERM)
	time.Sleep(killGrace)
	_ = syscall.Kill(-pid, syscall.SIGKILL)
}
