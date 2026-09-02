// Package executor provides low-level shell command execution with output capture.
package executor

import (
	"bytes"
	"context"
	"os/exec"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// killGrace is how long a cancelled command's process tree is given to exit
// between the two termination sweeps (POSIX: SIGTERM before SIGKILL; Windows:
// the first TerminateProcess sweep before the re-snapshot sweep).
const killGrace = 3 * time.Second

// OutputChunkFunc receives output chunks in real-time as they are produced.
type OutputChunkFunc func(chunk string, isStderr bool)

type streamWriter struct {
	buf     *bytes.Buffer
	onChunk func(string)
	mu      sync.Mutex
}

func (sw *streamWriter) Write(p []byte) (int, error) {
	sw.mu.Lock()
	defer sw.mu.Unlock()
	n, err := sw.buf.Write(p)
	if sw.onChunk != nil && len(p) > 0 {
		sw.onChunk(string(p))
	}
	return n, err
}

// RunCommand executes cmdStr via the host shell and captures stdout and stderr separately.
// A non-zero exit code is NOT treated as an error — it is returned as exitCode.
// err is only non-nil for failures that prevent execution from starting (e.g. exec not found).
//
// It is a thin wrapper around RunCommandCtx with a background (non-cancellable)
// context, preserved for existing call sites and tests.
func RunCommand(cmdStr string) (stdout, stderr string, exitCode int, err error) {
	return RunCommandCtx(context.Background(), cmdStr)
}

// RunCommandCtx executes cmdStr via the host shell and captures output, honouring ctx.
func RunCommandCtx(ctx context.Context, cmdStr string) (stdout, stderr string, exitCode int, err error) {
	return RunCommandCtxStream(ctx, cmdStr, nil)
}

// RunCommandCtxStream executes cmdStr via the host shell (`sh -c` on POSIX,
// PowerShell on Windows — see the platform_*.go files) and captures stdout and
// stderr separately, honouring ctx for cancellation and streaming chunks to onChunk if non-nil.
func RunCommandCtxStream(ctx context.Context, cmdStr string, onChunk OutputChunkFunc) (stdout, stderr string, exitCode int, err error) {
	cmd := newShellCmd(cmdStr)

	var outBuf, errBuf bytes.Buffer
	if onChunk != nil {
		cmd.Stdout = &streamWriter{buf: &outBuf, onChunk: func(s string) { onChunk(s, false) }}
		cmd.Stderr = &streamWriter{buf: &errBuf, onChunk: func(s string) { onChunk(s, true) }}
	} else {
		cmd.Stdout = &outBuf
		cmd.Stderr = &errBuf
	}

	if startErr := cmd.Start(); startErr != nil {
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
			killProcessTree(cmd.Process.Pid)
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
