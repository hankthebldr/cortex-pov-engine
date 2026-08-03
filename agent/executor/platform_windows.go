//go:build windows

package executor

// Windows process control for the executor.
//
// Two things POSIX gives free have to be built by hand here:
//
//   - THE SHELL. There is no `sh`. Windows scenarios are PowerShell, and
//     Windows PowerShell 5.1 is the only interpreter guaranteed present on a
//     customer's image (see script.go).
//
//   - THE TREE KILL. There is no process group a signal can reach. `syscall`
//     exposes neither CreateJobObject nor CTRL_BREAK delivery to a
//     non-console child, so an abort that only calls TerminateProcess on the
//     shell leaves every tool the scenario launched running while SimCore
//     reports the run stopped. We instead snapshot the process table and
//     terminate the shell's descendants leaf-first (see proctree.go).
//
// Staying inside `syscall` keeps the beacon dependency-free, which is the
// property that lets it cross-compile to a single static binary a DC can copy
// onto an air-gapped host.

import (
	"os"
	"os/exec"
	"syscall"
	"time"
	"unsafe"
)

// newShellCmd builds the per-step command: a PowerShell process carrying the
// exit-code wrapper, base64-encoded so no quoting layer can corrupt the TTP.
//
// CREATE_NEW_PROCESS_GROUP detaches the child from the beacon's console group,
// so a Ctrl-C delivered to an interactively-run beacon does not race our own
// orderly teardown of an in-flight step.
func newShellCmd(cmdStr string) *exec.Cmd {
	args := append(append([]string{}, powerShellArgs...),
		"-EncodedCommand", encodePowerShellCommand(powerShellExitWrapper(cmdStr)))
	cmd := exec.Command(powerShellExe, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP}
	return cmd
}

// newAnchorCmd builds the persistent anchor for a ChainSession: PowerShell in
// `-Command -` mode, which executes statements read from stdin. Each step is
// then dispatched from that anchor as a nested PowerShell child (script.go), so
// the causality chain is a genuine parent→child process tree.
func newAnchorCmd(cgoImage string) *exec.Cmd {
	args := append(append([]string{}, powerShellArgs...), "-Command", "-")
	cmd := exec.Command(powerShellExe, args...)
	cmd.Env = anchorEnv()
	if cgoImage != "" {
		// Windows builds the child's command line from Args (Path is passed
		// separately as lpApplicationName), so rewriting Args[0] relabels the
		// observed command line exactly as the POSIX argv[0] rewrite does,
		// while still executing powershell.exe.
		cmd.Args[0] = cgoImage
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP}
	return cmd
}

// anchorExitScript is what Close() writes to end the anchor cleanly. PowerShell
// in -Command - mode terminates on `exit` just as sh does.
const anchorExitScript = "exit\n"

// anchorEnv suppresses the terminal control sequences a PowerShell host prompt
// loop emits around every statement it reads from stdin. Verified against
// PowerShell 7 (which honours TERM) — the sentinel matcher strips them anyway
// (stripANSI), but keeping them out of the stream in the first place also keeps
// them out of the step output stored as evidence in the run record.
func anchorEnv() []string {
	return append(os.Environ(), "TERM=dumb")
}

// chainStepScript renders one step for the anchor shell.
func chainStepScript(marker, wrappedCmd string) string {
	return powerShellChainStepScript(marker, wrappedCmd)
}

// killProcessTree terminates pid and every descendant of it, leaf-first.
//
// There is no graceful phase. The POSIX path can send SIGTERM and let a tool
// clean up, but a console-less Windows child has no equivalent that is
// deliverable here — WM_CLOSE needs a window and CTRL_BREAK_EVENT needs a
// shared console. TerminateProcess is therefore the only reliable primitive,
// and reliability is the requirement: an abort the operator believes happened
// must actually have happened.
//
// The table is re-snapshotted after the kill sweep so processes spawned in the
// window between snapshot and termination are still caught. killGrace is the
// pause between the two sweeps.
func killProcessTree(pid int) {
	root := uint32(pid)
	terminateTree(root)
	time.Sleep(killGrace)
	// Second sweep: anything the first pass raced (a child created after the
	// snapshot but before its parent died) is reparented or still live.
	terminateTree(root)
}

func terminateTree(root uint32) {
	entries, err := snapshotProcesses()
	if err != nil {
		// Snapshotting can fail under low resources or a restricted token. Fall
		// back to killing at least the process we own rather than nothing.
		terminatePID(root)
		return
	}
	for _, target := range buildKillOrder(entries, root) {
		terminatePID(target)
	}
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

func terminatePID(pid uint32) {
	if pid == 0 {
		return
	}
	h, err := syscall.OpenProcess(syscall.PROCESS_TERMINATE, false, pid)
	if err != nil {
		return // already gone, or not ours to kill
	}
	defer syscall.CloseHandle(h)
	_ = syscall.TerminateProcess(h, 1)
}
