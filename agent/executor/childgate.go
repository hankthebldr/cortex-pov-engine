package executor

// The child gate keeps an orphan reaper from stealing an exit status.
//
// In a pod the beacon is a child subreaper: orphans of finished steps reparent
// to it and must be collected with a blanket Wait4(-1). That call cannot tell an
// orphan from a child os/exec is about to Wait on — if it collects the latter,
// cmd.Wait fails with "no child processes" and a step that exited 7 has no exit
// code. So every child this agent will Wait on is started through the gate, and
// the reaper only runs while the gate is held with no such child alive.
//
// One mutex, not a bare counter: the reaper's "count is zero" check and its
// Wait4 loop happen under the lock, and a starter takes the same lock to count
// itself in BEFORE Start. A child therefore either exists after the reaper
// finished (and is counted before it can exit), or the reaper sees it counted
// and skips. There is no window between the check and the Wait4.

import (
	"os/exec"
	"sync"
)

var childGate struct {
	mu     sync.Mutex
	active int
}

func enterChild() {
	childGate.mu.Lock()
	childGate.active++
	childGate.mu.Unlock()
}

func leaveChild() {
	childGate.mu.Lock()
	childGate.active--
	if childGate.active < 0 {
		childGate.mu.Unlock()
		panic("executor: child gate released more times than entered")
	}
	childGate.mu.Unlock()
}

// StartTracked starts cmd counted as a child this process will Wait on. Call
// release exactly once, after cmd.Wait has returned; extra calls are no-ops.
func StartTracked(cmd *exec.Cmd) (release func(), err error) {
	enterChild()
	if err := cmd.Start(); err != nil {
		leaveChild()
		return func() {}, err
	}
	var once sync.Once
	return func() { once.Do(leaveChild) }, nil
}

// RunTracked is cmd.Run through the gate.
func RunTracked(cmd *exec.Cmd) error {
	release, err := StartTracked(cmd)
	if err != nil {
		return err
	}
	defer release()
	return cmd.Wait()
}

// ActiveChildren reports how many gate-tracked children are alive (started and
// not yet waited on).
func ActiveChildren() int {
	childGate.mu.Lock()
	defer childGate.mu.Unlock()
	return childGate.active
}

// ReapWhenIdle runs reap with the gate held, but only when no tracked child is
// alive; otherwise it returns 0 without calling reap. reap must not block.
func ReapWhenIdle(reap func() int) int {
	childGate.mu.Lock()
	defer childGate.mu.Unlock()
	if childGate.active > 0 {
		return 0
	}
	return reap()
}
