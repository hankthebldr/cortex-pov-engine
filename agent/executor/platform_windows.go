//go:build windows

package executor

// Windows process control for the executor.
//
// Two things POSIX gives free have to be built by hand here:
//
//   - THE SHELL. There is no `sh`. Windows scenarios are PowerShell (or cmd
//     batch when SimCore resolves a step to shell=cmd), and Windows PowerShell
//     5.1 is the only interpreter guaranteed present on a customer's image
//     (see script.go). Both are launched by ABSOLUTE path under %SystemRoot%
//     (BEACON-12), never resolved through PATH.
//
//   - THE TREE KILL. There is no process group a signal can reach. The tree is
//     a Job Object instead (jobobject_windows.go): every child is assigned to
//     one right after Start, and an abort terminates the job, which reaches
//     every descendant without ever naming one by pid.
//
// Staying inside `syscall` (plus kernel32 through syscall.NewLazyDLL) keeps the
// beacon dependency-free, which is the property that lets it cross-compile to a
// single static binary a DC can copy onto an air-gapped host.

import (
	"fmt"
	"os"
	"os/exec"
	"sync"
	"syscall"
)

var (
	systemRootOnce sync.Once
	systemRoot     string
)

func hostSystemRoot() string {
	systemRootOnce.Do(func() { systemRoot = os.Getenv("SystemRoot") })
	return systemRoot
}

// powerShellExePath is the absolute path of Windows PowerShell 5.1, resolved
// once from %SystemRoot% (C:\Windows when it is unset).
func powerShellExePath() string { return windowsPowerShellPath(hostSystemRoot()) }

// cmdExePath is the absolute path of cmd.exe, resolved the same way.
func cmdExePath() string { return windowsCmdPath(hostSystemRoot()) }

// normalizeStepStderr turns the CLIXML that Windows PowerShell 5.1 writes to a
// redirected stderr back into text (see clixml.go). Applied to every step's
// captured stderr on this host; bytes that are not that envelope are unchanged.
func normalizeStepStderr(s string) string { return decodeCLIXMLStderr(s) }

// stderrChunkFilter is normalizeStepStderr for the live-streamed path: it
// returns the callback to hand stderr chunks to, and a flush to call once the
// process has exited.
func stderrChunkFilter(emit func(string)) (write func(string), flush func()) {
	f := newCLIXMLChunkFilter(emit)
	return f.Write, f.Flush
}

// newShellCmd builds the per-step command: a PowerShell process carrying the
// exit-code wrapper, base64-encoded so no quoting layer can corrupt the TTP.
//
// CREATE_NEW_PROCESS_GROUP detaches the child from the beacon's console group,
// so a Ctrl-C delivered to an interactively-run beacon does not race our own
// orderly teardown of an in-flight step.
func newShellCmd(cmdStr string) *exec.Cmd {
	argv := powerShellArgv(powerShellExePath(), cmdStr)
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP}
	return cmd
}

// newShellCmdFor builds the per-step command for a step shell: "" and
// "powershell" are PowerShell, "cmd" is cmd.exe running the author's batch text
// from a temp file (PUSH-18). The returned cleanup removes that file and must be
// called after the command has exited.
//
// BEACON-18: a PowerShell step whose command line would not fit in
// CreateProcess's 32,767 characters is refused here, with the sizes, instead
// of failing inside Start() as "The filename or extension is too long". (A cmd
// step runs from a file, so its text never touches the limit.)
func newShellCmdFor(shell, cmdStr string) (*exec.Cmd, func(), error) {
	switch shell {
	case "", ShellPowerShell:
		if err := checkPowerShellCommandLine(powerShellExePath(), cmdStr); err != nil {
			return nil, func() {}, err
		}
		return newShellCmd(cmdStr), func() {}, nil
	case ShellCmd:
		file, cleanup, err := writeCmdScript(cmdStr)
		if err != nil {
			return nil, func() {}, err
		}
		argv, cmdLine := cmdInvocation(cmdExePath(), file)
		cmd := exec.Command(argv[0], argv[1:]...)
		cmd.SysProcAttr = &syscall.SysProcAttr{
			CmdLine:       cmdLine,
			CreationFlags: syscall.CREATE_NEW_PROCESS_GROUP,
		}
		return cmd, cleanup, nil
	default:
		return nil, func() {}, fmt.Errorf("step shell %q is not executable on windows (powershell|cmd)", shell)
	}
}

// newAnchorCmd builds the persistent anchor for a ChainSession: PowerShell in
// `-Command -` mode, which executes statements read from stdin. Each step is
// then dispatched from that anchor as a nested child (script.go), so the
// causality chain is a genuine parent→child process tree.
func newAnchorCmd(cgoImage string) *exec.Cmd {
	args := append(append([]string{}, powerShellArgs...), "-Command", "-")
	cmd := exec.Command(powerShellExePath(), args...)
	cmd.Env = anchorEnv()
	if cgoImage != "" {
		// BEACON-08: Windows builds the child's COMMAND LINE from Args, while
		// Path is passed separately as lpApplicationName. Rewriting Args[0]
		// therefore relabels the command line ONLY — the process IMAGE is still
		// powershell.exe, and image-keyed telemetry (image name, image path,
		// hash, signer) reports powershell.exe, not cgoImage. Unlike POSIX
		// argv[0], it does not change what a sensor calls the process.
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

// chainStepScriptFor renders one step for the anchor under a step shell. A cmd
// step's batch text goes to a temp file the anchor hands to cmd.exe; cleanup
// removes it once the step's sentinel has been read.
func chainStepScriptFor(marker, stepID, shell, wrappedCmd string) (string, func(), error) {
	switch shell {
	case "", ShellPowerShell:
		// BEACON-18: the anchor launches the step child with the same argv, so
		// the same limit applies. Refused here the step is an execution error
		// that names its cause; let through it was a child "not launched".
		if err := checkPowerShellCommandLine(powerShellExePath(), wrappedCmd); err != nil {
			return "", func() {}, err
		}
		return powerShellChainStepScriptFor(powerShellExePath(), marker, stepID, wrappedCmd), func() {}, nil
	case ShellCmd:
		file, cleanup, err := writeCmdScript(wrappedCmd)
		if err != nil {
			return "", func() {}, err
		}
		return powerShellChainCmdStepScript(cmdExePath(), marker, stepID, file), cleanup, nil
	default:
		return "", func() {}, fmt.Errorf("step shell %q is not executable on windows (powershell|cmd)", shell)
	}
}
