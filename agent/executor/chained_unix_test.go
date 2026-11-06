//go:build !windows

package executor

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// BEACON-04 (POSIX half). Close() used to cancel the session AFTER the anchor
// had already exited cleanly, which fired the tree-kill at a pid whose process
// was gone: on Windows that pid is free for reuse the moment Wait released the
// handle, and a SYSTEM beacon would TerminateProcess whatever now holds it. On
// POSIX the same late kill reached into the group and killed the background
// children of steps that had SUCCEEDED, which the per-step path never does.
// Once the anchor has exited, Close must not kill anything.
func TestChainSession_CloseDoesNotKillAfterTheAnchorExited(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}

	pidFile := filepath.Join(t.TempDir(), "bg.pid")
	// A successful step that leaves a background child running, exactly as a
	// per-step `sh -c 'sleep 30 &'` would.
	if _, _, rc, err := s.RunStep("sleep 30 >/dev/null 2>&1 & echo $! > " + pidFile); err != nil || rc != 0 {
		t.Fatalf("step: rc=%d err=%v", rc, err)
	}
	b, err := os.ReadFile(pidFile)
	if err != nil {
		t.Fatalf("read pid: %v", err)
	}
	bg, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil || bg <= 0 {
		t.Fatalf("bad background pid %q", b)
	}
	t.Cleanup(func() { _ = syscall.Kill(bg, syscall.SIGKILL) })

	_ = s.Close()

	// The old late kill was SIGTERM then, after killGrace, SIGKILL. Wait past
	// the SIGTERM leg and check the child is still there.
	time.Sleep(500 * time.Millisecond)
	if err := syscall.Kill(bg, 0); err != nil {
		t.Fatalf("background child %d of a SUCCESSFUL step was killed by Close() after the anchor "+
			"had already exited (%v) — Close must not tree-kill a finished session", bg, err)
	}
}
