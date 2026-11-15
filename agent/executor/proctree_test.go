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
		{PID: 100, PPID: 4, Created: 10},
		{PID: 200, PPID: 100, Created: 20},
		{PID: 300, PPID: 200, Created: 30},
		{PID: 201, PPID: 100, Created: 21},
		{PID: 999, PPID: 4, Created: 5},
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
		{PID: 100, PPID: 4, Created: 10},
		{PID: 200, PPID: 100, Created: 20},
		{PID: 300, PPID: 200, Created: 30},
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
		// Equal creation times, so the creation-time filter cannot break the
		// cycle and the visited-set guard is what is exercised.
		done <- buildKillOrder([]procEntry{
			{PID: 100, PPID: 300, Created: 10},
			{PID: 200, PPID: 100, Created: 10},
			{PID: 300, PPID: 200, Created: 10},
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
		{PID: 100, PPID: 100, Created: 10}, // self-parent
		{PID: 200, PPID: 100, Created: 20},
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
		{PID: 200, PPID: 100, Created: 20},
		{PID: 200, PPID: 100, Created: 20},
		{PID: 100, PPID: 4, Created: 10},
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

// BEACON-04. The Windows snapshot reports a ParentProcessID that is NEVER
// updated when the parent exits, and pids are recycled. A long-lived process
// whose real parent died hours ago can therefore carry the PID our freshly
// started root just reused. Walking by PPID alone adopts it as "our" child and
// a SYSTEM beacon TerminateProcess-es an unrelated customer process. A real
// descendant is always created AT OR AFTER its parent, so a row created before
// the parent it names is a stale PPID and must never enter the kill order.
func TestBuildKillOrder_ExcludesStaleParentPIDRows(t *testing.T) {
	const rootCreated = 1_000_000
	order := buildKillOrder([]procEntry{
		{PID: 100, PPID: 4, Created: rootCreated},
		{PID: 200, PPID: 100, Created: rootCreated + 10},  // real child
		{PID: 300, PPID: 200, Created: rootCreated + 20},  // real grandchild
		{PID: 777, PPID: 100, Created: rootCreated - 500}, // older than root: stale PPID
		{PID: 888, PPID: 777, Created: rootCreated - 400}, // descendant of the stale row
	}, 100)

	for _, bystander := range []uint32{777, 888} {
		if indexOf(order, bystander) >= 0 {
			t.Errorf("pid %d was created BEFORE the root it names as parent (a recycled PPID) "+
				"but is in the kill order %v — a SYSTEM beacon would kill an unrelated process", bystander, order)
		}
	}
	for _, want := range []uint32{100, 200, 300} {
		if indexOf(order, want) < 0 {
			t.Errorf("real descendant %d missing from kill order %v", want, order)
		}
	}
}

// descendantCandidates only decides what to OPEN; it is over-inclusive by
// design (no time filter) but still never includes the root, pid 0, or loops.
func TestDescendantCandidates_IsReachabilityOnly(t *testing.T) {
	got := descendantCandidates([]procEntry{
		{PID: 100, PPID: 300},
		{PID: 200, PPID: 100},
		{PID: 300, PPID: 200},
		{PID: 400, PPID: 4},
		{PID: 0, PPID: 100},
	}, 100)
	if !got[200] || !got[300] {
		t.Fatalf("reachable pids missing: %v", got)
	}
	if got[100] || got[400] || got[0] {
		t.Fatalf("root, an unrelated pid or pid 0 is a candidate: %v", got)
	}
}

// A row whose creation time could not be read proves nothing about its
// parentage, so it is not adopted: the job object is the primary kill path and
// the snapshot sweep only ever adds processes it can prove are ours.
func TestBuildKillOrder_UnknownCreationTimeIsNotAdopted(t *testing.T) {
	order := buildKillOrder([]procEntry{
		{PID: 100, PPID: 4, Created: 50},
		{PID: 200, PPID: 100, Created: 0},
	}, 100)
	if indexOf(order, 200) >= 0 {
		t.Fatalf("a child with an unknown creation time was adopted: %v", order)
	}
}
