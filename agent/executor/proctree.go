package executor

// Process-tree resolution, kept platform-neutral so the ORDERING logic — the
// part that decides what actually dies — is unit-testable on any host.
//
// WHY this exists at all: on POSIX one `kill(-pgid)` reaches every descendant
// because the kernel maintains the process group. Windows has no equivalent
// reachable from the standard library: `CreateJobObject` is not exposed by
// `syscall`, and `TerminateProcess` kills exactly ONE process. Terminating only
// the shell we spawned would leave every tool it launched (nmap, a python
// stager, a PowerShell child) running while the operator's UI says the run
// stopped — an abort that lies is worse than no abort at all.
//
// So on Windows the platform layer snapshots the live process table
// (CreateToolhelp32Snapshot) and hands the (pid, ppid) pairs to buildKillOrder,
// which walks the descendants of our root and returns them LEAF-FIRST. Killing
// leaves first stops a parent from spawning a replacement child in the window
// between our snapshot and the kill.

// procEntry is one (pid, parent pid) pair from a process-table snapshot.
type procEntry struct {
	PID  uint32
	PPID uint32
}

// buildKillOrder returns root and all of its descendants ordered deepest-first
// (leaves before their parents, root last).
//
// It is defensive about a hostile/inconsistent snapshot because the Windows
// process table is sampled without a lock and pids are RECYCLED:
//
//   - a pid that is its own parent (pid 0/4 style artefacts) is not treated as
//     a child of itself, which would otherwise recurse forever;
//   - a cycle anywhere in the parent chain is broken by a visited set, so a
//     recycled pid pointing back up the tree cannot hang the abort path;
//   - duplicate pids in the snapshot are collapsed;
//   - pid 0 is never returned — terminating it is meaningless and dangerous.
//
// If root does not appear in the snapshot it is still returned (it may have
// exited between the snapshot and the call; the caller's TerminateProcess will
// simply fail harmlessly), but no children are invented for it.
func buildKillOrder(entries []procEntry, root uint32) []uint32 {
	if root == 0 {
		return nil
	}

	// children[ppid] = pids whose parent is ppid. Self-parenting entries are
	// dropped here so the traversal can never revisit its own start node.
	children := make(map[uint32][]uint32, len(entries))
	seen := make(map[uint32]bool, len(entries))
	for _, e := range entries {
		if e.PID == 0 || e.PID == e.PPID {
			continue
		}
		if seen[e.PID] {
			continue // duplicate snapshot row
		}
		seen[e.PID] = true
		children[e.PPID] = append(children[e.PPID], e.PID)
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
		for _, c := range children[pid] {
			walk(c)
		}
		order = append(order, pid)
	}
	walk(root)

	return order
}
