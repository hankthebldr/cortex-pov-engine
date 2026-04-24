// Package executor provides low-level shell command execution with output capture.
package executor

import (
	"bytes"
	"os/exec"
	"syscall"
)

// RunCommand executes cmdStr via "sh -c" and captures stdout and stderr separately.
// A non-zero exit code is NOT treated as an error — it is returned as exitCode.
// err is only non-nil for failures that prevent execution from starting (e.g. exec not found).
func RunCommand(cmdStr string) (stdout, stderr string, exitCode int, err error) {
	cmd := exec.Command("sh", "-c", cmdStr)

	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf

	runErr := cmd.Run()
	stdout = outBuf.String()
	stderr = errBuf.String()

	if runErr != nil {
		// Check whether it is purely an exit-code failure or a real execution error.
		if exitErr, ok := runErr.(*exec.ExitError); ok {
			if status, ok := exitErr.Sys().(syscall.WaitStatus); ok {
				exitCode = status.ExitStatus()
			} else {
				exitCode = 1
			}
			// Non-zero exit is not an error from the caller's perspective.
			return stdout, stderr, exitCode, nil
		}
		// Real error (binary not found, permission denied, etc.)
		return stdout, stderr, -1, runErr
	}

	return stdout, stderr, 0, nil
}
