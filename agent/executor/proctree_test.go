package executor

import (
	"testing"
)

// indexOf returns the position of pid in order, or -1.
func indexOf(order []uint32, pid uint32) int {
	for i, p := range order {
		if p == pid {
			return i
		}
	}
	return -1
}

// The whole point of the Windows abort path: every DESCENDANT is killed, not
// just the shell we spawned. A kill that leaves orphans is worse than no abort
// because the operator believes the run stopped.
func TestBuildKillOrder_IncludesEveryDescendant(t *testing.T) {
	// 100 (our shell) → 200 → 300, and 100 → 201. 999 is an unrelated process.
	entries := []procEntry{
		{PID: 100, PPID: 4},
		{PID: 200, PPID: 100},
		{PID: 300, PPID: 200},
		{PID: 201, PPID: 100},
		{PID: 999, PPID: 4},
	}
	order := buildKillOrder(entries, 100)

	for _, want := range []uint32{100, 200, 201, 300} {
		if indexOf(order, want) < 0 {
			t.Errorf("pid %d would survive the abort — not in kill order %v", want, order)
		}
	}
	if indexOf(order, 999) >= 0 {
		t.Errorf("unrelated pid 999 is in the kill order %v — abort would kill bystanders", order)
	}
	if len(order) != 4 {
		t.Errorf("kill order = %v, want exactly the 4 tree members", order)
	}
}

// Leaves must die before their parents: killing a parent first gives it a window
// to spawn a replacement child that our snapshot never saw.
func TestBuildKillOrder_IsLeafFirst(t *testing.T) {
	entries := []procEntry{
		{PID: 100, PPID: 4},
		{PID: 200, PPID: 100},
		{PID: 300, PPID: 200},
	}
	order := buildKillOrder(entries, 100)

	if indexOf(order, 300) > indexOf(order, 200) {
		t.Errorf("grandchild 300 killed after child 200: %v", order)
	}
	if indexOf(order, 200) > indexOf(order, 100) {
		t.Errorf("child 200 killed after root 100: %v", order)
	}
	if order[len(order)-1] != 100 {
		t.Errorf("root must be killed last, order = %v", order)
	}
}

// The Windows process table is sampled without a lock and pids are RECYCLED, so
// a cycle in the parent chain is reachable in production. It must not hang the
// abort path — a hung abort is an abort that never happens.
func TestBuildKillOrder_SurvivesCycles(t *testing.T) {
	done := make(chan []uint32, 1)
	go func() {
		// 100 → 200 → 300 → 100 (recycled pid pointing back at the root).
		done <- buildKillOrder([]procEntry{
			{PID: 100, PPID: 300},
			{PID: 200, PPID: 100},
			{PID: 300, PPID: 200},
		}, 100)
	}()

	order := <-done
	if len(order) != 3 {
		t.Fatalf("cycle handling returned %v, want the 3 distinct pids once each", order)
	}
	seen := map[uint32]int{}
	for _, p := range order {
		seen[p]++
		if seen[p] > 1 {
			t.Fatalf("pid %d emitted twice: %v", p, order)
		}
	}
}

// A self-parenting row (pid 0 / pid 4 artefacts on Windows) must not make the
// walker treat the root as its own child.
func TestBuildKillOrder_SelfParentIsNotAChild(t *testing.T) {
	order := buildKillOrder([]procEntry{
		{PID: 100, PPID: 100}, // self-parent
		{PID: 200, PPID: 100},
	}, 100)
	if len(order) != 2 {
		t.Fatalf("order = %v, want [200 100]", order)
	}
	if order[1] != 100 {
		t.Fatalf("root not last: %v", order)
	}
}

// Duplicate snapshot rows must not produce duplicate TerminateProcess targets.
func TestBuildKillOrder_DeduplicatesSnapshotRows(t *testing.T) {
	order := buildKillOrder([]procEntry{
		{PID: 200, PPID: 100},
		{PID: 200, PPID: 100},
		{PID: 100, PPID: 4},
	}, 100)
	if len(order) != 2 {
		t.Fatalf("order = %v, want 2 unique pids", order)
	}
}

// Refusing to build an order for pid 0 is a safety property: pid 0 is the System
// Idle Process and "kill everything whose parent is 0" would be catastrophic.
func TestBuildKillOrder_RefusesPidZero(t *testing.T) {
	if order := buildKillOrder([]procEntry{{PID: 1, PPID: 0}}, 0); order != nil {
		t.Fatalf("buildKillOrder(root=0) = %v, want nil", order)
	}
}

// A root that has already exited (absent from the snapshot) is still returned so
// the caller's TerminateProcess can try — but no children are invented for it.
func TestBuildKillOrder_MissingRootStillReturnsRoot(t *testing.T) {
	order := buildKillOrder([]procEntry{{PID: 999, PPID: 4}}, 100)
	if len(order) != 1 || order[0] != 100 {
		t.Fatalf("order = %v, want [100]", order)
	}
}
