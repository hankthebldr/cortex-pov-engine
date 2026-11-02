// Package executor — chained (causality-anchored) execution.
//
// In the default path (RunCommandCtx) each TTP step is an independent `sh -c`
// process, so on the endpoint the steps are SIBLINGS under the beacon — the
// Cortex XDR causality engine roots the chain at the agent, not at a realistic
// initial-access process, and the steps do not share one causality group.
//
// A ChainSession fixes that for scenarios that declare a causality contract: it
// launches ONE persistent anchor shell per run and feeds each step's (already
// identity-wrapped) command into it. Every step therefore execs as a CHILD of
// the same anchor process — a real fork/exec tree the kernel/eBPF process-create
// path records — so the sensor traces one connected causality chain rooted at
// the anchor (labelled from the scenario's cgo_anchor). Per-step stdout/stderr
// and exit codes are still delimited (via a per-session sentinel) so the beacon
// keeps its one-/output-POST-per-step attribution and fail-fast semantics.
package executor

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
)

// ErrAnchorTerminated is returned (wrapped) by RunStep, with rc == -1, when the
// chain's anchor shell died before emitting a step's exit-code sentinel: the
// step's outcome is unknown. Callers match it with errors.Is.
var ErrAnchorTerminated = errors.New("chain anchor shell terminated")

// ChainSession is a persistent anchor shell that runs steps as its children.
// Not safe for concurrent RunStep calls — steps are sequential by design.
type ChainSession struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	outCh  chan string // stdout lines from the scanner goroutine
	errCh  chan string // stderr lines from the scanner goroutine
	marker string      // per-session sentinel token (unlikely to appear in output)
	cancel context.CancelFunc
	ctx    context.Context

	// exited is closed when the anchor process has exited; waitErr is its Wait
	// result. One goroutine calls cmd.Wait() (it may be called exactly once) so
	// RunStep can notice the anchor's death directly instead of on pipe EOF,
	// which an orphaned step holding the pipes' write ends would delay.
	exited  chan struct{}
	waitErr error

	cancelled atomic.Bool
	wg        sync.WaitGroup
	closeOnce sync.Once
}

// NewChainSession starts the anchor shell. cgoImage, when non-empty, is used as
// the anchor's argv[0] so the process's command line reflects the intended CGO
// image (e.g. "nginx"); the executed binary is still the host shell (/bin/sh on
// POSIX, powershell.exe on Windows). The session is started so its whole
// descendant tree is terminable, so a ctx cancel (operator abort / shutdown)
// tears the entire chain down at once.
func NewChainSession(ctx context.Context, cgoImage string) (*ChainSession, error) {
	sctx, cancel := context.WithCancel(ctx)

	cmd := newAnchorCmd(strings.TrimSpace(cgoImage))

	stdin, err := cmd.StdinPipe()
	if err != nil {
		cancel()
		return nil, fmt.Errorf("chain session stdin: %w", err)
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return nil, fmt.Errorf("chain session stdout: %w", err)
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		cancel()
		return nil, fmt.Errorf("chain session stderr: %w", err)
	}

	if startErr := cmd.Start(); startErr != nil {
		cancel()
		return nil, fmt.Errorf("chain session start: %w", startErr)
	}

	s := &ChainSession{
		cmd:    cmd,
		stdin:  stdin,
		outCh:  make(chan string, 4096),
		errCh:  make(chan string, 4096),
		marker: fmt.Sprintf("__CORTEXSIM_STEP_%d__", os.Getpid()),
		cancel: cancel,
		ctx:    sctx,
		exited: make(chan struct{}),
	}
	go func() {
		s.waitErr = cmd.Wait()
		close(s.exited)
	}()

	// Scanner goroutines: stream each pipe line-by-line onto a buffered channel.
	s.wg.Add(2)
	go s.scan(stdoutPipe, s.outCh)
	go s.scan(stderrPipe, s.errCh)

	// Watcher: on ctx cancel, tear down the whole anchor tree so an in-flight
	// step (and the idle anchor) die together — same contract as RunCommandCtx.
	go func() {
		<-sctx.Done()
		s.cancelled.Store(true)
		if s.cmd.Process != nil {
			killProcessTree(s.cmd.Process.Pid)
		}
	}()

	return s, nil
}

func (s *ChainSession) scan(r io.Reader, ch chan<- string) {
	defer s.wg.Done()
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		// trimEOL: PowerShell terminates lines with CRLF and Scanner only strips
		// the \n, so without this the stderr sentinel (an exact match on the bare
		// marker) never fires on Windows and every step would block.
		ch <- trimEOL(sc.Text())
	}
	close(ch)
}

// RunStep writes one identity-wrapped command to the anchor, executes it as a
// child of the anchor, and returns that step's captured stdout/stderr and exit
// code. exitCode 130 with context.Canceled indicates an operator abort / cancel.
//
// wrappedCmd is the exact string the default path would pass to `sh -c` (e.g.
// `runuser -l www-data -c '...'`), so identity-harness semantics are identical.
func (s *ChainSession) RunStep(wrappedCmd string) (stdout, stderr string, exitCode int, err error) {
	if s.cancelled.Load() || s.ctx.Err() != nil {
		return "", "", 130, context.Canceled
	}

	// The step runs in a real CHILD PROCESS of the anchor (a POSIX subshell, or a
	// nested powershell.exe on Windows) so it is a descendant of the one CGO —
	// a connected causality tree — while the sentinels are emitted by the ANCHOR
	// itself so they still fire if the step called `exit`. See script.go.
	script := chainStepScript(s.marker, wrappedCmd)
	if _, werr := io.WriteString(s.stdin, script); werr != nil {
		return "", "", -1, fmt.Errorf("chain write: %w", werr)
	}

	// Collect both streams concurrently up to their sentinels so a chatty stream
	// can never deadlock the other.
	var outB, errB strings.Builder
	var rc int
	var rcErr error
	var sawRC atomic.Bool // true once the anchor emitted this step's exit-code sentinel
	var wg sync.WaitGroup
	wg.Add(2)

	go func() { // stdout: lines until "<marker> <rc>"
		defer wg.Done()
		for {
			select {
			case line, ok := <-s.outCh:
				if !ok {
					return
				}
				// Sentinel matching is done on an ANSI-stripped copy: a PowerShell
				// anchor's prompt loop glues terminal control sequences onto the
				// following line, and an unmatched sentinel hangs the step forever.
				// The line stored in the body below is the RAW one — a TTP's output
				// is evidence and is kept byte-for-byte.
				if clean := stripANSI(line); strings.HasPrefix(clean, s.marker+" ") {
					if v, e := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(clean, s.marker+" "))); e == nil {
						rc = v
						sawRC.Store(true)
					} else {
						rcErr = e
					}
					return
				}
				if outB.Len() > 0 {
					outB.WriteByte('\n')
				}
				outB.WriteString(line)
			case <-s.ctx.Done():
				return
			}
		}
	}()

	go func() { // stderr: lines until bare "<marker>"
		defer wg.Done()
		for {
			select {
			case line, ok := <-s.errCh:
				if !ok {
					return
				}
				if stripANSI(line) == s.marker {
					return
				}
				if errB.Len() > 0 {
					errB.WriteByte('\n')
				}
				errB.WriteString(line)
			case <-s.ctx.Done():
				return
			}
		}
	}()

	// The anchor can die while its (now orphaned) step subshell still holds the
	// pipes' write ends, so EOF alone would not arrive until the orphan exits (or
	// the step times out). Watch the anchor directly: when it exits without this
	// step's sentinel, tree-kill the orphan. cmd.Wait() (in s.exited) has by then
	// closed our read ends, so the scanners finish at once and RunStep returns
	// ErrAnchorTerminated below via the !sawRC path -- the anchor died first, so
	// the witness is anchor_terminated, never "step signaled". The kill is not
	// awaited: killProcessTree sleeps killGrace, and RunStep must not.
	stepDone := make(chan struct{})
	defer close(stepDone)
	go func() {
		select {
		case <-s.exited:
			if !sawRC.Load() {
				killProcessTree(s.cmd.Process.Pid)
			}
		case <-stepDone:
		}
	}()

	wg.Wait()

	if s.cancelled.Load() || s.ctx.Err() != nil {
		return outB.String(), errB.String(), 130, context.Canceled
	}
	if rcErr != nil {
		return outB.String(), errB.String(), -1, fmt.Errorf("chain sentinel parse: %w", rcErr)
	}
	if !sawRC.Load() {
		// The anchor closed its pipes before emitting this step's exit-code
		// sentinel — it died mid-step (OOM-killed, or a step that killed the
		// anchor shell, e.g. `kill $$` in a subshell). The step's outcome is
		// UNKNOWN. Returning the zero-valued rc here would report exit 0 for a
		// step that never completed — the exact false-green the engine exists to
		// eliminate. Surface it as an execution error with a non-zero exit so
		// executeTaskChained fails the run instead of recording a passing step.
		return outB.String(), errB.String(), -1,
			fmt.Errorf("%w before step completed — step result unknown", ErrAnchorTerminated)
	}
	return outB.String(), errB.String(), rc, nil
}

// Close ends the anchor shell cleanly (EOF on stdin) and waits for it to exit.
// Safe to call more than once.
func (s *ChainSession) Close() error {
	var err error
	s.closeOnce.Do(func() {
		_, _ = io.WriteString(s.stdin, anchorExitScript)
		_ = s.stdin.Close()
		<-s.exited
		err = s.waitErr
		s.cancel()
		s.wg.Wait()
	})
	return err
}
