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
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
)

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

	cancelled atomic.Bool
	wg        sync.WaitGroup
	closeOnce sync.Once
}

// NewChainSession starts the anchor shell. cgoImage, when non-empty, is used as
// the anchor's argv[0] so the process's command line reflects the intended CGO
// image (e.g. "nginx"); the executed binary is still /bin/sh. The session runs
// in its own process group so a ctx cancel (operator abort / shutdown) tears the
// whole chain down at once.
func NewChainSession(ctx context.Context, cgoImage string) (*ChainSession, error) {
	sctx, cancel := context.WithCancel(ctx)

	cmd := exec.Command("sh")
	if strings.TrimSpace(cgoImage) != "" {
		// Rewrite argv[0] only (Args[0]); the binary remains sh.
		cmd.Args[0] = cgoImage
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

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
	}

	// Scanner goroutines: stream each pipe line-by-line onto a buffered channel.
	s.wg.Add(2)
	go s.scan(stdoutPipe, s.outCh)
	go s.scan(stderrPipe, s.errCh)

	// Watcher: on ctx cancel, SIGTERM→SIGKILL the whole anchor group so an
	// in-flight step (and the idle anchor) die together — same contract as
	// RunCommandCtx.
	go func() {
		<-sctx.Done()
		s.cancelled.Store(true)
		if s.cmd.Process != nil {
			killProcessGroup(s.cmd.Process.Pid)
		}
	}()

	return s, nil
}

func (s *ChainSession) scan(r io.Reader, ch chan<- string) {
	defer s.wg.Done()
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		ch <- sc.Text()
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

	// Run the step in a SUBSHELL `( ... )` — a real forked child of the anchor
	// (so the step is a descendant of the one CGO, a connected causality tree)
	// that ALSO isolates `exit`/`set -e` from the anchor and gives no cross-step
	// state, exactly matching the default per-step `sh -c` semantics. The
	// sentinel printfs run in the ANCHOR (outside the parens) so they always
	// fire even if the subshell called `exit`. $? is captured immediately after
	// the subshell so it reflects the step, not the printf.
	script := "(\n" + wrappedCmd + "\n)\n__cs_rc=$?" +
		fmt.Sprintf("; printf '\\n%s %%d\\n' \"$__cs_rc\"", s.marker) + // stdout: marker + rc
		fmt.Sprintf("; printf '\\n%s\\n' 1>&2", s.marker) + // stderr: bare marker
		"\n"
	if _, werr := io.WriteString(s.stdin, script); werr != nil {
		return "", "", -1, fmt.Errorf("chain write: %w", werr)
	}

	// Collect both streams concurrently up to their sentinels so a chatty stream
	// can never deadlock the other.
	var outB, errB strings.Builder
	var rc int
	var rcErr error
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
				if strings.HasPrefix(line, s.marker+" ") {
					if v, e := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(line, s.marker+" "))); e == nil {
						rc = v
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
				if line == s.marker {
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

	wg.Wait()

	if s.cancelled.Load() || s.ctx.Err() != nil {
		return outB.String(), errB.String(), 130, context.Canceled
	}
	if rcErr != nil {
		return outB.String(), errB.String(), -1, fmt.Errorf("chain sentinel parse: %w", rcErr)
	}
	return outB.String(), errB.String(), rc, nil
}

// Close ends the anchor shell cleanly (EOF on stdin) and waits for it to exit.
// Safe to call more than once.
func (s *ChainSession) Close() error {
	var err error
	s.closeOnce.Do(func() {
		_, _ = io.WriteString(s.stdin, "exit\n")
		_ = s.stdin.Close()
		err = s.cmd.Wait()
		s.cancel()
		s.wg.Wait()
	})
	return err
}
