package executor

// Process-tree resolution, kept platform-neutral so the ORDERING and FILTERING
// logic — the part that decides what actually dies — is unit-testable on any
// host.
//
// On Windows the primary tree kill is a Job Object (jobobject_windows.go),
// which names no pid. This file serves its FALLBACK, used only when a job could
// not be created or assigned: snapshot the live process table
// (CreateToolhelp32Snapshot), open each candidate and read its creation time,
// then let buildKillOrder decide who is genuinely a descendant of our root and
// return them LEAF-FIRST. Killing leaves first stops a parent from spawning a
// replacement child in the window between the snapshot and the kill.

// procEntry is one row of a process-table snapshot.
type procEntry struct {
	PID  uint32
	PPID uint32
	// Created is the process creation time (Windows FILETIME, 100ns ticks),
	// read through a handle opened on the process. 0 means unknown.
	Created uint64
}

// descendantCandidates returns every pid reachable from root by ParentProcessID
// alone (root excluded). It is deliberately over-inclusive: it only decides
// which processes are worth OPENING to read a creation time. buildKillOrder
// decides who dies.
func descendantCandidates(entries []procEntry, root uint32) map[uint32]bool {
	children := make(map[uint32][]uint32, len(entries))
	for _, e := range entries {
		if e.PID == 0 || e.PID == e.PPID {
			continue
		}
		children[e.PPID] = append(children[e.PPID], e.PID)
	}
	out := map[uint32]bool{}
	visited := map[uint32]bool{root: true}
	stack := []uint32{root}
	for len(stack) > 0 {
		p := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		for _, c := range children[p] {
			if visited[c] {
				continue
			}
			visited[c] = true
			out[c] = true
			stack = append(stack, c)
		}
	}
	return out
}

// buildKillOrder returns root and its PROVABLE descendants ordered deepest-first
// (leaves before their parents, root last).
//
// A row is adopted as a child of parent p only when BOTH creation times are
// known and the child was created at or after p (BEACON-04). ParentProcessID is
// never updated when a parent exits and pids are recycled, so a process whose
// real parent died long ago can name the pid our root has just reused; it was
// necessarily created BEFORE our root, and adopting it would have a SYSTEM
// beacon terminate an unrelated customer process. A row whose creation time
// could not be read proves nothing and is not adopted.
//
// It is also defensive about an inconsistent snapshot (the table is sampled
// without a lock):
//
//   - a pid that is its own parent (pid 0/4 style artefacts) is not treated as
//     a child of itself, which would otherwise recurse forever;
//   - a cycle anywhere in the parent chain is broken by a visited set;
//   - duplicate pids in the snapshot are collapsed (first row wins);
//   - pid 0 is never returned — terminating it is meaningless and dangerous.
//
// If root does not appear in the snapshot it is still returned (it may have
// exited between the snapshot and the call; the caller's terminate through its
// held handle fails harmlessly), but no children are invented for it.
func buildKillOrder(entries []procEntry, root uint32) []uint32 {
	if root == 0 {
		return nil
	}

	created := make(map[uint32]uint64, len(entries))
	children := make(map[uint32][]uint32, len(entries))
	seen := make(map[uint32]bool, len(entries))
	for _, e := range entries {
		if e.PID == 0 || seen[e.PID] {
			continue // pid 0, or a duplicate snapshot row
		}
		seen[e.PID] = true
		created[e.PID] = e.Created
		if e.PID != e.PPID {
			children[e.PPID] = append(children[e.PPID], e.PID)
		}
	}

	var order []uint32
	visited := map[uint32]bool{}

	// Post-order DFS: emit a node only after all of its descendants.
	var walk func(pid uint32)
	walk = func(pid uint32) {
		if visited[pid] {
			return // cycle guard — a recycled pid pointing back up the tree
		}
		visited[pid] = true
		parentCreated := created[pid]
		for _, c := range children[pid] {
			cc := created[c]
			if parentCreated == 0 || cc == 0 || cc < parentCreated {
				continue // stale or unprovable parentage: not ours
			}
			walk(c)
		}
		order = append(order, pid)
	}
	walk(root)

	return order
}
