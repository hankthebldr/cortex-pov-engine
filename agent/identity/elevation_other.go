//go:build !windows

package identity

import "os"

// tokenElevated is "running as root" on POSIX. The Windows identity rules are
// the only consumer; POSIX resolution does not read it.
func tokenElevated() bool { return os.Geteuid() == 0 }
