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

// Termination values reported in Outcome.Termination.
const (
	TerminationExited   = "exited"   // process ended on its own (any exit status)
	TerminationSignaled = "signaled" // process was killed by a signal we did not initiate
	TerminationAborted  = "aborted"  // ctx cancelled: operator/abort-driven kill
)

// Outcome is the full result of one command, including HOW it ended. ExitCode
// alone cannot tell a step that ran `exit 137` from one killed by SIGKILL (both
// read 137); Termination and Signal carry that distinction. On Windows, which
// has no signal concept, Termination is "exited" or "aborted" and Signal is 0.
type Outcome struct {
	Stdout      string
	Stderr      string
	ExitCode    int
	Termination string
	Signal      int
}

// RunCommandCtxStream executes cmdStr via the host shell and returns the four
// values it always has; it is a thin wrapper over RunCommandCtxStreamOutcome.
func RunCommandCtxStream(ctx context.Context, cmdStr string, onChunk OutputChunkFunc) (stdout, stderr string, exitCode int, err error) {
	o, err := RunCommandCtxStreamOutcome(ctx, cmdStr, onChunk)
	return o.Stdout, o.Stderr, o.ExitCode, err
}

// RunCommandCtxStreamOutcome executes cmdStr via the host shell (`sh -c` on POSIX,
// PowerShell on Windows — see the platform_*.go files) and captures stdout and
// stderr separately, honouring ctx for cancellation and streaming chunks to onChunk if non-nil.
func RunCommandCtxStreamOutcome(ctx context.Context, cmdStr string, onChunk OutputChunkFunc) (Outcome, error) {
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
		return Outcome{Stdout: outBuf.String(), Stderr: errBuf.String(), ExitCode: -1, Termination: TerminationExited}, startErr
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

	stdout := outBuf.String()
	stderr := errBuf.String()

	if cancelled.Load() {
		// Operator/abort-driven termination. Surface a stable, non-fatal signal.
		return Outcome{Stdout: stdout, Stderr: stderr, ExitCode: 130, Termination: TerminationAborted}, context.Canceled
	}

	if runErr != nil {
		if exitErr, ok := runErr.(*exec.ExitError); ok {
			exitCode, termination, sig := 1, TerminationExited, 0
			if status, ok := exitErr.Sys().(syscall.WaitStatus); ok {
				if status.Signaled() {
					// Killed by a signal we did not initiate via ctx — convention 128+signal.
					sig = int(status.Signal())
					exitCode = 128 + sig
					termination = TerminationSignaled
				} else {
					exitCode = status.ExitStatus()
				}
			}
			// Non-zero exit is not an error from the caller's perspective.
			return Outcome{Stdout: stdout, Stderr: stderr, ExitCode: exitCode, Termination: termination, Signal: sig}, nil
		}
		// Real error (should be rare after a successful Start).
		return Outcome{Stdout: stdout, Stderr: stderr, ExitCode: -1, Termination: TerminationExited}, runErr
	}

	return Outcome{Stdout: stdout, Stderr: stderr, ExitCode: 0, Termination: TerminationExited}, nil
}
