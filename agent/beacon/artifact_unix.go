//go:build !windows

package beacon

// POSIX half of artifact staging. Mirrors executor/platform_unix.go: only the
// genuinely syscall-dependent pieces live behind the build tag — the dest
// policy and stage-root logic take `goos` explicitly and stay in artifact.go so
// a Linux runner can exercise the Windows rules.

import (
	"os"
	"os/exec"
	"runtime"
	"syscall"
)

// openFlags creates the .part file.
//
// O_EXCL refuses an existing name and O_NOFOLLOW refuses a final-component
// symlink — both at the syscall, so there is no check-then-create race to lose.
// A server-supplied dest is a write on a host the DC does not own, by a process
// that is frequently root.
func openFlags() int {
	return os.O_WRONLY | os.O_CREATE | os.O_EXCL | syscall.O_NOFOLLOW
}

// applyMode sets the POSIX permission bits on the staged file.
//
// Files default 0755 (read+execute for all) and the beacon deliberately does
// NOT chown to the step identity: that would need root and would hand the target
// user a file they can then modify — a privilege-escalation primitive planted on
// a customer's jumpbox. Read+execute is sufficient and is the minimum that works.
func applyMode(path, mode string) error {
	m, err := parseMode(mode)
	if err != nil {
		return err
	}
	return os.Chmod(path, m)
}

// execProbeSupported reports whether a noexec pre-flight is meaningful here.
func execProbeSupported() bool { return true }

// defaultExecProbe runs the staged probe script. Injectable via
// BeaconClient.artifactProbeExec so the failure branch is testable from CI,
// which cannot mount a noexec filesystem unprivileged.
func defaultExecProbe(probePath string) error {
	return exec.Command(probePath).Run()
}

func hostGOOS() string { return runtime.GOOS }
