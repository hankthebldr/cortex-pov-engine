//go:build linux

package main

import "syscall"

// prSetChildSubreaper is PR_SET_CHILD_SUBREAPER from <linux/prctl.h>.
const prSetChildSubreaper = 36

// setChildSubreaper makes orphaned descendants reparent to this process rather
// than to PID 1, so the reaper can collect them.
func setChildSubreaper() error {
	if _, _, errno := syscall.RawSyscall(syscall.SYS_PRCTL, prSetChildSubreaper, 1, 0); errno != 0 {
		return errno
	}
	return nil
}
