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
// the first job termination before the second).
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
	return RunShellCtxStreamOutcome(ctx, "", cmdStr, onChunk)
}

// RunShellCtxStreamOutcome is RunCommandCtxStreamOutcome under an explicit step
// shell: "" is the host default (sh on POSIX, PowerShell on Windows); on
// Windows "powershell" and "cmd" (PUSH-18) are also accepted. A shell this
// host cannot execute is an error, never a silent fall-back to the default —
// running cmd batch text under sh would execute garbage that may still exit 0.
func RunShellCtxStreamOutcome(ctx context.Context, shell, cmdStr string, onChunk OutputChunkFunc) (Outcome, error) {
	cmd, cleanup, buildErr := newShellCmdFor(shell, cmdStr)
	if buildErr != nil {
		return Outcome{ExitCode: -1, Termination: TerminationExited}, buildErr
	}
	defer cleanup()

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
	// Taken immediately after Start, while the child's pid is still pinned by
	// os/exec's own handle (BEACON-04): on Windows this is where the child joins
	// its Job Object; on POSIX it records the process group.
	killer := attachTreeKiller(cmd)

	// Watch ctx in a goroutine; on cancel, tear the whole tree down (POSIX:
	// SIGTERM the group then SIGKILL after a grace period; Windows: terminate
	// the job). waitDone stops the watcher once the command returns. cancelled
	// is read by the main goroutine after Wait() returns and written by the
	// watcher, so it must be accessed atomically.
	waitDone := make(chan struct{})
	var cancelled atomic.Bool
	go func() {
		select {
		case <-ctx.Done():
			cancelled.Store(true)
			killer.Kill()
		case <-waitDone:
		}
	}()

	runErr := cmd.Wait()
	close(waitDone)
	// Reaped: from here on nothing may be killed on this command's behalf. A
	// Kill already in progress still completes (and Windows keeps its handles
	// open until it has); one that had not started is refused.
	killer.Release()

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
