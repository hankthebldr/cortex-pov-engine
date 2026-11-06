//go:build windows

package executor

// Windows tree kill (BEACON-04) — a Job Object, never a pid.
//
// What base did: snapshot the process table, walk ParentProcessID from the
// shell's pid, TerminateProcess leaf-first, sleep killGrace, do it again. Three
// facts make that unsafe for a beacon that runs as SYSTEM:
//
//   - os/exec's Wait releases the process handle, after which the pid is free
//     for Windows to hand to any new process. shell.go and chained.go swept by
//     that bare pid — including after Wait (the second sweep, Close() after the
//     anchor had exited, the anchor-death path).
//   - ParentProcessID is never updated when a parent exits, so a long-lived
//     process whose parent died long ago names whatever pid was later reused.
//   - SYSTEM can TerminateProcess almost anything, including a customer's EDR
//     helper or the beacon itself.
//
// What this does: right after Start, open OUR OWN handle to the child (which
// pins its pid: a pid is not reused while any handle to the process is open),
// create a Job Object and assign the child to it. Children inherit job
// membership, so the job IS the tree. An abort calls TerminateJobObject, which
// names no pid at all. KILL_ON_JOB_CLOSE is deliberately NOT set: a successful
// step's background children survive when the handles close, exactly as they
// do on POSIX.
//
// If the job cannot be created or assigned (pre-Windows-8 nested-job limits, a
// restrictive parent job), the fallback is the snapshot walk — but rooted at
// the HELD handle and filtered by creation time (buildKillOrder), with every
// victim opened and checked before it is terminated through that same handle.
//
// Residual, stated rather than assumed away: the child runs for the few
// microseconds between CreateProcess and AssignProcessToJobObject outside the
// job. A grandchild spawned in that window is not in the job. powershell.exe
// and cmd.exe spend far longer than that initialising before running any step
// text, so no shipped step can reach it, but it is not structurally impossible.
// (Closing it needs CREATE_SUSPENDED plus a thread handle os/exec does not
// expose.)

import (
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"
	"unsafe"
)

var (
	modkernel32                  = syscall.NewLazyDLL("kernel32.dll")
	procCreateJobObjectW         = modkernel32.NewProc("CreateJobObjectW")
	procAssignProcessToJobObject = modkernel32.NewProc("AssignProcessToJobObject")
	procTerminateJobObject       = modkernel32.NewProc("TerminateJobObject")
)

const (
	processTerminate               = 0x0001
	processSetQuota                = 0x0100
	processQueryLimitedInformation = 0x1000
	processSynchronize             = 0x00100000
)

// treeKiller owns the right to tear a started command's whole tree down.
type treeKiller struct {
	osProc  *os.Process    // fallback only: Go's own handle-backed Kill
	proc    syscall.Handle // OUR handle to the root; pins its pid
	job     syscall.Handle // 0 when the job could not be set up
	pid     uint32
	created uint64 // root creation time (FILETIME ticks), for the fallback filter

	mu        sync.Mutex
	killing   int
	releasing bool
	closed    bool
}

// attachTreeKiller must be called right after cmd.Start().
func attachTreeKiller(cmd *exec.Cmd) *treeKiller {
	k := &treeKiller{osProc: cmd.Process, pid: uint32(cmd.Process.Pid)}
	// cmd.Process still holds Go's handle here (Wait has not run), so this pid
	// cannot have been reused yet.
	h, err := syscall.OpenProcess(
		processSetQuota|processTerminate|processQueryLimitedInformation|processSynchronize, false, k.pid)
	if err != nil {
		return k
	}
	k.proc = h
	k.created = processCreationTime(h)
	if job, ok := createJobObject(); ok {
		if assignProcessToJobObject(job, h) {
			k.job = job
		} else {
			_ = syscall.CloseHandle(job)
		}
	}
	return k
}

// Kill terminates the tree, waits killGrace, and terminates it again (a dying
// process can be mid-CreateProcess). A Kill that is already running finishes
// even if Release is called meanwhile; the handles are closed only once it has.
// A Kill requested after Release is refused: the command has been reaped and
// there is nothing left that this killer can prove is ours.
func (k *treeKiller) Kill() {
	k.mu.Lock()
	if k.releasing || k.closed {
		k.mu.Unlock()
		return
	}
	k.killing++
	k.mu.Unlock()
	defer k.endKill()

	k.sweep()
	time.Sleep(killGrace)
	k.sweep()
}

func (k *treeKiller) endKill() {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.killing--
	if k.killing == 0 && k.releasing {
		k.closeLocked()
	}
}

// Release is called once the command has been waited on.
func (k *treeKiller) Release() {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.releasing = true
	if k.killing == 0 {
		k.closeLocked()
	}
}

func (k *treeKiller) closeLocked() {
	if k.closed {
		return
	}
	k.closed = true
	if k.job != 0 {
		_ = syscall.CloseHandle(k.job)
	}
	if k.proc != 0 {
		_ = syscall.CloseHandle(k.proc)
	}
}

func (k *treeKiller) sweep() {
	if k.job != 0 {
		terminateJobObject(k.job, 1)
		return
	}
	k.snapshotSweep()
}

// terminateRoot ends the root through a handle — ours, or Go's — never a pid.
func (k *treeKiller) terminateRoot() {
	if k.proc != 0 {
		_ = syscall.TerminateProcess(k.proc, 1)
		return
	}
	if k.osProc != nil {
		_ = k.osProc.Kill() // handle-backed; ErrProcessDone once waited
	}
}

// snapshotSweep is the no-job fallback. Every candidate is opened FIRST, its
// creation time read through that handle, the order decided by buildKillOrder
// (which drops any row created before the parent it names — a stale PPID), and
// the victim terminated through the SAME handle, so a pid recycled between the
// snapshot and the kill cannot be hit.
func (k *treeKiller) snapshotSweep() {
	rows, err := snapshotProcesses()
	if err != nil {
		k.terminateRoot()
		return
	}
	rootSeen := false
	for i := range rows {
		if rows[i].PID == k.pid {
			rootSeen = true
			break
		}
	}
	if !rootSeen {
		// The root has exited (its row is gone) but its pid is still pinned by
		// our handle, so a row naming it as parent really is its child.
		rows = append(rows, procEntry{PID: k.pid, PPID: k.pid})
	}
	cands := descendantCandidates(rows, k.pid)
	handles := make(map[uint32]syscall.Handle, len(cands))
	defer func() {
		for _, h := range handles {
			_ = syscall.CloseHandle(h)
		}
	}()
	for i := range rows {
		pid := rows[i].PID
		if pid == k.pid {
			rows[i].Created = k.created
			continue
		}
		if !cands[pid] {
			continue
		}
		if _, dup := handles[pid]; dup {
			continue
		}
		h, err := syscall.OpenProcess(processTerminate|processQueryLimitedInformation, false, pid)
		if err != nil {
			continue // Created stays 0: unprovable, so not adopted
		}
		rows[i].Created = processCreationTime(h)
		handles[pid] = h
	}
	for _, pid := range buildKillOrder(rows, k.pid) {
		if pid == k.pid {
			k.terminateRoot()
			continue
		}
		if h, ok := handles[pid]; ok {
			_ = syscall.TerminateProcess(h, 1)
		}
	}
}

func createJobObject() (syscall.Handle, bool) {
	r, _, _ := procCreateJobObjectW.Call(0, 0)
	if r == 0 {
		return 0, false
	}
	return syscall.Handle(r), true
}

func assignProcessToJobObject(job, proc syscall.Handle) bool {
	r, _, _ := procAssignProcessToJobObject.Call(uintptr(job), uintptr(proc))
	return r != 0
}

func terminateJobObject(job syscall.Handle, exitCode uint32) {
	_, _, _ = procTerminateJobObject.Call(uintptr(job), uintptr(exitCode))
}

// processCreationTime returns h's creation time in FILETIME ticks, or 0.
func processCreationTime(h syscall.Handle) uint64 {
	var created, exited, kernel, user syscall.Filetime
	if err := syscall.GetProcessTimes(h, &created, &exited, &kernel, &user); err != nil {
		return 0
	}
	return uint64(created.HighDateTime)<<32 | uint64(created.LowDateTime)
}

// snapshotProcesses reads the live process table via the ToolHelp API.
func snapshotProcesses() ([]procEntry, error) {
	snap, err := syscall.CreateToolhelp32Snapshot(syscall.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil, err
	}
	defer syscall.CloseHandle(snap)

	var entry syscall.ProcessEntry32
	entry.Size = uint32(unsafe.Sizeof(entry))

	if err := syscall.Process32First(snap, &entry); err != nil {
		return nil, err
	}
	out := make([]procEntry, 0, 256)
	for {
		out = append(out, procEntry{PID: entry.ProcessID, PPID: entry.ParentProcessID})
		if err := syscall.Process32Next(snap, &entry); err != nil {
			// ERROR_NO_MORE_FILES ends the walk; any other error still leaves us
			// with a usable partial table, which is better than aborting the kill.
			break
		}
	}
	return out, nil
}
