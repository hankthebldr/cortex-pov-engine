//go:build windows

package beacon

// Windows half of artifact staging.
//
// Two honest limits are recorded here rather than papered over:
//
//  1. There is no O_NOFOLLOW analogue in stdlib syscall. O_EXCL refuses an
//     existing name INCLUDING an existing reparse point, but a junction created
//     between our check and the create is a residual gap.
//  2. NTFS has no exec bit — executability is extension + ACL. `mode` is
//     recorded in the ledger and otherwise ignored. Printing "mode 0755 applied"
//     on Windows would be exactly the small lie the taxonomy exists to stop.

import (
	"os"
	"runtime"
)

func openFlags() int {
	return os.O_WRONLY | os.O_CREATE | os.O_EXCL
}

// applyMode is a no-op on Windows: see the file comment. It still validates the
// mode string so a malformed spec fails the same way on both platforms.
func applyMode(path, mode string) error {
	_, err := parseMode(mode)
	return err
}

// execProbeSupported is false: Windows has no `noexec` mount concept, so the
// probe would test nothing. AppLocker / WDAC can block execution and cannot be
// pre-flighted — stated, not silently assumed away.
func execProbeSupported() bool { return false }

func defaultExecProbe(probePath string) error { return nil }

func hostGOOS() string { return runtime.GOOS }
