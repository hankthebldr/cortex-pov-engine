//go:build !linux

package main

import (
	"fmt"
	"runtime"
)

// setChildSubreaper is linux-only (PR_SET_CHILD_SUBREAPER); elsewhere orphans
// reparent to PID 1 and this reports that --subreaper cannot be honoured.
func setChildSubreaper() error {
	return fmt.Errorf("child subreaper is not supported on %s", runtime.GOOS)
}
