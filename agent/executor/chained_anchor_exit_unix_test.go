//go:build !windows

package executor

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// When the anchor is SIGKILLed mid-step, the orphaned step still holds the
// pipes' write ends, so pipe EOF alone would delay ErrAnchorTerminated until the
// orphan exits (or the step timeout). RunStep must notice the anchor's death
// directly, tree-kill the orphan, and return promptly.
func TestChainSession_AnchorKilledReturnsPromptlyAndReapsOrphan(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()

	pidFile := filepath.Join(t.TempDir(), "orphan.pid")
	type res struct {
		rc  int
		err error
		at  time.Time
	}
	done := make(chan res, 1)
	start := time.Now()
	go func() {
		// The step is a long-lived child of the anchor that records its pid.
		_, _, rc, e := s.RunStep("echo $$ > " + pidFile + "; exec sleep 30")
		done <- res{rc, e, time.Now()}
	}()

	var orphan int
	for i := 0; i < 100 && orphan == 0; i++ {
		if b, e := os.ReadFile(pidFile); e == nil && strings.TrimSpace(string(b)) != "" {
			orphan, _ = strconv.Atoi(strings.TrimSpace(string(b)))
		}
		time.Sleep(20 * time.Millisecond)
	}
	if orphan == 0 {
		t.Fatal("step never recorded its pid")
	}

	killedAt := time.Now()
	if e := syscall.Kill(s.cmd.Process.Pid, syscall.SIGKILL); e != nil { // the anchor ONLY
		t.Fatalf("kill anchor: %v", e)
	}

	select {
	case r := <-done:
		if !errors.Is(r.err, ErrAnchorTerminated) || r.rc != -1 {
			t.Fatalf("want ErrAnchorTerminated rc=-1, got rc=%d err=%v", r.rc, r.err)
		}
		if d := r.at.Sub(killedAt); d > 2*time.Second {
			t.Fatalf("anchor death detected after %s (started %s ago), want < 2s", d, time.Since(start))
		}
	case <-time.After(10 * time.Second):
		t.Fatal("RunStep still blocked 10s after the anchor was killed (waiting on the orphan's pipes)")
	}

	// The orphan was reaped by the beacon, not left running.
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if syscall.Kill(orphan, 0) != nil {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("orphaned step pid %d is still alive after anchor death", orphan)
}
